# 开发任务书：问股对话列表「置顶」

> 状态：已实施  
> 基线：`adb59b1f`  
> 任务类型：`feat`  
> 影响面：问股会话列表 / 会话菜单 / Agent Chat 会话 API / 存储层 / i18n / 测试 / 文档

## 1. 背景与现状

### 1.1 用户诉求

参照 DeepSeek 对话界面，为「问股」页的对话列表增加「置顶」能力：

- 对话行/标题的「更多操作」菜单中可以置顶、取消置顶。
- 置顶后的对话固定显示在列表最上方，单独成组，不参与「今天 / 昨天 / 更早」分组。
- 置顶状态持久化，刷新页面、切换会话、重开浏览器后仍然生效。
- 与现有重命名、删除一样，从历史行和当前会话标题两个入口都能操作。

### 1.2 现有链路

| 入口 | 当前位置 | 现状 |
| --- | --- | --- |
| 会话列表 UI | `apps/dsa-web/src/components/chat/ChatSessionSidebar.tsx` | 纯展示组件，按 `sessionGroups` 渲染分组，行内只有标题与「更多」按钮 |
| 会话分组 | `apps/dsa-web/src/pages/ChatPage.tsx` `groupSessionsByDate()` | 固定按 `last_active` 分「今天 / 昨天 / 更早」三组 |
| 会话菜单 | `apps/dsa-web/src/pages/ChatPage.tsx` `chat-session-menu` | 仅「重命名」「删除对话」，入口 `openSessionMenu()` 同时服务列表中行和顶部标题 |
| 前端状态 | `apps/dsa-web/src/stores/agentChatStore.ts` | `sessions` 由 `loadSessions()` 整体拉取，没有单会话写操作 |
| 前端 API | `apps/dsa-web/src/api/agent.ts` | 有 `getChatSessions` / `renameChatSession` / `deleteChatSession` |
| 后端接口 | `api/v1/endpoints/agent.py` | `GET /chat/sessions` 返回 `SessionItem`，`PATCH` 改名，`DELETE` 删除 |
| 服务层 | `src/services/agent_chat_session_service.py` | `list_sessions()` / `rename_session()` / `delete_session()` |
| 存储层 | `src/storage.py` `DatabaseManager.get_chat_sessions()` | 从 `conversation_messages` 聚合，按 `max(created_at)` 倒序；会话元数据表有 `conversation_session_states` / `conversation_session_titles` |

### 1.3 需要解决的问题

1. 当前列表只有时间分组，长期关注的一只股票的会话会被新会话不断压到底部。
2. 会话菜单没有可扩展的置顶状态位，前端也没有单会话的乐观更新入口。
3. 会话列表有 `limit`（默认 50），置顶必须发生在分页截断之前，否则老会话置顶后仍会消失。
4. 会话删除后必须同步清理置顶记录，避免残留脏数据。

## 2. 目标与非目标

### 2.1 目标

- G1：会话菜单新增「置顶对话 / 取消置顶」，列表中行与顶部标题菜单行为一致。
- G2：置顶会话在列表顶部以独立分组 `置顶` 展示，置顶组按置顶时间倒序；未置顶会话保持原有「今天 / 昨天 / 更早」逻辑。
- G3：置顶状态服务端持久化，刷新/重开浏览器后保持；`GET /chat/sessions` 返回 `is_pinned`。
- G4：置顶排序在 SQL 层完成，且不受 `limit` 截断影响。
- G5：置顶/取消置顶失败时前端回滚乐观更新并提示，不污染列表。
- G6：删除会话同时删除置顶记录；未知会话置顶返回 404。

### 2.2 非目标

- 不做置顶数量上限、拖拽排序、批量置顶。
- 不改变会话重命名、删除、导出、发送通知的既有契约。
- 不为机器人（Telegram / 飞书）侧新增置顶交互。
- 不新增配置项，不改 `.env.example`。

## 3. 交互与视觉规格

1. 会话菜单顺序：`置顶/取消置顶` → `重命名` → `删除对话`，置顶项不带 danger 样式。
2. 菜单文案随当前状态切换：未置顶显示「置顶对话」，已置顶显示「取消置顶」；`zh` / `en` 同步。
3. 置顶分组标题为「置顶」/「Pinned」，位于列表最上方，与「今天」同层级。
4. 已置顶会话行在标题前显示 12px 的 `Pin` 图标（`aria-hidden`，不改变可访问名称）。
5. 取消置顶后会话回到自身 `last_active` 对应的日期分组，不跳变到「今天」。
6. 交互反馈：点击菜单后菜单立即关闭；服务端失败时恢复原状态并给出错误 toast（复用现有 `sendToast`）。

## 4. 数据与接口契约

### 4.1 存储

新增表 `conversation_session_pins`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | `VARCHAR(100)` PK | 与 `conversation_messages.session_id` 对齐 |
| `pinned_at` | `DATETIME NOT NULL` | 服务器本地 naive 时间（`datetime.now`），与「会话」表时间基准一致 |

- 新表由 `Base.metadata.create_all` 创建，不需要 SQLite `ALTER TABLE` 迁移。
- `pinned_at` 在 `tests/test_time_basis_contract.py` 的 `LOCAL_TIME_TABLES` 登记。
- `delete_conversation_session()` 同时删除对应置顶记录。

### 4.2 列表排序

`DatabaseManager.get_chat_sessions()` 在聚合查询中 `LEFT JOIN conversation_session_pins`：

```sql
ORDER BY (pins.pinned_at IS NULL), pins.pinned_at DESC, max(messages.created_at) DESC
```

- 置顶会话永远排在未置顶会话之前，因此在 `LIMIT` 截断下不会被丢弃。
- 每条记录新增 `is_pinned: bool`。

### 4.3 HTTP

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| `PUT` | `/api/v1/agent/chat/sessions/{session_id}/pin` | `{"pinned": true}` | `{"session_id": "...", "pinned": true}` |
| `GET` | `/api/v1/agent/chat/sessions` | — | `sessions[].is_pinned: bool` |

- 会话不存在（没有任何消息）返回 `404 Session not found`。
- 请求体缺少 `pinned` 或类型错误由 FastAPI 返回 `422`；不做隐式默认值。
- `pinned` 为幂等操作：重复置顶会刷新 `pinned_at`；重复取消是 no-op。

## 5. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `src/storage.py` | 新增 `ConversationSessionPin` 模型；`get_chat_sessions()` 左连接并按置顶排序、输出 `is_pinned`；新增 `set_conversation_session_pinned()`；删除会话时清理置顶 |
| 2 | `src/services/agent_chat_session_service.py` | 新增 `set_session_pinned()` 透传存储层 |
| 3 | `api/v1/endpoints/agent.py` | `SessionItem` 增加 `is_pinned`；新增 `PUT .../pin` 接口 |
| 4 | `apps/dsa-web/src/api/agent.ts` | `ChatSessionItem.is_pinned`；新增 `pinChatSession()`（响应归一化为布尔） |
| 5 | `apps/dsa-web/src/stores/agentChatStore.ts` | 新增 `setSessionPinned()`：乐观更新 → 调接口 → 重新拉列表；失败回滚并抛出；新会话默认 `is_pinned: false` |
| 6 | `apps/dsa-web/src/pages/ChatPage.tsx` | `SessionListItem` 增加 `is_pinned`；`groupSessionsByDate()` 增加置顶分组；菜单新增置顶项；失败 toast |
| 7 | `apps/dsa-web/src/components/chat/ChatSessionSidebar.tsx` | 行内渲染 Pin 图标 |
| 8 | `apps/dsa-web/src/research.css` | `.chat-session-pin` 样式 |
| 9 | `apps/dsa-web/src/i18n/uiText.ts` | `chat.pinSession` / `chat.unpinSession` / `chat.groupPinned` / `chat.pinSaveFailed`（zh + en） |
| 10 | `tests/test_storage.py` | 置顶排序、`is_pinned`、取消置顶、删除清理 |
| 11 | `tests/test_agent_chat_api.py` | 置顶接口持久化、列表返回、404、422 |
| 12 | `apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts` | 乐观更新、成功刷新、失败回滚 |
| 13 | `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx` | 菜单置顶/取消置顶调用与分组 |
| 14 | `docs/full-guide.md`、`docs/CHANGELOG.md` | 能力说明与变更记录 |

## 6. 验收用例

1. 新建会话 → 菜单点「置顶对话」→ 行移动到「置顶」组、显示 Pin 图标；刷新页面后仍在置顶组。
2. 置顶一个 `last_active` 超过 50 条会话之前的老会话 → 列表仍返回该会话且排第一。
3. 置顶后再取消 → 回到原日期分组；菜单文案在两种状态间正确切换。
4. 会话置顶失败（接口 500）→ 列表状态回滚，出现错误提示，不产生幽灵置顶。
5. 删除已置顶会话 → 会话消失，重建同 `session_id` 的会话不继承旧置顶。
6. 未知 `session_id` 置顶 → 404；`pinned` 缺失 → 422。
7. `zh` / `en` 界面下菜单文案、分组标题、失败提示均为对应语言。

## 7. 兼容性与回滚

- `is_pinned` 为追加字段：旧前端忽略即可；旧后端不返回时前端按 `false` 归一化。
- 新表为纯追加，不修改既有列，旧版本代码可继续运行（忽略置顶表）。
- 回滚：删除新接口与新表，前端回退菜单项与分组逻辑；历史会话数据不受影响。

## 8. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| Python 存储 / API | `python -m pytest tests/test_storage.py tests/test_agent_chat_api.py tests/test_time_basis_contract.py -m "not network"`、`python -m py_compile` 改动文件 |
| Web 前端 | `cd apps/dsa-web && npm run lint && npm test -- --run && npm run build` |
| 文档 | 核对命令、接口路径、文件名与仓库一致 |
