# 问股加速开发任务书

状态：待实施，实施需用户另行确认 · 2026-11-11 · 基线 `89683b78` · 任务类型：refactor + feat

本任务书把 [问股响应耗时分析与加速方案](stock-chat-latency-optimization-plan.md) 拆成可独立提交的开发任务。目标是把一次完整分析的大模型往返从至少 4 次降到 2 次，去掉取数路径上的固定等待，并让回答边生成边显示。原因分析和收益估算见方案文档，这里只写做什么、改哪里、怎么验收。

方案文档里第 1 批估算为 1–2 天。细化后发现K线缓存规则和配套测试的工作量更大，第 1 批（T1–T4）上调为 3–4 天，全部任务合计约 8–10 个工作日。

## 范围

- 覆盖 Web 问股及加载同一份 Web 构建的桌面端，主路径是单 Agent 模式（`AGENT_ARCH=single`）的 `POST /api/v1/agent/chat/stream`。
- Bot 聊天与 Web 问股共用提示词和取数层，T1–T4 的改动对它同样生效，需要回归。流式输出（T5、T6）只面向 Web。
- 每日分析报告、扫描器、回测不在范围内。它们使用的报告提示词 `AGENT_SYSTEM_PROMPT`、批量防封休眠和冻结日期取数路径保持现状。
- 多 Agent 模式（`AGENT_ARCH=multi`）只受益于 T2–T4。它的流式输出和阶段并行列在候选项里，T0 确认线上在用 `multi` 时再提前。

## 任务总览

| 任务 | 内容 | 对应方案项 | 依赖 | 预计 |
| --- | --- | --- | --- | --- |
| T0 | 每轮 LLM 耗时日志与实测基线 | 第 0 步 | 无 | 0.5 天 |
| T1 | 问股提示词：合并取数轮、按问题取数、历史工具结果时效 | 1-1、1-2 | T2 | 1 天 |
| T2 | K线缓存新鲜度、并发去重与写回 | 1-3 | T0 | 1–1.5 天 |
| T3 | 交互路径取消固定休眠 | 1-4 | T0 | 0.5 天 |
| T4 | 单次超时与模型配置 | 1-5、1-6 | T0 | 1 天 |
| T5 | 流式输出后端 | 第 2 批 | T0 | 2–3 天 |
| T6 | 流式输出前端 | 第 2 批 | T5 | 1.5–2 天 |
| T7 | 文档、配置同步与上线 | 全部 | 随各任务 | 0.5 天 |

建议拆成 5 个 PR，按顺序合入：T0；T2 + T3；T1；T4；T5 + T6。T1 必须晚于 T2，否则并行取数会让同一份K线被下载多次。

## 已定的默认决策

以下几点在方案里留了口子，这里给出默认做法。实施时有不同意见，先和需求方确认再改。

- **盘中仍要当日K线。** 开盘后到次日开盘前，缓存必须含当日这根K线才算新鲜。盘中技术指标要反映当前价格，所以盘中仍会联网，重复下载由 60 秒内存缓存消除。
- **历史工具结果的复用时效由服务端控制，默认 600 秒。** 模型拿不到当前时间，无法自己判断数据新旧。超过时效的历史工具结果不注入上下文，模型自然会重新取数。
- **流式中途失败时先撤回再走现有降级链。** 已显示的临时文本清空，然后按现有顺序尝试备用模型，可靠性语义与现在一致。
- **流式输出由客户端显式开启，服务端另有总开关且首发默认关闭。** 旧版前端会把不认识的事件塞进进度列表，所以不能无条件下发增量事件。
- **流式首版只做单 Agent。** 多 Agent 的最终回答可能在决策阶段之后被再加工，放到候选项。

## T0 · 每轮 LLM 耗时日志与实测基线

入口是 `run_agent_loop` 的 LLM 调用。[runner](../../src/agent/runner.py#L465)

- 在 `llm_adapter.call_with_tools` 前后计时，每轮写一行 INFO 日志，字段包含步号、模型、耗时秒数、`prompt_tokens`、`completion_tokens` 和本轮工具调用数。token 数取自 `response.usage`，缺失时写 0。
- 在已有的 `Agent completed in N steps` 日志里追加 LLM 累计耗时和工具累计耗时，便于一行看出时间花在哪一侧。
- 只加日志。不新增 SSE 字段，不改 `RunLoopResult` 的对外结构，不新增脚本。
- 用 10 个真实问题在线上或与线上同配置的环境取基线：A 股完整分析 2 个（交易时段、非交易时段各一），美股完整分析 2 个，港股完整分析 1 个，只问价格或估值 2 个，对已分析标的的追问 3 个。每题记录总耗时、LLM 轮数、每轮耗时、每个工具耗时。工具耗时可直接读该条答复的 `conversation_messages.thinking_steps_json`。
- 同时记录线上配置：`AGENT_ARCH`、`LITELLM_MODEL`、`AGENT_LITELLM_MODEL`、各数据源优先级、`AGENT_*_TOOL_TIMEOUT_S`、`AGENT_ORCHESTRATOR_TIMEOUT_S`。
- 基线表格和配置清单贴在 PR 描述或评论里，不作为仓库文件合入。
- 测试：在 `tests/test_agent_executor.py` 或现有 runner 用例里，用日志捕获断言每轮出现一行耗时日志，且字段齐全。
- **完成条件**　任选一题，各轮 LLM 耗时与各工具批次耗时之和与整轮耗时相差不超过 5%。基线表覆盖 10 题。线上若在用 `multi` 或思考型模型，在结论里明确写出，并据此调整后续任务的优先级。

## T1 · 问股提示词：合并取数轮与按问题取数

依赖 T2。入口是 `CHAT_SYSTEM_PROMPT` 和 `LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT`，两个模板必须同步修改；历史工具结果的注入在 `build_agent_chat_context_bundle`。[通用模板](../../src/agent/executor.py#L427) · [默认技能模板](../../src/agent/executor.py#L390) · [上下文拼装](../../src/agent/chat_context.py#L141-L235)

- **删除分阶段约束。** 去掉「必须严格按阶段执行」的四阶段流程和「禁止将不同阶段的工具合并到同一次调用」的警告。
- **完整分析一轮取齐。** 用户要求分析、评估或给出操作建议时，第一轮在同一次回复里并行调用 `get_realtime_quote`、`get_daily_history`、`analyze_trend`、`search_stock_news`，A 股个股再加 `get_chip_distribution`。问题需要基本面、资金流或大盘板块时，对应工具也放进同一轮。工具结果返回后直接作答，只有关键数据缺失才追加一轮。
- **单点问题只取所需。** 只问价格、涨跌、估值、某个指标或某条消息时，只调用对应的一两个工具并简短回答。
- **追问复用已有数据。** 当前上下文里已有同一标的的工具结果时直接使用。用户明确要求最新数据时全部重取。涉及当前价格的判断可以只重取 `get_realtime_quote`。
- **保留的规则。** 必须用工具取真实数据、不编造数字、应用激活的技能、风险优先、工具失败不重复调用，以及模板里的 `{market_guidelines}`、`{default_skill_policy_section}`、`{skills_section}`、`{language_section}` 四个占位符。
- **服务端控制复用时效。** 新增配置 `AGENT_CHAT_TOOL_REUSE_SECONDS`，默认 600，0 表示不过滤。拼装上下文时，`created_at` 早于时效的 provider turn 不注入，并在诊断信息里用新的丢弃原因计数，做法与现有的模型不匹配、预算超限一致。可见的问答文本不受影响。
- 不改报告用的 `AGENT_SYSTEM_PROMPT` 和 `LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT`，不改多 Agent 各阶段的提示词。
- 测试：
  - 提示词契约测试：两个模板都不再包含分阶段禁止语句，都包含一轮并行取数的要求，四个占位符可以正常 `format`。
  - `tests/test_agent_chat_executor.py` 或上下文相关用例：超过时效的 provider turn 不出现在模型输入里，时效内的照常注入，配置为 0 时与现状一致。
  - 真实模型回归：用 T0 的 10 题复测。这一项不进 CI，结果贴 PR。
- **完成条件**　10 题中完整分析类至少 4/5 在 2 次 LLM 往返内完成，且第一轮同时发出行情、K线、技术指标和新闻调用。单点问题和追问的往返不超过 2 次。回答仍引用行情、技术、筹码、新闻四类数据，结论与改动前同题回答没有实质退化。Bot `/chat` 抽查 2 题通过。PR 描述附修改后的完整提示词。

## T2 · K线缓存新鲜度、并发去重与写回

入口是 `load_history_df`。[history_loader](../../src/services/history_loader.py#L141) 调用方有 5 处 Agent 工具和 1 处筛选服务。[get_daily_history](../../src/agent/tools/data_tools.py#L333-L384) · [技术分析工具](../../src/agent/tools/analysis_tools.py#L25-L30) · [筛选服务](../../src/services/screening_service.py#L3363)

**新鲜度规则。** 现在的条件是库里最新K线日期不早于服务器自然日的今天。[现状](../../src/services/history_loader.py#L165-L176) 改为按标的所属市场的本地时间判断，市场用 `get_market_for_stock` 解析，日历用 `trading_calendar` 里已有的交易所日历。[交易日历](../../src/core/trading_calendar.py#L239)

| 市场本地时间 | 缓存须包含的最新K线日期 |
| --- | --- |
| 非交易日 | 上一个交易日 |
| 交易日开盘前 | 上一个交易日 |
| 交易日开盘后，含午休和收盘后 | 当日 |
| 日历不可用或市场无法识别 | 服务器自然日当天，即现状 |

- 这条规则只作用于没有显式 `target_date`、也没有冻结日期的默认路径。每日分析通过 `set_frozen_target_date` 走的路径不变。
- `get_effective_trading_date` 在盘中返回上一个已收盘交易日，不能直接当作本规则使用。建议在 `trading_calendar` 里新增一个返回上表结果的小函数，供 `load_history_df` 调用。
- DB 查询区间的结束日同步改为上表日期，避免美股场景下用服务器日期查出空窗。

**并发去重与短时缓存。**

- 同一标的、同一截止日、同一窗口档位的联网取数在进程内合并成一次。并发到达的调用等待同一个结果，结果在内存里保留 60 秒。
- 缓存键使用严格模式下绑定的规范代码，沿用 `_history_code_candidates` 的身份规则。`HistoryIdentityError` 仍在查缓存之前抛出。指数和同码股票不得共用缓存项。
- 联网取数的窗口向上取整到两档：不超过 120 个交易日的请求统一取 120，超过的取 365。返回给调用方时按各自请求的 `days` 截取，保持各工具现有的行数语义。
- 返回 DataFrame 的副本，调用方修改不得污染缓存。失败结果只通知当次等待者，不进缓存。缓存条目数设上限并按时间淘汰。

**写回数据库。**

- 给 `load_history_df` 增加是否写回的参数，默认关闭。5 处 Agent 工具调用开启，筛选服务保持关闭。
- 联网取到的K线在这一处统一写回，`_handle_get_daily_history` 里现有的写回逻辑改为复用它，避免写两次。[现有写回](../../src/agent/tools/data_tools.py#L346-L361)
- 写回失败只记日志，不影响返回。

测试扩展 `tests/test_history_loader.py`，现有 6 个用例保持通过，新增覆盖：

- 周六、节假日、交易日开盘前，库里有上一交易日K线时命中缓存，不联网。
- 交易日开盘后库里只有昨日K线时联网；库里有当日K线时命中。
- 美股标的在北京时间上午命中缓存，取的是美东日期对应的最新交易日。
- 日历抛异常时退回现状规则。
- 5 个线程并发请求同一标的，底层取数只被调用 1 次；60 秒后再次请求会重新取数；取数失败后下一次请求会重试。
- 冻结日期路径行为不变。
- 写回参数开和关两种情况；指数与同码股票互不串用。
- 筛选服务相关的现有用例通过。

**完成条件**　周末和开盘前各问一次同一只 A 股完整分析，日志显示本轮K线联网下载 0 次或 1 次。交易时段同一轮内 5 个K线相关工具合计只联网 1 次。美股标的在北京时间白天不再每个工具各下载一次。

## T3 · 交互路径取消固定休眠

入口是两个数据源的 `_enforce_rate_limit`，以及问股的执行入口。[efinance](../../data_provider/efinance_fetcher.py#L331-L350) · [akshare](../../data_provider/akshare_fetcher.py#L453-L472)

- 在 `data_provider` 内新增一个表示「交互式取数」的 `ContextVar` 和配套的上下文管理器。命名和位置与现有的 `TOOL_CANCEL_EVENT`、冻结日期变量保持一致的风格。
- 交互式上下文中，`_enforce_rate_limit` 只在距上次请求不足 `sleep_min` 时补足差额，不再执行每次请求前的随机休眠。非交互上下文的行为一个字节都不变。
- 进入交互式上下文的位置有三处：`AgentChatExecutor.execute_turn`、`AgentExecutor.chat` 和多 Agent 的问股执行入口。`AgentExecutor.run` 和每日分析流水线不进入。[单 Agent 入口](../../src/agent/chat_executor.py#L140)
- 上下文必须在执行线程内设置。SSE 接口用 `loop.run_in_executor` 启动执行线程，这个调用不会复制协程里的上下文。工具线程由 runner 用 `copy_context` 启动，会继承执行线程的设置。[工具线程](../../src/agent/runner.py#L942)
- 基本面等内部另起线程的取数路径如果没有继承上下文，会保持原有休眠。这属于安全退化，本任务不处理，在 PR 里列出受影响的工具。
- 同一个数据源实例的调用由管理器的锁串行化，休眠发生在锁内，所以并行工具打到同一数据源时休眠会逐个累加。T1 合并取数之后这一点更明显，T3 应与 T2 同批合入。[数据源调用锁](../../data_provider/base.py#L787-L791)
- 美股数据源 Finnhub 和 Alpha Vantage 各有 0.3–1.5 秒的固定休眠，本任务不动。
- 测试：新增用例覆盖两个数据源。交互式上下文下，上次请求很久以前时不休眠；间隔不足时只补足差额；非交互上下文仍调用随机休眠。现有 7 个引用 `_enforce_rate_limit` 或 `random_sleep` 的测试文件保持通过。
- **完成条件**　问股一轮内首次联网取数没有固定等待，连续两次请求同一数据源的间隔不小于 `sleep_min`。每日分析的日志里休眠行为与改动前一致。上线后一周内对比 `record_provider_run` 记录的数据源失败率，没有明显上升。

## T4 · 单次超时与模型配置

**工具超时。** 使用现有配置项，不改代码。[默认值](../../src/config.py#L1134-L1137)

- 线上设置 `AGENT_DATA_TOOL_TIMEOUT_S=20`、`AGENT_SEARCH_TOOL_TIMEOUT_S=20`、`AGENT_ANALYSIS_TOOL_TIMEOUT_S=30` 作为起始值，再按 T0 基线里各类工具的 P95 调整，取 P95 的 1.5–2 倍。
- 这三项对后台报告的 Agent 同样生效，设置前确认报告流程里最慢的工具不会被误伤。代码里的默认值保持 0。

**单次 LLM 调用上限。** 入口是 `LLMToolAdapter.call_completion` 的模型降级循环。[call_completion](../../src/agent/llm_adapter.py#L599-L682)

- 新增配置 `AGENT_LLM_CALL_TIMEOUT_S`，浮点数，默认 0，表示不设单次上限，与现状一致。
- 上限必须作用在每个模型的单次尝试上，取「单次上限」和「整体剩余预算」中较小的一个。不能把它当作传给 `call_completion` 的总超时，否则主模型耗尽时间后，备用模型没有机会执行。
- Router 配置了 `num_retries=2`。实施时核对 LiteLLM Router 是否对超时异常重试。如果重试，最坏等待是上限的 3 倍，需要让超时直接切到下一个模型，或在文档里写明实际最坏值。[Router 配置](../../src/agent/llm_adapter.py#L490-L494)
- 线上建议值 90 秒。

**强制思考开关。** 入口是 `_call_litellm_model` 里对 `get_thinking_extra_body` 的调用。[调用位置](../../src/agent/llm_adapter.py#L705-L718)

- 新增配置 `AGENT_OPT_IN_THINKING`，布尔值，默认 true，与现状一致。设为 false 时 Agent 不再为 `deepseek-chat` 这类需显式开启的模型附加思考参数。
- `get_thinking_extra_body` 还被 `src/analyzer.py` 的报告生成使用。开关只在 Agent 适配层判断，函数本身和报告路径不变。
- 验证同一会话先开后关思考后继续对话不报参数错误。历史消息里带着 `reasoning_content`，部分供应商在非思考模式下会拒收。

**取数轮用快模型。** 只改配置。

- 用现有的 `AGENT_LITELLM_MODEL` 把问股 Agent 指向响应快的非思考型模型，用 T0 的 10 题对比耗时和回答质量，结论贴 PR 或运维记录。
- 切换模型后，旧模型留下的 provider turn 会因模型不匹配被丢弃，属于现有行为。

**配置同步。** 两个新配置项按 `AGENT_*_TOOL_TIMEOUT_S` 的先例处理：`src/config.py` 的字段和环境变量解析、`.env.example`、`docs/full-guide.md` 与 `docs/full-guide_EN.md` 的环境变量表。这组工具超时项目前没有进 Web 设置页的配置注册表，新项默认也不进。

测试：

- 参照 `tests/agent/test_tool_timeout.py` 的写法，覆盖 `AGENT_LLM_CALL_TIMEOUT_S` 的解析和非法值处理。
- 适配层用例：主模型超过单次上限后切到备用模型并成功返回；整体预算小于单次上限时以整体预算为准；配置为 0 时调用参数与现状一致。
- 思考开关为 false 时请求里没有思考参数，为 true 时与现状一致；`src/analyzer.py` 的相关用例不受影响。

**完成条件**　模拟主模型挂起，问股在单次上限附近切到备用模型并正常返回。模拟某个数据工具挂起，该工具在配置的时限内以超时结束，其余数据照常用于回答。两个新配置项不设置时，行为与基线一致。

## T5 · 流式输出后端

入口是 SSE 接口、单 Agent 执行器、执行循环和 LLM 适配层。事件契约见现有文档。[接口](../../api/v1/endpoints/agent.py#L612) · [执行器](../../src/agent/chat_executor.py#L140) · [执行循环](../../src/agent/runner.py#L410) · [适配层](../../src/agent/llm_adapter.py#L691) · [事件契约](../agent-stream-events.md)

**开关。**

- `ChatRequest` 新增可选字段 `stream_content`，布尔值，默认 false。
- 新增服务端配置 `AGENT_CHAT_STREAM_CONTENT`，布尔值，首发默认 false。两者都为真时才下发正文增量。
- 非流式的 `POST /api/v1/agent/chat`、Bot、报告流程不传回调，行为不变。

**适配层。**

- `call_completion` 增加可选的文本增量回调。提供回调时以流式方式调用 LiteLLM，逐块读取，把正文片段交给回调。
- 读完后用 LiteLLM 的 `stream_chunk_builder` 把分块还原成完整响应，再交给现有的 `_parse_litellm_response`。工具调用、`reasoning_content`、`provider_blocks`、`thought_signature` 和用量统计都走原解析路径。
- 只转发正文。模型的思考内容不作为增量下发，这一点与「分析过程」任务里私有推理不外露的约定一致。
- 部分 OpenAI 兼容供应商在流式下需要显式请求才返回用量。取不到时使用 `stream_chunk_builder` 的估算值，并保证 `persist_llm_usage` 不因缺字段报错。
- 参数自动修正 `call_litellm_with_param_recovery` 只包住发起请求这一步。开始读取分块之后出现的错误不再触发参数重试。
- 中途失败的处理：已经下发过增量的那次尝试失败时，先通过回调通知撤回，再按现有顺序尝试下一个模型。

**执行循环。**

- `run_agent_loop` 增加可选的正文回调，不传时逻辑与现在完全一致。
- 新增两个事件：`content_delta` 带 `step` 和 `text`；`content_reset` 带 `step` 和 `reason`。在 `stream_events.py` 里补对应的构造函数。
- 某一轮先输出了文字、最后却返回工具调用时，在执行工具前发出 `content_reset`。
- `generating` 事件现在在最后一轮 LLM 返回之后才发。[现状](../../src/agent/runner.py#L575-L576) 开启流式后改为在某一轮收到第一个正文片段时发出，每轮最多一次；该轮被撤回后，下一次出字时重新发。未开启流式时保持原时机。
- `done` 仍携带完整正文，它是唯一的权威结果。保证最后一次撤回之后的全部增量拼接起来与 `done.content` 完全一致。

**执行器与接口。**

- `AgentRunRequest` 增加可选的正文回调字段，由 `LiteLLMAgentBackend` 透传给执行循环。
- `execute_turn` 里包装回调时，`content_delta` 和 `content_reset` 只转发给传输层，不进入 `collected_progress_steps`，不触发 `update_running_progress`，也不进入最终保存的 `thinking_steps`。[进度落库](../../src/agent/chat_executor.py#L151-L170)
- 增量在服务端合并后再下发，建议每 50 毫秒或累计约 24 个字符发一次，避免逐 token 产生事件。
- 刷新或断线后的恢复沿用现有的会话详情轮询。运行中的临时文本不落库，恢复后先看到进度，结束时看到完整回答。

**与其他任务的衔接。** [首次提问引导与精读回答](stock-chat-guided-answer-task.md) 计划在输出前增加一道回答精编。它落地之后，流式下发的应是精编那一次调用的输出，实施前与该任务的负责人对齐顺序。

测试：

- 适配层：用伪造的分块迭代器覆盖纯文本、纯工具调用、先文本后工具调用、中途异常四种情况，断言还原出的 `LLMResponse` 与非流式路径字段一致。
- `tests/test_agent_stream_events.py`：新事件的字段，`generating` 在两种模式下的时机，撤回后的事件顺序。
- `tests/test_agent_chat_executor.py`：增量事件不进入进度收集和落库，保存的 `thinking_steps` 与未开启流式时一致。
- `tests/test_agent_chat_api.py` 和 `tests/test_agent_sse_cleanup.py`：两个开关的四种组合里只有全开时出现增量事件；增量拼接等于 `done.content`；客户端中途断开后清理行为不变。
- `tests/test_llm_adapter_provider_trace.py` 保持通过。用线上实际使用的模型做一次带工具调用的多轮流式会话，确认 Claude 的思考块、Gemini 的 `thought_signature` 这类需要原样回传的字段没有丢。丢失的供应商在该模型上关闭流式。

**完成条件**　开启后，单 Agent 问股在最后一轮 LLM 开始输出的 2 秒内下发第一个 `content_delta`。四类分块情况的事件序列符合上面的约定。关闭任一开关时，SSE 事件序列与基线逐字段一致。

## T6 · 流式输出前端

依赖 T5。入口是请求类型、会话状态和问股页面。[请求](../../apps/dsa-web/src/api/agent.ts#L220) · [事件处理](../../apps/dsa-web/src/stores/agentChatStore.ts#L608-L685) · [加载中渲染](../../apps/dsa-web/src/pages/ChatPage.tsx#L1592)

- `ChatStreamRequest` 增加 `stream_content`，Web 发起问股时固定传 true。
- 在 `processLine` 里先处理 `content_delta` 和 `content_reset`，再走通用分支。这两类事件不得进入 `progressSteps`。[通用分支](../../apps/dsa-web/src/stores/agentChatStore.ts#L683-L684)
- 新增当前请求的临时正文状态。收到增量时追加，收到撤回时清空，收到 `done` 或 `error`、用户停止、切换会话、请求归属校验失败时清空。沿用 `ownsStream` 的归属判断，晚到的增量不得写入别的会话。
- 加载中且临时正文非空时，在进度区下方用现有的 Markdown 组件渲染一条进行中的助手消息。收到 `done` 后由服务端全文替换，同一轮只出现一条助手消息。
- 渲染节流，建议合并到每帧或至少间隔 50 毫秒更新一次。用 3000 字以上的回答确认输入框和滚动不卡顿。
- 自动滚动的依赖里加入临时正文。用户向上滚动查看历史时，不被新增内容拉回底部，沿用现有的判断。
- 用户点「停止接收」后丢弃临时正文，后台完成后的结果仍通过现有恢复路径出现。
- 服务端未开启流式或是旧版服务端时收不到增量事件，页面表现与现在一致。
- 测试：
  - `agentChatStore.test.ts`：增量累加、撤回清空、`done` 替换、归属不符的增量被忽略、增量不进入 `progressSteps`、无增量时与现状一致。
  - `ChatPage.test.tsx`：进行中的消息随增量出现，结束后只剩一条最终消息，停止后临时文本消失。
- **完成条件**　真实问股时正文逐段出现，结束时内容与服务端保存的一致。刷新、切换会话、停止、断线重连都不产生重复消息或残留文本。执行 `cd apps/dsa-web && npm ci && npm test && npm run lint && npm run build` 通过，桌面端加载同一份构建可用。PR 附桌面和移动端在出字过程中与完成后的截图。

## T7 · 文档、配置同步与上线

随各 PR 完成，不单独成 PR。

- **事件契约。** `docs/agent-stream-events.md` 增加 `content_delta`、`content_reset`、请求字段 `stream_content` 和 `generating` 的新时机，写明事件为追加式、需客户端显式开启。
- **配置。** 4 个新配置项同步到 `.env.example`、`docs/full-guide.md` 和 `docs/full-guide_EN.md`：`AGENT_CHAT_TOOL_REUSE_SECONDS`、`AGENT_LLM_CALL_TIMEOUT_S`、`AGENT_OPT_IN_THINKING`、`AGENT_CHAT_STREAM_CONTENT`。两份指南里另补 `AGENT_*_TOOL_TIMEOUT_S` 的建议值说明。请求字段 `stream_content` 记在事件契约文档里。
- **变更记录。** 每个 PR 在 `docs/CHANGELOG.md` 的 `[Unreleased]` 加一条扁平条目，类型按实际选 `改进` 或 `新功能`。
- **门禁。** 后端 PR 执行 `./scripts/ci_gate.sh`。涉及前端的 PR 执行前端构建命令。
- **上线顺序。**
  1. T0 上线后取基线。
  2. T2 + T3、T1、T4 依次上线，每次用同一组 10 题复测并与基线对比。
  3. T5 + T6 上线时服务端开关保持关闭，确认无回归后打开 `AGENT_CHAT_STREAM_CONTENT`。
  4. 稳定一周后评估是否把流式开关的默认值改为开启。
- **回滚。** 各任务相互独立。提示词、配置类改动恢复原值即可。流式关闭服务端开关就回到一次性返回，不需要回退前端。K线缓存和限速的改动按提交回退，不涉及数据库结构变更。

## 候选项

以下对应方案的第 3 批，本轮不实施。满足立项条件时另写任务。

| 候选 | 内容 | 立项条件 |
| --- | --- | --- |
| 服务端预取 | 标的确定且判定为完整分析时，在第一轮 LLM 之前并行取标准数据包，LLM 只需 1 轮 | T1 上线后，第一轮 LLM 仍占完整分析总耗时的 15% 以上 |
| 精简上下文 | 问股场景K线工具返回最近约 20 根加统计摘要；按标的类型收窄工具集 | 最后一轮输入超过 2 万 token，或输入费用成为主要成本 |
| 两段模型 | 取数轮用快模型，写回答用强模型 | T4 的快模型对比显示回答质量不可接受 |
| 工程开销 | 跨请求复用 LLM 适配器与 Router；进度落库节流；启动预热 | T0 基线显示准备阶段或冷启动超过 2 秒 |
| 多 Agent 流式与并行 | 决策阶段流式输出；技术与情报阶段并行 | T0 确认线上使用 `AGENT_ARCH=multi` |

## 总体验收

全部任务完成后，用 T0 的 10 题在同一环境复测，与基线对比：

- 完整分析的 LLM 往返中位数不超过 2 次。
- 完整分析总耗时中位数比基线下降 30% 以上。
- 开启流式后，完整分析从发送到出现正文的中位数不超过 20 秒，简单追问不超过 12 秒。
- 模拟单个数据源或模型通道挂起时，等待不超过配置的单次上限，问股仍能返回可用回答或明确的失败提示。
- 回答质量抽检无实质退化，数据源失败率无明显上升，Bot 聊天和每日分析报告行为不变。

以上阈值来自方案文档的估算。T0 基线出来后，如果估算与实测差距较大，以实测为准重新确定阈值并更新本文。

## 未验证项

- 线上实际的模型、Agent 架构和数据源优先级，由 T0 确认。
- LiteLLM Router 对超时异常的重试行为，以及各供应商在流式下对用量、思考块和签名字段的支持情况，由 T4、T5 实施时核对。
- 取消固定休眠后各数据源的限流阈值，由 T3 上线后的失败率观察确认。
