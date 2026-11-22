# 开发任务书：问股「分析过程」可收起 + 展开内容限高滚动

> 状态：已实施  
> 基线：`60d4594a`（feat: 分析运行过程可感知 + 问股首问先判断再提问）  
> 任务类型：`fix`（可收起）+ `feat`（限高滚动）  
> 影响面：问股历史消息的「分析过程」折叠块 / `ChatPage` 展开状态 / i18n / 样式 / 测试  
> 建议单 PR 合入（纯前端，无接口 / 契约变化）

## 0. 结论先行

两个问题，一个根因方向：

1. **展开后收不回去**：`ChatThinkingBlock` 只有「点分类标签」这一种展开方式，没有对应的「收起」入口；`ChatPage.toggleCategory` 只会把消息 id 加进 `expandedThinking`、从不移除，展开态只会累积。
2. **展开一大坨很占空间**：明细区把所有步骤无限制竖排，几十个工具调用会撑满整个消息卡片。

改法：给折叠块加一个显式「收起」动作（并清理残留展开态），把展开后的明细放进一个约 5 行高的滚动容器里，行内单行省略。

## 1. 现状与证据

| # | 现象 | 证据 |
| --- | --- | --- |
| T1 | 没有「收起」入口 | `apps/dsa-web/src/components/chat/ChatThinkingBlock.tsx`：整个组件只有标签按钮 `onClick={() => onToggleCategory(messageId, category)}`（第 228 行），无任何 collapse 控件；全仓 grep「收起/折叠/collapse」在问股思考块内无匹配 |
| T2 | 展开态只增不减 | `apps/dsa-web/src/pages/ChatPage.tsx:1147-1161` `toggleCategory`：`setExpandedCategories` 做增减切换，但 `setExpandedThinking` 只 `next.add(msgId)`、永不删除 |
| T3 | 明细无高度限制 | `ChatThinkingBlock.tsx:245-271`：`{expanded && <div className="space-y-3 animate-fade-in">…}` 直接渲染每个分类的整段 `border-l-2` 明细，无 `max-height` / `overflow` |
| T4 | 明细行不截断 | 同文件第 256-263 行：每行 `<span className="leading-relaxed">{text}×N</span>`，长文案（带 record_count / date_range / cache 后缀）会换行拉高 |
| T5 | 折叠态仍显示全部标签 | 同文件第 216-243 行：标签行无条件渲染，即使从未展开过也占一排（与「占空间」体感相关，见 §3 决策） |

## 2. 目标与非目标

### 2.1 目标

- G1：任一分类展开时，摘要行右侧出现「收起」按钮，点击后整块回到折叠态。
- G2：折叠最后一个分类时，同步清掉该消息的 `expandedThinking`，不残留空展开态。
- G3：展开后的明细放在单个限高（约 5 行）容器内，超出部分滚动查看，不再无限制撑高。
- G4：明细行单行省略，完整文案通过 `title` 提示可见。
- G5：中英文案、深色/浅色主题下滚动条与「收起」按钮观感一致。

### 2.2 非目标

- 不改成「展开全部到新抽屉 / 模态」，保持内联折叠。
- 不改分类归并规则、`summarizeProgressSteps`、计数汇总逻辑。
- 不改消息记忆化结构（`ChatMessageItem` / `ChatThinkingBlock` 的 `memo` 边界不变）。
- 不涉及后端、接口、SSE、落库。

## 3. 交互与视觉规格

### 3.1 状态模型

每个消息维护两个独立维度：

- `expandedThinking: Set<messageId>`——该消息是否处于「展开」状态（有任一分类展开）。
- `expandedCategories: Set<`${messageId}:${category}`>`——具体展开了哪些分类。

约束：`expandedThinking` 中某消息对应的所有分类都被折叠后，必须从 `expandedThinking` 移除该消息 id（T2 修复）。

### 3.2 折叠态（默认）

```
分析过程 · 12 次工具调用 · 3 个分析阶段 · 1 项异常
[K线获取(2)] [基本面分析(1)] [新闻搜索(3)] …   ← 标签行（见 §3.5 决策）
```

- 不显示明细，不显示「收起」。

### 3.3 展开态

```
分析过程 · 12 次工具调用 · 3 个分析阶段 · 1 项异常          [收起]
[K线获取(2)] [基本面分析(1)] [新闻搜索(3)] …
┌─ 限高滚动容器（max-height ≈ 5 行，overflow-y: auto）────┐
│  K线获取                                           ▾   │
│  ✓ 600519 · K线获取 · 240 条记录 · 1.2s              │
│  ✓ 600519 · 分时数据 · 0.8s                          │
│  ✓ 基本面分析 · 1.1s                                  │
│  …（滚动查看更多）                                      │
└────────────────────────────────────────────────────────┘
```

- 「收起」按钮位于摘要行右侧，`aria-label` 明确，`aria-expanded` 反映状态。
- 展开多个分类时，所有明细合并进同一个滚动容器（按分类顺序），容器整体限高，不逐分类各自堆高。

### 3.4 限高与截断

- 容器：`max-height: 9rem`（约 5 行 × 1.5rem 行高 + 上下内边距），`overflow-y: auto`；内容不足 5 行时不出现滚动条、不预留空白。
- 明细行：单行 `overflow: hidden; text-overflow: ellipsis; white-space: nowrap`，`title` 属性放完整文案（含 `×N` 后缀）；`tooltip` 遵循仓库「无原生 title 的治理约束」——改用已有 `Tooltip` 组件，不直接写原生 `title`（见 `tests/ui_governance.test.ts` 对 native title 的检查）。
- 滚动条细条样式（`::-webkit-scrollbar` 8px + `scrollbar-width: thin`），深/浅主题用 `hsl(var(--border))` 轨道、`hsl(var(--muted))` 滑块，避免刺眼。

### 3.5 决策点（建议方案，实施时可确认）

- 标签行是否始终显示？当前始终显示（T5）。建议**保留始终显示**：标签同时承担「展开入口 + 分类概览」，改动面最小；限高只作用于明细区。若产品希望更省空间，可改为「折叠态隐藏标签、只显示摘要 + 展开按钮」，切换成本低（一个条件渲染），但会改变现有交互，需单独确认。本任务书按「保留标签行」编写。

## 4. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `apps/dsa-web/src/pages/ChatPage.tsx` | `toggleCategory` 在「删除最后一个分类」后同步 `setExpandedThinking` 移除该消息 id；新增 `collapseThinking(messageId)`：清空该消息全部 `expandedCategories` 并从 `expandedThinking` 移除；把 `collapseThinking` 传给 `ChatMessageItem` |
| 2 | `apps/dsa-web/src/components/chat/ChatMessageItem.tsx` | props 增加 `onCollapseThinking(messageId)`，透传给 `ChatThinkingBlock` |
| 3 | `apps/dsa-web/src/components/chat/ChatThinkingBlock.tsx` | 摘要行右侧在「有任一分类展开」时渲染「收起」按钮（调 `onCollapse(messageId)`）；明细区外层包一个限高滚动容器；明细行改单行截断 + `Tooltip` |
| 4 | `apps/dsa-web/src/i18n/uiText.ts` | 新增 `chat.thinking.expand` / `chat.thinking.collapse`（zh + en） |
| 5 | `apps/dsa-web/src/research.css`（或 `index.css` 的 `chat-progress` 区块） | 新增 `.chat-thinking-details`（限高 + overflow + 细滚动条）、`.chat-thinking-row`（单行截断）样式 |
| 6 | `apps/dsa-web/src/components/chat/__tests__/ChatThinkingBlock.test.tsx`（新增） | 覆盖：默认折叠不渲染明细；点标签展开；点「收起」回到折叠；明细容器限高存在；展开态清理（无分类展开时 `expandedThinking` 不含该消息） |
| 7 | `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx` | 若既有「展开分类」用例查询方式受新按钮影响则同步更新（保持通过）；新增「收起后 expandedCategories 为空」断言 |

## 5. 验收用例

1. 展开「K线获取」分类：明细出现，摘要行右侧出现「收起」。
2. 点「收起」：明细消失，整块回到折叠态；再点标签可再次展开（幂等）。
3. 同时展开多个分类后点「收起」：全部折叠，不残留。
4. 展开一个分类再点同一个标签折叠：明细消失，且 `expandedThinking` 不再含该消息（无空展开态）。
5. 明细超过 5 行：容器限高、内部滚动；行内单行省略，悬停可见完整文案。
6. 明细不足 5 行：容器自适应、无滚动条、无多余空白。
7. 英文界面「展开/收起」文案正确；深色主题滚动条与「收起」按钮清晰。
8. 运行中的消息（`loading`）与历史消息渲染行为不变，打字 / 其他状态变化不触发历史思考块重渲染（memo 边界不变）。

## 6. 兼容性与回滚

- 纯前端，无接口、schema、存储、配置变化。
- 展开状态仍存于 `ChatPage` 本地 `useState`，不持久化（与现状一致，刷新后默认折叠）。
- 回滚：`revert` 本 PR 即可，无数据迁移或上游联动。

## 7. 验证矩阵

| 改动面 | 验证 |
| --- | --- |
| Web 前端 | `cd apps/dsa-web && npm run lint && npm test -- --run && npm run build`；重点跑 `ChatThinkingBlock.test.tsx`、`ChatPage.test.tsx`、`ui_governance.test.ts` |
| 视觉 | 深/浅主题下截图：折叠态、展开态（<5 行）、展开态（>5 行滚动）、收起后；截图放 PR 描述，不入库 |

## 8. 实施记录

- 「收起」按钮与明细限高滚动已实现；`expandedThinking` 改为由 `expandedCategories` 派生（`expandedMessageIds`），从结构上消除「展开态只增不减」的残留问题。
- §3.5 决策按「标签行始终显示」落地。
- 明细限高 `9rem`（约 5 行），超过滚动；行内单行省略，完整文案用 `Tooltip`（不使用原生 `title`）。
- 新增 `ChatThinkingBlock.test.tsx`（折叠默认态、限高容器、收起回调、标签切换）；`ChatMessageItem.test.tsx` 补充新 prop。
- 验证：`npm run lint`、`npm test -- --run`（120 个测试文件全通过）、`npm run build` 均通过。

## 9. 交付说明（默认结构）

- 改了什么：给「分析过程」加显式「收起」、修复展开态残留、明细限高约 5 行滚动并单行截断。
- 为什么：展开后无收回入口、明细无高度上限撑满卡片。
- 验证情况：上述命令 + 截图。
- 未验证项：无（纯前端，无网络/环境依赖）。
- 风险点：低；需确认 §3.5「标签行是否始终显示」的产品决策。
- 回滚方式：`revert`。
