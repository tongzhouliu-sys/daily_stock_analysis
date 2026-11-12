# 问股回复终态与可追问性修复：开发任务书

日期：2026-11-12。状态：已实施。任务类型：fix。建议优先级：P0。

产品依据：[问股回复终态与可追问性修复需求](stock-chat-stream-terminal-recovery-requirements.md)。目标：修复多 Agent 问股“决策完成但界面不结束”、刷新后仍显示运行中、停止按钮无法真正退出加载态的问题，并补齐可追问所需的终态契约。

实施状态：T1–T7 已按本文落地，验证结果见交付说明与 `docs/CHANGELOG.md`；改动尚未提交。

## 1. 开发边界与基线

核对基线为本地 HEAD `bd178b28` 及 2026-11-12 工作区。工作区已有其他未提交改动；实施前先读取最新状态，保留既有改动，不重置、覆盖或擅自提交。

| 已有位置 | 本任务相关职责 |
| --- | --- |
| `api/v1/endpoints/agent.py` | `/chat/stream`、`/chat`、会话详情；`run_sync`、`event_generator`、SSE 终态 |
| `src/agent/orchestrator.py` | 多 Agent `prepare_turn` / `execute_turn`；运行状态、消息保存、阶段执行 |
| `src/agent/chat_executor.py` | 单 Agent `execute_turn`；已有 `try/finally` 清理路径，可作为对齐样板 |
| `src/agent/conversation.py` | `set_running`、`update_running_progress`、`add_message` |
| `src/storage.py` | `ConversationSessionState`、运行状态与进度持久化、会话详情快照 |
| `src/services/agent_chat_session_service.py` | 会话详情返回 `is_running` / `running_progress_steps` |
| `apps/dsa-web/src/stores/agentChatStore.ts` | SSE 消费、停止、轮询、详情恢复、消息追加 |
| `apps/dsa-web/src/api/agent.ts` | 流式请求类型、会话详情类型、取消接口（如实施） |
| `apps/dsa-web/src/pages/ChatPage.tsx` | 输入框/停止按钮/发送按钮的禁用与恢复 |
| `docs/agent-stream-events.md` | `done`、`error`、停止/断线语义的契约文档 |

范围含 Web 问股、共用 Web 构建的 Desktop、单 Agent 与多 Agent 流式/非流式问股。Bot、报告、定时任务、扫描器和交易执行不在本次范围；Bot 复用同一执行器时只继承终态清理。

## 2. 目标契约

### 2.1 终态顺序

```text
accepted
  → 执行（stage/tool/thinking...）
  → 保存助手消息（成功或失败）
  → 清除本轮 running 状态
  → 发送 done / error（除非连接已断开）
```

要求：

- 助手消息保存成功后才能清除运行状态；清除运行状态后才能发送 `done`。
- 连接断开时不再发送事件，但执行器仍必须完成“保存终态消息 + 清除本轮运行状态”。
- 任何异常路径都必须走同一终态清理；不能只覆盖正常返回。

### 2.2 运行状态归属

运行状态从“会话级布尔值”升级为“会话 + 当前轮次”：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `is_running` | bool | 兼容字段；当前会话是否有运行中的轮次 |
| `running_turn_id` | string/null | 当前运行轮次的稳定标识；无运行时为 null |
| `running_started_at` | datetime/null | 当前轮次开始时间 |
| `running_heartbeat_at` | datetime/null | 最近一次进度/心跳时间 |

只有 `running_turn_id` 匹配的轮次可以更新进度、清除运行状态。旧轮次的迟到完成、异常或断线清理不得清除新轮次状态。

### 2.3 停止语义

本期保留“停止接收当前回复”的产品语义：

- 客户端停止后立即结束本轮 UI 加载态，允许追问。
- 不强制要求后台模型立即停止；后台任务继续执行并最终保存消息。
- 旧轮次不得改写新轮次的运行状态和消息列表。
- 如果产品后续要求“停止即取消”，新增服务端取消接口，作为独立 P1 任务，不阻塞本期 P0。

## 3. 任务拆解

| 任务 | 负责人角色 | 依赖 | 交付物 | 需求映射 |
| --- | --- | --- | --- | --- |
| T0：复现与冻结失败用例 | 后端、前端、测试 | 无 | 可重复失败的回归用例、环境确认 | R1–R5 |
| T1：多 Agent 终态与运行归属 | 后端 | T0 | `orchestrator` 终态清理、轮次归属、DB 字段 | R2、R3、R7 |
| T2：API 终态与详情契约 | 后端 | T1 | SSE/详情字段、异常路径、契约测试 | R1、R2、R6 |
| T3：前端 `done` 终态处理 | 前端 | T2 | 立即落地消息、取消 reader、恢复输入 | R1、R7 |
| T4：停止与轮询/详情竞态 | 前端 | T3 | 令牌/代次校验、停止不可复活 | R4、R5 |
| T5：stale running 恢复 | 后端、前端 | T1、T2 | 心跳、过期判定、启动清理 | R6 |
| T6：可选服务端取消（P1） | 后端、前端 | T2、T4 | 取消接口与幂等取消 | R8 |
| T7：文档、验证与交付 | 全体 | T1–T5 | 契约文档、CHANGELOG、验证记录 | 全部 |

## 4. 详细任务

### T0 · 复现与冻结失败用例（P0）

**目的：** 先把用户现象变成可重复的失败测试，再改代码。

**动作：**

1. 确认运行环境：`AGENT_ARCH`、实际 commit、是否多 worker/多副本、生产是否启用 `AGENT_CHAT_STREAM_CONTENT`。
2. 后端新增/扩展测试：
   - `tests/test_agent_chat_api.py`：多 Agent 流式路径必须能调用 `AgentOrchestrator.execute_turn`，不得因 `stream_content` 参数抛 `TypeError`。
   - `tests/test_multi_agent.py`：`execute_turn` 成功、返回失败、抛异常三条路径都必须调用 `set_running(..., False, ...)`；旧轮次完成不得清除新轮次。
   - `tests/test_agent_chat_session_service.py`：详情返回运行归属与心跳字段。
3. 前端新增测试：
   - `apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts`：`accepted` + `done` 后连接不关闭，仍必须追加消息并结束加载。
   - 同文件：轮询请求 pending 时点击停止，响应返回后 `loading` 不再变回 true。
   - 同文件：停止后旧请求的迟到消息不追加、不清除新请求状态。
4. 手工复现并保存证据：多 Agent 完成、刷新、停止、停止后追问各一次；记录网络事件和会话详情 `is_running`。

**验收：** 新测试在当前 HEAD 上失败，失败原因分别对应 B1–B5；不是用 mock 掉真实风险层。

### T1 · 多 Agent 终态与运行归属（P0）

**文件：** `src/agent/orchestrator.py`、`src/agent/conversation.py`、`src/storage.py`、`src/services/agent_chat_session_service.py`。

**改动：**

1. `AgentOrchestrator.execute_turn` 增加 `stream_content: bool = False` 参数；当前不实现正文增量时安全忽略并记录 debug 日志，禁止因未知参数失败。
2. 在 `prepare_turn` 或 `execute_turn` 开始时生成稳定的 `turn_id`（优先复用已有 `run_id`，没有则 `uuid4`）。
3. `set_running` / `update_running_progress` 增加可选 `turn_id`：
   - 置 true：写入 `running_turn_id`、`running_started_at`、`running_heartbeat_at`。
   - 置 false：只有 `running_turn_id` 为空或匹配时才清除；不匹配时 no-op。
   - 进度更新：只有匹配的 `running_turn_id` 才允许写入。
4. 把 `execute_turn` 从“线性执行”改为 `try/finally`：
   - `try` 内执行 pipeline、保存成功/失败助手消息、构造 `AgentResult`。
   - `finally` 内按 `turn_id` 清除运行状态和进度；即使保存消息抛异常也要尝试清理。
   - 终态消息保存失败时记录明确日志，并让 `done`/`error` 反映失败；不能留下运行中状态。
5. 对齐单 Agent：`src/agent/chat_executor.py` 的 `set_running` / `update_running_progress` 也传 `turn_id`，确保单/多 Agent 使用同一 CAS 语义。
6. `ConversationSessionState` 追加可空列：`running_turn_id`、`running_started_at`、`running_heartbeat_at`；沿用现有轻量迁移方式，不删列、不重建表。
7. `AgentChatSessionService` 详情返回 `running_turn_id`、`running_started_at`、`running_heartbeat_at`；旧数据为 null 时保持兼容。
8. 在每次进度回调中更新 `running_heartbeat_at`；心跳失败不影响主流程。

**测试：**

- 成功路径：pipeline 返回成功，断言助手消息保存、`set_running(false, turn_id)` 被调用、结果为 success。
- 失败路径：pipeline 返回失败，断言失败消息保存、运行状态清除。
- 异常路径：pipeline 抛异常，断言异常向上抛出/转为 error 前运行状态已清除。
- 重叠路径：旧 turn 的 finally 不得清除新 turn 的 `running_turn_id`。
- 契约路径：`execute_turn(turn, progress_callback=..., stream_content=False/True)` 均不报参数错误。

**验收：** 多 Agent 成功/失败/异常后详情 `is_running=false`；旧轮次迟到清理不影响新轮次。

### T2 · API 终态与详情契约（P0）

**文件：** `api/v1/endpoints/agent.py`、`src/schemas/`（如需要）、`tests/test_agent_chat_api.py`。

**改动：**

1. `run_sync` 保持向执行器传递 `progress_callback` 和 `stream_content`；执行器侧已兼容，不能再通过删参数绕过契约。
2. `done` 事件继续携带 `assistant_message_id`、`thinking_steps`、`backend`、`request_id`、`session_id`；确保多 Agent 路径也有值。
3. `error` 事件继续携带 `request_id`、`session_id`、`error_code`；执行器异常时不得再额外发送一个 `done`。
4. `event_generator` 的清理逻辑：
   - `done`/`error` 已发出后立即结束，不再等待额外事件。
   - 客户端断开时，当前“等待 fut 5 秒”保留为观测；不强制取消后台任务，但必须确保后台任务的终态清理不依赖客户端连接。
   - 对 `run_coroutine_threadsafe` 的投递失败做保护，避免事件循环关闭后抛未处理异常。
5. 会话详情响应追加运行归属/心跳字段；不改变现有 `messages`、`session_state` 字段。
6. 更新 `docs/agent-stream-events.md` 的 `done` 终止语义、停止/断线说明和详情字段。

**测试：**

- 真实 `AgentOrchestrator` 签名 + 流式 API：事件序列为 `accepted` → `done`，不得出现 `error` 参数错误。
- 执行器抛异常：事件序列为 `accepted` → `error`；随后详情 `is_running=false`。
- 旧客户端只读 `success`/`content`/`error` 时仍兼容。

**验收：** 多 Agent 流式 API 能完成一轮真实调用；成功、失败、异常三种终态均一致。

### T3 · 前端 `done` 终态处理（P0）

**文件：** `apps/dsa-web/src/stores/agentChatStore.ts`、`apps/dsa-web/src/api/agent.ts`。

**改动：**

1. 在 `processLine` 中把 `done` 视为终止信号：
   - 记录 `finalContent`、`finalClarification`、`finalDomainGuard`、`finalBackend`。
   - 记录 `finalAssistantMessageId`（统一转字符串）和 `finalThinkingSteps`。
   - 设置 `receivedDoneEvent=true`。
   - 返回“终止”标记，外层循环立即 `await reader.cancel()` 并 `break`，不再等待 EOF。
2. `done.error_code=timeout` 时仍终止本轮：设置 `terminalStatus='timeout'`，结束加载，显示超时状态；不能只 `return` 后继续读流。
3. 消息追加改为“终止后统一执行一次”：
   - 优先使用 `finalAssistantMessageId` 作为消息 ID；缺失时才用临时 ID。
   - 优先使用 `finalThinkingSteps`；为空数组时以空数组为准，不偷偷用本地事件。
   - 用 `messages.some(m => m.id === id)` 去重，兼容轮询已先追加同一消息。
4. `finally` 保持清理 `loading`、`progressSteps`、`abortController`、`activeRequestId`，但不得在 `stopStream` 已接管后覆盖停止状态。
5. 更新 `ChatStreamRequest` 类型：补充 `stream_content?: boolean` 的注释；本期前端不默认发送。
6. `ChatPage` 在 `loading=false` 时立即恢复 textarea 和发送按钮；不需要新增 UI，只验证现有条件。

**测试：**

- `accepted` + `done` 后流保持打开：消息立即出现，`loading=false`，reader 被取消。
- `done.success=false`：错误提示出现，输入恢复。
- `done.error_code=timeout`：超时状态出现，输入恢复。
- `done.assistant_message_id` 与轮询已有消息 ID 相同：不重复追加。
- `done.thinking_steps=[]`：不回落使用浏览器事件。

**验收：** 不依赖 SSE EOF；任何 `done` 都在 1 秒内结束本轮。

### T4 · 停止与轮询/详情竞态（P0）

**文件：** `apps/dsa-web/src/stores/agentChatStore.ts`。

**改动：**

1. 在 store 闭包中增加轮次/轮询令牌：
   - `streamEpoch`：每次 `startStream` / `stopStream` / `resetSession` 递增。
   - `pollToken`：每次 `startPolling` / `stopPolling` 递增。
   - `detailReadSequence` 已有，复用它标记详情读取归属。
2. `stopStream`：
   - 立即 `stopPolling()`，递增 `pollToken`；
   - 递增 `streamEpoch`，使当前 stream 的 `ownsStream()` 失效；
   - 递增 `detailReadSequence`，使在途详情读取失效；
   - 清空 `abortController`、`activeRequestId`、`loading`、`progressSteps`，设置 `terminalStatus='stopped'`；
   - abort 当前 controller；
   - 不等待服务端确认（本期停止语义为停止接收）。
3. `startPolling`：
   - 启动时捕获 `token = ++pollToken`。
   - `await getChatSessionMessages` 返回后重新检查：`token === pollToken`、`get().loading === true`、`sessionId` 未变、`sessionEpoch` 未变；任一不满足直接 return，不 `set` 任何状态。
   - 轮询追加消息前再次检查 token，避免停止后追加旧消息。
4. `readDetail`：
   - 保持 `ownsRead` 的 session/epoch/sequence 校验；停止递增 sequence 后，旧读取不得写状态。
   - `streamOwnsTransientState` 判断继续保留：活跃 SSE 拥有瞬时状态，详情读取不得关闭 loading。
5. `startStream` 的 `ownsStream` 增加 `streamEpoch` 校验；旧流的事件、错误和 finally 不得影响新流。
6. `resetSession`、`switchSession`、`startNewChat` 同步递增令牌，保持与停止一致的失效语义。

**测试：**

- 轮询 pending 时停止 → 响应返回后 `loading=false`、消息不追加。
- 详情 pending 时停止 → 响应返回后不覆盖状态。
- 停止后立即 `startStream` 新问题 → 旧流迟到 `done`/`error` 不写入新消息列表。
- 连续点击停止 → 状态稳定、无异常、按钮保持发送态。

**验收：** 停止后不存在任何异步回调可以把 `loading` 改回 true。

### T5 · stale running 恢复（P1）

**文件：** `src/storage.py`、`src/services/agent_chat_session_service.py`、`apps/dsa-web/src/stores/agentChatStore.ts`、`apps/dsa-web/src/api/agent.ts`。

**改动：**

1. 详情返回 `running_heartbeat_at`；心跳在每次进度回调更新。
2. 前端增加统一阈值（建议 90 秒，集中常量）：
   - `is_running=true` 且心跳未过期：恢复运行态并继续轮询。
   - `is_running=true` 但心跳过期：不进入无限 loading；显示“上一轮分析可能已中断，可继续提问”，允许发送。
   - `is_running=false`：正常结束，不再用 `hasUnfinishedTurn` 单独维持 30 秒恢复态。
3. 服务端启动时清理明显 stale 的 `is_running=true`：启动时间早于进程启动、且心跳超过阈值；清理只影响运行标记，不删除消息。
4. 保留 `RUNNING_RECOVERY_GRACE_MS` 作为“刚接受但尚未写心跳”的短暂宽限，不与 stale 阈值混用。

**测试：**

- 详情 `is_running=true` + 新心跳：恢复 loading。
- 详情 `is_running=true` + 旧心跳：不 loading，输入可用。
- 启动清理：模拟崩溃遗留状态，服务启动后详情为 false。
- 正常完成：不会被误判 stale。

**验收：** 任何情况下刷新都不会无限显示运行中。

### T6 · 可选服务端取消（P1，非本期阻塞）

仅在产品明确要求“停止即取消”时实施。

**建议接口：**

```http
POST /api/v1/agent/chat/cancel
Content-Type: application/json

{"session_id": "...", "request_id": "..."}
```

响应：

```json
{"cancelled": true, "already_terminal": false}
```

**实现要点：**

- 新增按 `session_id + request_id/turn_id` 的活跃轮次注册表；单进程用内存，多 worker 用数据库 `cancel_requested_at` 或共享存储。
- `stopStream` 先本地恢复 UI，再 best-effort 调用取消接口，不阻塞输入。
- 执行器在阶段边界、工具调用前检查取消标记；LLM 调用无法立即中断时，返回后丢弃结果并写取消终态。
- 取消终态使用 `done.success=false, error_code='cancelled'`；保持旧客户端可读。
- 幂等：重复取消、已结束轮次取消都返回 200，不抛 500。
- 清理注册表，避免内存泄漏。

**测试：** 取消后详情 `is_running=false`；取消中的迟到 `done` 不覆盖新轮次；重复取消幂等。

### T7 · 文档、验证与交付

**文档：**

- 更新 `docs/agent-stream-events.md`：`done` 为终态、客户端不等待 EOF、停止/断线语义、运行归属/心跳字段。
- 更新 `docs/CHANGELOG.md` 的 `[Unreleased]`：实现后添加 `[修复]` 条目；本次仅文档时添加 `[文档]` 条目。
- 如新增取消接口，补充 API 文档和 `docs/full-guide.md` / `docs/full-guide_EN.md` 的相关说明。
- 在 `docs/tasks/chat-stream-stuck-fix.md` 顶部补充“后续复检”链接，说明上一轮任务书只覆盖单 Agent 导入错误与本地停止清理，避免读者误以为问题已完全关闭。

**验证命令：**

```bash
python -m py_compile \
  api/v1/endpoints/agent.py \
  src/agent/orchestrator.py \
  src/agent/chat_executor.py \
  src/agent/conversation.py \
  src/storage.py \
  src/services/agent_chat_session_service.py

python -m pytest -m "not network" \
  tests/test_multi_agent.py \
  tests/test_agent_chat_api.py \
  tests/test_agent_chat_executor.py \
  tests/test_agent_chat_session_service.py \
  tests/test_agent_sse_cleanup.py

cd apps/dsa-web
npm ci
npm test -- agentChatStore ChatPage
npm run lint
npm run build
```

**手工验收：**

1. `AGENT_ARCH=multi` 完成一轮分析，确认正文、结束态、追问、刷新。
2. 执行中点击停止，确认按钮立即恢复、轮询不复活、可立即追问。
3. 构造 `done` 后连接不关闭的 SSE，确认页面仍结束。
4. 运行中切走再切回、刷新、重新打开 Desktop，确认状态一致。
5. 旧轮次未结束时开始新轮次，确认消息和运行状态不串轮。
6. 手工制造 stale `is_running`，确认刷新后可继续。

**PR 要求：**

- 涉及问股页面状态变化，PR 描述附修复前后截图或录屏；至少包含“决策完成”“停止后”“刷新后”三个状态。
- 说明是否覆盖单 Agent / 多 Agent、Web / Desktop、流式 / 非流式。
- 说明未验证项（如多 worker 部署、真实网关缓冲）和回滚方式。

## 5. 验收矩阵

| 检查项 | 命令/方式 | 阻断 |
| --- | --- | --- |
| Python 语法 | `python -m py_compile` 变更文件 | 是 |
| 后端单测 | `pytest -m "not network"` 指定文件 | 是 |
| 前端单测 | `npm test -- agentChatStore ChatPage` | 是 |
| 前端 lint/build | `npm run lint && npm run build` | 是 |
| 多 Agent 手工 | 真实 `AGENT_ARCH=multi` 一轮 | 是 |
| 单 Agent 回归 | `AGENT_ARCH=single` 一轮 | 是 |
| 停止竞态 | 轮询 pending 时停止 | 是 |
| done 不关闭 | 模拟 SSE keep-open | 是 |
| 刷新恢复 | 完成后刷新 / 运行中刷新 | 是 |
| Desktop | 加载同一 Web 构建 | 条件阻断 |
| 多 worker | 如生产多副本，验证共享取消/运行状态 | 条件阻断 |

## 6. 发布与回滚

**发布顺序：**

1. 先发布后端 T1/T2（终态清理 + 详情字段），再发布前端 T3/T4；旧前端可忽略新增字段。
2. 验证单 Agent 与多 Agent 各一轮，再观察日志中是否还有 `is_running` 长时间为 true。
3. T5 心跳恢复可随后端一起发布；T6 取消能力按产品决策单独发布。

**回滚：**

- 代码回滚到修复前版本；数据库新增可空列保留，不删除、不重建。
- 回滚后旧版本仍能读取已有消息和会话；已清除的运行状态不会自动恢复，属于可接受状态。
- 如果 T6 取消接口回滚，前端停止仍应保持本地立即恢复，不依赖取消接口。

## 7. 风险与注意事项

- **不要用无条件 `set_running(false)` 修复。** 这会在新旧轮次重叠时清掉新轮次状态，必须使用 `turn_id` 条件更新。
- **不要只在前端清 loading。** 服务端仍报告 running 时，刷新和轮询会把状态带回来；T1/T2 是根因修复。
- **不要只在 `done` 后等 EOF。** 必须主动取消 reader；否则网关/代理行为会让问题复现。
- **不要把轮询的 `await` 后校验省掉。** 只检查 `sessionId` 不足以阻止停止后的迟到响应。
- **不要用 mock 替代真实执行器签名测试。** B1 就是因为测试只覆盖了 MagicMock/单 Agent 路径才漏掉。
- **UI 改动需截图证据。** 按仓库规则，报告/Web UI 变更的 PR 描述必须附受影响页面截图。

## 8. 交付物

1. 后端修复：多 Agent 终态清理、轮次归属、详情字段、API 契约。
2. 前端修复：`done` 立即落地、停止不可复活、轮询/详情归属校验。
3. 测试：后端多 Agent 成功/失败/异常/重叠，前端 keep-open/pending-poll/stop/late-event。
4. 文档：`docs/agent-stream-events.md`、`docs/CHANGELOG.md`、上一轮任务书的复检说明。
5. PR 验证记录：单/多 Agent、流式/非流式、Web/Desktop、停止/刷新截图与未验证项。
