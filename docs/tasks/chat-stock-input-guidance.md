# 需求与开发任务书：问股「输入标的」引导体验优化

> 状态：待实施（需求定稿；§10 的 5 项待所有者决定，不阻塞实施）
> 任务类型：`feat`（内含 6 处 `fix`：P1 / P3 / P7 / P8 / U8 / U10）
> 影响面：问股空状态 / 输入框 / 首问理解卡 / 会话中澄清卡 / 首问标的解析 / 股票索引别名与排序 / i18n / 测试
> 基线：`main` @ `3ccd2d5d`（2026-11-28）。行号会漂移，本文统一用「文件 + 函数/组件名」引用
> 实施方式：拆 4 个批次，按 A → B → C → D 顺序提交，每批可独立回滚

## 0. 写给接手工程师

### 0.1 一句话目标

用户不需要事先知道股票代码和写法：输入名称、简称、拼音首字母或代码，都能在**发送前**选到确定的标的；没认出来时，界面说明原因并给出一步到位的下一步，而不是只显示「没能确定你想分析的股票」。

### 0.2 已核实的关键事实（改代码前先认领）

- 问股输入只有一个自由文本 `<textarea>`（`components/chat/ChatInputArea.tsx`）。首页用的名称/代码/拼音联想组件 `StockAutocomplete`，在问股页只出现在「没认出股票」之后的卡片里。
- 首问走 `POST /api/v1/agent/chat/intake`（`interaction_version=2`）：`AgentChatIntakeService.create_intake_v2` → `object_resolution.resolve_intake_object` → `intake_questions.build_smart_intake`。结果为 `resolved` / `not_required` 时自动开跑；`ambiguous` / `missing` 时出 `ChatUnderstandingCard`。
- 前后端读同一份索引 `apps/dsa-web/public/stocks.index.json`，共 31,710 行：美股 23,357、A 股 5,207、港股 2,739、北交所 315、指数 33、日股 30、韩股 29；**没有 ETF**；只有 117 行带别名；`popularity` 全部是 100。后端经 `src/data/stock_index_loader.get_chat_stock_lookup()` 读取；另有远端索引缓存路径 `src/services/stock_index_remote_service.py`。
- `resolve_intake_object` 单次约 0.7 秒（本机每条取样 3 次，693–707ms）。所以**不能**做「每次击键请求服务端预览」，联想必须用前端本地索引。
- `ChatPage` 已持有 `stockIndex`（`useStockIndex`）和 `watchlistCodes`，本需求不需要新增数据请求。
- 消息里的显式代码在会话中是直接切换（`decide_chat_object` 返回 `confirm`，不出澄清卡）。因此「选股后把 `名称（代码）` 写进输入框」不需要新接口。按市场分层抽样 55 行，其中 54 行今天已能解析回所选标的（失败的 1 行是英文名含 `&` 和 `.` 的美股）。

### 0.3 约束

- 不改变 `docs/ai/STATUS.md` 记录的 R9 标的绑定口径（由 `tests/test_agent_active_stock_integration.py::test_established_object_subject_question_contract` 固定）。批次 A 的解析改动只作用于首问（没有已建立标的）。
- 不新增接口、不新增配置项；不在前端复刻后端解析规则（`utils/chatIntentHints.ts` 头注释已写明两侧不得各有一套）。
- T40 的「打字不重渲染历史消息」必须保持：联想状态放在 `ChatInputArea` 内部，不提升到 `ChatPage`。
- 新增文案只进 `apps/dsa-web/src/i18n/uiText.ts`，zh / en 同时补齐。
- `StockAutocomplete` 被首页使用，改动必须对首页行为零影响（新增能力一律走带默认值的可选 prop）。

## 1. 现状与问题

### 1.1 用户诉求

问股界面引导用户输入代码的设计不友好，要求对这一部分做深度优化。

### 1.2 现有链路

| 环节 | 位置 | 现状 |
| --- | --- | --- |
| 空状态 | `ChatGuideCards.tsx`；文案 `chat.welcomeTitle` / `chat.emptyDescriptionDefault` | 一句说明加 6 条示例问题 |
| 示例问题 | `ChatPage.tsx::QUICK_QUESTIONS` | 写死在页面里；点击立即发送，并把该策略写入会话选择 |
| 输入框 | `ChatInputArea.tsx`；`chat.composerPlaceholder` | 纯文本；placeholder「给问股发送消息，例如：分析 600519」 |
| 当前标的 | `ChatInputArea` 底部 `.chat-object-status` | 小字「当前讨论对象未确认」或「当前讨论：…」，不可点击 |
| 首问解析 | `object_resolution.resolve_intake_object` | 先 `decide_chat_object`，再匹配大盘关键词，最后做别名子串扫描 |
| 首问卡片 | `ChatUnderstandingCard.tsx` | `ambiguous`：候选单选加搜索；`missing`：只有搜索和「修改问题」 |
| 会话中澄清 | `StockClarificationCard.tsx` | 切换 / 保持 / 询问三态，带 10 秒自动确认 |
| 联想能力 | `StockAutocomplete` + `hooks/useAutocomplete.ts` + `utils/searchStocks.ts` | 精确 / 前缀 / 包含三档计分，同分按 `popularity` |

### 1.3 实测：常见输入现在会得到什么

方法：在基线上直接调用 `classify_question_intent` → `resolve_intake_object` → `build_smart_intake`，数据为仓库内置索引，不写库。

| 编号 | 输入 | 现在的结果 | 用户实际遇到的情况 |
| --- | --- | --- | --- |
| P1 | `aapl` | `resolved`，标的是 **AAPJ**（名称 AAP），`auto_start=true` | 直接开始分析一只错的股票 |
| P2 | `tsla`、`nvda怎么样` | `missing` | 弹「没能确定你想分析的股票」；而 `TSLA`、`分析 nvda` 都正常 |
| P3 | `分析 123456`、`分析 999999` | `resolved`（无名称），`auto_start=true` | 对不存在的代码直接开跑 |
| P4 | `分析苹果`、`英伟达怎么样`、`特斯拉最近为什么跌`、`台积电怎么样`、`看看阿里` | `missing` | 美股没有中文名可认 |
| P5 | `700` | `missing` | `00700`、`hk00700`、`0700.HK` 都正常，短码不行 |
| P6 | `最近行情怎么样`、`半导体板块还能追吗`、`推荐几只股票`、`我想买点白酒股`、`帮我看看我的股票` | `missing`，阻塞 | 不是个股问题，却必须选一只股票或改问题才能继续 |
| P7 | `APPLE（AAPL）怎么样` | `ambiguous`（AAPJ / AAPL） | 名称和代码都写对了，仍被要求二选一 |
| P8 | 卡片搜索框提交 `苹果`、`随便写` | `validate_chat_identity` 不报错，返回以该文本为代码的标的 | 任意文本可被确认为分析标的 |
| P9 | `分析 QQQ`、`分析 510300` | `resolved`，无名称 | 能分析，但界面只显示代码（索引无 ETF） |
| P10 | `分析 300750 和 002594` | `ambiguous` | 给了两只代码，被要求只选一只 |

P1 与 P7 同源：`_scan_alias_identities` 用 `alias in text` 做子串判断，AAPJ 的名称 `AAP` 命中了 `aapl`。同文件之外的 `chat_object_decision.object_mentions` 已经用 `stock_scope._has_ascii_token_boundaries` 做整词判断，这里漏了。

前端联想排序（把 `searchStocks.ts` 的计分规则原样套在同一份索引上计算）：

| 编号 | 查询 | 结果 |
| --- | --- | --- |
| S1 | `苹果`、`google` | 0 条。下拉不出现，也没有「没找到」提示 |
| S2 | `alibaba` | 前两条是粉单 `ALBBY`、`ALBHF`，`BABA` 排第三；`ali` 的前五条全是无关美股 |
| S3 | `700` | 前五条是代码含 700 的 A 股，腾讯（00700）不在其中 |
| S4 | `tsm` | 第一条正确，其后 4 条全是带 `(OTC)` 的粉单 |

### 1.4 读码发现的界面问题

| 编号 | 位置 | 问题 |
| --- | --- | --- |
| U1 | `chat.emptyDescriptionDefault`、`chat.composerPlaceholder` | 只示范一个 A 股代码；没说支持哪些市场、代码怎么写、可以直接写名称。英文界面的 placeholder 示例仍是 `600519`，与同页说明里的 `AAPL` 不一致 |
| U2 | `ChatPage.tsx::QUICK_QUESTIONS` | 6 条全是中文硬编码（英文界面也显示中文）；全是策略术语问法（缠论 / 波浪 / 箱体 / 情绪周期）；5 条 A 股、1 条港股、没有美股 |
| U3 | `ChatInputArea.tsx` | 输入过程中没有任何「认出了哪只股票」的反馈，对不对要等发送后服务端返回 |
| U4 | `.chat-object-status` | 新会话显示「当前讨论对象未确认」：用词偏内部、不可点击、不能用来选股或换股 |
| U5 | `ChatUnderstandingCard`（`missing`） | 只有标题、搜索框、禁用的「请选择一只股票」和「修改问题」。`chat.understanding.missingHelp` 已定义但从未渲染；没有原因说明、没有自选股可点、没有「不指定股票直接问」的出口（`agentChatStore.skipIntake` 存在，页面未接线） |
| U6 | 同上 | 搜索框的 label 和 placeholder 是同一句「搜索股票名称或代码」，重复显示 |
| U7 | 同上，`handleSearchSubmit` | 搜索选中后卡片里没有「已选」呈现，只有主按钮文字变成「分析 XXX」；选中对象的 `market` 写死 `UNKNOWN`、`asset_type` 写死 `stock` |
| U8 | `ChatPage.tsx` 的 `intakeError` 渲染条件 | 只在 `!intakeDraft` 时渲染。卡片还在时确认失败，页面没有任何提示 |
| U9 | `StockAutocomplete` / `SuggestionsList` | 无结果时下拉不出现；候选项右侧显示「精确 / 前缀 / 包含 / 模糊」；列表 id 写死 `suggestions-list`（同页两个实例会重复）；`aria-autocomplete="none"`，没有 `aria-activedescendant` |
| U10 | `StockClarificationCard` | 「输入股票代码」按钮只是把焦点移到输入框。`ask` 形态下用户点选候选后，标题仍是「需要你确认具体标的」，副标题变成「请在上方输入股票或指数代码。」——用户已经选完，且输入框在下方 |
| U11 | `_validate_object_selection` | 校验失败的文案是英文 `Unknown stock selection` |

### 1.5 根因归纳

1. **引导方式是「示范一种写法」，不是「帮用户选」**。联想能力只放在失败之后的卡片里，输入框本身是纯文本。
2. **解析器对常见写法不稳**：小写、短码、美股中文名、ASCII 子串误命中；并且把「代码形状合法」当成了「股票存在」。
3. **失败路径是死胡同**：不说原因、不给现成选项、不给不选股的出口，确认失败也不提示。
4. **索引数据不支撑中文用户找美股**：没有中文别名，没有热度排序。

### 1.6 未核实项

- P8 只核实到校验层（`validate_chat_identity` 接受任意文本）。以这类标的开跑后下游的具体表现没有跑。
- 手机端卡片内搜索下拉是否被软键盘遮挡没有实机验证：下拉用 `position: fixed` 向下展开，而卡片紧贴页面底部的输入区，存在遮挡风险。
- 远端索引缓存是否默认启用、与内置文件的选择规则（`find_existing_stock_index_path`）没有核实，批次 B 开工前先确认。
- `skip` 动作在 `object` 必答题未答时能否通过没有跑测试，见任务 D2。

## 2. 目标与非目标

### 2.1 目标

- G1：名称、简称、拼音首字母、代码四种输入，都能在发送前选到确定的标的。
- G2：§1.3 的 P1–P8 全部修复，不再绑定错的或不存在的标的。
- G3：没认出股票时，卡片说明原因并给出一步到位的下一步；非个股问题不被强制选股。
- G4：入口文案与示例覆盖 A 股 / 港股 / 美股，中英文界面各有一套。
- G5：会话中换股有显式入口。
- G6：不新增接口；旧页面（缓存中的前端）仍可完成首问。

### 2.2 非目标

- 不改机器人端（`bot/commands/ask.py`）的引导文案。
- 不把 ETF 加入索引（P9），不改多标的对比的交互（P10），见 §10。
- 不优化解析耗时（0.7 秒），不移除 10 秒自动确认，见 §10。
- 不动 `interaction_version=1` 的旧首问路径，不改首页 `StockAutocomplete` 的行为。

## 3. 需求规格

### 3.1 R1 空状态

1. 说明文案改为 §3.6 的 `chat.emptyDescriptionDefault`，明确「写名称就行，不知道代码也可以」。
2. 说明下方常显一行格式提示（`chat.inputFormatsHint`）：`A股 600519 · 港股 00700 · 美股 AAPL · 指数 沪深300`。
3. 新增「从自选开始」：取 `watchlistCodes` 前 6 只，chip 显示「名称 代码」。点击后把 `formatStockMention`（§4.2）加一个空格写入输入框并聚焦，**不发送**。没有自选时整组不渲染。
4. 示例问题迁入 `uiText.ts`，zh / en 各一组，各 6 条，覆盖：买卖判断、涨跌原因、美股、港股、两只对比、大盘；最多保留 1 条策略问法，且仅在对应策略可用时显示。点击行为不变（立即发送）；不带策略的示例不改动会话的策略选择。手机端仍只显示前 4 条。

   中文组：`茅台现在能买吗` / `腾讯最近为什么跌` / `英伟达 NVDA 的主要风险是什么` / `比亚迪和宁德时代哪个更值得拿` / `今天大盘怎么样` / `用缠论分析茅台`（策略）。
   英文组由实施者按同样的覆盖面编写，全部使用代码明确的写法（如 `Is NVDA a buy right now?`）。每条示例都必须满足验收用例 3。

### 3.2 R2 输入框

1. **选股入口**：输入框工具条新增「选股票」按钮（`Search` 图标加文字；手机端只显示图标并带 `aria-label`）。点击打开选股面板。
2. **选股面板**（新组件 `ChatStockPicker`）：
   - 桌面：输入框上方的弹层，宽度同输入框，最高 320px，向上展开。手机（`useTouchLayout()` 为真）：用现有 `components/common/BottomSheet.tsx`。
   - 内容：搜索输入（自动聚焦，字号 16px）。未输入时显示「自选」列表；输入后显示结果，复用 `useAutocomplete` + `searchStocks`，上限 8 条。
   - 每行显示市场徽标、名称、代码；**不显示**匹配类型徽标。
   - 无结果时显示 `chat.picker.noResult`，不留空白。
3. **选中**：在输入框光标处插入 `formatStockMention(item, language)` 加一个空格，关闭面板，焦点回到输入框，不自动发送。
4. **被动联想**：光标在文本末尾且不在输入法组合态时，按 §4.3 取「待查片段」调 `searchStocks(片段, index, { limit: 5 })`；最高分 ≥ `MATCH_SCORE.PREFIX_MIN` 才显示（同一弹层，不带搜索框）。
   - `↑` / `↓` 移动高亮，`Tab` 或点击采纳（把片段替换为 mention），`Esc` 关闭，且在片段变化前不再弹出。
   - **`Enter` 的语义不变**：只有用户用方向键移动过高亮时，Enter 才是采纳；否则照常发送。
   - 手机端改为输入框上方一行横向 chip（最多 3 个），不遮挡键盘。
5. **标的 chip**：会话已有标的时，工具条显示「讨论中：名称 代码」chip，替代底部 `.chat-object-status` 文本；点击打开选股面板用于换股。没有标的时不再显示任何「未确认」文案。`stateContract === 'legacy'` 时，现有的「旧版服务」说明放进 chip 的 `title`。
6. **换股预告**（P1）：草稿里有由面板插入、且仍留在文本中的 mention，并且其代码与当前标的不同时，输入框上方显示一行 `chat.switchPreview`。判断只依据「面板插入记录」，不在前端复刻解析规则。
7. **placeholder 三态**：无标的 `chat.composerPlaceholder`；有标的 `chat.composerPlaceholderWithObject`；首问卡片等待中沿用 `chat.intake.composerHint`。

### 3.3 R3 首问解析正确性（后端，仅首问）

1. ASCII 别名必须整词命中：`_scan_alias_identities` 对 ASCII 别名复用 `stock_scope._has_ascii_token_boundaries`。`aapl` 不得解析为 AAPJ，`APPLE（AAPL）怎么样` 解析为 AAPL。
2. 小写代码与大写等价：没有已建立标的时，一个 ASCII 整词等于索引内的代码（大小写不敏感），且不在现有常用词表中（复用 `src/agent/orchestrator.py::_COMMON_WORDS`，`stock_scope` 已在用，不新建），结果与大写一致。`aapl`、`tsla`、`nvda怎么样` 均为 `resolved`。
3. 代码形状合法但索引里没有、也不是已登记指数：**不得自动开跑**。返回 `status="ambiguous"`、`reason="unknown_code"`、`candidates=[该代码的 identity（无名称）]`、`recommended=None`，`plan.auto_start=false`。不硬拦截，因为 ETF / 基金 / 新股不在索引里但可以分析。
4. 港股短码：整条消息（或「分析类动词 + 数字」）是 1–4 位数字，补零后命中港股时，返回 `status="ambiguous"`、`reason="short_code"`，候选为该港股并设为推荐。数字出现在句子其他位置不触发。
5. 非个股问题不拦截：扩充 `_MARKET_SCOPE_PATTERNS`，使 P6 里的行情 / 板块 / 行业 / 选股 / 推荐类问题返回 `not_required`。该判断只在没有识别到任何标的时才到达（现有顺序已保证），`分析茅台行情` 这类不受影响。`帮我看看我的股票` 仍是 `missing`，由 R4 的自选快捷承接。
6. 对象选择校验：`_validate_object_selection` 对候选之外的选择，要求「索引里存在」或「满足第 3 条的代码形状」，否则 422 `invalid_intake_answer`。
7. 契约：对索引里任意一行，`formatStockMention` 的输出单独成句、以及后接一个问句时，都解析回该行。

### 3.4 R4「没认出股票」卡片

1. 标题与说明按 `resolution.reason` 分形态，文案见 §3.6。没有 `reason` 的旧数据走通用 `missing` / `ambiguous` 形态。
2. 快捷选择：`missing` 形态在搜索框上方显示自选股 chip（最多 6 个），点击即选中。
3. 搜索：
   - label 与 placeholder 分开（`searchLabel` / `searchPlaceholder`）。
   - 无结果时显示 `chat.picker.noResult`。
   - 选中后在卡片内显示「已选」行（市场徽标、名称、代码、移除按钮）；`market` 与 `asset_type` 取自索引项，不再写死。
   - 回车提交自由文本：仅当文本通过 `validateStockCode` 时接受为「未收录代码」，否则不建立选择，显示无结果提示。
4. 出口：`missing` 形态新增次按钮 `chat.understanding.askWithoutStock`，调用 `skipIntake`。`ambiguous` 形态不显示。注意 `skip` 用的是通用背景，会丢掉从问题里推断出的偏好，本期接受。
5. 错误：确认或跳过失败时，在卡片内显示错误行（`role="alert"`），保留已选内容。`invalid_intake_answer` 显示前端文案 `chat.understanding.invalidSelection`，不直接展示服务端 message。
6. `unknown_code` 形态：主按钮「仍按 123456 分析」，说明这个代码不在股票库里。
7. `short_code` 形态：标题直接问「你是指 腾讯控股（HK00700）吗？」，候选默认选中。

### 3.5 R5 会话中换股

1. 澄清卡的「输入股票代码」改名「换一只股票」，点击打开 R2 的选股面板（保留现有的暂停倒计时）。
2. `ask` 形态选定后：标题改为「已确认」，副标题沿用 `resolvedSwitch` / `resolvedKeep`。删除「请在上方输入股票或指数代码。」。
3. 标的 chip 换股，见 R2 第 5 条。

### 3.6 R6 文案

| key | zh | en |
| --- | --- | --- |
| `chat.emptyDescriptionDefault`（改） | 直接写股票名称或代码提问，不知道代码也可以，输入时会帮你找。 | Ask about any stock by name or ticker. Suggestions appear as you type. |
| `chat.inputFormatsHint`（新） | A股 600519 · 港股 00700 · 美股 AAPL · 指数 沪深300 | US AAPL · HK 00700 · A-share 600519 · Index CSI 300 |
| `chat.composerPlaceholder`（改） | 输入股票名称或代码提问，如：茅台现在能买吗 | Ask about a stock, e.g. Is NVDA a buy right now? |
| `chat.composerPlaceholderWithObject`（新） | 继续问 {name}，或输入其他股票换一只 | Keep asking about {name}, or name another stock to switch |
| `chat.pickStock`（新） | 选股票 | Pick a stock |
| `chat.picker.searchPlaceholder`（新） | 名称、代码或拼音首字母，如 茅台 / 600519 / gzmt / AAPL | Name or ticker, e.g. Apple / AAPL / 00700 |
| `chat.picker.watchlist`（新） | 自选 | Watchlist |
| `chat.picker.noResult`（新） | 没有找到「{query}」。可以试试代码，或公司全称。 | No match for “{query}”. Try the ticker or the full company name. |
| `chat.currentObject`（改） | 讨论中：{object} | Discussing: {object} |
| `chat.switchPreview`（新） | 发送后将改为讨论 {name} | Sending this switches the discussion to {name} |
| `chat.guide.fromWatchlist`（新） | 从自选开始 | Start from your watchlist |
| `chat.understanding.missingTitle`（改） | 没在问题里找到具体股票 | No specific stock found in your question |
| `chat.understanding.missingHelp`（改并启用） | 选一只股票继续；如果问的是大盘或板块，可以不指定股票。 | Pick a stock to continue. For market or sector questions you can skip this. |
| `chat.understanding.unknownCodeTitle`（新） | 股票库里没有 {code} | {code} is not in the stock list |
| `chat.understanding.unknownCodeHelp`（新） | 可能是 ETF、基金、新上市代码，或输入有误。 | It may be an ETF, a fund, a new listing, or a typo. |
| `chat.understanding.analyzeAnyway`（新） | 仍按 {code} 分析 | Analyze {code} anyway |
| `chat.understanding.shortCodeTitle`（新） | 你是指 {name}（{code}）吗？ | Did you mean {name} ({code})? |
| `chat.understanding.searchLabel`（新） | 换一只 | Choose another |
| `chat.understanding.searchPlaceholder`（改） | 同 `chat.picker.searchPlaceholder` | 同 `chat.picker.searchPlaceholder` |
| `chat.understanding.selected`（新） | 已选 | Selected |
| `chat.understanding.askWithoutStock`（新） | 不指定股票，直接提问 | Ask without a stock |
| `chat.understanding.invalidSelection`（新） | 这不是有效的股票或代码，请从列表里选。 | That is not a valid stock or ticker. Pick one from the list. |

`StockClarificationCard` 的文案目前在组件内 `copyFor`，本次改动的三条（`inputButton`、`ask` 形态的已确认标题与副标题）就地修改，不做整体迁移。

### 3.7 R7 无障碍与手机端

- 联想输入使用 `aria-autocomplete="list"`、`aria-activedescendant`，列表 id 用 `useId` 生成。
- 选股面板可用键盘完成：打开后焦点在搜索框，`Esc` 关闭并把焦点还给触发按钮。
- 手机端：面板与卡片内的输入框字号 16px；候选行高度不小于 44px；候选列表不被软键盘遮挡（卡片内搜索在手机端向上展开，或改用 `BottomSheet`）。

## 4. 数据与接口契约

### 4.1 接口

- 不新增接口、不新增字段、不新增配置项。
- `ObjectResolution.reason` 新增取值 `unknown_code`、`short_code`。旧页面不读 `reason`，会按 `ambiguous` 渲染候选单选，仍可完成首问。
- `PATCH /api/v1/agent/chat/intake/{intake_id}` 对非法对象选择返回 422（现在是放行），属于行为收紧。

### 4.2 `formatStockMention(item, language)`

选股写入输入框的唯一格式来源（新文件 `apps/dsa-web/src/utils/stockMention.ts`）：

- 标签取 `nameZh`；如果 `nameZh` 不含中日韩文字，则取 `aliases` 中第一个含中日韩文字的别名；都没有则不带标签。
- 有标签：zh 输出 `标签（displayCode）`，en 输出 `标签 (displayCode)`。无标签：只输出 `displayCode`。
- 例：`贵州茅台（600519）`、`腾讯控股（00700）`、`苹果（AAPL）`（批次 B 之后）、`EVOA`。

美股英文名不写入输入框：索引里的英文名是缩写（如 `EVO TRSP.&.EN.SVS.`），对用户没有信息量，且是抽样中唯一解析失败的写法。

### 4.3 待查片段 `deriveStockQuery(draft)`

同文件内的纯函数：

1. 取最后一个分隔符之后的文本。分隔符为空白、中英文标点和括号。
2. 去掉开头的动词前缀：`分析` / `看看` / `看下` / `研究` / `诊断` / `查一下` / `帮我看看` / `帮我分析` / `换成` / `改看` / `切换到` / `对比` / `比较`，其后可带「一下」。
3. 长度不在 2–12 之间返回空。
4. 纯 ASCII 片段若在 `utils/chatStockCode.ts::FREE_TEXT_TICKER_DENYLIST` 中，返回空（需把该常量导出，不复制）。

### 4.4 别名与热度

- 新增种子文件 `scripts/stock_index_seeds/stock_aliases.csv`，列为 `canonical_code,alias`。初始内容：`src/data/stock_mapping.py::STOCK_NAME_MAP` 里 19 个外盘代码的中文名，加上实施者补充的常用简称（至少覆盖 P4 的 `台积电`、`阿里`，以及 S1 的 `谷歌` / `google`）。
- 两处消费同一个文件，不得各自再维护别名表：
  - `scripts/generate_stock_index.py`：生成索引时合并进 `aliases`，让前端联想可用。
  - `get_chat_stock_lookup()`：读取索引后叠加，让远端刷新的索引同样带别名。
- `popularity` 在生成脚本里赋值：种子文件内的代码 100；名称含 `(OTC)` 的美股 10；其余 50。
- 索引行结构（10 元组）不变。

## 5. 开发任务

### 批次 A：首问解析正确性（后端，`fix`）

| # | 文件 | 改动 | 对应 |
| --- | --- | --- | --- |
| A1 | `src/agent/object_resolution.py::_scan_alias_identities` | ASCII 别名整词命中 | R3.1 / P1 / P7 |
| A2 | `src/agent/object_resolution.py::resolve_intake_object` | 首问小写代码等价 | R3.2 / P2 |
| A3 | 同上；`src/agent/intake_questions.py::build_smart_intake` | `unknown_code`：确认单候选、无推荐时能正常出题且 `auto_start=false` | R3.3 / P3 |
| A4 | `src/agent/object_resolution.py` | `short_code` | R3.4 / P5 |
| A5 | `src/agent/object_resolution.py::_MARKET_SCOPE_PATTERNS` | 扩充非个股范围 | R3.5 / P6 |
| A6 | `src/services/agent_chat_intake_service.py::_validate_object_selection` | 存在性校验 | R3.6 / P8 |
| A7 | `tests/test_smart_intake.py`、`tests/test_agent_chat_intake_service.py`、`tests/test_agent_chat_intake_api.py` | §1.3 的 P1–P8 逐条成为参数化用例；R3.7 的契约用例按市场分层取样，不要全量遍历（单次解析约 0.7 秒） | 全部 |

验证：`./scripts/ci_gate.sh`。`tests/test_agent_active_stock_integration.py` 不应有任何改动。

### 批次 B：索引别名与搜索排序（`改进`）

| # | 文件 | 改动 | 对应 |
| --- | --- | --- | --- |
| B0 | — | 先确认远端索引是否默认启用，以及与内置文件的选择规则，把结论写进提交说明 | §1.6 |
| B1 | `scripts/stock_index_seeds/stock_aliases.csv`（新）、`scripts/generate_stock_index.py`、`src/data/stock_index_loader.py::get_chat_stock_lookup` | 别名种子与两处消费 | §4.4 / P4 / S1 |
| B2 | `scripts/generate_stock_index.py` | `popularity` 赋值 | §4.4 / S2 / S4 |
| B3 | `apps/dsa-web/public/stocks.index.json` | 按 `scripts/refresh_stock_index.py` 的现有流程重新生成并同步 | — |
| B4 | `apps/dsa-web/src/utils/searchStocks.ts` | 港股代码去掉前导零后与查询完全相等，按精确代码命中计分 | S3 |
| B5 | `tests/test_stock_index_loader.py`、`apps/dsa-web/src/utils/__tests__/searchStocks.test.ts` | 别名命中、排序用例；断言 `STOCK_NAME_MAP` 的每个外盘代码在种子文件里都有中文别名，防止两者漂移 | — |

验证：`./scripts/ci_gate.sh`；`cd apps/dsa-web && npm run lint && npm test && npm run build`。

### 批次 C：空状态与输入框（前端，`feat`）

| # | 文件 | 改动 | 对应 |
| --- | --- | --- | --- |
| C1 | `apps/dsa-web/src/utils/stockMention.ts`（新） | `formatStockMention`、`deriveStockQuery` 及单测 | §4.2 / §4.3 |
| C2 | `apps/dsa-web/src/components/chat/ChatStockPicker.tsx`（新）；`SuggestionsList.tsx` 增加可选 prop `showMatchType`（默认 `true`）、`listId` | 选股面板 | R2.2 / U9 |
| C3 | `apps/dsa-web/src/components/chat/ChatInputArea.tsx` | 选股按钮、被动联想、标的 chip、键盘规则；联想状态留在本组件内 | R2 / U3 / U4 |
| C4 | `apps/dsa-web/src/pages/ChatPage.tsx` | 传入 `stockIndex`、自选列表；插入逻辑；placeholder 三态；移除底部 `objectStatusText` | R2.5 / R2.7 |
| C5 | `apps/dsa-web/src/components/chat/ChatGuideCards.tsx`、`ChatPage.tsx::QUICK_QUESTIONS` | 格式提示、自选 chip、示例迁入 `uiText.ts`（`skill` 改为可选） | R1 / U1 / U2 |
| C6 | `apps/dsa-web/src/i18n/uiText.ts`、`apps/dsa-web/src/research.css` | 文案与样式 | R6 |
| C7 | `components/chat/__tests__/ChatInputArea.test.tsx`、`pages/__tests__/ChatPage.test.tsx`、`e2e/fixtures/chat-mobile.spec.ts` | 见 §6 用例 1–9 | — |

### 批次 D：首问卡片与澄清卡（前端，`feat` + `fix`）

| # | 文件 | 改动 | 对应 |
| --- | --- | --- | --- |
| D1 | `apps/dsa-web/src/components/chat/ChatUnderstandingCard.tsx` | reason 分形态、自选快捷、已选行、搜索无结果态、自由文本规则、错误行 | R4 / U5–U7 |
| D2 | `apps/dsa-web/src/pages/ChatPage.tsx` | 接线 `skipIntake`；把 `intakeError` 传进卡片。先写测试确认 `skip` 在 `object` 题未答时能通过；如被拦下，在 `update_intake` 的 `skip` 分支放行并补后端用例 | R4.4 / R4.5 / U8 |
| D3 | `apps/dsa-web/src/components/chat/StockClarificationCard.tsx` | 文案修正；「换一只股票」打开选股面板 | R5 / U10 |
| D4 | `apps/dsa-web/src/components/StockAutocomplete/StockAutocomplete.tsx` | 唯一列表 id、`aria-*`、可选的无结果态 prop（默认关闭，首页不变） | R7 / U9 |
| D5 | `components/chat/__tests__/ChatUnderstandingCard.test.tsx`、`StockClarificationCard.test.tsx`、`pages/__tests__/ChatPage.test.tsx` | 见 §6 用例 10–18 | — |

### 每个批次都要做

- `docs/CHANGELOG.md` 的 `[Unreleased]` 追加扁平条目。
- 批次 C、D 属于 Web UI 改动：按 `AGENTS.md` 留存改动前后截图（桌面 + 手机宽度，zh + en），截图不入库。
- 批次 D 合入后更新 `docs/ai/STATUS.md` 的「首问引导」一行，并评估 `docs/full-guide.md` / `docs/full-guide_EN.md` 的问股条目是否需要同步。

## 6. 验收用例

**空状态与输入框**

1. 新会话：说明下方能看到一行格式提示；有自选时显示「从自选开始」，没有自选时该组不出现。
2. 点自选 chip：输入框写入 `贵州茅台（600519） `，光标在末尾，没有发送。
3. 逐条点击示例问题（zh 与 en 两组）：都直接开始分析，标的或范围正确，不出现「没在问题里找到具体股票」。
4. 输入 `茅`：不联想；输入 `茅台`：出现「贵州茅台 600519」；继续输入 `茅台现在能买吗`：联想自动消失。
5. 联想出现时直接按 Enter：照常发送，联想不拦截。按 `↓` 再按 Enter：采纳联想，不发送。按 `Tab`：采纳第一条。
6. 输入法组合态下回车确认候选：不发送、不采纳。
7. 点「选股票」→ 输入 `gzmt` → 回车选中第一条：输入框写入 `贵州茅台（600519） `。
8. 选股面板输入 `苹果`：批次 B 之后第一条是 AAPL；输入 `alibaba`：BABA 排在所有带 `(OTC)` 的条目之前；输入 `700`：第一条是腾讯控股。
9. 手机宽度：选股面板以底部面板出现；被动联想是输入框上方的一行 chip；键盘弹出时候选可见。

**解析**

10. `aapl`、`AAPL`、`分析 aapl` 三种写法得到同一只股票（AAPL）。
11. `tsla`、`nvda怎么样` 直接开始分析对应美股。
12. `分析 123456`：不开跑，出现「股票库里没有 123456」；点「仍按 123456 分析」后开跑。
13. `700`：出现「你是指 腾讯控股（HK00700）吗？」；`茅台跌了 700 块怎么办` 不触发该卡片。
14. `最近行情怎么样`、`半导体板块还能追吗`、`推荐几只股票`：直接开始回答，不要求选股。
15. `分析苹果`、`英伟达怎么样`（批次 B 之后）：直接开始分析对应美股；`看看阿里`：给出美股 / 港股候选。

**卡片**

16. `帮我看看我的股票`：卡片显示原因说明、自选 chip、搜索框，以及「不指定股票，直接提问」。点自选 chip 后卡片内出现「已选」行，主按钮变为「分析 XXX」。
17. 卡片搜索框输入 `随便写` 回车：不建立选择，显示无结果提示。直接调接口提交该文本返回 422。
18. 模拟确认接口失败：卡片内出现错误提示，已选内容保留，可重试。
19. 点「不指定股票，直接提问」：开始回答，不再出现第二张要求选股的卡片。

**会话中**

20. 已在讨论茅台：工具条显示「讨论中：贵州茅台 600519」；点击 → 选宁德时代 → 输入框写入 `宁德时代（300750） `，上方出现「发送后将改为讨论 宁德时代」；发送后直接切换，不出现澄清卡。
21. 澄清卡 `ask` 形态点选一个候选：卡片显示「已确认」，不再出现「请在上方输入…」。
22. 澄清卡点「换一只股票」：打开选股面板，倒计时暂停。

**回归**

23. 首页分析输入框的联想行为、样式与基线一致（候选项仍显示匹配类型徽标）。
24. 从报告页「追问 AI」进入问股：不出现选股卡片，标的取自报告。
25. `interaction_version=1` 的旧首问路径仍可完成首问。

## 7. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| 后端（批次 A、B） | `./scripts/ci_gate.sh`；最低要求 `python -m py_compile <changed_python_files>` |
| 前端（批次 B、C、D） | `cd apps/dsa-web && npm ci && npm run lint && npm test && npm run build` |
| 手机端 | Playwright `e2e/fixtures/chat-mobile.spec.ts` 新增用例 9 |
| 索引 | 重新生成后跑 `tests/test_stock_index_loader.py`、`tests/test_stock_index_remote_service.py`、`tests/test_refresh_stock_index.py` |
| 文档 | 核对 `docs/CHANGELOG.md`、`docs/ai/STATUS.md` 与实现一致 |

交付说明需写明：是否覆盖了首问接口（`/chat/intake` 的创建与确认）和问股流式接口；真实模型下的端到端首问是否手工验证过。

## 8. 兼容性、风险与回滚

- **旧页面**：`reason` 新值被忽略，按 `ambiguous` 渲染，可完成首问。旧页面卡片里提交自由文本会得到 422 且不显示错误（U8 的旧行为），刷新后恢复。
- **批次 A5 误放行**：把本该选股的问题当成大盘问题直接回答。靠 A7 的参数化用例约束，且该判断只在没有识别到任何标的时才到达。
- **批次 A2 误识别**：把普通英文词当成代码。约束为「整词、等于索引内代码、不在常用词表」，并且只作用于首问。
- **批次 B 别名歧义**：`阿里` 同时指向美股和港股，会走候选卡而不是直接开跑，符合预期；两字别名的子串风险由现有 `SHORT_NAME_RIGHT_BOUNDARY_CHARS` 规则处理。
- **被动联想打扰**：输入普通句子时可能短暂弹出（如输入 `中国` 时）。由分数阈值、防抖和「Enter 不被拦截」三条约束兜住；仍觉得打扰时可只保留显式选股入口（删除 C3 的被动联想部分，不影响其余功能）。
- **回滚**：四个批次各自 `git revert` 即可。批次 B 回滚时同时还原 `stocks.index.json`；无数据库迁移，无配置回滚。

## 9. 被否决的方案

- **服务端实时预览「将分析哪只股票」**：解析单次约 0.7 秒，按击键请求不可行；而且需要新接口。
- **选股后以结构化字段随请求发送（不写进文本）**：需要在首问和流式两个接口加字段，并定义「字段与文本不一致」时谁优先，会触碰 R9 契约。写入 `名称（代码）` 文本已能被现有解析唯一识别，用户也能看见并修改自己发送的内容。
- **把示例问题改成「点击只填充、不发送」**：会改变现有点击即问的行为，对第一次使用的人反而多一步。改为新增不发送的「从自选开始」，两种入口并存。

## 10. 待所有者决定

1. **会话中的裸小写代码与美股中文名**：已有标的时，`tsla怎么样` 现在按「维持当前标的」处理（实测 `aapl` → `maintain`）。是否纳入切换，涉及 R9 契约，本期不动。
2. **两只代码是否直接按对比处理**（P10）：`分析 300750 和 002594` 现在要求二选一。
3. **10 秒自动确认是否保留**：切换 / 保持两种澄清会在 10 秒后自动发送「确认」。
4. **ETF 是否进索引**（P9）：`QQQ`、`510300` 能分析但没有名称，联想也搜不到。
5. **解析耗时**：首问「正在理解问题」至少 0.7 秒，来自对 3 万多个名称逐一做正则扫描；轮次内还会再执行一次 `decide_chat_object`。是否单独立项优化。
