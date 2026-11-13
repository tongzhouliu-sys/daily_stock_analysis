# 问股智能引导 v2 需求：先解析标的，再决定问什么

日期：2026-11-13。任务类型：feat + UX 重构。建议优先级：P0。状态：需求定稿，待实施。

核对基线：本地 HEAD `51412d62`。本文只读当前代码、类型定义、题库和测试，未启动服务、未调用模型、未修改运行代码。文中“当前行为”均指该基线的静态调用链，不代表线上部署版本。

关联文档：

- [问股首次引导与精读回答](stock-chat-guided-answer-task.md)：v1 引导的产品背景；本文对其“1–5 题、对象歧义优先、市场选项”部分做 v2 修订。
- [问股回复终态与可追问性修复需求](stock-chat-stream-terminal-recovery-requirements.md)：v2 不改变终态、停止、轮询和会话归属契约。
- [非投资问题识别与提醒需求](stock-chat-domain-guard-requirements.md)：v2 的自动开始必须继续经过 domain guard。

---

## 1. 结论

当前问股引导的核心问题不是“题目文案不够好”，而是**没有先做标的解析，也没有利用会话里已经知道的信息**：

1. 服务端用一条很粗的正则判断“要不要问市场”，导致“分析贵州茅台”“分析 00700”“分析 0700.HK”“分析 aapl”“分析腾讯”等明确输入也会被追问“你关注哪个市场的标的？”。
2. 只要能开始研究，题库又固定补问“目标、周期、风险侧重”，不管用户原问题是否已经说清楚，也不管本会话上一轮已经确认过什么。
3. “对象”题实际问的是市场枚举（A 股/港股/美股），不是具体标的；选完以后 `object_scope` 存的是 `a_share`/`hk`/`us`，并没有真正锁定股票身份。
4. 首屏快捷问题是一组固定示例，和用户的自选、最近分析、当前会话、市场时段都没有关系，所以每次打开都像同一张静态海报。
5. 整个引导是 3–5 页的分步问卷，用户为了问一句“分析 600519”要先点很多次；界面没有“我已经理解了，直接开始”的默认路径。

v2 的目标是把引导从“问卷”改成**一次标的解析 + 一张可编辑的分析简报**：能确定的对象直接确定，能推断的背景标成推断，只有真正会改变结论的歧义才提问；默认不阻断研究。

一句话验收标准：**用户输入可确定市场的股票代码或名称时，系统不得再问“哪个市场”；一次普通问股默认 0–1 个问题，最多 2 个。**

---

## 2. 用户问题与典型场景

### 2.1 用户原话拆解

> “每次都是出来这几个问题，一点判断都没有。比如对方给股票代码系统应该先判断是哪个市场而不是问客户这么傻的问题。”

包含三个独立诉求：

| 诉求 | 含义 | v2 对应 |
| --- | --- | --- |
| 不要每次固定问同样的问题 | 问题应随输入、会话、标的变化 | R4–R6：意图推断 + 会话继承 + 动态选题 |
| 系统先判断，不要问用户能查到的事 | 代码/名称能解析出市场，就不该问市场 | R1–R3：确定性标的解析 |
| 引导界面要“有判断” | 要展示系统理解了什么、为什么这么理解、哪里不确定 | R7–R8：分析简报 + 来源标记 |

### 2.2 当前代码下的典型推演

以下按 `api/v1/endpoints/agent.py`、`src/agent/intake_questions.py` 的现有逻辑静态推演：

| 用户输入 | 当前是否问“哪个市场” | 当前还会问什么 | 用户感受 |
| --- | --- | --- | --- |
| `分析贵州茅台` | 会。`_needs_object_intake` 看到中文且无 6 位数字/大写 ticker，返回 true | 目标、周期、风险 | 名称唯一，仍被问市场 |
| `分析腾讯` | 会 | 目标、周期、风险 | 港股身份明确，仍被问市场 |
| `分析 00700` | 会。`_EXPLICIT_TICKER_RE` 只认 `\d{6}` 或 `HK\d{4,5}`，5 位纯数字不匹配 | 目标、周期、风险 | 代码已经给了，仍被问市场 |
| `分析 0700.HK` | 会。后缀形式不匹配 | 目标、周期、风险 | 同上 |
| `分析 aapl` | 会。正则只认大写 `[A-Z]{1,5}` | 目标、周期、风险 | 小写 ticker 被当成“没给标的” |
| `分析 600519` | 不会问市场 | 目标、周期、风险 | 代码已明确，仍被固定三问 |
| `分析 AAPL` | 不会问市场，但 | 目标、周期、风险 | 每次都是这三题 |
| `比较 600519 和 中芯国际` | 不会。只要消息里任意位置命中一个 ticker，`_needs_object_intake` 就返回 false | 研究重点、阅读方式 | 中芯国际的 A/H 歧义被完全忽略 |
| `今天大盘怎么样` | 会。中文、无 ticker | 目标、周期、风险 | 大盘问题被当成“缺少个股对象” |
| `什么是市盈率` | 不会问市场 | 阅读方式 | 概念题也要先走问卷 |
| `继续看看`（会话已有标的） | 会。中文、无 ticker | 目标、周期、风险 | 已确认的会话标的没有被继承 |

这说明问题不在某一个正则写漏，而是**引导层没有复用会话服务里已经存在的标的解析能力**。

### 2.3 影响

- 引导完成率低：用户在第一个“市场”问题就流失，或随便点一个市场，后续分析对象仍然不对。
- 错误背景进入研究：`object_scope=a_share` 被当成“分析对象”，但用户实际想看的是某只股票；模型只能从原问题再猜一次。
- 打扰型提问：目标、周期、风险在“分析一下 X”里未必需要；系统可以用“未指定/综合判断”开始，再在答案里说明限制。
- 首屏无信息增量：固定快捷问题没有利用自选、最近分析、当前会话和市场时段，用户感知不到“系统懂我”。
- 维护成本高：`OBJECT_EXPLICIT_PATTERNS` 里硬编码了 `600519`、`300750`、`002594`、`688981`、`hk00700` 和少数美股 ticker；新增标的就要改代码，必然继续漏。

---

## 3. 现状与根因

### 3.1 入口链路

```text
Web ChatPage 首问
  → POST /api/v1/agent/chat/intake
  → _needs_object_intake(message)                  # 粗粒度正则
  → AgentChatIntakeService.create_intake()
  → select_questions(message, stock_clarification_needed=...)
  → 固定题库 / 硬编码 explicit patterns
  → IntakeDraft(pending)
  → ChatIntakeCard 逐题选择
  → PATCH confirm
  → POST /chat/stream + intake_id/revision
  → accept_for_chat() → AnalysisBrief → 研究
```

### 3.2 根因清单

| 编号 | 位置 | 机制 | 用户影响 |
| --- | --- | --- | --- |
| B1 | `api/v1/endpoints/agent.py:101`、`:120-124` | `_EXPLICIT_TICKER_RE` 只认大写 1–5 字母、6 位数字、`HK` 前缀；`_needs_object_intake` 只判断“有中文且没有 ticker” | 5 位港股、后缀代码、小写 ticker、中文名称全部被误判为缺对象；混合输入只要有一个 ticker，其他歧义对象也不会被追问 |
| B2 | `src/agent/intake_questions.py:179-183` | `OBJECT_EXPLICIT_PATTERNS` 硬编码少量代码和美股 ticker | 新代码/名称不在名单就漏判，必须持续打补丁 |
| B3 | `src/agent/intake_questions.py:33-47` | `QUESTION_OBJECT` 的选项是市场枚举，不是具体标的 | 用户选了“港股”仍然不知道是哪只；`object_scope` 存 `hk` 而不是 `HK00700` |
| B4 | `src/agent/intake_questions.py:287-313` | 研究题固定追加 `goal`、`horizon`、`risk_focus`，不读会话已有 brief，不判断问题是否已经说明 | 每次都是同样的三问 |
| B5 | `src/agent/intake_questions.py:355-385` | `build_analysis_brief` 把 `object_scope` 直接写成选项 id；未知维度只记名字，没有来源/证据 | 推断与用户明示混在一起，无法在 UI 上解释“为什么这么理解” |
| B6 | `apps/dsa-web/src/pages/ChatPage.tsx:62-72`、`:1524-1548` | `QUICK_QUESTIONS` 是固定 6 条，空状态无条件渲染 | 首屏没有上下文，也不随使用变化 |
| B7 | `apps/dsa-web/src/components/chat/ChatIntakeCard.tsx` | 一题一页、进度条、上一步/下一步、最后确认页 | 简单问题也要多点几次；没有“直接开始”的主路径 |
| B8 | `apps/dsa-web/src/components/chat/StockClarificationCard.tsx:172-182` | `ambiguous_name` / `multiple_objects` 也会在 10 秒后自动确认推荐项 | 跨市场同名标的可能自动选错市场 |
| B9 | `src/services/agent_chat_session_service.py:190-220`、`src/agent/chat_object_decision.py` | 已有确定性标的解析、候选、当前会话对象和历史恢复能力，但引导层没有复用 | 同一系统里两套判断，行为不一致 |
| B10 | `src/agent/stock_scope.py:240-260` | `extract_stock_code_spans()` 默认只识别大写 ticker 和 `hk` + 4–5 位；小写 ticker 仅在切换/比较语境识别 | 直接复用它做 v2 解析会漏掉 `分析 aapl`、`分析 hk700`，需要独立的 token 提取步骤 |

### 3.3 已有可复用能力

v2 不新建一套股票解析器，优先复用：

| 能力 | 位置 | 可复用点 |
| --- | --- | --- |
| 代码/名称/指数解析 | `src/services/stock_list_parser.py` `parse_analysis_target()`、`IndexRegistry` | 前缀/后缀/裸码、A/H/US、指数 canonical |
| 代码规范化 | `src/services/stock_code_utils.py` `normalize_code()`、`is_code_like()` | 5/6 位、前缀、后缀、美股 ticker |
| 会话对象决策 | `src/agent/chat_object_decision.py` `decide_chat_object()`、`object_mentions()` | 代码/名称、歧义、比较、当前对象保持 |
| 股票名称/代码查找 | `src/data/stock_index_loader.py` `get_chat_stock_lookup()` | 名称、别名、代码、`assetType`，行内已有市场字段 |
| 会话主对象 | `src/services/agent_chat_session_service.py` `_active_from_snapshot()` | 当前会话已确认标的、刷新恢复 |
| 会话 brief | `agent_chat_session_service` 的 assistant `answer_meta.brief_snapshot` | 上一轮已确认背景，可继承 |
| 自选与历史 | `apps/dsa-web/src/hooks/useWatchlist.ts`、`api/history.ts` | 首屏建议和候选排序 |

---

## 4. 目标与非目标

### 4.1 目标

1. 用户给出可确定市场/具体标的的代码或名称时，引导层不再询问市场。
2. 引导层只问“会改变研究范围或结论”的问题；普通问股默认 0–1 题，最多 2 题。
3. 引导界面先展示“系统理解到的事实”，再展示“仍不确定、可调整的背景”；推断必须标注来源，不能冒充用户选择。
4. 新会话首问在信息足够时自动开始研究，不强制确认；用户可通过简报条随时调整。
5. 跨市场同名/同公司多地上市的标的（如中芯国际）用“具体候选”提问，不用“市场”提问；推荐项必须说明理由。
6. 同一会话追问继承已确认的标的与背景，不重复问；用户明确切换标的后再更新。
7. 首屏建议基于自选、最近分析、当前会话和市场时段生成，不再固定六条。
8. 旧客户端和 v1 引导保持可用；v2 由 `interaction_version` 显式启用。

### 4.2 非目标

- 不优化模型回答质量、工具速度、数据源覆盖。
- 不新增日股/韩股/台股数据源；只保证已有解析能力在引导层被正确使用，不支持的市场要明确说明。
- 不重做整个问股页面视觉，不改历史侧栏、报告页、Bot `/chat`、定时任务和交易链路。
- 不引入用户画像、长期偏好存储或跨会话个性化；v2 只使用当前会话、自选和最近分析。
- 不做“自动交易”“自动下单”或任何交易授权。
- 不把 LLM 推断作为默认必需路径；P0 以确定性规则为主。

---

## 5. 产品原则

### P1 · 先解析，后提问

任何问题出现前，先跑标的解析。解析结果决定要不要问、问什么。禁止用“消息里有中文”推断“缺少标的”。

### P2 · 能查到的不要问用户

代码、名称、别名、当前会话标的、报告标的、自选，都是系统可以查的。只有查不到或有多个同等候选时才问。

### P3 · 只问会改变结论的

目标、周期、持仓、风险偏好不是必填项。用户没说，系统可以标“未指定”并用中性方式回答；不要为了填满 brief 而问。

### P4 · 推断不是事实

`explicit_message` / `user_choice` 才能作为用户约束；`system_inferred` 只能作为“分析侧重点建议”，必须在简报和提示词里标注，不能写成“用户偏好稳健/用户空仓”。

### P5 · 默认可开始

引导是加速器，不是关卡。信息足够时直接开始；用户看到简报条后仍可调整。只有对象歧义、比较缺参与方、报告指代不明等硬阻断才必须确认。

### P6 · 一次问清，不分页

v2 不用逐题向导。所有需要用户做的事在同一张卡片里完成；对象候选、背景 chip、开始按钮同屏。

### P7 · 候选要具体，不要市场枚举

问“中芯国际 688981（A股）还是 00981（港股）”，不问“你关注哪个市场”。候选必须带名称、代码、市场/交易所、资产类型和推荐理由。

---

## 6. 目标体验

### 6.1 引导状态机

```text
收到首问
  ├─ 概念/方法问题 ────────────────→ 直接回答（0 题）
  ├─ 报告追问/持仓/大盘/自选 ──────→ 继承上下文，直接开始（0 题）
  ├─ 标的唯一确定 ─────────────────→ 生成简报，自动开始（0 题）
  ├─ 标的有多个具体候选 ───────────→ 候选卡（1 题，必须点选）
  ├─ 标的缺失且必须知道 ───────────→ 标的搜索（1 题）
  ├─ 比较缺参与方/指代不明 ────────→ 澄清（1 题）
  └─ 不支持的市场/代码 ────────────→ 说明 + 选择（1 题，不猜测）
```

补充边界：`POST /chat/intake` 当前只允许会话首问创建草稿，已有消息的会话会返回 `intake_not_first_turn`。因此 v2 的解析/选题主要作用于首问；同一会话的追问继续走现有 `decide_chat_object()` + `_reuse_session_brief()`，继承已确认标的和简报，不重复创建问卷。首问之后的“换成比亚迪”等明确切股仍由现有对象决策更新，不在本轮重新弹引导卡。

### 6.2 场景 A：代码明确，直接开始

用户：`分析 600519`

```text
你：分析 600519

[已识别] 贵州茅台 600519 · A股 · 上交所 · 综合判断 · 周期未指定   [调整]
（研究直接开始，回答流式输出）
```

行为：

- 对象解析为 `600519` / `贵州茅台` / `CN` / `stock`，不再问市场。
- 0 个问题，`auto_start=true`；前端不等待点击，直接携带 `intake_id` / `intake_revision` 发起研究。
- 目标、周期、持仓未指定，不阻塞；简报条显示“未指定”，研究按中性方式开始。
- 不出现“你关注哪个市场”的问题，也不出现必须确认的分页问卷。
- 简报条的“调整”用于下一轮：本轮已开始时，文案明确“本轮已开始，调整将用于下一轮”；若研究尚未 accepted，则取消当前轮并按新简报重发。

### 6.3 场景 B：中文名称唯一，直接开始

用户：`分析腾讯`

```text
你：分析腾讯

[已识别] 腾讯控股 HK00700 · 港股 · 港交所 · 综合判断 · 周期未指定   [调整]
（研究直接开始）
```

“腾讯”是 `腾讯控股` 的别名，精确别名在索引中唯一命中 00700，不询问市场；展示市场标签是“告知”，不是“提问”。`腾讯控股` 本身也是唯一精确名称；`腾讯音乐-SW` 不参与精确名称/别名命中。

### 6.4 场景 C：跨市场同名，选具体标的

用户：`分析中芯国际`

```text
┌ 需要确认标的 ─────────────────────────────────┐
│ “中芯国际”在 A 股和港股都有上市：              │
│                                                │
│ ● 中芯国际  688981   A股 · 上交所   [推荐]     │
│   理由：与你当前会话的 A 股标的一致             │
│                                                │
│ ○ 中芯国际  00981    港股 · 港交所             │
│                                                │
│ [都不是，搜索其他代码或名称]                    │
└────────────────────────────────────────────────┘
```

行为：

- 服务端返回 `status=ambiguous`、两个具体候选、`recommended_option_id` 和理由。
- 不出现“A 股/港股/美股”三层选项。
- 只有用户点选后才开始研究；点选即提交，不再追加确认页。此场景不自动确认、不倒计时。
- 若当前会话已有 A 股标的，推荐 A 股；否则按“自选 → 最近分析 → 默认市场”排序。

### 6.5 场景 D：代码格式有效但库内没有

用户：`分析 999999`

```text
┌ 没有找到这个代码 ─────────────────────────────┐
│ 999999 的格式像 A 股代码，但当前股票库未收录。  │
│ [重试] [搜索其他标的] [仍然尝试分析]            │
└────────────────────────────────────────────────┘
```

行为：

- 不猜测市场，不把 `999999` 改写成别的代码。
- 提供“仍然尝试分析”，把原始代码交给研究层；如果数据源也拿不到，回答中说明无数据。
- 不把“代码有效”当成“标的存在”。

### 6.6 场景 E：大盘/持仓/自选，不要问个股

用户：`今天大盘怎么样` / `我的持仓怎么样` / `看看我的自选`

```text
已识别：市场范围  A股（来自当前配置/自选）
目标：大盘复盘
[调整范围]  [开始分析]
```

行为：

- 市场类问题解析为 `scope=market`，不是“缺少个股对象”。
- 若部署配置或自选只能确定一个市场，直接使用；若同时配置 A/H/US 且问题没有限定，才问“看哪个市场”，选项是具体市场范围，不是个股。
- 持仓/自选解析为 `scope=portfolio/watchlist`，直接开始。

### 6.7 场景 F：会话追问，继承背景

用户先问 `分析贵州茅台`，再问 `它最近怎么样`。

```text
你：它最近怎么样

[本会话] 贵州茅台 600519 · A股 · 上交所 · 沿用上一轮背景（综合判断，周期未指定）   [调整]
（研究直接开始）
```

行为：

- 追问不创建新的 intake 草稿（服务端只允许首问创建），从 `active_stock_context` 继承标的，从最近一次 confirmed 草稿或上一轮 `brief_snapshot` 继承已确认背景。
- 不重复问目标、周期、风险，也不再展示首问问卷。
- 用户说“换成比亚迪”时，才更新标的并清理旧标的的持仓/报告上下文。
- 例中使用贵州茅台是因为名称唯一；`宁德时代` 在本地索引同时对应 300750（A 股）和 03750（港股），属于场景 C 的跨市场歧义，不能直接当作已确定 A 股。

### 6.8 场景 G：概念题，不引导

用户：`什么是市盈率？`

行为：直接回答，不出现任何选择题；服务端可创建零题草稿用于 exactly-once 幂等，但不展示引导卡片。

### 6.9 场景 H：信息完整，只确认一次

用户：`分析 600519，长期，已持有且盈利，稳健一点`

```text
[已识别] 贵州茅台 600519 · A股 · 上交所 · 评估已有持仓 · 长期 · 已持有 · 稳健   [调整]
（研究直接开始）
```

行为：0 个问题；所有维度都标记“来自你的问题”，`auto_start=true`，简报条只做告知；不出现确认页。

### 6.10 首屏建议

空会话不再固定六条。建议卡按以下优先级生成 3–4 条，并显示来源标签：

1. **继续上次**：最近一次分析/最近会话的标的 + 上次使用的 skill。
2. **自选关注**：自选列表前 2–3 只，附“自选”标签。
3. **市场时段**：盘前/盘中/盘后不同模板，如“复盘今日 A 股”“看看今日港股异动”。
4. **能力发现**：一个与当前可用 skill 相关的示例，附“方法”标签。

```text
┌ 基于你的关注 ─────────────────────────────────┐
│ [继续] 贵州茅台 600519 · 上次分析  3 天前      │
│ [自选] 宁德时代 300750 · 趋势与关键位置        │
│ [大盘] 今日 A 股复盘                           │
│ [方法] 用缠论看一只自选股                      │
└──────────────────────────────────────────────┘
```

无自选、无历史时，展示能力型建议，但每次根据市场时段/可用 skill 轮换，不出现完全相同的固定四条。

---

## 7. 功能需求

### R1 · 确定性标的解析（P0）

服务端在创建 intake 前必须调用统一的标的解析，输出结构化结果。解析顺序：

1. **显式代码**：`600519`、`600519.SH`、`SH600519`、`00700`、`0700.HK`、`HK00700`、`hk700`、`AAPL`、`aapl`、`7203.T`、`005930.KS`、`2330.TW`。使用 `parse_analysis_target()` / `normalize_code()`，不另写股票正则。
2. **精确名称/别名**：`get_chat_stock_lookup()` 的 `names`，NFKC + casefold 精确匹配；唯一命中即 resolved。
3. **当前会话标的**：无显式对象、且是追问/指代（“它”“这只”“继续”“那现在呢”）时，继承 `active_stock_context`。
4. **报告标的**：问题含“这份报告/上面/报告里”且 `active_report_context` 存在时，使用报告标的。
5. **市场/组合范围**：`大盘/市场/A股/港股/美股/上证/恒生/纳指/标普` → `scope=market`；`持仓/组合/自选` → `scope=portfolio/watchlist`。
6. **概念/方法**：`什么是/怎么理解/解释` → `scope=concept`，不要求对象。
7. **多标的比较**：`比较/对比/vs/和` 且能解析出 ≥2 个具体对象时，返回 `scope=compare`、`objects=[...]`、`status=resolved`；少于 2 个可解析对象时返回 `missing` 并进入补标的/澄清。
8. **模糊名称**：本地索引做前缀/子串匹配，最多 5 个候选，返回 `status=ambiguous`；只有 1 个候选时也必须让用户确认（“你是要找 X 吗？”），不得静默自动选。
9. **缺失**：以上都不命中，返回 `status=missing`，由前端展示标的搜索，不问市场。

实现约束：

- 不能复用 `api/v1/endpoints/agent.py` 的 `_EXPLICIT_TICKER_RE` 或 `_needs_object_intake()` 作为判断入口。
- 不能只依赖 `extract_stock_code_spans()`：该函数默认只识别大写 ticker 与 `hk` + 4–5 位，`aapl` 只在切换/比较语境识别，`hk700` 也不匹配。v2 必须先做 NFKC、按空白/标点切 token，再对候选 token 调用 `parse_analysis_target()`；附着形式（`分析600519`、`分析hk700`）用受控的 ASCII/数字片段补充提取。
- 5 位纯数字按港股处理；3 位裸数字（如 `700`）不猜测为港股，返回 `missing`，要求补全或搜索。
- 解析失败时不得把市场枚举当作对象答案。

禁止：

- 用“消息含中文”判断缺少对象。
- 用硬编码股票名单判断对象是否明确。
- 把 5 位纯数字一律当 A 股；5 位是港股。
- 把 `0700.HK`、`aapl`、`hk700` 当无效输入。
- 只有一个模糊候选时静默自动选。

### R2 · 市场与资产标签（P0）

解析结果必须带用户可读标签：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| `market` | `CN` / `HK` / `US` / `JP` / `KR` / `TW` / `UNKNOWN` | 机器可读 |
| `market_label` | `A股 · 上交所` / `港股 · 港交所` / `美股` | 用户可读；交易所未知时只写市场 |
| `asset_type` | `stock` / `index` / `etf` / `fund` | 与现有 `asset_type` 契约一致 |
| `canonical_id` | `600519.SH` / `HK00700` / `AAPL` | 与现有解析器一致 |
| `stock_code` | `600519` / `HK00700` / `AAPL` | 现有会话/工具使用的代码 |
| `stock_name` | `贵州茅台` / `腾讯控股` | 索引名称；没有时为空 |

标签只用于“告知已识别”，不改变解析结果。`market` 由 `parse_analysis_target()` 的 `exchange` / `canonical_id` 和后缀规则推导，不新增一套交易所表；交易所信息不确定时不得编造（例如美股不强行写 NASDAQ/NYSE）。现有 `StockIdentity` 只有 `stock_code` / `canonical_id` / `asset_type` / `stock_name`，v2 的 `ObjectIdentity` 是在其外层追加 `market` / `market_label`，不修改 `StockIdentity` 的既有契约。

### R3 · 歧义与候选（P0）

- `status=ambiguous` 时必须返回 1–5 个具体候选，每个候选包含 R2 字段。
- 1 个候选表示“确认”而不是“选择”：卡片问“你是要找 X 吗？”，提供“是 / 搜索其他”；不得静默自动选，也不得因为没有第二个候选改判 missing。
- 2–5 个候选表示“选择”：按推荐顺序展示，必须显式点选后才开始研究。
- `recommended_option_id` 在候选 ≥1 时必填，并返回 `recommendation_reason`（用户可读）。
- 推荐排序：当前会话同市场 → 自选 → 最近 30 天分析 → 精确名称优先于别名 → 股票优先于指数/ETF → `MARKET_REVIEW_REGION` 默认市场 → 稳定 canonical 排序。
- 用户选择候选后，brief 的 `object` 必须写具体身份，不能写市场 id；身份由服务端根据已存候选映射，不信任客户端自报。
- 候选卡不得倒计时自动确认；跨市场/多对象/单候选确认场景都必须显式操作。

### R4 · 意图推断（P0，确定性）

从原问题提取已有背景，至少覆盖：

| 维度 | 识别示例 | 处理 |
| --- | --- | --- |
| `goal` | 能不能买、值不值得关注、已持有/被套、为什么涨、怎么分析 | 命中即 `explicit_message`，不再问 |
| `horizon` | 短线、几天、1–4 周、半年、长期、今年 | 命中即 explicit |
| `position` | 空仓、没买、持有、盈利、被套、加仓、减仓 | 只按明确表达；不从泛化语句推断 |
| `risk_focus` | 稳健、保守、怕回撤、能接受波动、激进 | 命中即 explicit |
| `research_focus` | 估值、基本面、消息、技术面、风险、对比 | 命中即 explicit，可多选 |
| `reading_style` | 简单说、详细、先给结论、通俗解释 | 命中即 explicit；否则用 UI 默认 |

要求：

- 每个推断带 `source`、`confidence`、`evidence`（原文片段/区间）。`explicit_message` 的 evidence 必须能在原文中定位。
- 不做“用户没说过但看起来像”的高风险推断；持仓、风险偏好尤其保守。
- 推断结果只用于减少提问和调整分析侧重，不写成用户事实。
- P0 不调用 LLM；P1 可增加“低置信度时一次结构化抽取”，必须有超时、闭集候选和确定性回退，默认关闭。

### R5 · 会话背景继承（P0）

- 同一会话的追问默认继承上一轮已确认的 `brief_snapshot`。
- 继承项在 UI 标“沿用本会话”，用户可改。
- 用户明确改变某维度时，只更新该维度；不重置其他已确认背景。
- 用户明确切换标的时，清除旧标的专属的持仓、报告、行情上下文；周期/阅读方式等通用偏好可保留。
- 新会话不继承另一个会话的 brief；可以使用自选/最近分析做首屏建议，但不能把别的会话的持仓当成当前事实。
- 刷新/切换会话后从服务端恢复，不依赖 localStorage。

### R6 · 动态选题（P0）

v2 使用 `MAX_QUESTIONS_V2=2`，v1 的 `MAX_QUESTIONS=5` 不变。选题规则：

| 场景 | 问题数 | 是否自动开始 |
| --- | --- | --- |
| 对象唯一确定，概念/报告追问/持仓/大盘/自选 | 0 | 是 |
| 对象唯一确定，普通研究，目标/周期/风险缺失 | 0 | 是，缺失项标“未指定” |
| 对象唯一确定，信息完整 | 0 | 是 |
| 对象跨市场/多候选 | 1（候选选择） | 否 |
| 模糊名称仅 1 个候选 | 1（确认“是这只吗”） | 否 |
| 对象缺失且必须知道 | 1（标的搜索） | 否 |
| 比较缺参与方 | 1（补标的） | 否 |
| 比较参与方都已确定 | 0 | 是 |
| 报告指代不明/多个报告对象 | 1（指代澄清） | 否 |
| 代码有效但库内没有 | 1（信息卡：重试/搜索/仍尝试） | 否 |
| 不支持的市场 | 1（信息卡：换标的/仍尝试） | 否 |
| 背景冲突（如“长期”又说“只看明天”） | 1（澄清） | 否 |

明确不再默认询问：

- “你关注哪个市场的标的？”
- “这次最想解决什么问题？”（除非用户输入完全无法判断意图，且不同目标会显著改变工具选择）
- “你更关注多长时间？”（默认未指定，答案中说明尺度限制）
- “你目前持有这只股票吗？”（只有用户提到持仓相关时才作为可选项，不阻塞）
- “你希望怎样阅读答案？”（使用 UI 默认/上次选择）

### R7 · 分析简报卡（P0）

用一张紧凑卡片替代逐题向导：

```text
┌ 已识别你的问题 ───────────────────────────────┐
│ 标的   贵州茅台 600519  A股 · 上交所  [改]    │
│ 目标   综合判断（未指定）              [改]    │
│ 周期   未指定                          [改]    │
│ 持仓   未指定                          [改]    │
│ 风险   未指定                          [改]    │
│ 输出   先看重点                        [改]    │
│                                                │
│ 来源：标的来自你的问题；目标/周期未指定         │
│ [不用调整，直接开始]              [开始分析]   │
└────────────────────────────────────────────────┘
```

要求：

- 一屏完成，不分页、不显示“1/5”进度条。
- 每一行显示值 + 来源标记：`来自你的问题` / `沿用本会话` / `系统推断` / `未指定`。
- 来源为 `system_inferred` 的值必须有“推断”样式和 tooltip/说明，不能和用户选择同样呈现。
- 点“改”打开该维度的轻量选择器（popover / 底部 sheet），不跳到下一页。
- “开始分析”是唯一主按钮；“不用调整，直接开始”是次按钮/文字链，二者都立即提交当前简报，区别只是是否先让用户确认已识别的项，不存在单独的“确认页”。
- 对象行可点开候选/搜索；候选卡沿用 R3。
- 卡片只在 `auto_start=false`（歧义、缺失、比较缺参与方等）时作为阻断卡展示；`auto_start=true` 时不展示阻断卡，直接收成一条 `ChatBriefStrip`，显示“已识别：贵州茅台 600519 · A股 · 综合判断 · 未指定周期 [调整]”。
- 自动开始后的 `ChatBriefStrip` 是当前轮的只读摘要；“调整”打开 R7 卡片，保存后的 brief 写入最近一次 confirmed 草稿，供下一轮继承；文案必须说明“本轮已开始，调整将用于下一轮”。

### R8 · 自动开始与简报条（P0）

- `plan.auto_start=true` 时，前端不再等待用户点“开始分析”，直接发研究请求。
- 自动开始前必须满足：对象 `resolved`（比较场景要求 `objects` 全部 resolved）或 scope 为 concept/market/portfolio/report；没有 blocking question；domain guard 通过。
- 自动开始仍创建 intake 草稿并标记 `auto_confirmed=true`，保留 exactly-once 接受语义；不绕过 `intake_id`/`intake_revision`。
- 自动开始的 brief 中，`system_inferred` 只能作为“分析侧重建议”，`unknown_fields` 保持未指定；提示词不得把它们写成用户偏好。
- 回答上方显示简报条；点“调整”打开 R7 卡片。若当前轮尚未 accepted，取消当前轮并按新简报重发；若已开始，修改写入最近一次 confirmed 草稿，下一轮优先继承，并明确文案“本轮已开始，调整将用于下一轮”。
- 自动开始不得用于对象 ambiguous/missing/unsupported，也不得用于比较参与方未全部确定、或用户明确说“先别分析/只解释”的消息。
- 概念题可以直接回答，不展示简报条；`intake_auto_start_rate` 指标不把概念题计入分母，避免把“直接回答”误读成“自动开始研究”。

### R9 · 标的澄清卡（P0）

现有 `StockClarificationCard` 保留“切换/保持当前”的能力，但调整：

- 标题从“先确认要分析的对象”改为“你要分析哪一只？”；候选卡显示名称、代码、市场标签、推荐理由。
- `ambiguous_name` / `multiple_objects` 不显示 10 秒倒计时，不自动确认。
- 只有服务端返回 `auto_confirm_eligible=true` 且 `action=keep`（保持当前标的）或唯一高置信推荐时，才允许倒计时；默认 false。
- “输入股票代码”改为“搜索其他代码或名称”，复用 R1 的搜索/候选。
- 历史消息中的澄清卡不重新启动倒计时，不自动发送（保持现有约束）。
- 澄清解析过程仍可展示“理解提问 → 识别候选 → 等待确认”，但不得用动画拖延用户操作。

### R10 · 首屏建议（P1）

- 新增 `useChatSuggestions()`，输入：当前会话、自选、最近分析、可用 skill、市场时段、UI 语言。
- 输出 3–4 条，每条包含：`label`、`message`、`skill?`、`stockContext?`、`reason`、`priority`。
- 建议必须可直接发送，不依赖后续追问；例如“看看贵州茅台 600519 最近走势”，而不是“分析一只股票”。
- 无上下文时使用能力型建议，并按日期/市场时段轮换；不得每次完全一致。
- 建议生成失败时回退到 3 条静态但轮换的模板，不阻塞页面。
- 不展示用户没有权限/不可用的 skill；沿用现有 `availableSkillIds` 过滤。

### R11 · 不支持/未知处理（P0）

- 不支持的市场（如未启用日股数据源）：明确说明“当前数据源暂不支持日股”，提供“换一只/仍然尝试/解释原因”，不把用户引导到 A/H/US 三选一。
- 代码格式有效但库内没有：见场景 D。
- 名称为空/无意义（如“分析一下”）：展示标的搜索，不创建空 brief。
- 解析服务异常：保留原问题，提供“重试/直接分析”，不伪造解析结果。

### R12 · 文案、国际化与可访问性（P0）

- 所有新增文案进 `apps/dsa-web/src/i18n/uiText.ts`，中英同步；不得在组件里硬编码中文。
- 市场标签、来源标签、推荐理由、错误文案都要有英文版本。
- 使用原生 radio/checkbox 或 ARIA `radiogroup`；候选卡支持 Tab/方向键/Enter/Space，焦点可见。
- 对象解析结果用 `aria-live="polite"` 播报；不要每次输入都打断。
- 移动端 320/390/768/1440 px 无横向溢出；编辑面板用底部 sheet，避开软键盘和安全区。
- 深浅主题、减少动画模式可用；颜色不是唯一状态表达。
- 自动开始后要有明确的“已按以下背景开始”提示，不能让用户以为系统忽略了问题。

---

## 8. 数据与接口契约

### 8.1 对象解析结果

```json
{
  "status": "resolved",
  "scope": "stock",
  "identity": {
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "canonical_id": "600519.SH",
    "asset_type": "stock",
    "market": "CN",
    "market_label": "A股 · 上交所"
  },
  "candidates": [],
  "recommended_option_id": null,
  "recommendation_reason": null,
  "evidence": {
    "kind": "bare_cn_code",
    "text": "600519",
    "start": 3,
    "end": 9
  },
  "reason": "unique_code"
}
```

字段约束：

- `status`: `resolved` | `ambiguous` | `missing` | `unsupported` | `not_required`。
- `scope`: `stock` | `index` | `etf` | `market` | `portfolio` | `watchlist` | `report` | `concept` | `unknown`。
- `identity` 在 resolved 时必填；ambiguous 时为 null；candidates 至少 2 个。
- `evidence` 只暴露用户可理解的信息，不返回内部注册表路径、异常栈或 provider 细节。
- `reason` 取稳定枚举：`unique_code`、`unique_name`、`session_inherited`、`report_context`、`cross_market_listing`、`multiple_objects`、`fuzzy_match`、`no_object`、`unsupported_market`、`invalid_code`。

### 8.2 AnalysisBrief v2（向后兼容）

在现有字段上追加，不删除 v1 字段：

```json
{
  "brief_version": 2,
  "object_scope": "a_share",
  "object": {
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "canonical_id": "600519.SH",
    "asset_type": "stock",
    "market": "CN",
    "market_label": "A股 · 上交所"
  },
  "goal": "worth_watching",
  "horizon": null,
  "position": null,
  "risk_focus": null,
  "focus_areas": ["trend_levels"],
  "reading_style": "highlights",
  "unknown_fields": ["horizon", "position", "risk_focus"],
  "sources": {
    "object": "explicit_message",
    "goal": "explicit_message",
    "horizon": "unknown",
    "position": "unknown",
    "risk_focus": "unknown",
    "focus_areas": "system_inferred",
    "reading_style": "user_default"
  },
  "evidence": {
    "object": "600519",
    "goal": "值不值得关注"
  },
  "assumptions": [
    "未指定周期，按中期与短期结合的综合视角回答"
  ],
  "auto_confirmed": false
}
```

要求：

- `object_scope` 保留给 v1/提示词兼容；v2 以 `object` 为准。`object_scope` 不再写 `a_share/hk/us` 作为“具体标的”，最多作为市场范围。
- `sources` 的值：`explicit_message` | `user_choice` | `session_inherited` | `system_inferred` | `user_default` | `unknown`。
- `assumptions` 只能由 `system_inferred`/`user_default` 产生；`explicit_message`/`user_choice` 不得写入 assumptions。
- `unknown_fields` 与 `sources=unknown` 保持一致。
- v1 客户端看不到新字段也不受影响；v2 客户端缺字段时按 unknown 处理，不猜测。

### 8.3 Question v2（向后兼容）

在现有 `Question` 上追加：

```json
{
  "id": "object_choice",
  "dimension": "object",
  "kind": "object_choice",
  "type": "single",
  "title": "你要分析哪一只？",
  "help": "“中芯国际”在 A 股和港股都有上市。",
  "required": true,
  "depends_on": null,
  "recommended_option_id": "cn_688981",
  "allow_free_text": true,
  "free_text_kind": "stock_search",
  "options": [
    {
      "id": "cn_688981",
      "label": "中芯国际 688981 · A股 · 上交所",
      "identity": {
        "stock_code": "688981",
        "stock_name": "中芯国际",
        "canonical_id": "688981.SH",
        "asset_type": "stock",
        "market": "CN",
        "market_label": "A股 · 上交所"
      }
    }
  ]
}
```

要求：

- `kind`: `object_choice` | `object_search` | `scope_clarify` | `info_confirm`。
- `options[].identity` 可选；对象题必须带具体身份，不能只带市场 id。
- `allow_free_text`/`free_text_kind` 用于搜索；服务端必须校验返回的代码/名称，不能直接信任自由文本。
- `MAX_QUESTIONS=2`；对象题最多 1 个，另一题只能是必要的 scope 澄清。
- v1 题库和 `select_questions()` 保留，v2 新路径不修改其行为。

### 8.4 接口与版本

| 接口 | v2 变化 |
| --- | --- |
| `POST /api/v1/agent/chat/intake` | 请求增加 `interaction_version=2`；响应增加 `resolution`、`brief_preview`、`plan`（`auto_start`、`blocking_reason`）、`session_context` |
| `PATCH /api/v1/agent/chat/intake/{id}` | 答案支持对象候选 `identity`；返回更新后的 `resolution`、`brief_preview`、`plan` |
| `GET /api/v1/agent/chat/intake/{id}` | 返回同样的 v2 字段；旧字段保留 |
| `POST /api/v1/agent/chat`、`/chat/stream` | 沿用 `interaction_version`、`intake_id`、`intake_revision`；v2 自动开始的草稿也走同一接受路径 |
| `GET /api/v1/agent/chat/sessions/{id}` | 沿用 `pending_intake`、`analysis_brief`；追加 `intake_plan` 可选字段 |

版本策略：

- `interaction_version < 2`：走现有 v1 逻辑，行为不变。
- `interaction_version >= 2`：走 v2 解析/选题/简报；旧客户端不会误入。
- 不新增环境变量；v2 是契约版本，不是部署开关。
- 服务端对 v2 字段缺省、类型错误、未知枚举一律 422，不静默降级成市场枚举。

### 8.5 持久化

- P0 不新增表、不新增列：`intake_drafts.questions_json` 可保存 v2 `Question`（含 `identity`），`answers_json` 保存选择；`brief_preview` 和 `resolution` 可由原问题 + 会话快照确定性重算。
- 若实施中发现重算成本或一致性问题，再评估增加 `context_json` 列；必须单独迁移、兼容旧行、失败可回滚。
- `schema_version` 从 1 升到 2，仅用于标记草稿结构；读取时兼容 1。
- 不把解析结果写进用户消息正文，不把内部候选/异常写入历史。

---

## 9. 规则细节

### 9.1 对象解析优先级

| 优先级 | 输入形态 | 处理 | 失败时 |
| --- | --- | --- | --- |
| 1 | 显式代码（前后缀、裸码、ticker） | `parse_analysis_target` | 有效格式但未收录 → `missing` + 原代码 |
| 2 | 精确名称/别名 | `get_chat_stock_lookup().names` | 多命中 → `ambiguous` |
| 3 | 当前会话标的 | 追问/指代时继承 | 无当前标的 → 继续 |
| 4 | 报告标的 | 报告指代时继承 | 报告不可用 → 明确提示 |
| 5 | 市场/组合范围 | `scope=market/portfolio/watchlist` | 多市场 → 具体市场选择 |
| 6 | 概念/方法 | `scope=concept` | 直接回答 |
| 7 | 模糊名称 | 本地索引前缀/子串，最多 5 个 | 无候选 → `missing` |
| 8 | 缺失 | 标的搜索 | 不猜测 |

### 9.2 候选排序

1. 当前会话同市场/同资产类型。
2. 自选列表命中。
3. 最近 30 天分析记录命中。
4. 精确名称命中优先于别名命中。
5. `asset_type=stock` 优先于 `index`/`etf`（名称像公司时）。
6. `MARKET_REVIEW_REGION` 对应市场。
7. `canonical_id` 稳定排序。

推荐理由用模板生成，不调用模型：

- “与你当前会话的 A 股标的一致”
- “在你的自选列表中”
- “你最近分析过这只”
- “A 股主市场默认推荐”
- “名称完全匹配”

### 9.3 自动开始判定

```text
auto_start =
  interaction_version >= 2
  AND domain_guard.passed
  AND resolution.status in {resolved, not_required}
  AND plan.blocking_questions == 0
  AND NOT user_says_stop_or_explain_only
```

补充：

- `scope=market/portfolio/watchlist/report/concept` 视为 `not_required`。
- `resolution.status=ambiguous/missing/unsupported` 一律不自动开始。
- 自动开始不跳过 `intake_id`/`intake_revision`；草稿状态 `confirmed`，`auto_confirmed=true`。
- 自动开始后用户点“调整”只影响下一轮；若当前轮尚未真正 accepted，可取消当前轮并按新 brief 重发。

### 9.4 未指定与默认值

| 维度 | 未指定时 | 禁止 |
| --- | --- | --- |
| 目标 | 用“综合判断”作为分析侧重建议，标 `system_inferred` | 写成“用户想买入/想卖出” |
| 周期 | 不默认短线/长线；答案说明尺度限制 | 写成“用户关注 1–4 周” |
| 持仓 | 保持 unknown；不假设空仓/持有 | 写成“用户空仓”或“用户已持有” |
| 风险 | 保持 unknown；用中性风险呈现 | 写成“用户风险偏好均衡/稳健” |
| 阅读方式 | 用 UI 默认“先看重点” | 写成用户选择 |
| 对象 | 必须 resolved 或明确 scope；否则阻断 | 用市场枚举代替具体标的 |

### 9.5 与 domain guard 的关系

- v2 先跑 domain guard，再决定是否创建 intake / 自动开始。
- 明确非投资问题：直接返回提醒，不创建简报、不问标的。
- 模糊问题：可以创建简报，但 `auto_start=false`，先请用户补充投资背景。
- 混合问题：保留可分离的投资子请求；简报只覆盖投资部分。

---

## 10. 非功能需求

| 类别 | 要求 |
| --- | --- |
| 延迟 | 标的解析只读本地索引/缓存，P95 ≤ 100 ms；intake 创建（不含模型）P95 ≤ 300 ms；不得在解析路径调用 AkShare/网络 |
| 确定性 | 同一输入 + 同一会话快照必须得到同一解析/选题结果；不得依赖随机数或模型温度 |
| 可用性 | 索引缺失/损坏时回退到代码形状解析 + 标的搜索，不阻塞整页；解析异常可重试/直接分析 |
| 兼容 | v1 客户端行为不变；v2 新字段全部可选/可缺省；旧草稿可读 |
| 可观测 | 记录 `resolution_status`、`resolution_reason`、`question_count`、`auto_start`、`candidate_count`、耗时；不记录用户持仓原文、账号、完整问题正文 |
| 安全 | 不把内部注册表路径、provider、异常栈返回前端；自由文本搜索必须服务端校验；不得越权读取其他会话 |
| 一致性 | 自动开始、手动确认、刷新恢复、双标签页、重复请求仍只接受一次研究 |
| 可访问 | 键盘、屏幕阅读器、深浅主题、减少动画、320–1440 px |
| 可维护 | 解析/推断/选题各自独立模块；题库枚举集中；禁止在组件里维护股票名单 |

---

## 11. 验收标准

### 11.1 标的解析用例（必须全过）

| # | 输入 | 期望 status | 期望身份/范围 | 期望问题数 | 期望自动开始 |
| --- | --- | --- | --- | --- | --- |
| 1 | `分析 600519` | resolved | 贵州茅台 / 600519 / CN | 0 | 是 |
| 2 | `分析贵州茅台` | resolved | 贵州茅台 / 600519 / CN | 0 | 是 |
| 3 | `分析 00700` | resolved | 腾讯控股 / HK00700 / HK | 0 | 是 |
| 4 | `分析 0700.HK` | resolved | 腾讯控股 / HK00700 / HK | 0 | 是 |
| 5 | `分析 hk700` | resolved | 腾讯控股 / HK00700 / HK | 0 | 是 |
| 6 | `分析 腾讯` | resolved | 腾讯控股 / HK00700 / HK | 0 | 是 |
| 7 | `分析 AAPL` | resolved | Apple / AAPL / US | 0 | 是 |
| 8 | `分析 aapl` | resolved | Apple / AAPL / US | 0 | 是 |
| 9 | `分析 中芯国际` | ambiguous | 688981 CN / 00981 HK | 1 | 否 |
| 10 | `分析 比亚迪` | resolved | 比亚迪 / 002594 / CN | 0 | 是 |
| 10b | `分析 比亚迪股份` | resolved | 比亚迪股份 / 01211 / HK | 0 | 是 |
| 11 | `分析 苹果` | missing | 本地索引无此精确名称 | 1（搜索） | 否 |
| 12 | `今天大盘怎么样` | not_required | market=CN（或配置市场） | 0 | 是 |
| 13 | `我的持仓怎么样` | not_required | portfolio | 0 | 是 |
| 14 | `看看我的自选` | not_required | watchlist | 0 | 是 |
| 15 | `什么是市盈率` | not_required | concept | 0 | 是（直接回答） |
| 16 | `继续看看`（会话有宁德时代） | resolved | 宁德时代 / 300750 / CN | 0 | 是 |
| 17 | `它最近怎么样`（会话有标的） | resolved | 继承当前标的 | 0 | 是 |
| 18 | `分析 999999` | missing | 原代码 999999 | 1（信息卡） | 否 |
| 19 | `分析 7203.T` | resolved/unsupported | 丰田 / JP | 0/1 | 视数据源 |
| 20 | `分析 005930.KS` | resolved/unsupported | 三星电子 / KR | 0/1 | 视数据源 |
| 21 | `比较 600519 和 AAPL` | resolved | 两个具体标的 | 0/1 | 是/否 |
| 22 | `分析一下` | missing | 无对象 | 1（搜索） | 否 |

验收方式：新增表驱动单测覆盖以上用例；关键用例再加 API 集成测试和前端组件测试。第 11 例当前按“本地索引无精确名称 → 搜索”验收；若后续补中文别名映射，可转为 resolved，但必须有明确别名数据，不能靠模型猜。

### 11.2 交互验收

1. 输入 `分析 600519`，页面不出现“哪个市场”，直接显示简报并开始研究。
2. 输入 `分析 00700`、`0700.HK`、`aapl`，行为同上。
3. 输入 `分析 中芯国际`，出现两个具体候选（名称+代码+市场+推荐理由），不出现市场三选一；不点选不开始。
4. 候选卡不倒计时；只有用户点选或搜索后才继续。
5. 同一会话追问不重复问目标/周期/风险；简报显示“沿用本会话”。
6. 点“调整背景”打开轻量编辑，不跳页；修改后简报实时更新。
7. 自动开始后显示简报条；回答正常流出，不因简报条重复请求。
8. “直接分析”可用，未知项不被写成空仓/均衡/短线。
9. 刷新页面恢复 pending 草稿/已确认简报；不重复创建、不重复研究。
10. 双标签页/双击/重试只接受一次研究。
11. 概念题、报告追问、持仓/大盘/自选不出现无关选择题。
12. 首屏建议至少有一条来自自选/最近分析（有数据时），且可一键发送；无数据时建议轮换。
13. 中英文、深浅主题、320/390/768/1440 px、键盘和屏幕阅读器可用。
14. 解析失败/索引不可用时，页面不白屏，保留问题并可重试/直接分析。
15. 旧客户端（interaction_version=1）仍按原流程工作。

### 11.3 质量门槛

- 11.1 全部解析用例 100% 通过；跨市场候选不得静默选中。
- 新增/修改的 Python 文件 `py_compile` 通过，`python -m pytest -m "not network"` 通过。
- Web `npm run lint`、`npm run build`、相关 `npm test` 通过。
- 不新增网络依赖到 intake 创建路径；解析 P95 ≤ 100 ms（本地基准）。
- 不破坏 v1 API/存储/历史；旧草稿可读。
- PR 附关键页面截图（桌面/手机、深浅主题、候选/简报/自动开始），临时截图不入库。

---

## 12. 指标

上线后按周观察（只做聚合，不记录用户原文）：

| 指标 | 目标 | 说明 |
| --- | --- | --- |
| `intake_object_question_rate`（可确定代码/名称） | 0% | 最关键指标 |
| `intake_question_count` P50 / P90 | 0 / ≤2 | 对比 v1 的 3–5 |
| `intake_auto_start_rate` | 60%–80% | 信息足够时直接开始 |
| `intake_completion_rate` | ≥90% | 展示引导后进入研究 |
| `intake_candidate_correction_rate` | <1% | 用户改选候选的比例 |
| `intake_search_rate` | <10% | 解析失败率 |
| `suggestion_click_rate` | 观察 | 首屏建议有效性 |
| `time_to_first_research` | 下降 | 从发送到 accepted |
| `wrong_object_feedback` | <0.5% | 用户明确说“不是这只”的比例 |

指标埋点不得包含用户持仓、账号、完整问题正文；只记录枚举、计数、耗时。

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 名称碰撞导致错误推荐 | 分析错标的 | 候选卡必须显示具体身份；ambiguous 不自动开始；推荐理由可见 |
| 索引过期/缺行 | 解析失败或候选缺失 | 回退代码形状解析 + 搜索；明确“库内未收录”；可“仍然尝试” |
| 自动开始绕过确认 | 用户没机会改背景 | 只在高置信场景自动开始；简报条常驻可调整；高风险维度保持 unknown |
| 推断被当成事实 | 模型误判用户持仓/风险 | `sources` 区分；prompt 只把 explicit/user_choice 当约束；assumptions 只做分析侧重 |
| v2 破坏 v1 | 旧客户端异常 | `interaction_version` 分流；v1 题库/接口行为冻结；新字段全部可选 |
| 选择题变少导致研究泛化 | 回答不够贴切 | 答案中说明未指定项；简报条引导补充；后续追问继承 |
| 线程/会话竞争 | 重复研究、串会话 | 复用现有 revision CAS、session_generation、base_state_version、一次性消费 |
| 解析阻塞首问 | 体验变慢 | 只读本地缓存；P95 预算；超时回退搜索；不调用网络/模型 |
| 文案/交互复杂 | 用户看不懂 | 一屏简报、来源标签、具体候选；中英文同步；可用性测试 |

---

## 14. 发布与回滚

### 14.1 发布顺序

1. 后端契约 + 解析/推断/选题 + 测试（v1 不变）。
2. Web 类型/API/store 支持 v2，但默认仍发 v1。
3. Web UI 切换到 v2（`interaction_version=2`），灰度/本地验证。
4. 文档、CHANGELOG、截图和验收记录。

### 14.2 回滚

- Web 回退到发送 `interaction_version=1`，服务端 v1 路径保持可用。
- 后端保留 v1 分支和旧题库；不回滚数据库（P0 无新列）。
- 旧草稿继续可读；v2 草稿在回滚后按“未知字段忽略 + 旧题库”处理，不破坏会话。
- 若新增 `context_json` 列，必须先做兼容迁移和备份演练，再允许写入。

### 14.3 监控

- 观察 `resolution_status`、`question_count`、`auto_start`、`candidate_count`、错误码。
- 出现 `missing`/`unsupported` 异常升高时，优先检查股票索引/市场配置，不先改文案。
- 出现用户改选候选升高时，检查候选排序和推荐理由。

---

## 15. 待产品确认的决策

| # | 决策 | 建议 | 备选 |
| --- | --- | --- | --- |
| D1 | 跨市场同名无上下文时的默认推荐 | 当前会话 → 自选 → 最近分析 → `MARKET_REVIEW_REGION` → CN | 一律 CN；一律让用户选 |
| D2 | “分析 X”没有目标时是否自动开始 | 是，标“综合判断（未指定）”，简报条可调整 | 先问一题目标 |
| D3 | 是否保留歧义场景 10 秒自动确认 | 否；仅“保持当前标的”可倒计时 | 保留但默认关闭 |
| D4 | 是否引入 LLM 意图抽取 | P1 可选，默认关闭；P0 确定性规则 | 首版即启用 |
| D5 | 首屏建议的数据范围 | 当前会话 + 自选 + 最近 30 天分析 + 市场时段 | 只做静态模板轮换 |
| D6 | 不支持市场的默认动作 | 明确说明 + “仍然尝试/换一只” | 直接拒绝 |

---

## 16. 附录：当前代码证据

| 证据 | 位置 |
| --- | --- |
| 粗粒度“缺对象”判断 | `api/v1/endpoints/agent.py:101`、`:120-124` |
| 市场枚举题 | `src/agent/intake_questions.py:33-47` |
| 固定三问 | `src/agent/intake_questions.py:287-313` |
| 硬编码股票名单 | `src/agent/intake_questions.py:179-183` |
| `object_scope` 写选项 id | `src/agent/intake_questions.py:355-385` |
| 固定快捷问题 | `apps/dsa-web/src/pages/ChatPage.tsx:62-72`、`:1524-1548` |
| 逐题向导 | `apps/dsa-web/src/components/chat/ChatIntakeCard.tsx` |
| 歧义自动确认 | `apps/dsa-web/src/components/chat/StockClarificationCard.tsx:172-182` |
| 已有确定性对象解析 | `src/agent/chat_object_decision.py`、`src/services/agent_chat_session_service.py:190-220` |
| 已有代码/指数解析 | `src/services/stock_list_parser.py:290+`、`src/services/stock_code_utils.py:198-247` |
| 已有名称/代码查找 | `src/data/stock_index_loader.py:505-544` |
| 自选/历史能力 | `apps/dsa-web/src/hooks/useWatchlist.ts`、`apps/dsa-web/src/api/history.ts` |

---

本文只定义需求与验收，不代表功能已实现。具体开发拆分、文件清单、依赖顺序和工作量见[开发任务书](stock-chat-intake-v2-tasks.md)。
