# 配置收敛为单一声明源（方案建议）

> 状态：方案（proposal），**不在本地实施**
> 关联任务：审计 T41、T17；决策项 Q1（继续跟上游）、Q3（本地 CLI 生成后端）
> 现状基线：2026-11-15，`main`

## 1. 结论先行

配置体系的大改属于上游核心文件改造。按任务书 Q1 的默认决策（**继续跟上游，不做彻底分叉**），本项以「提给上游的方案」交付，本地只保留两件事：

1. 结构性测试作为过渡保护（T17 已完成：`tests/test_config_env_loading.py`）。
2. 本文档记录问题、目标设计、迁移路径和兼容性边界，供提交上游 issue / PR 时直接引用。

本地**不**执行拆除三处声明、不新增第四套抽象，避免与上游持续合并冲突。

## 2. 现状与量化证据

三份文件合计约 1.5 万行，同一个配置键的类型、默认值、范围、界面元数据要分别维护：

| 文件 | 行数 | 承担职责 |
| --- | --- | --- |
| `src/config.py` | 3973 | `Config` dataclass 字段与默认值、`_load_from_env` 环境变量解析、`__post_init__` / `validate()` / `validate_structured()` 校验 |
| `src/core/config_registry.py` | 5335 | 键的展示名、分组、`validation`（min/max/enum）、帮助文案、`.env` 示例代码块 |
| `src/services/system_config_service.py` | 5761 | 键清单、`_validate_value` / `_validate_numeric_range` 运行时校验、跨字段校验、读写与掩码 |

扫描三份源码中出现的引号包裹键名：

- 出现在 `config.py` 的键：285 个
- 出现在 `config_registry.py` 的键：275 个
- 出现在 `system_config_service.py` 的键：155 个
- **同时出现在三份文件的键：101 个**；出现在其中两份的：114 个

也就是说，约 101 个键至少要手工同步 3 处（很多还要额外同步 `.env.example`、`docs/`、前端设置页分组），任何一个漏改都会造成"文档/界面/运行时"三者不一致。

### 2.1 一个具体键的同步成本

以 `GEMINI_MODEL`（默认 `gemini-3.6-flash`）为例，改一次默认值需要动 6 个位置：

| 位置 | 内容 |
| --- | --- |
| `src/config.py:1065` | `gemini_model: str = "gemini-3.6-flash"`（dataclass 默认值） |
| `src/config.py:1728` | `os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')`（渠道解析时的兜底） |
| `src/config.py:1993` | `gemini_model=os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')`（`cls(...)` 装载） |
| `src/core/config_registry.py:1490` | 键的展示元数据（分组、说明） |
| `src/core/config_registry.py:4722` | `.env` 示例代码块里的默认值文本 |
| `src/services/system_config_service.py:3725` | `(effective_map.get("GEMINI_MODEL") or "gemini-3.6-flash")`（Web 设置页读取兜底） |

`AGENT_LLM_CALL_TIMEOUT_S` 这类相对简单的键也有 2 处（字段声明 + `parse_env_float` 装载），若加范围校验还要在 registry 的 `validation` 与 service 的 `_validate_numeric_range` 之间再对齐一次。

### 2.2 已经出现过的漂移

- T17：`agent_chat_tool_reuse_seconds`、`agent_llm_call_timeout_s`、`agent_opt_in_thinking`、`agent_chat_stream_content` 四个字段在 `config.py` 与 `.env.example` 里都有，但 `_load_from_env` 从未读取，配置写了不生效。
- 默认值漂移：同一个键在 `Config` 字段、`os.getenv` 兜底、Web 服务读取兜底三处必须写同一个字面量，任何一处漏改都会让"启动配置"和"设置页显示"不一致。

## 3. 目标设计：一张声明表驱动全部

### 3.1 单一真源

新增一张 `CONFIG_FIELDS` 声明表，每个键一条记录，字段覆盖当前分散在三处的所有信息：

```python
ConfigField(
    key="AGENT_LLM_CALL_TIMEOUT_S",       # 环境变量名
    attr="agent_llm_call_timeout_s",      # Config 属性名
    type="float",                         # 解析器类型
    default=0.0,                          # 唯一默认值
    validation={"min": 0.0, "max": 600.0},
    section="agent",                      # 设置页分组
    label="LLM 单次调用超时",
    description="0 表示不限制单次调用耗时",
    env_example="AGENT_LLM_CALL_TIMEOUT_S=90",
    sensitive=False,
)
```

### 3.2 一个解析器，三处消费

| 消费方 | 从声明表得到什么 |
| --- | --- |
| `Config` 装载 | `_load_from_env` 由表驱动循环生成，不再逐键手写 `parse_env_*` |
| `system_config_service` | 键清单、类型、范围校验、掩码、分组、帮助文案全部读表 |
| 文档与示例 | `.env.example` 与文档中的默认值由脚本从表生成并做 `--check` 校验 |

### 3.3 明确不放进声明表的东西

- 跨字段规则（如"启用 A 必须同时配置 B"）保留为独立校验函数，注册到表上的 `cross_checks` 键，而不是塞进单字段记录。
- 需要访问外部系统才能确定的值（运行时探测的模型列表、数据源能力）不进入声明表。

## 4. 迁移路径（建议上游按阶段接纳）

1. **阶段 0（本地已完成）**：结构性测试守住 `Config` 字段 ↔ `_load_from_env` 装载一致，新增字段未装载会直接失败。
2. **阶段 1**：新增声明表与生成器，但**不接线**；用测试断言"表 ↔ `Config` 字段"双向一致，历史键差异进入显式豁免清单。
3. **阶段 2**：`_load_from_env` 改为表驱动，逐键删除手写 `parse_env_*`；行为等价由现有配置测试与阶段 1 的一致性断言共同保证。
4. **阶段 3**：`system_config_service` 的键清单、`validation`、分组、文案改为读表；跨字段校验保留。
5. **阶段 4**：`.env.example` 与文档默认值改为脚本生成，CI 加 `--check`。

每个阶段都可独立合入、独立回滚，不需要一次性大爆炸式替换。

## 5. 兼容性与风险

| 风险 | 处理 |
| --- | --- |
| 外部脚本直接读 `Config` 属性名 | 声明表必须保留 `attr` 映射，属性名不允许顺手重命名 |
| 部分键只存在于 registry 或 service，不在 `Config` | 阶段 1 的豁免清单逐项写明原因，禁止静默丢键 |
| 环境变量解析语义差异（空串 / `"false"` / `"0"`） | 解析器沿用现有 `parse_env_bool/int/float` 语义，不改写边界行为 |
| 与上游持续冲突 | 方案优先提交上游；本地不落地结构性删除，只保留测试与文档 |
| 密钥类字段暴露 | `sensitive` 标记驱动掩码，保持现有 `mask_token` 行为不变 |

## 6. 与 Q3（本地 CLI 生成后端）的关系

`GENERATION_BACKEND` 目前可选 `litellm` / `codex_cli` / `claude_code_cli` / `opencode_cli`，后三者均为本地 CLI 生成后端，在 `config_registry.py:155-158` 统一标注为 experimental。

按 Q3 的默认结论处理：

- **不删除**（属于上游文件与上游能力）。
- **不再新增维护投入**：不为它们补充新特性、不做专项适配；出现故障时按"上游同步"处理，而不是本地加固。
- 声明表迁移时仍为这些键保留记录，避免它们变成"只存在于 registry 的孤儿键"。

## 7. 验收（本方案交付）

- 结构性测试可运行：`python -m pytest tests/test_config_env_loading.py -q`。
- 本文档中的行数、键数量、具体行号与当前 `main` 一致；源码变化后需同步更新（与 T42 的 `docs/ai/CODEMAP.md` 检查机制一并维护）。
- 提交上游所需的材料：本文档 + `tests/test_config_env_loading.py` + 一张声明表示例（阶段 1 草案，待上游确认后再落地）。
