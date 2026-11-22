# 开发任务书（实施版）：分析运行过程可感知 + 问股首问「先判断，再提问」

> 状态：待实施 · 基线：`91f68756` · 类型：`feat`（内含 3 处 `fix`：H3 / C2 / I6）
> 建议拆 3 个 PR 依次合入：**PR-A 首页运行反馈 → PR-B 问股运行反馈 → PR-C 首问理解卡**。PR 之间无代码依赖，可独立回滚。

---

## 0. 写给接手工程师

### 0.1 一句话目标

1. 首页点「分析」后，主阅读区立刻出现一张运行卡（阶段 / 当前步骤 / 已用时 / 取消），手机同样可见，失败可重试。
2. 问股发送后消息立即上屏，进行中面板有阶段、步骤时间线、已用时、停止，连接假死能自愈。
3. 首问改成「先判断、再提问」：服务端在创建草稿的同一请求里完成标的解析和背景推断；标的明确就直接开始，只有标的不明确才拦截，且只问一道带候选和推荐的问题。

### 0.2 关键事实（已核实，改代码前先认领）

- 首页分析：`stockPoolStore.submitAnalysis` → `analysisApi.analyzeAsync`（返回 `TaskAccepted{taskId}`），真正进度靠 `/api/v1/analysis/tasks/stream`（`useTaskStream`）和 30 秒轮询 `refreshActiveTasks`。
- 首页的 Agent 分析路径：`pipeline._analyze_with_agent` 里 `executor = build_agent_executor(...)` 返回 `AgentExecutor` 或 `AgentOrchestrator`，随后 `executor.run(message, context=initial_context)` **没有进度回调**，导致 58% 后长时间无事件（这是 H3 的根因）。
- `AgentExecutor.run(task, context)` / `AgentOrchestrator.run(task, context)` 都不接受 `progress_callback`；但 `AgentExecutor._run_loop(..., progress_callback=...)` 和 `AgentOrchestrator._execute_pipeline(..., progress_callback=...)` **已经接受**。所以只需要给两个 `run` 加透传参数。
- 问股流：`agent_chat_stream` 每 15 秒 `yield ": heartbeat\n\n"`（`HEARTBEAT_INTERVAL_SECONDS = 15.0`），前端 `agentChatStore.startStream` 的 `processLine` 只处理 `data: ` 行，心跳被丢弃；`STREAM_IDLE_TIMEOUT_SECONDS = 300.0`。
- 首问草稿：`storage.create_intake_draft` 写死 `answers_json="[]"`、`status="pending"`。表 `intake_drafts` 已有 `answers_json` 和 `status` 两列，**无需迁移**，只是多写初值。
- 标的解析现成：`src/agent/chat_object_decision.decide_chat_object(message, current, registry, lookup) -> ObjectDecision(kind, target, participants, changes_target, candidates)`；候选推荐在 `agent_chat_session_service._pick_recommended(candidates, current)` 和 `_clarification_payload(...)`。`registry, lookup = _stock_lookup()` 在 `agent_chat_session_service` 模块内。
- 意图推断现成但有误判：`intake_questions.classify_question_intent(message)` 返回 `{question_type, explicit_dims}`，**丢掉了命中的具体值**。前端还有一份平行规则 `utils/chatIntentHints.ts`。
- 交互版本：`POST /chat/intake` 目前 `interaction_version` 固定 `ge=0, le=1`，前端 `agentIntake.create` 没发这个字段；`/agent/status` 返回 `guided_intake: true`（`api/v1/schemas/system_config.py::AgentBackendStatusResponse` + `agent.py::_agent_status_response`）。
- 前端类型/结构：`types/analysis.ts` 的 `TaskInfo` 没有 `stage`/`progressUpdatedAt`；`agentChatStore` 的 `Message`、`ProgressStep`、`AgentChatState`、`AgentChatActions` 见 `stores/agentChatStore.ts` 顶部。
- 阈值常量：`useTaskStream` 无心跳间隔常量（后端 30 秒写死在 `analysis.py::task_stream` 的 `timeout=30`）；`HEARTBEAT_INTERVAL_SECONDS` 写死在 `agent.py`。

### 0.3 代码风格约束（务必遵守）

- 后端新增模块放 `src/services/`（分析进度）或 `src/agent/`（首问），不写进 `src/config.py` / `src/storage.py` 之外的既有大文件，除非是必要的小改。
- 所有阈值/文案/枚举只有一个来源；前端不要复制后端已有的表（工具显示名、阶段表、候选推荐）。
- 前端新增组件目录：`apps/dsa-web/src/components/run-status/`（共享）、`apps/dsa-web/src/components/tasks/`（首页卡）、`apps/dsa-web/src/components/chat/`（理解卡）。文案进 `i18n/uiText.ts`（zh + en）。
- 不要破坏 `ChatPage` / `HomePage` 已有的记忆化（`ChatThinkingBlock`、`ChatMessageItem`、`sidebarContent` 都是 `memo`）；每秒计时必须限定在叶子组件内。
- 每处改动同步跑 `./scripts/ci_gate.sh` 或 `cd apps/dsa-web && npm run lint && npm test && npm run build`；改 AI 协作资产时跑 `python scripts/check_ai_assets.py`。

---

## 1. 现状与问题定位（证据索引）

证据编号 H/C/I 用于下文「每个任务的验证」里指代。

### 1.1 首页分析（H）

| # | 现象 | 精确位置 |
| --- | --- | --- |
| H1 | 提交后主区域无变化 | `HomePage.tsx` `research-reading` 只渲染旧报告或空态；`submitAnalysis` 的 `isAnalyzing` 只覆盖提交往返 |
| H2 | 窄屏看不到进度 | `research.css` `.research-records { display:none }`，`min-width:48rem` 才显示；`TaskPanel` 在 `sidebarContent` 里 |
| H3 | 选策略/Agent 模式卡 58% | `pipeline.py` `use_agent` 分支 `_emit_progress(58, ...)` → `_analyze_with_agent` → `executor.run(...)` 无回调，下一条是 94 |
| H4 | 等模型首包无事件 | `analyzer.py` `_emit_progress(68, ...)` 后到 `stream_progress_callback` 首次回调前无事件 |
| H5 | 无时间维度 | `TaskPanel.tsx` 只有百分比和一行截断 `message`；`startedAt` 已下发未用 |
| H6 | 排队不可见 | pending 任务恒 `0%` `「任务已加入队列」`，无队列位置 |
| H7 | 断线无提示 | `useTaskStream.ts` `heartbeat` 监听为空函数；`onError` 只 `console.warn` |
| H8 | 结果一闪而过 | `useDashboardLifecycle.ts` `scheduleTaskRemoval` 完成 2s / 失败 5s 移除任务项 |
| H9 | 大盘复盘无进度 | `analysis.py` `run_market_review` 无进度回调；`HomePage.pollMarketReviewStatus` 每 2s 显示 `任务状态：processing（N%）`，`run_market_review` 由 `submit_background_task` 提交（`_execute_task` 会把它置成 10% 后不再动） |
| H10 | 开发者文案 | `analyzer.py`「LLM 已接收请求，等待响应」「LLM 返回完成，正在解析 JSON」「已接收 N 字符」；`pipeline.py`「正在切换 Agent 分析链路」 |

### 1.2 问股进行中（C）

| # | 现象 | 精确位置 |
| --- | --- | --- |
| C1 | 消息不上屏 | `agentChatStore.startStream` 收到 `accepted` 才 `messages.push(userMessage)`；首问 `createIntake` 期间线程只有欢迎卡 + 输入框小字 |
| C2 | 步骤无样式 | `ChatLiveProgress.tsx` 用了 `.chat-live-details/.chat-live-step/.chat-live-step-dot/.chat-streaming-preview`，全无 CSS 定义 |
| C3 | 无时间无存活信号 | 后端 15s 心跳是 `: heartbeat` 注释行，`processLine` 只处理 `data: ` 丢弃之；`STREAM_IDLE_TIMEOUT_SECONDS=300` |
| C4 | 假死卡住 | `agentApi.chatStream` 与 `reader.read()` 无读超时 |
| C5 | 生成阶段最安静 | `AGENT_CHAT_STREAM_CONTENT=false` 默认，生成回答只有一句「正在生成回答...」 |
| C6 | 文案重复/中英混排 | `runner.py` 每轮「「X」已完成，继续深入分析...」；`chatProgress.describeProgressStage` 硬编码「正在连接...」；`stage_start.message` 是英文；`ChatThinkingBlock` 硬编码「分析过程/超时/条记录」 |
| C7 | 刷新只有一句话 | `chatProgress.describeProgressStage` 的 `recovering` 分支只显示「后台任务仍在运行...」 |

### 1.3 问股首问（I）

| # | 现象 | 精确位置 |
| --- | --- | --- |
| I1 | 「已理解」卡空 | `storage.create_intake_draft` 写死 `answers_json="[]"`；`ChatSmartSummaryCard` 已解析标签区首屏恒空 |
| I2 | 判断被丢 | `intake_questions.classify_question_intent` 只记录维度不保留值；`build_analysis_brief` 只读 `answers`，推断值不进 brief |
| I3 | 能查到的还在问 | `QUESTION_OBJECT` 问市场；`decide_chat_object` 用本地索引即可给标的，但只在确认后的 `/chat/stream` 里才执行 |
| I4 | 两道闸两套样式 | 首问卡点「开始」后名称歧义再出 `StockClarificationCard`（1.3s 假动画 + 10s 倒计时） |
| I5 | 每次要点一下 | `_confirmation_question()` 信息完整也要确认；`IntakePlan.auto_start` 定义了但没实现 |
| I6 | 交互缺陷 | 每点一个选项就 `PATCH` 一次并整卡置灰；`research_focus` 最多选 2 但 `ChatPage.onUpdateAnswer` 恒写单值；`title/help` 未展示；「开始」和「跳过」区别没说明 |
| I7 | 平行实现 | 选项文案三份（`ChatSmartSummaryCard.LABELS`、`utils/intakeLabels.ts` 无生产引用、后端 `QuestionOption.label`）；意图规则两份（`chatIntentHints.ts` 与 `intake_questions.py`）；`uiText.ts` 20 个 `chat.intake.*` 只用了 5 个；`index.css` `.chat-intake-*` 多数无引用；`OBJECT_EXPLICIT_PATTERNS` 硬编码代码 |
| I8 | 契约半成品 | `schemas/agent_intake.py` 已定义 `ObjectResolution/IntakePlan.auto_start/IntakeCreateResponse.resolution/brief_preview/plan/AnalysisBrief.object/sources/evidence`，无生产代码填充 |
| I9 | 视觉不统一 | 首问卡 `border-radius:2px`（`index.css`）、澄清卡 `16px`（`research.css`）、用户气泡 `14px` |

---

## 2. 设计规则（全 PR 共用）

1. **即时回应**：点击后不等任何网络响应就要有可见变化；超过 10 秒的等待必须有「当前步骤 + 已用时 + 取消」。
2. **反馈放在视线所在处**：主阅读区/消息线程，不依赖侧栏抽屉。
3. **只展示真实状态**：不做假动画，不编造 ETA；无进度数据用不定进度条 + 已用时。
4. **静止 ≠ 死掉**：存活信号可见；确实偏久时如实说明并给「继续等 / 取消」。
5. **先做功课再开口**：能查到的不问、读到的要展示、只为会导致分析对象错误的事拦截。
6. **一套模型两处复用**：首页与问股共用阶段条、计时、状态配色和文案规则。
7. **面向用户的语言**：主文案不出现 LLM / JSON / Agent 链路等实现词。

### 2.1 统一运行状态模型（前端共享，`utils/runStatus.ts`）

| 状态 | 触发 | 图标 | 主文案 | 操作 |
| --- | --- | --- | --- | --- |
| `queued` | 任务 `pending` | 空心圆 | 排队中 · 前面还有 N 个 | 取消 |
| `running` | `processing` / 流式进行 | 半圆转 | 当前阶段 + 当前步骤 | 取消/停止、查看详情 |
| `slow` | 当前步骤静默 > `SLOW_HINT` | 同 | 追加「这一步通常较慢，仍在进行」 | 同 |
| `stalled` | 静默 > `STALL_HINT` | 同，边框 warning | 「等待时间较长。任务仍在运行，可以继续等待或取消」 | 取消升主按钮 |
| `disconnected` | 实时连接断开 > 5s | 断开图标 | 「实时连接已断开，正在重连，进度可能延迟」 | 无新增 |
| `done` | `completed`/`done` | 对勾 | 分析完成 | 查看报告 |
| `failed` | `failed`/`error` | 感叹号 | 用户可读原因 | 重试、查看详情、关闭 |
| `cancelled` | `cancelled`/停止 | 斜杠圆 | 已取消 | 关闭 |

### 2.2 阈值常量（前端一处定义，`utils/runStatus.ts`）

| 常量 | 首页任务 | 问股 |
| --- | --- | --- |
| `SLOW_HINT_MS` | 20_000 | 15_000 |
| `STALL_HINT_MS` | 90_000 | 60_000 |
| `DISCONNECT_HINT_MS` | 5_000 | 5_000 |
| 连接静默判定 | `heartbeatIntervalMs * 2.5` | `heartbeatIntervalMs * 3` |

心跳间隔由服务端在 `connected` / `accepted` 事件下发，前端不写死 15/30。已用时基准：任务用 `started_at`（排队用 `created_at`），问股本轮用 `running_started_at`（刷新后）或本地 `turnStartedAt`（本页发起）。

---

## 3. 共享数据契约（先做，两个 PR 都依赖）

### 3.1 阶段入口表（后端唯一来源）

新文件 `src/services/analysis_progress.py`：

```python
STAGE_MARKET_DATA = 10
STAGE_FUNDAMENTALS = 32
STAGE_NEWS = 46
STAGE_GENERATING = 58
STAGE_FINALIZING = 93

def progress_stage(progress: int) -> str:
    """返回 queued|market_data|fundamentals|news|generating|finalizing"""
```

阶段区间：

| 阶段 key | 入口进度 | zh | en |
| --- | --- | --- | --- |
| `market_data` | 10 | 行情数据 | Market data |
| `fundamentals` | 32 | 基本面与趋势 | Fundamentals & trend |
| `news` | 46 | 新闻与舆情 | News & sentiment |
| `generating` | 58 | 生成报告 | Writing the report |
| `finalizing` | 93 | 校验与保存 | Checking & saving |

### 3.2 `TaskInfo` 载荷追加（`src/services/task_queue.py`）

`TaskInfo` 增加两个字段：`stage: Optional[str] = None`、`progress_updated_at: Optional[datetime] = None`。

`to_dict()` 增加：

```python
payload["stage"] = progress_stage(self.progress)
payload["progress_updated_at"] = (self.progress_updated_at or self.started_at or self.created_at).isoformat()
```

`update_task_progress` 在 `progress` 或 `message` 变化时写 `task.progress_updated_at = datetime.now()`。`_execute_task` 里 `task.progress = 10` 那行同时写 `progress_updated_at`。`market_review` 等 `submit_background_task` 任务不给 `stage`（`to_dict` 里按 `report_type == "market_review"` 跳过 stage）。

`to_dict` 后，SSE 的 `task_progress/task_started/task_completed/task_failed` 自动带上新字段。任务列表 `GET /api/v1/analysis/tasks` 走同一个 `to_dict`，无需额外改。

### 3.3 任务流 `connected` 事件（`api/v1/endpoints/analysis.py`）

`task_stream` 的 `connected` 事件增加 `heartbeat_interval_s`。把 `timeout=30` 抽成模块常量 `TASK_HEARTBEAT_INTERVAL_SECONDS = 30.0`，两个地方共用：

```python
yield _format_sse_event("connected", {
    "message": "Connected to task stream",
    "heartbeat_interval_s": TASK_HEARTBEAT_INTERVAL_SECONDS,
})
```

### 3.4 问股 `accepted` 事件（`api/v1/endpoints/agent.py`）

`_accepted_state_payload(accepted)` 之外，在 `accepted` 事件顶层追加 `heartbeat_interval_s`（取自 `HEARTBEAT_INTERVAL_SECONDS`）。改两处 `accepted_event = {...}`（正常路径和 `domain_guard` 路径）。

---

## 4. PR-A：首页运行反馈

### A1. 阶段入口字面量替换（`src/core/pipeline.py` + `src/analyzer.py`）

把恰好等于阶段入口值的 `_emit_progress` 字面量换成 `analysis_progress` 常量，**只换字面量，不动分支和顺序**：

- `pipeline.py`：`12→?`（12/16 不是阶段入口，保持字面量）、`18`（保持）、`32`→`STAGE_FUNDAMENTALS`、`46`→`STAGE_NEWS`、`58`→`STAGE_GENERATING`、`64/94/97`（保持，阶段内部值）。
- `analyzer.py`：`65/68/93/94/96/97` 全部保持字面量（都在 `generating` 或 `finalizing` 区间内，不引入常量）。

验证：`python -m py_compile` 通过；`pytest tests/test_run_flow.py -m "not network"` 中引用 32/46/58 的断言改为 `analysis_progress.STAGE_*`（见 A7）。

### A2. Agent 分析进度桥接（修 H3）

1. `src/agent/executor.py` `AgentExecutor.run` 签名改为：
   ```python
   def run(self, task: str, context: Optional[Dict[str, Any]] = None,
           progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> AgentResult:
   ```
   末尾 `return self._run_loop(messages, tool_decls, parse_dashboard=True, progress_callback=progress_callback)`。
2. `src/agent/orchestrator.py` `AgentOrchestrator.run` 同样加 `progress_callback` 参数，传给 `self._execute_pipeline(ctx, parse_dashboard=True, progress_callback=progress_callback)`。
3. `src/core/pipeline.py` `_analyze_with_agent` 里 `executor.run(...)` 改为传桥接回调：

   ```python
   def _agent_progress(event: Dict[str, Any]) -> None:
       # 取消检查点与异常吞吐沿用现有约定（TaskCancelledError 必须上抛）
       if event.get("type") == "tool_done":
           label = event.get("display_label") or event.get("tool") or "数据"
           self._emit_progress(STAGE_GENERATING, f"{stock_name}：正在{label}")
       elif event.get("type") == "generating":
           self._emit_progress(STAGE_GENERATING, f"{stock_name}：正在生成报告")

   agent_result = executor.run(message, context=initial_context,
                               progress_callback=_agent_progress)
   ```
   注意：`STAGE_GENERATING`（58）会被 `update_task_progress` 的只增不减钳制，Agent 内部会发多个 `tool_done`，但阶段不会回退。若想更细粒度，用 `min(STAGE_FINALIZING - 1, 58 + idx)` 每完成一个工具推进 1%，并把 `idx` 从事件序列取。首版建议用固定 58 + 当前工具名，避免复杂映射。

验证（覆盖 H3）：`tests/test_agent_chat_executor.py` 增加「`AgentExecutor.run(progress_callback=...)` 收到 tool_done/generating」用例；`tests/test_run_flow.py` 或新增 pipeline 测试「Agent 模式下 58→94 之间有进度事件」。

### A3. 运行反馈基础件（`components/run-status/`）

新增目录 `apps/dsa-web/src/components/run-status/`：

- `StageProgress.tsx`：`{ stages: {key,zh,en}[]; activeIndex: number; indeterminate?: boolean; compact?: boolean }`，渲染 5 段横条（或不定进度条），不依赖百分比推算。
- `RunStatusIcon.tsx`：`{ state }` 按 2.1 状态表返回图标（复用 `lucide-react`）。
- `ElapsedTime.tsx`：`{ startedAt: string }`，内部每秒 `setInterval` 更新 `mm:ss`，`aria-hidden` 计时文本 + 每 30s 更新一次的读屏隐藏文本。
- `useElapsedSeconds.ts`：`(startedAt: string | undefined) => number`，`Date.now()` 每秒刷新，只在叶子组件用。

新 `utils/runStatus.ts`：`deriveRunState(task | steps, lastActivityAt, heartbeatIntervalMs)` 纯函数 + 2.2 阈值常量 + 阶段表（中文文案从 `i18n/uiText.ts` 的 `runStatus.stage.*` 读，`en` 同步）。

验证：`utils/__tests__/runStatus.test.ts`（状态推导纯函数）、`components/run-status/__tests__/ElapsedTime.test.tsx`（假定时器，父组件不随秒重渲染用 `React.Profiler` 或渲染计数断言）。

### A4. 首页运行卡 `AnalysisRunCard`

新文件 `apps/dsa-web/src/components/tasks/AnalysisRunCard.tsx`。

Props：

```ts
interface AnalysisRunCardProps {
  focusedTask: TaskInfo;            // 本页最近一次提交的任务
  activeTasks: TaskInfo[];          // 用于汇总「另有 N 个任务进行中」
  onCancel: (taskId: string) => void;
  onRetry: (task: TaskInfo) => void;
  onOpenRunFlow: (task: TaskInfo) => void;
  onViewReport: (task: TaskInfo) => void;
  onOpenTasksPanel: () => void;     // 宽屏滚动到面板，窄屏打开抽屉
  isConnected: boolean;
  heartbeatIntervalMs: number;
  language: 'zh' | 'en';
}
```

状态来源：`focusedTask.status/progress/message/stage/progressUpdatedAt/startedAt/createdAt` + `isConnected`。完成态 1.5s 后收起（`setTimeout`，组件卸载清理）；失败态停留。渲染结构按设计稿：

```
◐ 正在分析 贵州茅台 600519                      已用时 01:24
●━━●━━●━━◐┄┄○   行情数据 / 基本面与趋势 / 新闻与舆情 / 生成报告 / 校验与保存
正在生成报告 · 已生成约 1,280 字
[取消分析]  查看运行详情          另有 2 个任务进行中 ›
```

在 `HomePage.tsx` 挂载（`research-reading-scroll` 顶部，报告/空态之上）。`submitAnalysis` 返回的 `TaskAccepted.taskId` 写入 `stockPoolStore.focusedTaskId`（新增 state + action `setFocusedTaskId`），SSE `task_created` 兜底；`refreshActiveTasks` 若发现该 id 已结束且当前 `focusedTaskId` 仍指向它，保持到卡片收起。

验证（覆盖 H1/H2/H5/H6/H8）：`components/tasks/__tests__/AnalysisRunCard.test.tsx` 覆盖各状态；`pages/__tests__/HomePage.test.tsx` 增加「提交后出现运行卡」、窄屏渲染、完成不抢占手动选中报告。

### A5. 任务面板对齐 + 失败保留 + 排队位置 + 窄屏角标

- `TaskPanel.tsx` 每个 `TaskItem` 增加：阶段名（`runStatus`）、`ElapsedTime`（`startedAt ?? createdAt`）、排队项显示「前面还有 N 个」（按 `activeTasks` 里 `createdAt` 更早的 pending 数）。
- `stockPoolStore.refreshActiveTasks` 的 `canPruneLocalTasks` 逻辑改为：本地处于 `failed` 且未被关闭（`dismissedTaskIds` 不含）的任务，即使远端快照不含它也不删除。新增 `dismissTask(taskId)` action（写入 `dismissedTaskIds` 并移除）。
- `useDashboardLifecycle.scheduleTaskRemoval`：`failed` 不再定时移除（只在 `onTaskFailed` 里 `syncTaskFailed`），`completed/cancelled` 仍按原定时移除。
- 窄屏角标：`research-records-button` 显示 `activeTasks.length`（`HomePage.tsx`，样式进 `research.css`）。

验证：`components/tasks/__tests__/TaskPanel.test.tsx`、`stores/__tests__/stockPoolStore.test.ts`（`refreshActiveTasks` 不删失败项）。

### A6. 连接状态与降级轮询（覆盖 H7）

`useTaskStream.ts`：

- `heartbeat` 监听改为更新内部 `lastActivityAtRef`（记录 `Date.now()`）。
- 新增返回 `heartbeatIntervalMs`（初始 `30000`，`connected` 事件里读到后更新）和 `lastActivityAt`。
- 静默重连：在 `connectSharedStream` 里起一个 `setInterval`，若 `Date.now() - lastActivityAt > heartbeatIntervalMs * 2.5` 且仍有订阅者，则 `reconnectSharedStream()`；`disconnect` 时清理。
- `useTaskStreamResult` 增加 `heartbeatIntervalMs: number; lastActivityAt: number | null`。

`useDashboardLifecycle.ts`：

- 接收 `isConnected` / `heartbeatIntervalMs`；当 `!isConnected` 且有活跃任务时，把 30 秒轮询临时加密到 3 秒（单独一个 effect 定时器，恢复连接后清除）。

验证：`hooks/__tests__/useTaskStream.test.tsx` 增加静默重连用例（假定时器 + mock EventSource）。

### A7. 大盘复盘接入运行卡（覆盖 H9，P1）

`HomePage.pollMarketReviewStatus`：`pending/processing` 分支不再写 warning 提示条，改为维持一个 `marketReviewTask`（`TaskInfo` 形态的本地对象，`progress` 用 status 的 `progress ?? 0`，无 `stage`），交给 `AnalysisRunCard` 以 `indeterminate` 渲染（标题「正在生成大盘复盘 · 实际市场：{region}」）。`triggerMarketReview` 返回的 `taskId` 在 `pollMarketReviewStatus` 里已有（`getStatus(taskId)`），把它作为 focused task。完成/失败提示条保留现状。

### A8. 任务文案改写（覆盖 H10，P1）

`analyzer.py`：

| 现值 | 新值（zh） | 说明 |
| --- | --- | --- |
| `LLM 已接收请求，等待响应` | `已提交给模型，等待开始生成` | en 界面只显示阶段名，不发这条 |
| `LLM 正在生成分析结果（已接收 {chars} 字符）` | `正在生成报告 · 已生成约 {chars} 字` | |
| `LLM 返回完成，正在解析 JSON` | `模型已返回，正在整理结果` | |
| `LLM 请求前等待 {x} 秒` | `正在排队等待模型（{x} 秒）` | |

`pipeline.py`：`正在切换 Agent 分析链路` → `正在启用深度分析`。

同步更新引用旧文案的断言：`tests/test_run_flow.py`、`apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts`、`apps/dsa-web/src/pages/__tests__/HomePage.test.tsx`（`grep -rn "LLM 已接收请求\|正在解析 JSON\|正在切换 Agent" tests apps/dsa-web/src` 找全）。

---

## 5. PR-B：问股运行反馈

### B1. 乐观上屏（覆盖 C1）

`stores/agentChatStore.ts`：

- `AgentChatState` 增加 `pendingUserMessage: { id: string; content: string } | null`。
- `startStream`：在 `set({ loading: true, ... })` 之前 `set({ pendingUserMessage: { id: `pending-${requestId}`, content: payload.message } })`。`accepted` 分支把 `pendingUserMessage` 置空并照旧追加 `userMessage`。`catch` 分支在 `!streamWasAccepted` 时清空 `pendingUserMessage`（并触发 `meta.onError`，文案按现有走）。
- `stopStream`：清空 `pendingUserMessage`。
- `createIntake`：发起时同样设 `pendingUserMessage`（这样首问点击后立即上屏，覆盖 I5 的空白期）；失败且走 direct 路径时 `startStream` 接管；成功时 `intakeDraft` 出来后 `pendingUserMessage` 清空（草稿卡里已渲染 `intakeDraft.original_message` 气泡）。

`ChatPage.tsx` 线程渲染：在 `messages.map` 之前、`intakeDraft` 块之后渲染 `pendingUserMessage` 的临时气泡（`chat-msg chat-msg-user`，样式加 `is-pending` 降低透明度，`research.css` 补类）。失败恢复：`handleSend` 的 `catch` 不直接写输入框，改为在 `chatError` 区提供「恢复刚才的问题」按钮（复用 `intakeSendPayload.message` 或 store 里的 `pendingUserMessage`）。

验证：`stores/__tests__/agentChatStore.test.ts` 增加「发送即出现 pending，accepted 后转正式，accepted 前失败清除」；`pages/__tests__/ChatPage.test.tsx` 增加「发送瞬间气泡存在、输入框清空」。

### B2. 进行中面板重做 + 样式补齐（覆盖 C2/C5/C6/C7）

`components/chat/ChatLiveProgress.tsx` 重写：

- 只依赖 `steps`（`ProgressStep[]`）+ `streamingContent` + `language` + 新 props `turnStartedAt`、`lastActivityAt`、`heartbeatIntervalMs`、`onStop`。
- 三阶段推导（只前进）：`理解问题`（发送→首个业务事件）→ `收集与分析`（首个 `stage_start/thinking/tool_start`）→ `生成回答`（首个 `generating/content_delta`）；`content_reset` 回到 `收集与分析`。
- 步骤时间线用 `utils/chatProgress.summarizeProgressSteps`（已有起止合并/折叠），删除 `ChatLiveProgress` 里旧的 `settledTools/settledStages` 过滤。进行中工具行显示自己的已等待秒数（用该步 `receivedAt`），完成行显示耗时 + 明细（`detail.record_count/date_range/data_source==='cache'`），失败/超时 danger 色 + 图标。
- 头部：阶段名 + 当前动作（来自 `describeProgressStage`）+ `ElapsedTime(turnStartedAt)` + 停止按钮。
- 存活文案：`deriveRunState` 输出 `slow/stalled/disconnected`，按 2.1 表追加文案。
- `streamingContent` 存在时正文渲染在面板下（`.chat-streaming-preview` 补齐样式 + 输入光标动画）。
- 默认显示最近 6 步，更早折叠为「展开更早的 N 步」。

`utils/chatProgress.ts`：

- `describeProgressStage` 删除硬编码中文，改为从 `i18n/uiText.ts` 传入翻译函数或返回 key；`thinking` 首轮/后续映射到固定文案（见 B4）。
- `ProgressStep` 增加 `receivedAt?: number`（`startStream.processLine` 里写 `Date.now()`）。

`research.css` 补齐：`.chat-live-progress/.chat-live-head/.chat-live-details/.chat-live-step/.chat-live-step-dot(.is-done/.is-error/.is-active)/.chat-live-actions/.chat-streaming-preview/.chat-streaming-caret`，配色用 `hsl(var(--primary))`、`hsl(var(--color-success))`、`hsl(var(--color-danger))`（确认这些 CSS 变量在 `index.css` 里存在，没有则用现有 `--color-success/--color-danger` 或新增）。

验证：`components/chat/__tests__/ChatLiveProgress.test.tsx`（新组件，覆盖三阶段、时间线、慢/久等文案）；`utils/__tests__/chatProgress.test.ts` 更新。

### B3. 存活监视与假死恢复（覆盖 C3/C4）

`api/agent.ts` `chatStream`：保持不变（不做读超时，避免误杀），假死交给看门狗。

`stores/agentChatStore.ts` `startStream` 读循环改造：

- 每个 `reader.read()` 成功读到的块（即使空块/心跳注释行）都更新 `lastActivityAtRef`，并 `set({ lastStreamActivityAt: Date.now() })`（低频，可节流到每秒一次，避免每秒触发 store 更新——用局部变量记录，超过 1s 才 `set`）。
- `accepted` 事件里读取 `heartbeatIntervalMs = event.heartbeat_interval_s * 1000`（默认 15000），存入 state。
- 看门狗：读循环里对 `reader.read()` 用 `Promise.race` + 一个可取消的 `silenceTimeout`（值 = `heartbeatIntervalMs * 3`）。超时触发时：**不调用停止接口**，直接 `ac.abort()`、`set({ loading:false, terminalStatus:'interrupted', progressSteps:[{type:'recovering'}], chatError:null })`，然后走现有 `readDetail` + `startPolling` 恢复。注意 `ownsStream()` / `streamEpoch` 归属校验不能被绕开——超时处理要判断 `ownsStream()` 后再写状态。
- `startPolling` 恢复分支：`detail.running_progress_steps` 有值时直接渲染时间线（B2 已支持 `recovering` 步骤 + 已用时取 `detail.running_started_at`）。

心跳间隔下发（后端，配合本任务）：`api/v1/endpoints/agent.py` 的 `accepted` 事件加 `heartbeat_interval_s`（见 3.4）。

验证：`stores/__tests__/agentChatStore.test.ts` 增加「读循环 3 个心跳周期无字节 → 转入恢复且未调用 stopChat」「恢复后消息正常落库」。手工用例对应第 9 节用例 15。

### B4. 进度文案进 `uiText.ts`（覆盖 C6，P1）

`i18n/uiText.ts` 增加命名空间（zh+en）：`chat.progress.connecting / understanding / planning / analyzingData / generating / recovering / disconnected / slowHint / stallHint`、`runStatus.stage.*`、`chat.live.stop` 等。

`chatProgress.ts` 的 `describeProgressStage` 改为接收翻译函数（或返回 key 由组件 `t()` 翻译），删除硬编码中文；`ChatThinkingBlock.tsx` 的「分析过程/超时/条记录/次工具调用/个分析阶段/项异常」改从 `uiText.ts` 读。

验证：`utils/__tests__/chatProgress.test.ts`、`components/chat/__tests__/ChatMessageItem.test.tsx` 更新；`grep -n "正在连接\|AI 正在思考\|超时\|条记录" apps/dsa-web/src/components/chat apps/dsa-web/src/utils/chatProgress.ts` 无残留（`i18n/uiText.ts` 除外）。

---

## 6. PR-C：首问理解卡

### C1. 服务端：创建即解析（覆盖 I1/I2/I3/I8）

**新增 `plan_intake()`**（放 `src/agent/intake_questions.py` 或同目录新文件 `intake_planning.py`；如引 `decide_chat_object`/`_stock_lookup` 产生循环依赖，则放新文件用延迟 import）：

```python
def plan_intake(message: str, *, registry, lookup,
                report_follow_up: bool, context) -> dict:
    # 返回 {resolution, questions, answers, brief, plan}
```

要点：

1. 解析：`decision = decide_chat_object(message, None, registry, lookup)`，映射到 `ResolutionStatus`：
   - `confirm`（有 target）→ `resolved`，`identity` 取 `decision.target.identity`；
   - `clarify` 且 `decision.candidates` 非空 → `ambiguous`，`candidates` + `recommended_option_id`（复用 `_pick_recommended` 逻辑，抽成共用函数）；
   - `clarify` 且无 candidates → `missing`；
   - `compare`（`participants` 非空）→ `resolved`（`identity=None`，`candidates=participants`，`scope=stock`）；
   - 概念问题（`_CONCEPT_QUESTION_RE` 命中）→ `not_required`；
   - 其他（无对象的新会话）→ `missing`。
2. 推断：`classify_question_intent` 改为返回命中值 + 依据原文（见 C3）。命中值进入 `answers`（`source="explicit_message"`）和 `brief`（`sources`/`evidence`）。
3. 题目 `questions`：
   - 阻塞题：仅当 `ambiguous`/`missing` 时生成一道 `id="object"` 的题，`kind="object_choice"`、`required=True`、`options` 带 `identity`（候选 + `recommended_option_id`）、`allow_free_text=True`、`free_text_kind="stock_search"`；`missing` 时选项为空（前端只显示搜索框）。
   - 偏好题：只为**有推断答案**的维度生成题库原题（`required=False`），承载预填答案；没有推断的维度不发，进 `brief.unknown_fields`。
4. `plan`：`auto_start = resolution.status in ("resolved", "not_required")`（含 compare）；`question_count = len(questions)`；`blocking_reason` 只在非 auto_start 时给 `ambiguous`/`missing`。

**`create_intake_v2`（`src/services/agent_chat_intake_service.py`）**：

```python
def create_intake_v2(self, *, message, session_id, session_generation,
                     request_id, stock_clarification_needed, report_follow_up,
                     intent_hints) -> IntakeDraft:
```

- 复用现有校验（`message` 非空/长度、`session_id`）。
- 调 `plan_intake`，得到 `questions/answers/plan/resolution`。
- `status = "confirmed" if plan["auto_start"] else "pending"`。
- `create_intake_draft(..., answers_json=json.dumps(answers), status=status)`：`create_intake_draft` 增加两个可选参数 `answers_json="[]"`、`status="pending"`（`src/storage.py`，兼容旧调用，不改表结构）。

**`POST /chat/intake`（`api/v1/endpoints/agent.py::create_chat_intake`）**：

- `IntakeCreateRequest.interaction_version` 放宽 `ge=0, le=2`。
- 当 `request.interaction_version >= 2` 时走 `create_intake_v2`，响应补 `resolution/brief_preview/plan`（`IntakeCreateResponse` 的字段已存在）；`schema_version=2`。`brief_preview` 用 `build_analysis_brief(questions, answers)`（已实现）再回填 `object/sources/evidence/auto_confirmed`（见 C2）。
- 否则走旧 `create_intake`，响应与基线逐字段一致。
- `update_chat_intake`：`brief_preview` 现有逻辑已覆盖；`update_intake` 需保留已存 `source`（见 C4）。

**`/agent/status`（`api/v1/endpoints/agent.py::_agent_status_response` + `api/v1/schemas/system_config.py::AgentBackendStatusResponse`）**：增加 `smart_intake: bool = False`，`_agent_status_response` 置 `smart_intake=True`。

验证：`tests/test_agent_chat_intake.py`、`test_agent_chat_intake_api.py`、`test_agent_chat_intake_service.py` 增加 v2 用例；**新增回归：`interaction_version=1` 的响应与基线逐字段一致**（把现有 v1 响应 dump 成 fixture 对比）。

### C2. 推断值进 brief（覆盖 I2）

`intake_questions.build_analysis_brief` 返回的 `AnalysisBrief` 增加 `object`、`sources`、`evidence`、`auto_confirmed` 的填充；`describe_analysis_brief` 对 `source in ("explicit_message","system_inferred")` 的项追加「（根据提问推断，以用户原话为准）」。

### C3. 意图规则收紧 + 保留命中值 + 依据

`classify_question_intent` 返回值改为：

```python
{ "question_type": ..., "inferred": { "goal": ("evaluate_holding", "持有"), "horizon": ("long", "长期"), "position": ("held_control_drawdown", "被套") } }
```

每条规则命中时带上匹配到的原文片段（`re.search(...).group(0)` 或显式关键词）。收紧规则，**修掉已知误判并配正反例**：

| 规则 | 反例（不得命中） |
| --- | --- |
| `我.*有` → evaluate_holding | 「我想看看有没有机会」 |
| `亏损` → held_control_drawdown | 「公司连续亏损还能买吗」 |
| `波动` → tolerant | 「最近波动大怎么办」 |
| `今天|明天|本周` → days | 「今天为什么跌」 |
| `学习` → learn_method | 「机器学习概念股」 |
| `一年` → long | 「一年内新高」 |

原则：只保留带第一人称或持仓语义的高精度规则（如 `被套/浮亏/套牢/持仓/持有/成本.*是/空仓/想入手`），宁可不猜。

前端 `utils/chatIntentHints.ts` 删除 `goal/horizon/position` 规则（只保留 `extracted_stock_code/inferred_market`，或整体删除由后端 `plan_intake` 负责对象识别——见 C5）。

### C4. 来源保留 + 自由选择标的 + 刷新语义

- `agent_chat_intake_service.update_intake`：`sanitized` 后，对 `question_id` 已在 `draft.answers` 且新 `option_ids` 与旧的相同时，保留旧 `source`（不覆盖成 `user_choice`）。
- 自由选择：`object` 题的 `option_ids` 允许是候选之外的 `canonical_id`，服务端用 `validate_chat_identity(token, registry, lookup)` 校验，失败抛 `IntakeServiceError("invalid_intake_answer")`。`QuestionOption.identity` 里带 `canonical_id` 供前端提交。
- 刷新：`find_pending_intake` 已能取回 `confirmed` 但未消费的草稿（`status` 已是 `confirmed`）。前端对这种草稿**不自动开流**，渲染理解条 + 「开始分析」按钮（见 C7）。

### C5. 确认标的贯通首轮（覆盖 I4，避免第二次澄清）

`src/services/agent_chat_session_service.py`：

- `prepare_session_turn(..., *, confirmed_object: Optional[Dict[str, Any]] = None, ...)` 透传给 `_resolve_session_turn`。
- `_resolve_session_turn` 开头：若 `confirmed_object` 非空、`active is None`（会话尚无标的）、且 `decision.kind in ("clarify",)` —— 用 `validate_chat_identity(confirmed_object["canonical_id"], registry, lookup)` 绑定为 `confirm` 路径（`active = _stock_record(...)`，`confirm=True`），不产生 `clarification`。`decision.kind == "confirm"` 时 `confirmed_object` 与决策一致则无影响，不一致以 `confirmed_object` 为准。

`api/v1/endpoints/agent.py::_prepare_chat_request`：

- 在 `intake_service.accept_for_chat(...)` 返回 `brief_payload` 后，若 `brief_payload.get("object")` 存在，则把 `confirmed_object=brief_payload["object"]` 传给 `prepare_session_turn`。
- 只对「本次请求刚接受 intake」生效；`_reuse_session_brief` 分支传 `confirmed_object=None`（否则会把标的钉死，破坏 R9 切换口径）。

验证：**`tests/test_agent_active_stock_integration.py::test_established_object_subject_question_contract` 不改且通过**；新增「intake 确认标的 → 首轮无 clarification」用例。

### C6. 前端：理解卡替换 `ChatSmartSummaryCard`（覆盖 I1/I3/I5/I6/I9）

**API 层 `apps/dsa-web/src/api/agentIntake.ts`**：

- `IntakeCreateRequest` 加 `interaction_version?: number`（发 2）。
- `IntakeCreateResponse` 加 `resolution?: ObjectResolution`、`brief_preview?: AnalysisBrief`、`plan?: IntakePlan`、`schema_version` 已是 number。
- `Question` 加 `kind?/recommended_option_id?/allow_free_text?/free_text_kind?`；`QuestionOption` 加 `identity?`。
- 新增类型 `ObjectResolution`、`IntakePlan`（对齐 `schemas/agent_intake.py` 的字段名，camelCase）。
- `AnalysisBrief` 加 `object?/sources?/evidence?/auto_confirmed?`。

**`api/agent.ts`**：`AgentBackendStatusResponse` 加 `smart_intake`（`types/systemConfig.ts` 同步）。

**`stores/agentChatStore.ts`**：

- `createIntake` 传 `interaction_version: 2`，把 `resolution/brief_preview/plan` 存入 `intakeDraft`（`IntakeDraft` 类型扩展），增加 `understanding`（`brief_preview` 的展示态）state。
- 新增 `startIntakeAnalysis` 现有逻辑复用：`plan.auto_start && intakeDraft.status === 'confirmed'` 时 `createIntake` 成功后直接调 `startIntakeAnalysis()`（不渲染确认卡）；否则渲染理解卡。
- `startIntakeAnalysis` 保持现有实现（`intake_id/intake_revision` 已带上，`interaction_version` 从 `intakeSendPayload` 透传）。

**组件 `components/chat/ChatUnderstandingCard.tsx`（新）**：

- Props：`draft`（含 `resolution/brief_preview/plan`）、`isLoading`、`onConfirm(answers)`、`onCancel`、`onUpdateAnswer(questionId, optionId)`、`onChooseCandidate(identity)`。
- 三种形态（由 `resolution.status` 决定）：
  - `resolved/not_required/compare`（`plan.auto_start`，不拦截）→ 渲染「理解条」（标的 chip + 推断背景 chips + 依据 tooltip），随后直接开流（`ChatPage` 里 `plan.auto_start` 时不挂确认卡，只显示理解条 + `ChatLiveProgress`）。
  - `ambiguous` → 候选单选列表（推荐项默认选中），单选本地 state，点主按钮一次性 `PATCH(confirm)`；`research_focus` 等多选题用多选 chips（修 I6 的单值 bug）；「都不是」→ 内嵌 `StockAutocomplete`。
  - `missing` → 只有搜索框 + 「修改问题」。
- 每点选项不 `PATCH`，只改本地 state（修 I6）；只有确认时请求一次。
- 展示推断 chips 的 `evidence` 依据（tooltip）。
- 删 `ChatSmartSummaryCard.tsx`（连同 `index.css` 里 `.chat-smart-summary-*`）。

**`pages/ChatPage.tsx`**：

- `handleSend` 里 `isFirstQuestion && guidedIntakeSupported` 分支：先判断 `agentStatus?.smartIntake`；不支持则走现有 `createIntake`（v1）路径。
- 渲染 `intakeDraft` 处：`plan.auto_start` 时不渲染确认卡（理解条由 `ChatUnderstandingCard` 的 `resolved` 形态渲染，紧跟 `intakeDraft.original_message` 气泡），否则渲染 `ChatUnderstandingCard`。
- 删除 `onUpdateAnswer` 里的单值逻辑（改多值），确认按钮只在 `plan.auto_start` 时自动触发。

**样式 `research.css`**：新增 `.chat-understanding-card` 共用卡片外壳（16px 圆角，主色描边），候选列表 `radiogroup`，chips，理解条。首问相关样式从 `index.css` 迁入。

验证：`components/chat/__tests__/ChatUnderstandingCard.test.tsx`（三形态 + 候选选择不请求 + 确认一次请求）、`pages/__tests__/ChatPage.test.tsx`（auto_start 直接开流、ambiguous 候选、missing 搜索）、`stores/__tests__/agentChatStore.test.ts`（`createIntake` 带 `interaction_version=2`、auto_start 直接 `startIntakeAnalysis`）。

### C7. 澄清卡并入外壳 + 删假动画（覆盖 I4/I9，P1）

`components/chat/StockClarificationCard.tsx`：

- 删除 `ANALYZE_STEP_MS/ANALYZE_SETTLE_MS`、`phase: 'analyzing'` 四步动画（`steps` 数组、`visibleSteps` effect、`analyzingButton`），挂载即 `phase === 'ready'`，结论直接呈现。
- 外壳类换 `.chat-understanding-card`（与理解卡统一）；`is-resolved` 态保留。
- 保留 10s 自动确认逻辑与按钮。

验证：`components/chat/__tests__/StockClarificationCard.test.tsx` 更新（不再断言「解析中…」动画）。

### C8. 清理遗留（覆盖 I7）

- 选项文案统一：**后端 `QuestionOption.label` 为唯一来源**。前端 `ChatUnderstandingCard` 直接用 `q.options[].label`，不再维护本地 LABELS 映射。
- 删 `utils/intakeLabels.ts`（无生产引用）或若需 `BRIEF_DIMENSIONS` 展示则保留并标注用途；二选一，不留两份。
- `utils/chatIntentHints.ts` 删 `goal/horizon/position` 规则（保留对象提取或整体删除）。
- `i18n/uiText.ts` 删无引用的 `chat.intake.*`（先 `grep` 确认引用）。
- `index.css` 删无引用的 `.chat-intake-*` 和全部 `.chat-smart-summary-*`。
- `intake_questions.py` 的 `OBJECT_EXPLICIT_PATTERNS` 在 v2 路径不再使用（旧 v1 路径如需保留则留，否则删）。

验证：逐项 `grep -rn` 无引用后删除；`npm run lint` 无未使用告警。

### C9. 候选与理解条行情快照（覆盖「先判断再问」体验，P1）

`ChatUnderstandingCard` 挂载后对 `resolution.candidates`（或唯一 `identity`）异步调 `GET /api/v1/stocks/{stock_code}/quote`（`api/stocks.ts` 已有 `getQuote` 或复用 `analysisApi` 的 stock 接口），显示 `current_price/change_percent`。失败或 >3s 不显示，不影响确认。字段来自 `api/v1/schemas/stocks.py::StockQuote`（`current_price/change_percent/stock_name`）。

### C10. 调整背景并重新回答（P2，本期只留接口余地，不实现）

在 `POST /chat/intake` 的「非首轮」限制里预留 `purpose=adjust` 扩展点，本期不做。

---

## 7. 每个任务的验证清单（按证据反查）

| 任务 | 覆盖证据 | 必跑测试 |
| --- | --- | --- |
| A1 | H10 | `pytest tests/test_run_flow.py -m "not network"` |
| A2 | H3 | `pytest tests/test_agent_chat_executor.py -m "not network"` + 新 bridge 用例 |
| A3/A4/A5 | H1/H2/H5/H6/H8 | `npm test -- components/run-status components/tasks stores/__tests__/stockPoolStore pages/__tests__/HomePage` |
| A6 | H7 | `npm test -- hooks/__tests__/useTaskStream` |
| A7 | H9 | `npm test -- pages/__tests__/HomePage` |
| A8 | H10 | 后端 + 前端文案断言更新 |
| B1 | C1 | `npm test -- stores/__tests__/agentChatStore pages/__tests__/ChatPage` |
| B2/B4 | C2/C5/C6/C7 | `npm test -- components/chat/__tests__/ChatLiveProgress utils/__tests__/chatProgress components/chat/__tests__/ChatMessageItem` |
| B3 | C3/C4 | `npm test -- stores/__tests__/agentChatStore` + 手工用例 15 |
| C1/C2/C3/C4 | I1/I2/I3/I8/I6 | `pytest tests/test_agent_chat_intake*.py -m "not network"` + v1 基线 fixture |
| C5 | I4 | `pytest tests/test_agent_active_stock_integration.py tests/test_agent_chat_session_service.py -m "not network"` |
| C6 | I1/I3/I5/I6/I9 | `npm test -- components/chat/__tests__/ChatUnderstandingCard pages/__tests__/ChatPage stores/__tests__/agentChatStore` |
| C7 | I4/I9 | `npm test -- components/chat/__tests__/StockClarificationCard` |
| C8 | I7 | `npm run lint` |
| C9 | — | `npm test -- components/chat/__tests__/ChatUnderstandingCard` |

---

## 8. 验收用例（按 PR 分组）

### PR-A（首页）

1. 提交「600519」：主阅读区同一帧出现运行卡，阶段条随进度前进，已用时每秒变化。
2. 375px 宽度重复 1：不开抽屉即可见运行卡；研究记录按钮有角标。
3. 选任一策略提交：58%→93% 之间进度持续变化，当前步骤显示正在执行的数据获取。
4. 首包延迟 25s：20s 出现「这一步通常较慢」；95s 进入久等态，取消升主按钮。
5. 同时提交 3 只：卡片跟随最后一只，显示「另有 2 个任务进行中」；排队项显示前面还有几个。
6. 失败：卡片与任务项停留、显示原因、可重试；30s 对账后失败项仍在；关闭后消失。
7. 运行中取消：立即「正在取消」，随后已取消并收起。
8. 断后端 10s：出现连接中断提示；恢复后消失且进度追平。
9. 正在看另一份手动打开的报告时任务完成：不跳转，卡片提供「查看报告」。
10. 触发大盘复盘：进行中显示不定进度 + 已用时，不出现英文状态枚举。

### PR-B（问股）

11. 发送问题：气泡立即出现、输入框清空；`accepted` 后转正式消息。
12. `accepted` 前失败：气泡消失、问题回到输入框。
13. 正常一轮：三阶段、时间线、总已用时；进行中工具行有自己秒数；失败工具行 danger 色 + 图标。
14. 业务事件静默 65s 但心跳正常：出现久等文案，任务不被打断。
15. 代理层切断数据但不关连接：3 个心跳周期后面板显示「连接中断，正在恢复进度」，回答完成后出现；期间未调用停止接口。
16. 运行中刷新：恢复态出现，已用时接续。
17. 英文界面走一轮：无中文残留。
18. 系统开减少动态效果：无转动/流动动画，状态可辨认。

### PR-C（首问）

19. 「分析 600519」：无确认卡，直接开始，理解条显示贵州茅台、代码、A股。
20. 「我持有茅台被套了，长期怎么看」：直接开始；理解条显示持仓+周期 chips，悬停显示依据；brief 中这两项非「未提供」。
21. 「什么是市盈率」：直接开始，不出现标的。
22. 「平安怎么样」：候选卡、推荐项已选中；选「平安银行」确认后只发一次 `PATCH`，随后开流，首轮不再出现第二次澄清卡，会话绑定平安银行。
23. 候选卡搜索选候选之外的股票：正常开始并绑定；直接调接口提交索引外代码返回 422。
24. 「换一只帮我看看」：缺少对象形态，只有搜索和修改问题。
25. 从首页报告「追问 AI」：不出现候选卡，标的取自报告。
26. 「我想看看有没有机会」「公司连续亏损还能买吗」「今天为什么跌」：理解条不出现持仓/周期背景。
27. 会话中途「宁德时代怎么样」：澄清卡直接呈现结论，无解析动画，自动确认照常。
28. 首问返回后、开流前刷新：恢复为理解条 + 「开始分析」，不自动开跑。
29. 旧页面（`interaction_version=1`）请求新后端：响应与基线一致。

---

## 9. 兼容性、风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 标的绑定口径被改（C5） | `confirmed_object` 只在首轮、且本次请求刚接受 intake、且会话无当前标的时生效；R9 契约测试不改并通过；历史 brief 不传 `confirmed_object` |
| 推断错误直接进 brief | 规则收紧 + 正反例；理解条展示依据；brief 文本标注「推断，以用户原话为准」；D1 可切保守策略 |
| 流归属复杂（B1/B3） | 乐观气泡不进 `messages`；看门狗只中止本地读取并复用恢复路径；补假死/接收前失败/停止后迟到事件用例 |
| 进度回退 | 依赖 `update_task_progress` 只增不减，桥接与流式进度都走它 |
| 上游合并冲突 | `analyzer.py`/`pipeline.py` 只替换阶段入口字面量和文案，不动结构 |
| 每秒计时拖慢页面 | 计时限定叶子组件；`React.Profiler` 确认父组件不随秒重渲染 |
| 旧文案断言失效 | A8/B4 一并更新，不留双份文案 |

兼容性：`stage/progress_updated_at/heartbeat_interval_s/smart_intake` 均为追加字段；首问 v2 由请求方显式声明，v1 路径保留一个版本。桌面端复用 Web 构建产物，壳层无改动。无数据库迁移。

回滚：三 PR 独立 revert。PR-A 回滚后任务载荷退回原字段，旧面板照常。PR-C 回滚后前端改发 `interaction_version=1`，服务端 v1 路径未动。

---

## 10. 待所有者决定

**D1 首问拦截策略**：推荐「只在标的不明确时拦截」（本任务书按此写，`plan_intake` 的 `auto_start` 一行可切）；备选「标的明确但读到背景时也先确认」。切换只改 `plan_intake`。

**D2 问股正文流式默认值**：`AGENT_CHAT_STREAM_CONTENT` 默认关闭，能力已实现。本任务不依赖它；建议 PR-B 合入后另开小 PR 改默认开启。是否执行由所有者决定。

---

## 11. 文档与证据

- `docs/CHANGELOG.md` `[Unreleased]` 每 PR 各加一行（扁平格式，不加 `###` 标题）。
- `docs/ai/FLOWS.md` 第 2 节第 5/6 步；`docs/ai/STATUS.md`「首问引导」与半成品表。
- `docs/full-guide.md` 任务流事件说明补 `stage/progress_updated_at/heartbeat_interval_s`；评估 `docs/full-guide_EN.md` 是否同步。
- 文件增删后 `python scripts/gen_ai_codemap.py`。
- PR 描述附受影响页面前后截图（首页运行卡各状态、问股面板、三种理解卡，含窄屏+深色），截图不入库。
