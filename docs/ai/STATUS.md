# STATUS · 能力现状与改动禁区

> 手写维护。每个阶段结束（发版 / 里程碑合入）时更新一次。
> 目的：新会话只读 `CODEMAP.md` + `STATUS.md` 就能判断「这个能力是不是默认开着」「这个文件能不能大改」。

## 1. 默认开启的能力（不需要额外配置）

| 能力 | 说明 | 代码锚点 |
| --- | --- | --- |
| 报告对象统一构造 | 同步分析、任务状态回退、历史详情返回同一份 `meta/summary/strategy/details` | `api/v1/report_builder.py` |
| 通知统一分发 | 所有渠道走 `send_with_results()`，逐渠道隔离失败并写诊断 | `src/notification.py` |
| 问股守卫规则层 | 规则判不出时直接放行，不再默认调模型分类 | `api/v1/endpoints/agent.py`（`_check_domain_guard`） |
| 首问引导（intake） | 新会话首问生成 1–5 个受控选项；后端不支持时前端回退 legacy 提示 | `api/v1/endpoints/agent.py:1121` |
| 数据新鲜度标注 | 日线落后时报告写「截至 X（落后 N 个交易日）」并降级数据块 | `src/analyzer.py`、`src/core/pipeline.py` |
| 分析任务取消 | `POST /api/v1/analysis/tasks/{task_id}/cancel`，排队即取消、运行中在进度检查点中止 | `api/v1/endpoints/analysis.py` |
| 选股单飞 | 已有 `pending/processing/cancel_requested` 选股任务时再次提交返回 409 | `api/v1/endpoints/screening.py` |
| 问股页面拆分与记忆化 | 侧栏 / 消息 / 进度 / 输入 / 引导卡独立组件，打字不重渲染历史消息 | `apps/dsa-web/src/components/chat/` |

**默认关闭**：`WEBUI_ENABLED=false`、`SCHEDULE_ENABLED=false`；通知渠道默认未配置，未配置就不发送。自动打 tag 需要 commit title 含 `#patch/#minor/#major`。

## 2. 已完成里程碑

| 任务 | 内容 | 状态 |
| --- | --- | --- |
| T37 | 美股超跌反弹回测引擎修复与重跑 | 已合入 |
| T38 | 通知分发合并为 `send_with_results()` 单一实现 | 已合入 |
| T39 | 历史记录 → 报告对象三份合一（`report_builder`） | 已合入 |
| T40 | `ChatPage` 拆分 + 选择器订阅 + 消息记忆化 + 输入法 Enter 保护 | 已合入 |
| T41 | 配置单一声明源：以方案交付（Q1 继续跟上游），本地保留 T17 结构性测试 | 已合入（文档） |
| T42 | 常青文档：`docs/ai/` 五份文档 + `scripts/gen_ai_codemap.py --check` | 已合入 |
| T43 | 测试治理：`tests/support/pipeline_factory.py` 统一 pipeline 构造，清理旧常量断言 | 已合入 |
| T44 | 双语补齐（第一批）：选股页与模型渠道编辑器文案迁入 `uiText.ts` | 已合入 |

## 3. 半成品与已知限制

| 项 | 状态 | 处理建议 |
| --- | --- | --- |
| 配置三处声明 | `config.py` / `config_registry.py` / `system_config_service.py` 仍需手工同步（101 个键同时出现在三处） | 方案见 `docs/config-single-source.md`；本地不再扩大同步面，新增键必须同时补齐 T17 测试与 `.env.example` |
| 本地 CLI 生成后端 | `codex_cli` / `claude_code_cli` / `opencode_cli` 标注 experimental | 按 Q3：不删、不再新增维护投入；故障按上游同步处理 |
| 微信仪表盘图片渲染 | 依赖 Pillow 与中文字体，缺字体时降级文本 | 排障见 `docs/alerts.md`、`docs/FAQ.md` |
| 前端双语文案 | 选股页与渠道编辑器已迁入 `uiText.ts`；扫描器整页、告警页、持仓页仍中英混排，`uiText.ts` 与 `report_language.py` 各维护一套 | 改文案时两边评估；新增页面文案只进 `uiText.ts`，旧页面按 T44 后续批次迁移 |
| 测试治理 | 部分大测试文件数千行、跨层 mock | 新建 pipeline 测试统一用 `tests/support/pipeline_factory.make_pipeline()`；旧断言不再为现状拍照 |

## 4. 已移出主干

- `data_provider/market_router/`：未接线，已按 Q2 移出主干；将来需要分钟线时以 Fetcher 形式重新接入，不要恢复旧目录。
- 调度器「重启会杀分析进程」的旧行为：已改为只重建调度线程。

## 5. 不要大改的上游文件

这些文件与上游持续合并，改动应当是「最小、可解释、可回滚」的补丁，不要顺手重构：

| 文件 | 原因 |
| --- | --- |
| `src/config.py` | 3973 行，字段/装载/校验三合一；大改会与上游冲突 |
| `src/core/config_registry.py` | 5335 行，键元数据与 `.env` 示例文本耦合 |
| `src/services/system_config_service.py` | 5761 行，校验与写入语义密集 |
| `src/storage.py` | 5355 行，表结构与迁移历史多，改字段需要考虑存量数据 |
| `data_provider/base.py` | 5164 行，fallback 主逻辑，改顺序影响所有数据源 |
| `src/analyzer.py` | 6032 行，Prompt 与结构化解析耦合 |
| `src/llm/local_cli_backend.py` | 2966 行，上游 experimental 能力 |

**判断方法**：改动前先看 `docs/ai/CODEMAP.md` 的「引用」列，引用次数高的模块影响面大；再确认是否有对应测试覆盖。

## 6. 文档维护入口

| 文档 | 维护方式 |
| --- | --- |
| `docs/ai/CODEMAP.md` | 生成：`python scripts/gen_ai_codemap.py`；CI `--check` 校验 |
| `docs/ai/FLOWS.md` | 手写，链路变化时更新 |
| `docs/ai/DATA-FLOW.md` | 手写，数据源 / 字段 / 提示词变化时更新 |
| `docs/ai/PITFALLS.md` | 手写，踩坑后追加 |
| `docs/ai/STATUS.md` | 手写，每个阶段结束更新 |
