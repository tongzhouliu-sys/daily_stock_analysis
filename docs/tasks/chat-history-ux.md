# 开发任务书：问股历史对话交互增强（回到上次提问 / 悬停操作 / 消息时间 / 右侧提问导航）

> 状态：已实施（需求定稿）  
> 任务类型：`feat`（纯前端）  
> 影响面：问股消息线程 / 消息悬停操作 / 消息时间戳 / 右侧提问导航 / i18n / 测试  
> 基线：以 `main` 当前 HEAD 为准（撰写时 `7190a261`，行号以实际实现时为准，本文件统一用「组件/函数名」引用，避免行号漂移）

## 1. 背景与现状

### 1.1 用户诉求

参照 ChatGPT / DeepSeek 的对话体验，对「问股」历史对话做三点增强：

1. **打开历史对话不要滚到底部**：当前打开/切换历史会话会自动滚动到消息流最底部（最新 assistant 回复的末尾）；正确行为是自动定位到「上一次提问」的位置（即最后一条用户提问处），让用户从问题读起。
2. **悬停显示操作与时间**：鼠标悬停在消息上时，像 ChatGPT 那样显示「修改」「复制」等操作；最好同时显示该次提问的具体日期和时间。
3. **多次提问的右侧导航**：一个会话内有多轮提问时，像 DeepSeek 那样在右侧提供可点击的提问列表（目录），点击后跳到对应提问并高亮。

### 1.2 现有链路

| 关注点 | 位置 | 现状 |
| --- | --- | --- |
| 打开/切换会话滚动 | `ChatPage.tsx` `useEffect([sessionId])` | 无条件把 `shouldStickToBottomRef.current = true`，再由滚动 effect 调 `scrollToBottom('auto')`，因此历史会话固定滚到底部 |
| 滚动工具 | `ChatPage.tsx` `scrollToBottom` / `requestScrollToBottom` / `syncScrollState` / `handleMessagesScroll`，`utils/chatScroll.ts` `isNearBottom` | 只有「贴底 / 显示回到底部按钮」两态，没有「锚定到某条消息」的能力 |
| 消息渲染 | `ChatPage.tsx` `messages.map` → `components/chat/ChatMessageItem.tsx` | `.chat-msg` 只有 class，没有 `data-message-id` / `data-role`，无法按 id 精确定位单条消息 |
| 悬停操作 | `ChatMessageItem.tsx` `chat-message-actions` | 仅 assistant 消息有「复制 / 导出」；user 气泡没有任何悬停操作 |
| 消息时间 | `api/agent.ts` `ChatSessionMessage.created_at` | 后端已返回每条消息的 `created_at`，但前端 `Message` 类型与 hydration 均丢弃它 |
| 消息类型 | `stores/agentChatStore.ts` `Message` | 只有 `id/role/content/skills/thinkingSteps/backend/stockClarification/domainGuard/answerMeta`，**没有时间戳** |
| 消息恢复 | `stores/agentChatStore.ts` `readDetail()` 与轮询新增消息映射 | 映射为 `{id, role, content, thinkingSteps, answerMeta}`（轮询只取 `{id, role, content, thinkingSteps}`），丢弃 `created_at` |
| 布局 | `research.css` `.chat-body`（flex column）→ `.chat-thread-scroll` → `.chat-thread`（max-width 1020px 居中） | 右侧没有提问导航容器；`.chat-body` 目前直接平铺「滚动区 + 输入区」 |

### 1.3 需要解决的问题

1. 打开历史会话应「定位到上一次提问」，但现有滚动模型只有「贴底」一种目标，且贴底状态在 `sessionId` 变化时被无条件强制置真，缺少「恢复锚点」这一第三种目标。
2. 前端丢弃了 `created_at`，无法显示任何消息时间，也无法在右侧导航里展示「某次提问的时间」。
3. user 消息没有悬停操作；assistant 消息缺少「修改」语义的操作入口（本仓库的 LLM 上下文由服务端持久化消息串接，无法就地改写历史，需用「回填到输入框编辑后重发」替代）。
4. 消息 DOM 没有稳定 id，无法实现「点击提问 → 滚动到对应位置」的跳转。
5. `.chat-body` 布局没有为右侧导航预留结构。

## 2. 目标与非目标

### 2.1 目标

- G1：打开/切换到**已有消息**的历史会话时，自动把最后一条 user 消息定位到视口顶部附近（带顶部留白），而不是滚到消息流最底部。
- G2：新会话 / 发送中 / 回复进行中，仍保持「贴底」跟随（现有行为不回退）。
- G3：user 消息悬停显示「修改（回填重发）」「复制」；assistant 消息保留「复制 / 导出」并补充时间；两种角色的操作按钮在悬停 / 键盘聚焦时出现（移动端始终可见）。
- G4：每条消息显示创建时间（相对时间短文案 + `title` 提供完整「YYYY-MM-DD HH:mm:ss」），并随界面语言 zh/en 本地化；「上次提问的具体日期和时间」至少要在最后一条 user 消息与右侧导航中明确可见。
- G5：会话内 user 消息 ≥ 2 条时，右侧显示「提问导航」面板，按时间顺序列出所有提问（单行截断 + 时间），点击滚动到对应提问并短暂高亮；滚动时高亮项随视口自动切换（scroll-spy）。
- G6：右侧导航可折叠，折叠状态本地持久化；窄屏下不挤压消息线程（降级为顶栏开关）。
- G7：以上交互不改变既有「发送 / 停止 / 追问 / 报告上下文 / 澄清 / 导出 / 通知」契约。

### 2.2 非目标

- 不做消息**就地编辑**（不重写服务端历史消息，不改 LLM 上下文拼接语义）——「修改」只做「回填到输入框，编辑后作为新的一轮发送」。
- 不做「重新生成当前回复」（regenerate）。
- 不给机器人端（Telegram / 飞书）新增导航或悬停交互。
- 不改后端接口（`created_at` 已存在，无需新增字段）；不改 `.env.example` / `config_registry`。

## 3. 交互与视觉规格

### 3.1 打开历史对话定位到「上一次提问」

1. 触发时机：`switchSession()` / `loadInitialSession()` 恢复出一条**已有消息**的历史会话后（`messages.length > 0` 且当前非 `loading`）。
2. 锚点对象：`messages` 中最后一条 `role === 'user'` 的消息。
3. 定位效果：将该消息顶部对齐到滚动视口顶部下方固定留白（建议 `16px`）处；若锚点接近末尾导致计算值超过最大滚动距离，则夹紧到最大滚动位置。
4. 与「贴底」的关系：
   - 恢复已有会话：不贴底，执行一次锚定。
   - 全新会话（空消息）：无锚点，展示引导卡，无需滚动。
   - `loading`（恢复运行中/正在流式）时：沿用贴底，等本轮结束前不锚定；本轮自然结束回到非 loading 后，仍以「最后一次提问」为基准（即不因中途刷新而跳到底部）。
5. 跳到底部能力保留：用户主动上滚后仍显示「有新消息 / 回到底部」按钮；点击行为不变。

### 3.2 悬停操作（修改 / 复制）与消息时间

1. 操作按钮沿用现有 `.chat-message-actions` / `.chat-action-btn` 视觉（悬停 / `:focus-visible` 出现，移动端 `@media (hover: none)` 常显）。
2. **user 消息**新增两个按钮：
   - `修改`（Pencil 图标）：点击后把该条提问原文写入输入框并聚焦（不自动发送），用户修改后按现有 `handleSend` 路径作为**新的一轮**发送；保留当前会话上下文（不切换会话、不清理报告上下文）。
   - `复制`（Copy 图标）：复用现有 `copyMessageToClipboard`。
3. **assistant 消息**：保留「复制 / 导出」，并新增时间展示（见下）；不新增「修改」。
4. **时间展示**：
   - 每条消息显示一个紧凑的相对时间短文案（如「刚刚 / n 分钟前 / n 小时前 / 昨天 HH:mm / MM-DD HH:mm」），颜色为 `--text-muted-text`，字号 `11px` 左右。
   - `title` 属性展示完整本地时间「YYYY-MM-DD HH:mm:ss」（zh/en 各自日期格式）。
   - user 消息：时间显示在气泡一侧（左/上）或悬停操作行内；assistant 消息：时间并入 `chat-msg-meta`（技能徽章旁）。
   - 至少保证「最后一次提问」的时间在消息本身与右侧导航中**同时可见**，满足「具体日期和时间」诉求。

### 3.3 右侧提问导航（DeepSeek 式目录）

1. 出现条件：当前会话 `user` 消息数 ≥ 2 时显示；否则整列隐藏，不影响线程宽度。
2. 位置与结构：`.chat-body` 内新增一层 `.chat-thread-area`（`flex:1; min-height:0; display:flex`），左为 `.chat-thread-scroll`（`flex:1`），右为 `.chat-outline`（宽 `240px`，`flex:none`，`border-inline-start`）；输入区仍在最下方全宽。
3. 面板内容：标题（如「提问导航 / Questions」）+ 提问条目列表，每条 = 单行截断的问题文本 + 该次提问的时间短文案；按时间正序。
4. 点击行为：滚动到对应 user 消息顶部（复用 3.1 的锚定函数）并短暂高亮该消息（背景闪烁 `~1.2s`）。
5. scroll-spy：滚动时计算当前视口顶部的「最近一条 user 消息」，高亮对应条目（去抖 / `requestAnimationFrame` 节流）。
6. 折叠：顶栏新增开关按钮（List/ListChecks 图标），折叠状态写入 `localStorage['dsa.chat.outlineCollapsed']`；窄屏（< 约 1100px）默认折叠，由开关临时展开为浮层覆盖，不挤压线程。

## 4. 数据与接口契约

### 4.1 前端类型

- `Message` 增加 `createdAt?: string | null`（沿用后端 `created_at` ISO 字符串）。
- `ChatSessionMessage` 已含 `created_at: string | null`，无需改动。

### 4.2 hydration 补齐

- `agentChatStore.readDetail()`：消息映射补 `createdAt: m.created_at`。
- 轮询新增消息映射：补 `createdAt`（轮询返回的 `ChatSessionMessage` 也带 `created_at`）。
- `startStream()` 本地构造的 user / assistant 消息：`createdAt = new Date().toISOString()`（与 `created_at` 语义一致，供立即渲染时间）。

### 4.3 DOM 锚点契约

- `.chat-msg` 增加 `data-message-id={message.id}` 与 `data-role={message.role}`，作为「锚定 / 高亮 / 定位」的唯一来源，避免依赖文本查找。

### 4.4 工具函数

- 新增 `utils/chatTime.ts`：`formatChatTimestamp(iso: string | null, lang: 'zh' | 'en', now?: number) => { short: string; full: string }`（短 = 相对时间，全 = 绝对时间），可注入 `now` 便于测试。
- 复用/扩展 `utils/chatScroll.ts`：新增 `scrollToMessage(viewport, messageEl, padding)` 与 `currentAnchorMessageId(viewport, userEls)`（供 scroll-spy 使用），保持纯函数便于单测。

## 5. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `apps/dsa-web/src/api/agent.ts` | 无需改（`created_at` 已有）；如需统一备注可在类型上补注释 |
| 2 | `apps/dsa-web/src/stores/agentChatStore.ts` | `Message` 增 `createdAt`；`readDetail` / 轮询 / `startStream` 三处补 `createdAt` |
| 3 | `apps/dsa-web/src/utils/chatTime.ts`（新） | `formatChatTimestamp` 及单测 |
| 4 | `apps/dsa-web/src/utils/chatScroll.ts` | 增 `scrollToMessage` / `currentAnchorMessageId` 纯函数及单测 |
| 5 | `apps/dsa-web/src/pages/ChatPage.tsx` | 恢复锚点 ref 与 effect；user 消息悬停操作（修改/复制）+ 时间；右侧导航数据（`outlineItems`）、渲染、点击跳转、scroll-spy、折叠开关；`ChatMessageItem` 传参新增 `createdAt`、`onEdit`、`onCopy`（user）、`data-*` 属性透传 |
| 6 | `apps/dsa-web/src/components/chat/ChatMessageItem.tsx` | 增 `data-message-id/data-role`；user 分支渲染操作行 + 时间；assistant 分支渲染时间；新增 `onEdit` prop |
| 7 | `apps/dsa-web/src/components/chat/ChatOutline.tsx`（新） | 右侧提问导航面板（展示组件） |
| 8 | `apps/dsa-web/src/research.css` | `.chat-thread-area`、`.chat-outline*`、`.chat-msg-time`、user 悬停操作、窄屏折叠浮层等样式 |
| 9 | `apps/dsa-web/src/i18n/uiText.ts` | 新增 `chat.editMessage` / `chat.outlineTitle` / `chat.outlineToggle` / `chat.outlineEmpty` / 时间相关文案（zh + en） |
| 10 | 测试 | `utils/chatTime.test.ts`、`utils/chatScroll.test.ts`；`ChatPage.test.tsx`（恢复锚定、悬停操作、编辑回填、导航渲染与跳转）；`ChatMessageItem.test.tsx`（时间/操作渲染） |
| 11 | 文档 | `docs/CHANGELOG.md` `[Unreleased]` 扁平条目；`docs/full-guide.md` 能力条目（与现有「问股」bullet 并列）；`docs/tasks/chat-history-ux.md`（本文件） |

## 6. 验收用例

1. 新建 3 轮提问的会话 → 切走 → 从侧边栏重新打开 → 视口顶部落在第 3 条 user 消息（或其上方 16px），而不是最底部；且不触发「回到底部」按钮。
2. 打开一个**正在运行**的会话 → 仍贴底跟随流式输出；结束后停留在最后回复底部。
3. 悬停任意 user 消息 → 出现「修改 / 复制」；点击「修改」→ 输入框填入原文并聚焦、不自动发送；修改后发送成为新的一轮。
4. 悬停 assistant 消息 → 出现「复制 / 导出」；每条消息可见相对时间，`title` 为完整「YYYY-MM-DD HH:mm:ss」，切 en 后文案本地化。
5. 会话含 ≥2 条 user 消息 → 右侧出现提问导航，条目为各次提问（截断）+ 时间；点击第 1 条 → 跳到第 1 条提问并高亮；上/下滚动时导航高亮随视口切换。
6. 会话仅 1 条 user 消息 → 右侧导航不出现；输入区与线程宽度正常。
7. 折叠导航 → 状态持久化，刷新后保持；窄屏默认折叠，点开关临时展开为浮层。
8. `zh` / `en` 界面下所有新增文案、时间格式正确。
9. 以上改动不影响既有「发送 / 停止 / 追问 / 报告上下文 / 澄清 / 导出 / 通知」。

## 7. 兼容性与回滚

- 纯前端改动：不新增/变更后端接口字段（`created_at` 已存在），不影响旧客户端与机器人端。
- `Message.createdAt` 为可选字段，旧 hydration 路径缺失时按 `null` 处理，不抛错。
- 回滚方式：`revert` 本 PR（或删除对应提交）即可，无数据迁移、无配置回滚。

## 8. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| 前端 | `cd apps/dsa-web && npm run lint && npm test && npm run build` |
| 滚动/时间工具 | 新增 `utils/chatTime` / `utils/chatScroll` 单测 |
| UI 交互 | `ChatPage.test.tsx` / `ChatMessageItem.test.tsx` 覆盖恢复锚定、悬停操作、编辑回填、导航渲染与跳转 |
| 视觉证据 | 实现时在 PR 描述附「历史会话打开定位到上次提问」「悬停操作」「右侧导航」三组截图（zh/en）；无法截图时给出 Playwright/e2e 产物路径与命令 |
| 文档 | 核对 `docs/CHANGELOG.md`、`docs/full-guide.md` 与实际实现一致 |
