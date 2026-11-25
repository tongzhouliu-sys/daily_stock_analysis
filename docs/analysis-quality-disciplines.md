# 分析质量纪律 · 需求书与任务书

> 来源：对 `docs/tyrone/fenxi.md` + `docs/tyrone/fenxi20261123.md`（DESK 美股日报方法论）的吸收评估。
> 结论：只吸收「Prompt/上下文层的分析纪律」与「用现有数据可算的确定性定标信号」，不动报告结构、不引新数据源、不照搬手工 desk 工作流。
> 本文是需求书（第 2 节）+ 任务书（第 3 节），供实施阶段对照执行。实施前请再读 `docs/ai/CODEMAP.md`、`docs/ai/STATUS.md`。

---

## 1. 范围总览

| 编号 | 项 | 优先级 | 性质 | 落点 |
| --- | --- | --- | --- | --- |
| P0-1 | 证据分级 | P0 | Prompt 层纪律 | 个股 / 大盘复盘 / 问股 |
| P0-2 | 多空分歧对照 | P0 | Prompt 层纪律 | 个股 / 大盘复盘 / 问股 |
| P1-1 | 收盘定位定标 | P1 | 确定性计算 | 大盘复盘（A 股等有 OHLC 的市场） |
| P2-1 | 场景化验证点 | P2 | 输出增强 | 大盘复盘「明日交易计划」 |
| P2-2 | us 区时间切片 | P2 | Prompt 层纪律 | 仅 us 区复盘 |
| P2-3 | 矛盾数据不采用 | P2 | Prompt 层规则 | 个股 / 大盘复盘 |

**明确不做（非目标）**：
- 跨资产信用定标（HYG/TLT/GLD 等美股 ETF）——需新数据源，违反「克制、稳定性优先」。
- 来源索引 / Clocks / Limits 成独立章节——牵动 `_split_report_sections`、通知解析、飞书同源 HTML 一整条链，回归面大。
- 手工数据采集层（读 X 账号、CBS 文字稿、授权资料库）——不可自动化，涉版权/抓取风险。
- 产业链传导、主轴提炼——需大量领域知识注入，短期投入产出比低。

**关键约束（贯穿全程）**：
- `src/analyzer.py`（6032 行）在 `docs/ai/STATUS.md` 第 5 节被列为「不要大改的上游文件」，改动必须是**最小、可解释、可回滚的补丁**，只增删 Prompt 指令文本，不重构解析结构。
- 新增字段/结构若只被 Prompt 引用、无真实生产调用方，不得合入（AGENTS.md 1.2）。优先走「Prompt 指令让 LLM 自行标注」的零结构改动路线，结构化字段只在确有消费方时再加。

---

## 2. 需求书

### P0-1 证据分级

- **目标**：让 LLM 在使用每条新闻前，先标注其可信度层级，避免把「转述卖方观点 / X 个人观点 / 未确认引语」当已确认事实来下结论。
- **现状**：新闻进 Prompt 只有来源域名 + 时间 + 「关联度」（`relevance_score/category/reasons`，是搜索侧算的与个股相关性，不是可信度）。三处入口均无可信度标注：
  - 个股：`src/analyzer.py:5252`（en）/ `:5267`（zh）新闻块指令，数据来自 `SearchResult.to_context()`（`src/search_service.py:290`）。
  - 大盘复盘：`src/market_analyzer.py:1739-1751` 组装 `news_text`。
  - 问股：`src/agent/tools/search_tools.py` 返回 `title/snippet/source/url/published_date`，下游 `intel_agent.py`/`risk_agent.py` 产出 `risk_alerts`/`latest_news`。
- **分级口径（建议四档）**：`已确认（官方公告/交易所口径）` / `观察中未确认（行情、持仓推断）` / `二手转述（卖方观点、媒体，未见原文）` / `个人观点或待核引语`。
- **验收标准**：
  1. 个股分析：LLM 在 `risk_alerts` / `positive_catalysts` / `latest_news` 中，对每条信息标注其可信度层级（至少二手转述及以上要显式区分）。
  2. 大盘复盘：报告正文对关键催化来源标明层级，不再默认把媒体转述写成事实。
  3. 问股：Agent 结论中引用二手转述时，明确「转述、未见原文」而非写成确定性事实。
- **非目标**：不在 Phase 1 改 `SearchResult` / `news_intel` 表结构；不引入自动可信度打分模型。

### P0-2 多空分歧对照

- **目标**：当催化信息彼此矛盾时，结构化列出「多方说法 / 空方说法 / 存疑」，并点明各说法的**来源立场**（供给方、已偏空券商、前从业者等），再判断市场当前是否在区分这些说法。
- **现状**：个股只有 `signal_attribution`（六字段「最强看多/看空信号」）用于解释推荐构成，不是分歧来源对照；大盘复盘和问股均无分歧结构化输出。
- **验收标准**：
  1. 个股/问股：当同一标的存在方向相反的催化时，输出「分歧」块，含各方说法 + 来源立场 + 「市场当前是否在区分」判断。
  2. 大盘复盘：`消息催化` 节在信息矛盾时列出分歧对照，而非只报单边。
- **非目标**：不做全市场分歧自动扫描；不要求每条新闻都做多空对照（仅在确有矛盾时触发）。

### P1-1 收盘定位定标

- **目标**：用现有指数 OHLC 算「收盘相对日内高低点位置」= `(current - low) / (high - low)`，判断「收在高位（有承接）还是低位（无承接）」，作为复盘的一个确定性诊断信号，弥补当前只报涨跌幅的不足。
- **现状**：`MarketIndex` 已有 `open/high/low/current/prev_close`（`src/market_analyzer.py:60-74`），`_build_indices_block` 已渲染开盘/最高/最低/振幅，但**没有**把「收盘位置」作为诊断维度输出。
- **验收标准**：
  1. 复盘指数表或 `market_light` reasons 中出现「收盘位于日内区间 X%（高/低位）」类信息。
  2. `high == low`（一字板/无振幅）等退化情况不产出除零或误导文案。
- **非目标**：不引等权指数、不引信用端 ETF；A 股/港股/日韩台只要 `_get_main_indices` 有 OHLC 就适用，缺失时静默降级。

### P2-1 场景化验证点

- **目标**：把大盘复盘「明日交易计划」里「一个触发失效条件」升级为「延续 vs 回补（或 进攻 vs 防守）两种情形」的观察条件对照，给出下一交易日可检验的验证点。
- **现状**：`src/market_analyzer.py:1694/1716` 目前只要求「一个触发失效条件」。
- **验收标准**：复盘「明日交易计划」输出两个场景各 1–3 个可观察验证点（如「第一小时涨幅守住 / 回吐」「关键位是否收复」），而非单条失效条件。
- **非目标**：不做自动追踪次日是否命中（回验跟踪不在本期范围）。

### P2-2 us 区时间切片

- **目标**：仅 `us` region 的复盘 Prompt 注入「现金收盘 / 期货电子盘 / 亚洲开盘 / 新闻截止」四时钟口径，避免把隔夜期货当现金收盘。
- **现状**：个股有 freshness 标注、市场层有交易日历 `get_market_now`，但 `us` 复盘没有显式区分上述四个时点。
- **验收标准**：us 复盘 Prompt 中出现时钟口径说明；`cn/hk/jp/kr/tw` 不受影响（不引入无意义改动）。
- **非目标**：不给 A 股等无隔夜期货主指数的市场加该口径。

### P2-3 矛盾数据不采用

- **目标**：加一条轻量规则：休市日出现行情数据、或同一时点数据互相矛盾时，视为异常不采用，并在报告标注。
- **现状**：无显式一致性校验规则；个股有「数据与情报结论一致」检查（`src/analyzer.py:2482/2758`），但非针对行情数据矛盾。
- **验收标准**：Prompt 出现「休市日/矛盾数据不采用」规则；LLM 在遇到矛盾时不自行圆场。
- **非目标**：不做自动数据一致性引擎，只做 Prompt 层规则。

---

## 3. 任务书

> 任务按实施顺序排列（依赖项在前）。每项标注改动锚点、验证、风险。

### T1 · P0-1 证据分级（个股经典路径）

- 改动：`src/analyzer.py:5252`（en）/ `:5267`（zh）新闻块指令，在「时间规则」后追加「来源可信度分级」要求（四档口径 + 输出到 `risk_alerts`/`positive_catalysts`/`latest_news` 条目时须标注层级）。
- 同步评估：`src/analyzer.py` 中结构化 schema 字段（`latest_news` 等）是否需追加 `source_reliability` 子字段——**仅在确认前端/通知有消费方时加**，否则只走 Prompt 文本标注。
- 验证：`python -m py_compile src/analyzer.py`；跑 `tests/` 中涉及 analyzer Prompt 快照/黄金文本的用例，确认仅指令文本变化、解析结构不变。
- 风险：Prompt 文本变化可能破坏依赖原文串的断言（golden test），需定位并更新为引用单一来源，不得「给现状拍照」。

### T2 · P0-1 证据分级（大盘复盘）

- 改动：`src/market_analyzer.py:1739-1751` 的 `news_text` 组装处或新闻块指令，要求正文对关键催化标来源层级；`_build_output_template_sections`（`:1642`）的「消息催化」节补一句分级要求。
- 验证：`python -m py_compile src/market_analyzer.py`；跑市场复盘相关测试。

### T3 · P0-1 证据分级（问股 Agent）

- 改动：`src/agent/tools/search_tools.py` 返回结构不变；在 Agent 的新闻消费指令处（`src/agent/agents/intel_agent.py` / `risk_agent.py` 的系统提示，及 `src/agent/executor.py` 的新闻提取说明）补「转述/个人观点须标注层级」要求。
- 验证：`python -m py_compile` 相关文件；跑 Agent 新闻证据相关测试（`tests/test_agent_news_backfill.py` 等）。

### T4 · P0-2 多空分歧对照

- 改动：
  - 个股：`src/analyzer.py` 输出 schema 增加可选「分歧」块或复用 `intelligence`，并在 `Analyze Task` 指令说明「仅在催化矛盾时输出，不强制」。
  - 大盘复盘：`消息催化` 节指令补「信息矛盾时列多方/空方/存疑 + 来源立场」。
  - 问股：`intel_agent` / `risk_agent` 产出说明补同样要求。
- 依赖：T1/T2/T3（先有可信度层级，来源立场分析才有依据）。
- 验证：三处 `py_compile` + 对应测试；新增一条覆盖「矛盾催化触发分歧块」的回归用例（用可控 mock 新闻）。

### T5 · P1-1 收盘定位定标

- 改动：`src/market_analyzer.py` 新增确定性函数（如 `_close_position_in_range`），在 `_build_indices_block`（`:1417`）或 `_build_market_light_reasons_zh/en`（`:1371/:1394`）中输出「收盘位于日内区间 X%」；`high == low` 时降级不输出。
- 验证：`python -m py_compile src/market_analyzer.py`；补单测覆盖正常、一字板、缺失 OHLC 三种情况。
- 风险：纯确定性，无 LLM 依赖，风险最低；注意 `current` 可能为盘中快照，文案应避免写成「收盘」而用「最新价/收盘」。

### T6 · P2-1 场景化验证点

- 改动：`src/market_analyzer.py:1694/1716` 及 `_build_output_template_sections` 中「明日交易计划」的提示语，从「一个触发失效条件」改为「两种情形各列观察验证点」。
- 验证：`py_compile` + 复盘模板测试；确认报告结构（章节标题）不变，仅该节内容要求变化。

### T7 · P2-2 us 区时间切片

- 改动：`src/market_analyzer.py` `_build_review_prompt`（`:1720`）内，仅 `self.region == "us"` 时注入「四时钟口径」说明块；其余 region 不动。
- 验证：`py_compile`；补一条 `us` 区 prompt 包含时钟说明、`cn` 区不包含的断言。

### T8 · P2-3 矛盾数据不采用

- 改动：`src/analyzer.py` 与 `src/market_analyzer.py` 的指令区各加一条「休市日/矛盾数据不采用」规则。
- 验证：`py_compile` + 相关 prompt 测试。

---

## 4. 实施顺序与依赖

```
T1 (证据分级·个股) ─┬─> T4 (多空分歧对照)
T2 (证据分级·复盘) ─┘        │
T3 (证据分级·问股) ──────────┘
T5 (收盘定位定标)   独立，可与 T1–T3 并行
T6 (场景化验证点)   依赖 T2 的复盘输出结构理解，可与 T4 并行
T7 (us 时间切片)    独立
T8 (矛盾数据)       独立，最后顺手收尾
```

- 建议分两个批次合入：**批次 A = T1–T4（P0 证据分级 + 多空分歧对照）**，**批次 B = T5–T8（P1+P2）**。
- 每批合入后跑 `./scripts/ci_gate.sh`（或至少 `python -m py_compile <changed>` + 相关 pytest）。

---

## 5. 验证矩阵（对应 AGENTS.md 第 6 节）

| 改动面 | 验证 |
| --- | --- |
| Python 后端（`src/analyzer.py`、`src/market_analyzer.py`、`src/agent/**`） | `python -m py_compile <changed>`；`./scripts/ci_gate.sh`；相关 pytest |
| 报告结构 / 通知 / HTML 渲染 | **本期不改报告章节结构**（T1–T8 均在章节内部改指令文本，不新增/删除 `###` 章节），故不触发报告解析、飞书 HTML、图片海报的联动改造。若实施中发现需动结构，必须先按 AGENTS.md 第 7 节评估三端联动。 |
| API / Schema | 仅当 T1 决定加 `source_reliability` 子字段时才涉及，且需同步 `api/v1/report_builder.py`、Web、Desktop 消费方；默认不加。 |
| 文档 | 本文件 + `docs/CHANGELOG.md` `[Unreleased]` 段（扁平格式，每项一行） |

**交付说明需覆盖**：改了什么、为什么、验证情况、未验证项、风险点、回滚方式（AGENTS.md 第 9 节）。

---

## 6. 风险与回滚

| 风险 | 应对 |
| --- | --- |
| Prompt 文本变化破坏 golden/快照测试 | 改指令时同步定位并更新断言为「引用单一来源」，不做现状拍照；T1–T8 均先 `py_compile` 再跑定向测试 |
| 证据分级让输出变长/变啰嗦 | 只要求「二手转述及以上显式区分」，已确认层级可不重复；控制为短标签 |
| 多空分歧被滥用为「每条都列分歧」 | 指令明确「仅在确有矛盾时触发」，非强制 |
| 收盘定位把盘中快照误当收盘 | 文案区分「最新价/收盘」，复用现有 phase 判断口径（`src/analyzer.py` 已有 `_today_has_realtime_overlay` 等） |
| 回滚 | 每项改动都是最小补丁，直接 revert 对应提交即可；无 DB 迁移、无配置项新增 |

---

## 7. 交付口径

- 本任务书本身是 docs 交付：`Docs only, tests not run`，但已逐条核对命令名、文件名、函数名与仓库一致（锚点见各节）。
- 实施批次落地后，`docs/CHANGELOG.md` `[Unreleased]` 追加：`- [改进] 分析质量纪律：证据分级 / 多空分歧对照 / 收盘定位定标 / 场景化验证点 / us 时间切片 / 矛盾数据不采用`（一项一行，不新增 `###` 类目标题）。
