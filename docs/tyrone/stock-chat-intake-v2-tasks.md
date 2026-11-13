# 问股智能引导 v2 开发任务书

日期：2026-11-13。对应需求：[问股智能引导 v2 需求](stock-chat-intake-v2-requirements.md)。任务类型：feat + UX 重构。状态：待实施。

核对基线：本地 HEAD `51412d62`。本任务书只描述实施计划，不代表代码已修改或已部署。

---

## 1. 实施原则

1. **先复用，后新增**：标的解析复用 `parse_analysis_target()`、`get_chat_stock_lookup()`、`decide_chat_object()`，不另写股票名单/市场正则。
2. **契约先行**：先定 v2 schema、`interaction_version=2` 分流和兼容策略，再写 UI。
3. **v1 冻结**：旧客户端、旧题库、旧接口行为不变；v2 新路径单独测试，不靠改旧断言“顺手通过”。
4. **不做大爆炸重构**：解析、推断、选题、UI 分四个可独立 review 的改动；每个 PR 可单独回滚。
5. **不新增环境变量**：v2 由 `interaction_version` 启用；不增加部署开关。
6. **P0 不引入网络/模型依赖**：解析与推断只读本地索引和会话快照；LLM 抽取留 P1。
7. **每步可验证**：每个任务都有单测/组件测试/手工验收，不把“构建通过”当功能完成。

---

## 2. 依赖与实施顺序

```text
T0 契约与决策确认
 ├─ T1 后端 schema / 类型 / 兼容层
 │   ├─ T2 标的解析服务
 │   │   └─ T3 意图推断与会话继承
 │   │       └─ T4 选题计划与 API 接入
 │   │           ├─ T5 前端类型 / API / store
 │   │           │   ├─ T6 简报卡与自动开始
 │   │           │   ├─ T7 候选/搜索与澄清卡
 │   │           │   └─ T8 首屏建议
 │   │           └─ T9 文案 / 样式 / 可访问性
 │   └─ T10 测试与回归
 └─ T11 文档 / CHANGELOG / 发布验收
```

可并行：

- T2 完成后，T5 可先按契约写类型和 mock，不必等 T4。
- T6/T7/T8 可在 T5 后并行，但 T6 优先，因为它是主路径。
- T10 的解析用例表可在 T2 开始时就写，按 TDD 推进。

建议 PR 拆分：

| PR | 内容 | 依赖 |
| --- | --- | --- |
| PR-1 | 本文档 + 需求文档 + CHANGELOG 文档条目 | 无 |
| PR-2 | T1 + T2：schema、解析服务、解析单测 | PR-1 |
| PR-3 | T3 + T4：意图推断、会话继承、选题、API 接入、API 测试 | PR-2 |
| PR-4 | T5 + T6：前端类型/API/store、简报卡、自动开始、组件测试 | PR-3 |
| PR-5 | T7 + T9：候选/搜索、澄清卡、文案、样式、a11y | PR-4 |
| PR-6 | T8：首屏建议 + 测试 | PR-5 |
| PR-7 | T10 + T11：回归、文档、截图、CHANGELOG | PR-2–PR-6 |

每个 PR 只做自己范围；不要在一个 PR 里同时改解析、UI 和文案。

---

## 3. 任务清单

### T0 · 契约与产品决策确认（0.5 人日）

**目标**：在写代码前锁定会影响实现的决策，避免返工。

**输入**：需求文档第 15 节 D1–D6。

**要做**：

1. 确认 D1 候选排序：当前会话 → 自选 → 最近 30 天分析 → `MARKET_REVIEW_REGION` → CN。
2. 确认 D2：`分析 X` 无目标时自动开始，标“综合判断（未指定）”。
3. 确认 D3：歧义场景不倒计时；仅“保持当前标的”可倒计时。
4. 确认 D4：P0 不调用 LLM；P1 另立任务。
5. 确认 D5：首屏建议数据范围。
6. 确认 D6：不支持市场的文案和动作。
7. 把结论回写需求文档“待确认决策”表，状态改为“已确认”。

**交付**：需求文档决策表更新；无代码改动。

**完成条件**：D1–D6 都有明确结论，没有“实现时再看”。

---

### T1 · 后端 schema、类型与兼容层（1 人日）

**目标**：定义 v2 契约，保证 v1 不变。

**涉及文件**：

- `src/schemas/agent_intake.py`
- `src/agent/intake_questions.py`（只加 v2 类型/常量，不改 v1 行为）
- `api/v1/endpoints/agent.py`（请求/响应模型）
- `tests/test_agent_chat_intake.py`（新增 v2 契约断言，保留 v1）
- `apps/dsa-web/src/api/agentIntake.ts`（类型，可与 T5 合并）

**要做**：

1. 新增 `ObjectIdentity`、`ObjectResolution`、`ResolutionEvidence`、`BriefSource`、`IntakePlan` Pydantic 模型。
2. `AnalysisBrief` 追加 `object`、`sources`、`evidence`、`assumptions`、`auto_confirmed`；`brief_version` 支持 2；v1 字段保留。
3. `Question` 追加 `kind`、`options[].identity`、`recommended_option_id`、`allow_free_text`、`free_text_kind`。
4. `IntakeCreateRequest`/`IntakeUpdateResponse` 追加 `interaction_version`、`resolution`、`brief_preview`、`plan`（可选）。
5. `MAX_QUESTIONS_V2 = 2`；v1 `MAX_QUESTIONS` 仍为 5。
6. 明确未知字段/未知枚举的处理：422，不静默降级。
7. 不做数据库迁移；`schema_version=2` 只标记草稿结构，读取兼容 1。

**测试**：

- v1 请求/响应快照不变。
- v2 新字段可序列化/反序列化；缺省字段按 unknown 处理。
- 非法 `status`/`scope`/`source`/`kind` 返回 422。
- `brief_version=1` 的旧数据可读。

**完成条件**：

- `python -m py_compile` 通过。
- `tests/test_agent_chat_intake.py` 通过。
- 新 schema 有至少 1 组 JSON round-trip 测试。

**风险**：`AnalysisBrief` 被 prompt/answer editor/前端多处消费；新增字段必须可选，不能改变 `model_dump(exclude_none=True)` 的旧输出。

---

### T2 · 标的解析服务（2.5 人日）

**目标**：把“这段输入到底指哪只标的/哪个范围”做成确定性、可测试的服务。

**新增文件**：

- `src/services/chat_intake_object_resolver.py`（建议名）
- `tests/test_chat_intake_object_resolver.py`

**复用**：

- `src/services/stock_list_parser.py` `parse_analysis_target()`、`IndexRegistry`
- `src/services/stock_code_utils.py` `normalize_code()`、`is_code_like()`
- `src/agent/chat_object_decision.py` `decide_chat_object()`、`object_mentions()`
- `src/data/stock_index_loader.py` `get_chat_stock_lookup()`
- `src/services/agent_chat_session_service.py` `_stock_lookup()` 的构造方式（不要 import 私有函数；可提取共享 helper）
- `src/services/market_symbol_utils.py` 的 JP/KR/TW 判断

**接口草案**：

```python
@dataclass(frozen=True)
class IntakeObjectResolution:
    status: str          # resolved | ambiguous | missing | unsupported | not_required
    scope: str           # stock | index | etf | market | portfolio | watchlist | report | concept | unknown
    identity: Optional[StockIdentity]
    candidates: tuple[StockIdentity, ...]
    recommended_option_id: Optional[str]
    recommendation_reason: Optional[str]
    evidence: dict
    reason: str

def resolve_intake_object(
    message: str,
    *,
    active_stock: Optional[dict],
    report_context: Optional[dict],
    watchlist: Sequence[str] = (),
    recent_codes: Sequence[str] = (),
    registry: IndexRegistry,
    lookup: dict,
    default_market: str = "CN",
) -> IntakeObjectResolution:
    ...
```

**要做**：

1. 实现需求 R1 的 8 级解析顺序。
2. 代码解析全部走 `parse_analysis_target()`；覆盖 `600519`、`600519.SH`、`SH600519`、`00700`、`00700.HK`、`HK00700`、`hk700`、`AAPL`、`aapl`、`7203.T`、`005930.KS`、`2330.TW`。
3. 名称/别名解析走 `lookup["names"]`，NFKC + casefold；唯一命中 resolved，多命中 ambiguous。
4. 当前会话/报告继承：只在“无显式对象 + 追问/指代”时使用；显式新对象优先。
5. 市场/组合/概念 scope 识别；不把“大盘”当缺个股。
6. 模糊匹配限制在本索引内，最多 5 个候选；不调用 AkShare/网络。
7. 候选排序/推荐理由按需求 9.2 实现。
8. 返回结构化 evidence（原文片段/区间），不返回内部路径。
9. 解析失败不抛异常到 API；返回 missing/unsupported + reason。
10. 增加性能保护：索引查找缓存；单次解析不重复构造 registry/lookup。

**测试（表驱动，至少 30 例）**：

- 需求 11.1 的全部用例。
- 额外边界：`000001`（A股平安银行）vs `00001`（港股长和）；`00700` vs `700`；`AAPL` vs `AAPL.US`；`SHOP` 不被拆成 `SH` 前缀；`MA` 不被当股票；`SPX` 指数；`sh000001` 指数 vs `000001` 股票；空字符串/纯中文/纯标点。
- 歧义/缺失：中芯国际（A/H 歧义）、比亚迪（精确名 → CN）、比亚迪股份（精确名 → HK）、苹果（本地无精确名 → 搜索）、腾讯（唯一）、茅台（唯一）。
- 会话继承：有 active + “继续看看”；有 active + “换成比亚迪”；无 active + “它怎么样”。
- 不支持：`7203.T` 在无 JP 数据源时返回 unsupported 而不是 resolved。
- 候选排序：当前会话市场优先、自选优先、最近分析优先。

**完成条件**：

- 新测试全绿；v1 测试不变。
- 解析 P95 ≤ 100 ms（本地基准，索引已加载）。
- 代码中不出现硬编码股票名单。
- `python -m py_compile` 通过。

**风险**：

- `get_chat_stock_lookup()` 首次加载可能读文件；需在服务启动/首次请求预热，或接受一次冷启动。不得在解析路径做网络请求。
- 名称歧义可能比预期多；候选卡必须能承载 2–5 个，超过 5 个用搜索。
- `parse_analysis_target()` 对未注册指数可能返回 unsupported；需区分“格式不支持”和“库内没有”。

---

### T3 · 意图推断与会话背景继承（1.5 人日）

**目标**：从原问题提取已有背景，减少固定三问；追问继承上一轮 brief。

**新增文件**：

- `src/services/chat_intake_intent_resolver.py`
- `tests/test_chat_intake_intent_resolver.py`

**要做**：

1. 实现需求 R4 的 6 个维度规则；每条规则带 `source=explicit_message`、`confidence`、`evidence`（原文片段/区间）。
2. 规则必须大小写/全半角/中英文兼容；不依赖硬编码股票代码。
3. 读取上一轮 `brief_snapshot`，按 R5 继承；标 `session_inherited`。
4. 用户明确改变维度时只覆盖该维度；切换标的时清理旧标的专属字段。
5. 生成 `assumptions`：只对 `system_inferred`/`user_default` 生成；explicit/user_choice 不生成。
6. 输出 `unknown_fields` 与 `sources` 一致。
7. P0 不调用 LLM；预留 `infer_with_llm()` 接口但默认不启用，P1 另立任务。
8. 规则冲突（如“长期”+“只看明天”）返回 `conflict`，由 T4 生成 1 个澄清问题。

**测试（至少 25 例）**：

- 目标：能不能买/值不值得关注/已持有/被套/为什么涨/怎么分析/学习。
- 周期：短线/几天/1–4 周/半年/长期/今年/日内。
- 持仓：空仓/没买/持有/盈利/被套/加仓/减仓。
- 风险：稳健/保守/怕回撤/能接受波动/激进。
- 研究重点：估值/基本面/消息/技术面/风险/对比。
- 阅读方式：简单说/详细/先给结论/通俗解释。
- 否定/假设句：`如果跌破就卖` 不推断为“已持有”；`不想买` 不推断为“看多”。
- 会话继承：上一轮 goal/horizon 继承；用户改 horizon 只改该项；切标的清理 position。
- 冲突：长期 vs 只看明天。
- 空输入/纯符号不产生 explicit。

**完成条件**：

- 推断结果可 JSON 序列化，字段稳定。
- 同一输入 + 同一 session 快照结果确定。
- 不把未知写成默认值（空仓/均衡/短线）。
- v1 题库不受影响。

---

### T4 · 选题计划与 API 接入（1.5 人日）

**目标**：用 v2 解析/推断结果决定问什么、是否自动开始，并接入 intake API。

**涉及文件**：

- `src/agent/intake_questions.py`（新增 `plan_intake_v2()`，保留 `select_questions()`）
- `src/services/agent_chat_intake_service.py`
- `api/v1/endpoints/agent.py`
- `tests/test_agent_chat_intake_service.py`
- `tests/test_agent_chat_intake_api.py`
- 新增 `tests/test_agent_chat_intake_v2_api.py`

**要做**：

1. 新增 `plan_intake_v2(message, resolution, inference, session_context)`，返回 `questions`（0–2）、`brief_preview`、`auto_start`、`blocking_reason`。
2. 选题规则严格按需求 R6/9.3；删除 v2 路径对 `_needs_object_intake` 的依赖。
3. `create_intake` 根据 `interaction_version` 分流：
   - v1：原逻辑，`stock_clarification_needed=_needs_object_intake(...)`。
   - v2：先 domain guard（如已有入口），再解析/推断/选题；不调用网络/模型。
4. v2 自动开始：草稿直接 `confirmed` + `auto_confirmed=true`；仍返回 `intake_id`/`revision`，由 `/chat/stream` 接受。
   - 注意 `create_intake_draft()` 当前固定写 `pending`；可在同一事务创建后确认，或给存储方法增加受控 `status` 参数，二选一，不能先返回 pending 再让前端补一次 PATCH。
   - 注意 `apply_branching()` 当前对空题组会补一道 `_confirmation_question()`；v2 零题草稿必须走独立确认路径，不能复用会补题的旧分支。
   - v2 brief 必须从 `resolution + inference + answers` 构建，不能只靠 `build_analysis_brief(questions, answers)`，否则零题时对象/来源会丢失。
5. v2 有问题：草稿 `pending`，返回 `resolution`/`brief_preview`/`plan`。
6. `update_intake` 支持对象候选答案：校验 `option.identity` 与服务端候选一致；不接受客户端伪造身份。
7. `accept_for_chat` 继续校验 original_message、generation、state version、revision；v2 不改变 exactly-once。
8. 会话详情的 `pending_intake`/`analysis_brief` 保持兼容；v2 追加 `intake_plan` 可选字段。
9. 错误码：v2 非法身份 422 `invalid_intake_answer`；歧义未确认 409 `intake_not_confirmed`；会话冲突沿用 409。
10. 日志：记录 resolution/status/question_count/auto_start/耗时；不记录问题正文/持仓。
11. 更新提示词投影：`describe_analysis_brief()` 与 `answer_editor._brief_text()` 要能渲染 v2 的 `object`（名称/代码/市场）、`sources`、`assumptions`；v1 brief 的输出保持原样，不能因为新增字段改变旧提示词。

**测试**：

- API：v2 创建 `分析 600519` → 0 题、auto_start=true、resolution resolved；`分析 中芯国际` → 1 题、候选 2 个、auto_start=false；`分析 00700`/`aapl`/`0700.HK` 同理。
- API：v2 概念题 → 0 题、auto_start=true、scope=concept。
- API：v2 大盘/持仓/自选 → 0 题、scope=market/portfolio/watchlist。
- API：v2 会话追问 → 继承 active + brief，0 题。
- API：确认候选后 brief.object 是具体代码，不是 `a_share`。
- API：客户端伪造 `identity` 被拒绝。
- 兼容：v1 请求仍返回原 1–5 题；旧草稿可读。
- 幂等：重复 request_id 只创建一次；双击/双标签只接受一次。
- 刷新：pending draft 恢复；confirmed brief 恢复。

**完成条件**：

- 需求 11.1 的 API 层用例全过。
- v1 既有测试不改断言即可通过。
- 自动开始不绕过 `intake_id`/`intake_revision`。
- 无新增环境变量。

---

### T5 · 前端类型、API 与 store（1 人日）

**目标**：让前端能消费 v2 契约，同时保持 v1 回退。

**涉及文件**：

- `apps/dsa-web/src/api/agentIntake.ts`
- `apps/dsa-web/src/api/agent.ts`
- `apps/dsa-web/src/stores/agentChatStore.ts`
- `apps/dsa-web/src/types/systemConfig.ts`（如需 capability 字段）
- `apps/dsa-web/src/api/__tests__/*`

**要做**：

1. 追加 v2 类型：`ObjectIdentity`、`ObjectResolution`、`BriefSource`、`IntakePlan`、`QuestionKind`、`QuestionOption.identity`。
2. `AnalysisBrief` 追加可选字段；所有旧字段保留。
3. `createIntake` 发送 `interaction_version=2`（由常量控制，便于回滚到 1）。
4. store 保存 `intakeResolution`、`intakePlan`、`autoStart`；`startStream` 支持自动开始。
5. `auto_start=true` 时：
   - 不再等待用户点确认；
   - 直接调用 `startStream`，携带 `intake_id`/`intake_revision`；
   - 记录 `briefSnapshot` 用于简报条；
   - 若创建/流式失败，回退到 pending 卡片/直接分析，不丢问题。
6. 旧服务端无 v2 字段时，按 v1 处理，不崩；前端以响应是否含 `resolution`/`plan` 判断 v2 是否可用，不强制新增 capability 字段。
7. 刷新恢复：pending 草稿 → 卡片；confirmed brief → 简报条；auto_start 进行中 → 运行态。
8. 停止/切换会话/新建会话时清理 v2 瞬态状态，避免串会话。

**测试**：

- API 类型/响应解析（含缺失字段）。
- store：auto_start 路径只发一次 stream；失败回退；双标签/重复点击幂等。
- store：v1 响应仍走旧路径。
- store：切会话/停止不被迟到响应复活（复用现有测试模式）。

**完成条件**：

- `npm run lint`、`npm test`、`npm run build` 通过。
- 不改现有 v1 组件契约。
- 类型无 `any` 滥用；新字段全部可选。

---

### T6 · 简报卡与自动开始 UI（2 人日）

**目标**：用一屏简报替代逐题向导；自动开始后显示简报条。

**涉及文件**：

- `apps/dsa-web/src/components/chat/ChatIntakeCard.tsx`（重构或新增 `ChatIntakeBriefCard.tsx`）
- `apps/dsa-web/src/components/chat/AnalysisBriefSummary.tsx`（复用/调整）
- 新增 `apps/dsa-web/src/components/chat/ChatBriefStrip.tsx`
- `apps/dsa-web/src/pages/ChatPage.tsx`
- `apps/dsa-web/src/research.css` 或对应 chat 样式
- `apps/dsa-web/src/i18n/uiText.ts`
- 测试：`ChatIntakeCard.test.tsx`、`ChatPage.test.tsx`、新增 `ChatBriefStrip.test.tsx`

**要做**：

1. 设计一屏卡片：对象行 + 5 个背景行 + 来源标签 + 主/次按钮。
2. 对象行：resolved 显示名称/代码/市场；ambiguous 显示候选（复用 T7 组件）；missing 显示搜索。
3. 背景行：值或“未指定”；来源标签；点“改”打开轻量选择器（popover/底部 sheet），不跳页。
4. 主按钮“开始分析”；次按钮“直接分析”；取消/关闭。
5. 自动开始后收起为 `ChatBriefStrip`：一行摘要 + “调整”；点击展开卡片。
6. 运行中修改：文案说明“本轮已开始，调整将用于下一轮”；若尚未 accepted，取消并按新 brief 重发。
7. 没有问题时完全不渲染卡片；直接开始。
8. 键盘：Tab 顺序、Enter/Space 选择、Esc 关闭；`aria-live` 播报解析/更新。
9. 移动端底部 sheet，避免软键盘遮挡；320/390 px 无溢出。
10. 删除 v2 路径的进度条/上一题/下一题；v1 组件可保留或按版本分支。

**测试**：

- `分析 600519` 渲染对象/未指定项/开始按钮；不出现“哪个市场”。
- `分析 中芯国际` 渲染两个候选 + 推荐理由；不点选不开始。
- 点“改”只展开当前维度，不跳页。
- 自动开始后渲染简报条；调整打开卡片。
- 未知项不显示“空仓/均衡/短线”。
- 键盘/ARIA 断言；中英文文案。
- 移动端样式快照或 class 断言。

**完成条件**：

- 桌面/手机、深浅主题截图附 PR。
- 无固定问题列表残留（v2 路径）。
- `npm run lint`/`build` 通过。

---

### T7 · 候选/搜索与澄清卡（1.5 人日）

**目标**：所有对象歧义都用具体候选/搜索解决，不用市场枚举；取消危险倒计时。

**涉及文件**：

- 新增 `apps/dsa-web/src/components/chat/StockCandidatePicker.tsx`
- `apps/dsa-web/src/components/chat/StockClarificationCard.tsx`
- `apps/dsa-web/src/pages/ChatPage.tsx`
- `apps/dsa-web/src/stores/agentChatStore.ts`（clarification 类型）
- `apps/dsa-web/src/i18n/uiText.ts`
- 测试：`StockClarificationCard.test.tsx`、新增 `StockCandidatePicker.test.tsx`

**要做**：

1. 候选卡：名称、代码、市场标签、资产类型、推荐 badge、推荐理由。
2. 搜索：输入代码/名称，前端过滤本地 `useStockIndex`（或调用后端搜索），展示最多 5 个；选择后写回 `Answer.option_ids`/identity。
3. 澄清卡接入候选组件；“保持当前”保留。
4. 移除 `ambiguous_name`/`multiple_objects` 的 10 秒倒计时；仅服务端 `auto_confirm_eligible=true` 且 `action=keep` 时倒计时。
5. 历史消息不重启倒计时、不自动发送（保持现有）。
6. 解析过程动画保留但缩短；不阻塞用户点击。
7. “输入股票代码”改为“搜索其他代码或名称”；空结果有明确文案。
8. 错误/超时回退：保留当前对象/原问题，允许重试。

**测试**：

- 跨市场候选渲染；推荐理由可见；点选后回调具体 identity。
- ambiguous 不出现倒计时；`auto_confirm_eligible=false` 不倒计时。
- keep 场景倒计时仍工作（如保留）。
- 搜索无结果/多结果/选择。
- 键盘/ARIA；中英文。

**完成条件**：

- 需求 11.1 第 9 例（中芯国际）与第 10b 例（比亚迪股份）在前端可完整走通。
- 不再出现“你关注哪个市场”的 v2 UI。
- 现有澄清测试更新后通过。

---

### T8 · 首屏动态建议（1 人日）

**目标**：空会话建议基于上下文，不再固定六条。

**涉及文件**：

- 新增 `apps/dsa-web/src/hooks/useChatSuggestions.ts`
- `apps/dsa-web/src/pages/ChatPage.tsx`
- `apps/dsa-web/src/api/history.ts`（如需最近分析）
- `apps/dsa-web/src/hooks/useWatchlist.ts`
- `apps/dsa-web/src/i18n/uiText.ts`
- 测试：新增 `useChatSuggestions.test.tsx`、`ChatPage.test.tsx`

**要做**：

1. 聚合：当前会话 active stock、最近会话、自选、最近分析、可用 skill、市场时段。
2. 生成 3–4 条，按优先级：继续上次 → 自选 → 市场时段 → 能力发现。
3. 每条含 `reason` 标签；对象建议带 `stockContext`；消息可直接发送。
4. 无数据时回退到轮换模板（按日期/市场时段），不固定。
5. 过滤不可用 skill；不展示无权限数据。
6. 失败静默回退，不阻塞页面。
7. 空状态文案从“试试这样问”改为“基于你的关注”（有上下文时）。

**测试**：

- 有自选/历史时生成对应建议；点击发送携带正确 stockContext/skill。
- 无数据时返回 3 条轮换建议；同一天稳定，不同天变化（mock 日期）。
- 不可用 skill 被过滤。
- API 失败回退。
- 中英文。

**完成条件**：

- 首屏不再出现固定六条（v2 路径）。
- 建议点击后不触发额外引导问题（对象已带）。
- `npm test`/`lint`/`build` 通过。

---

### T9 · 文案、样式与可访问性（1 人日）

**目标**：让 v2 在中英文、深浅主题、移动端和键盘下都可用。

**涉及文件**：

- `apps/dsa-web/src/i18n/uiText.ts`
- `apps/dsa-web/src/research.css` / chat 样式
- 组件文件（class/aria）

**要做**：

1. 新增文案 key（中英）：
   - `chat.intakeV2.resolvedTitle` / `objectLabel` / `marketLabel`
   - `chat.intakeV2.source.explicit` / `session` / `inferred` / `default` / `unknown`
   - `chat.intakeV2.adjust` / `start` / `direct` / `cancel`
   - `chat.intakeV2.candidateTitle` / `recommended` / `reason.*`
   - `chat.intakeV2.searchPlaceholder` / `noResult` / `searchOther`
   - `chat.intakeV2.notFoundTitle` / `unsupportedTitle` / `retry` / `tryAnyway`
   - `chat.intakeV2.autoStarted` / `adjustNextTurn`
   - `chat.suggestions.*`
2. 统一来源标签样式：explicit 中性、session 蓝色、inferred 警示/虚线、unknown 灰。
3. 不改变现有主题 token；复用 0–4px 圆角、1px 分隔线。
4. 移动端 sheet、触控区 ≥44/48px、无横向滚动。
5. 键盘/屏幕阅读器：radiogroup、aria-live、focus 管理、Esc。
6. 减少动画模式：关闭解析动画/倒计时动画。
7. 中英文文案不直译，保持产品语气一致。

**测试**：

- 中英文 key 完整性测试（如现有 i18n 测试）。
- 组件 aria 断言。
- 320/390/768/1440 px 手工/截图。
- 深浅主题截图。

**完成条件**：

- 无硬编码中文（v2 组件）。
- 中英文切换无缺失 key。
- PR 附桌面/手机、深浅主题截图。

---

### T10 · 测试与回归（2 人日）

**目标**：证明 v2 不破坏 v1，且关键场景全过。

**后端**：

- `pytest -m "not network"` 全量。
- 新增：
  - `tests/test_chat_intake_object_resolver.py`（≥30 例）
  - `tests/test_chat_intake_intent_resolver.py`（≥25 例）
  - `tests/test_agent_chat_intake_v2_api.py`（≥15 例）
- 更新：
  - `tests/test_agent_chat_intake.py`（v1 + v2 契约）
  - `tests/test_agent_chat_intake_service.py`（v1 不变；v2 新用例）
  - `tests/test_agent_chat_api.py`（interaction_version 分流）
- 回归重点：
  - v1 请求返回 1–5 题，行为不变。
  - v2 自动开始仍 exactly-once。
  - 会话冲突/过期 revision/伪造 identity 被拒。
  - 概念/报告/持仓/大盘/自选不被误判缺对象。
  - 跨市场候选不静默选中。
  - 解析失败不 500。
  - v2 brief 的对象/市场/来源/assumptions 能进入 `describe_analysis_brief()` 和 answer editor 的 `_brief_text()`；v1 输出不变。

**前端**：

- `npm test` 相关：
  - `ChatIntakeCard.test.tsx`
  - `ChatPage.test.tsx`
  - `StockClarificationCard.test.tsx`
  - `StockCandidatePicker.test.tsx`
  - `ChatBriefStrip.test.tsx`
  - `useChatSuggestions.test.tsx`
  - store/API 测试
- `npm run lint`
- `npm run build`

**手工**：

- 需求 11.1 的全部解析用例逐条走真实股票索引。
- 跨市场候选（中芯国际）点选；比亚迪/比亚迪股份按精确名直达。
- 自动开始 → 停止 → 调整 → 下一轮。
- 刷新/切会话/双标签。
- 320/390/768/1440、深浅、中英、键盘。
- 旧客户端模拟（interaction_version=1）。

**完成条件**：

- 所有阻断测试通过。
- 新增测试覆盖需求 11.1 的全部用例。
- 记录未覆盖项和原因。

---

### T11 · 文档、CHANGELOG 与发布验收（1 人日）

**目标**：让用户和维护者知道 v2 怎么用、怎么回滚。

**涉及文件**：

- `docs/full-guide.md`（问股首次引导章节）
- `docs/LLM_CONFIG_GUIDE.md`（如涉及模型/上下文，不涉及则不改）
- `docs/CHANGELOG.md`（Unreleased 扁平条目）
- 本文档 + 需求文档（状态更新）
- PR 描述/截图

**要做**：

1. 更新 `full-guide` 的“问股首次引导”章节：v2 行为、自动开始、候选、来源、回滚。
2. CHANGELOG 增加一条 `[改进]`/`[新功能]`，描述 v2；文档条目单独一条。
3. 更新需求文档状态为“已实施/部分实施”，列出偏差。
4. PR 描述按 AGENTS 交付结构：改了什么、为什么、验证、未验证、风险、回滚。
5. 附截图：简报卡、候选卡、自动开始简报条、首屏建议、移动端/深浅主题。
6. 不把临时截图合入仓库。

**完成条件**：

- 文档命令/文件名/配置与代码一致。
- 中英文文档同步评估（至少中文完整；英文如需同步，说明原因）。
- CHANGELOG 格式符合扁平条目规范。

---

## 4. 工作量汇总

| 任务 | 人日 | 可并行 |
| --- | --- | --- |
| T0 决策 | 0.5 | 否 |
| T1 契约 | 1 | 否 |
| T2 解析 | 2.5 | 否 |
| T3 推断/继承 | 1.5 | T2 后 |
| T4 选题/API | 1.5 | T3 后 |
| T5 前端数据层 | 1 | T2 后（按契约） |
| T6 简报/自动开始 | 2 | T5 后 |
| T7 候选/澄清 | 1.5 | T5 后 |
| T8 首屏建议 | 1 | T5 后 |
| T9 文案/样式/a11y | 1 | T6/T7 后 |
| T10 测试/回归 | 2 | 全程 |
| T11 文档/发布 | 1 | 最后 |
| **合计** | **16.5 人日** | 单人约 3.5 周；双人并行约 2 周 |

估算不含真实模型评测、线上灰度观察和产品反复。若 P1 LLM 意图抽取另加 2–3 人日。

---

## 5. 测试矩阵

### 5.1 解析矩阵（后端单测必须覆盖）

| 类别 | 输入 | 期望 |
| --- | --- | --- |
| A 股裸码 | 600519 / 300750 / 920493 | resolved CN，交易所正确 |
| A 股前后缀 | 600519.SH / SH600519 / 920493.BJ | resolved CN |
| 港股 | 00700 / 0700.HK / HK00700 / hk700 | resolved HK00700 |
| 美股 | AAPL / aapl / BRK.B / SHOP | resolved US；SHOP 不被拆前缀 |
| 日韩台 | 7203.T / 005930.KS / 2330.TW | resolved 对应市场；无数据源时 unsupported |
| 名称唯一 | 贵州茅台 / 腾讯 / 宁德时代 | resolved |
| 名称多候选/缺失 | 中芯国际 / 比亚迪 / 比亚迪股份 / 苹果 | 中芯国际 ambiguous；比亚迪 CN；比亚迪股份 HK；苹果 missing |
| 指数 | sh000001 / 000300.SH / SPX | index |
| 市场 | 大盘 / A股 / 港股 / 美股 | market scope |
| 组合 | 我的持仓 / 自选 | portfolio/watchlist |
| 概念 | 什么是市盈率 | concept |
| 会话继承 | 继续看看 / 它怎么样 | active stock |
| 缺失 | 分析一下 / 999999 | missing |
| 否定/假设 | 不要分析 600519 / 如果跌破就卖 | 不误判对象/持仓 |
| 歧义边界 | 000001 vs 00001；00700 vs 700 | 正确市场 |

### 5.2 选题矩阵（API/服务测试）

| 场景 | 期望问题数 | auto_start |
| --- | --- | --- |
| 600519 普通分析 | 0 | true |
| 600519 信息完整 | 0 | true |
| 中芯国际 | 1（候选） | false |
| 分析一下 | 1（搜索） | false |
| 比较 600519 + AAPL | 0–1 | true/false |
| 大盘 | 0 | true |
| 持仓/自选 | 0 | true |
| 报告追问 | 0 | true |
| 概念 | 0 | true |
| 不支持市场 | 1（信息卡） | false |
| 背景冲突 | 1（澄清） | false |
| 非投资问题 | 0（domain guard 提醒） | false |

### 5.3 前端交互矩阵

| 场景 | 断言 |
| --- | --- |
| 600519 | 无“哪个市场”；简报；自动开始 |
| 中芯国际 | 两候选；推荐理由；不点不开始 |
| 00700/0700.HK/aapl | 不出现市场问题 |
| 会话追问 | 沿用背景；不重复三问 |
| 调整背景 | 轻量编辑；不跳页 |
| 自动开始 | 简报条；流式正常；不重复请求 |
| 刷新 | 恢复 pending/confirmed；不重复研究 |
| 双标签/双击 | 只接受一次 |
| 概念/大盘/持仓 | 不出现无关题 |
| 首屏 | 至少一条上下文建议；可一键发送 |
| 移动/主题/i18n/a11y | 无溢出；键盘可用；文案完整 |

---

## 6. 完成定义（DoD）

一个任务只有同时满足以下条件才算完成：

1. 代码实现与需求 R 编号对应，没有“顺手加”的未说明行为。
2. 新增/修改的 Python 文件 `py_compile` 通过；相关 pytest 通过。
3. Web 改动 `npm run lint`、`npm run build`、相关 `npm test` 通过。
4. 新增测试覆盖正常、边界、错误、兼容四类；不只测 happy path。
5. v1 行为有回归测试；没有靠改旧断言掩盖破坏。
6. 文档/CHANGELOG 同步；新增文案中英完整。
7. PR 描述包含改了什么、为什么、验证、未验证、风险、回滚。
8. 涉及 UI 的 PR 附桌面/手机、深浅主题截图；临时截图不入库。
9. 没有新增环境变量、硬编码股票名单、网络依赖到解析路径。
10. 失败路径有明确用户文案和重试/回退，不白屏、不静默猜测。

---

## 7. 风险与回滚

| 风险 | 触发 | 回滚/缓解 |
| --- | --- | --- |
| v2 解析错误导致错标的 | 名称碰撞/索引过期 | 候选卡 + 不自动开始；回滚到 v1（`interaction_version=1`） |
| v2 自动开始引起投诉 | 用户想先确认 | 简报条可调整；服务端可把 `auto_start` 关闭；回滚 v1 |
| v1 被破坏 | 共用 schema/题库 | v1 分支冻结；PR-2/PR-3 必须跑 v1 回归；必要时 revert |
| 前端 UI 回归 | 组件重构 | 保留 v1 组件分支；按版本渲染；PR-4/5 可独立 revert |
| 性能下降 | 索引/解析重复构造 | 缓存 registry/lookup；P95 预算；超时回退搜索 |
| 文案缺失 | 中英文不同步 | i18n key 测试；PR-5 检查 |
| 数据库兼容 | 若新增 `context_json` | P0 不加列；如加，先迁移/备份/兼容旧行 |

回滚顺序：

1. Web 先回 `interaction_version=1`（无需后端发布）。
2. 后端保留 v1 分支；必要时 revert PR-3/PR-4，不动数据库。
3. 旧草稿继续可读；v2 草稿按未知字段忽略处理。
4. 不删除会话、不清空历史、不做破坏性降级。

---

## 8. 交付检查清单

- [ ] D1–D6 已确认并回写需求文档
- [ ] v2 schema/类型/兼容层完成
- [ ] 解析服务 + ≥30 例测试
- [ ] 推断/继承 + ≥25 例测试
- [ ] 选题/API + ≥15 例测试
- [ ] 前端类型/API/store
- [ ] 简报卡 + 自动开始 + 简报条
- [ ] 候选/搜索 + 澄清卡（无危险倒计时）
- [ ] 首屏动态建议
- [ ] 中英文文案 + 深浅主题 + 移动端 + a11y
- [ ] v1 回归全绿
- [ ] `pytest -m "not network"` / `npm lint` / `npm build` 通过
- [ ] 文档/CHANGELOG/截图/PR 描述
- [ ] 未验证项与风险已说明
