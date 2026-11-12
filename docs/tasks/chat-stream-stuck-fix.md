# 问股界面卡死与停止按钮失效修复任务书

> 2026-11-12 复检补充：本任务书只覆盖单 Agent 的导入错误和前端 `stopStream` 的本地状态清理，没有覆盖多 Agent 运行状态未清理、`done` 等待 EOF、停止后的轮询/详情竞态。后续完整分析见[问股回复终态与可追问性修复需求](../tyrone/stock-chat-stream-terminal-recovery-requirements.md)与[开发任务书](../tyrone/stock-chat-stream-terminal-recovery-tasks.md)。

## 背景

用户反馈：问股界面问完问题后卡死，决策 Agent 已完成，但无法追问，停止按钮也关不掉。

## 问题分析

### Bug 1: `chat_executor.py` 同步函数中使用 `await`（严重，阻断性）

**位置**: `src/agent/chat_executor.py:282`

**问题**: `execute_turn` 是同步函数（`def`），但在调用 `StockAnswerEditorService.format_response()` 时使用了 `await`。这导致 `SyntaxError: 'await' outside async function`，使得整个模块无法被导入。

**影响**:
- Single-agent 架构下，问股功能完全无法工作
- `build_agent_chat_executor()` 调用 `from src.agent.chat_executor import AgentChatExecutor` 时会抛出 `SyntaxError`
- Multi-agent 架构不受影响（绕过 `chat_executor.py`）

**根因**: T4（回答精编）功能添加时，`StockAnswerEditorService.format_response()` 是异步方法，但 `execute_turn` 是同步函数，且运行在 `loop.run_in_executor()` 的线程池线程中，没有运行的事件循环。

**修复方案**: 使用 `asyncio.run()` 包装异步调用。由于 `execute_turn` 运行在单独的线程中（通过 `run_in_executor`），没有继承主事件循环，`asyncio.run()` 会创建新的事件循环来执行协程。

### Bug 2: `stopStream` 没有立即清理 `loading` 状态（UI 卡死）

**位置**: `apps/dsa-web/src/stores/agentChatStore.ts:stopStream`

**问题**: 当用户点击停止按钮时，`stopStream` 只调用 `controller.abort()`，但依赖 `startStream` 的 `finally` 块来清理 `loading`、`abortController` 等状态。如果 `reader.read()` 不立即响应 abort（例如流已关闭、浏览器行为差异或网络问题），`loading` 会保持为 `true`。

**影响**:
- 无法发送新问题（`handleSend` 在 `loading=true` 时早期返回）
- 停止按钮仍然显示（因为 `loading` 为 true，渲染停止按钮而非发送按钮）
- 界面看起来"卡死"，用户无法继续交互

**根因**: `stopStream` 假设 `startStream` 的 `finally` 块会立即执行并清理状态，但没有考虑 `reader.read()` 可能不立即响应 abort 的情况。

**修复方案**: 
1. 在 `stopStream` 中立即清理 `loading`、`abortController`、`activeRequestId` 等状态
2. 将 `abortController` 设置为 `null`，使 `startStream` 的 `ownsStream()` 返回 `false`，防止 `finally` 块覆盖已清理的状态

## 任务清单

### 任务 1: 修复 `chat_executor.py` 中的 `await` 语法错误

**文件**: `src/agent/chat_executor.py`

**改动**:
1. 在文件顶部导入 `asyncio`（如果尚未导入）
2. 将第 282 行的 `await editor.format_response(...)` 改为 `asyncio.run(editor.format_response(...))`

**验证**:
```bash
python3 -m py_compile src/agent/chat_executor.py
```

**预期结果**: 编译成功，无 `SyntaxError`。

### 任务 2: 修复 `stopStream` 状态清理问题

**文件**: `apps/dsa-web/src/stores/agentChatStore.ts`

**改动**:
1. 在 `stopStream` 中，调用 `controller.abort()` 后立即设置 `abortController: null`
2. 立即设置 `loading: false`、`progressSteps: []`、`activeRequestId: null`
3. 确保 `startStream` 的 `finally` 块在 `ownsStream()` 返回 `false` 时不会覆盖已清理的状态

**验证**:
1. 启动 Web 前端，打开问股页面
2. 发送一个问题，等待分析完成
3. 在分析过程中点击停止按钮
4. 验证：
   - 停止按钮立即切换回发送按钮
   - 可以立即发送新问题
   - 界面不再卡死

**预期结果**: 停止按钮立即生效，用户可以继续交互。

### 任务 3: 更新测试

**文件**: 
- `apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts`
- `tests/test_agent_chat_executor.py`（如果存在）

**改动**:
1. 添加测试：验证 `stopStream` 立即清理状态
2. 添加测试：验证 `chat_executor.py` 可以正常导入（如果尚未存在）

### 任务 4: 更新 CHANGELOG

**文件**: `docs/CHANGELOG.md`

**改动**: 在 `[Unreleased]` 段添加修复条目。

## 验收标准

1. `python3 -m py_compile src/agent/chat_executor.py` 成功
2. 问股界面在分析完成后可以立即发送新问题
3. 停止按钮在点击后立即生效，用户可以继续交互
4. 现有测试通过
5. 新增测试覆盖修复的场景

## 风险与回滚

**风险**:
- Bug 1 修复使用 `asyncio.run()`，在 `execute_turn` 运行的线程池线程中应该安全，但需要验证不会与现有事件循环冲突
- Bug 2 修复改变了状态清理的时机，需要验证不会导致状态不一致

**回滚方式**:
- 如果修复导致新问题，可以 revert 相关 commit
- Bug 1 的回滚会重新引入 `SyntaxError`，但只影响 single-agent 架构
- Bug 2 的回滚会重新引入 UI 卡死问题，但只影响停止按钮行为

## 交付物

1. 修复后的代码
2. 更新的测试
3. 更新的 CHANGELOG
4. 本任务书的执行报告
