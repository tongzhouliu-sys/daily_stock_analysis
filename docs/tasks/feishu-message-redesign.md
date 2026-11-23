# 开发任务书：飞书推送消息格式深度优化

> 状态：已实施（P1）
> 基线：待实施前锁定（基于当前 `main` 的 `feishu_card.py` / `feishu_sender.py` / `formatters.py` 现状）
> 任务类型：`feat` + `refactor` + `docs`
> 影响面：飞书卡片渲染（`src/notification_sender/feishu_card.py`）、发送层（`feishu_sender.py`）、格式转换（`src/formatters.py`）、通知编排（`src/notification.py`）、测试、文档
> 关联：飞书卡片与报告 HTML 共用同一份解析（`docs/report-html.md`、`docs/ai/PITFALLS.md` 第 14 条），改动必须联动

---

## 1. 背景与现状

### 1.1 用户诉求

- 「飞书推送的消息格式太丑」，要求做**深度优化**，先出方案/任务书，确认后再实施。
- 期望：一眼能看出「结论 / 评分 / 动作 / 关键点位」，而不是一段被颜色标签和字段网格勉强撑起来的文字墙；长报告不要碎成一串「第 N/M 页」消息。

### 1.2 现有链路

| 环节 | 文件 / 函数 | 现状 |
| --- | --- | --- |
| 格式转换 | `src/formatters.py` `format_feishu_markdown()` | 通用 Markdown → lark_md：`##`→`▎ **标题**`、`###`→`· **标题**`、表格→`• 键：值`、引用→灰色 `<font>`，纯正则 |
| 卡片构建 | `src/notification_sender/feishu_card.py` `build_feishu_card()` | 只收 `content/max_bytes/include_disclaimer`，用 `_SECTION_TITLE_RE` 等正则把 lark_md **重新拆解**成富/轻/紧凑三档卡片 |
| 发送路由 | `src/notification_sender/feishu_sender.py` `FeishuSender` | Webhook 优先，App Bot 次之；`_send_feishu_message` / `_app_send_once` 均「先卡片、失败回退纯文本」 |
| 结构化数据 | `src/notification.py` 约 2905-2914 行 | 已组装 `render_context = {route_type, severity, report_type, results, structured_payload}` 传给飞书分支 |
| 结构化数据消费 | `src/notification_sender/feishu_sender.py` `send_to_feishu()` | **只用 `render_context.route_type` 判断是否补声明**；`structured_payload` 从未进入卡片构建 |
| 单股推送上下文 | `src/core/pipeline.py` 约 3992-3994 行 | 注释 F2「供飞书结构化渲染使用」，实际未落地 |
| 分享图数据 | `src/share_image.py` | 已从 `structured_payload` 解析出评分/动作/趋势/点位/维度并渲染成海报，飞书完全没复用 |
| 报告 HTML | `src/notification_sender/feishu_card.py` `parse_report_document()` | 与飞书卡片共用同一份章节/字段/脚注解析 |

### 1.3 需要解决的问题（根因）

1. **结构化数据「到站即弃」**：`structured_payload`（`action_label` / `sentiment_score` / `dashboard.battle_plan.sniper_points` / `trend.trend_score` / 维度评分 / 大盘阶段，字段以 `share_image.py` 现有解析为准）已经在通知链路里算好，飞书卡片却退回正则解析 Markdown。既浪费、又脆弱。
2. **视觉单一**：只用了 `<text_tag>` 颜色 + `fields` 网格，没有评分分级、动作徽章、并排对比、按钮、图片；「决策仪表盘 / 作战计划」这类结构化内容被拍平成文本。
3. **正则二次解析脆弱**：LLM 输出格式一旦漂移（标题层级、`• 键：值` 断行、表格列数），章节/字段解析错乱，标题回退到通用「📊 股票智能分析报告」。
4. **长报告碎片化**：超过 `FEISHU_MAX_BYTES` 就切成 N 条带「📄 N/M」的消息，无法一屏看全、上下文断裂。
5. **零交互**：卡片没有「查看完整报告 / 打开 K 线图 / 跳 Web」等 action，用户只能被动读卡片。

---

## 2. 目标与非目标

### 2.1 目标

- G1：`build_feishu_card()` 接收结构化渲染上下文（`render_context` / `structured_payload` 归一化子集），有结构化数据时优先结构化渲染，正文仍作 fallback。
- G2：单股报告顶部渲染「信号区」：八态动作徽章 + 0–100 评分分级色 + 趋势，关键点位（理想买 / 止损 / 目标）用高对比三列卡。
- G3：大盘 / 聚合报告顶部渲染市场阶段 / 情绪摘要，摘要区保持现有「共分析 N 只 · 🟢🟡🔴」副标题并强化。
- G4：个股摘要列表用 `column_set` 网格（名称 + 动作 + 评分），替代逐条 `• 名称：观望 | 评分 58 | 震荡` 长文本。
- G5：长报告提供「摘要卡片 + 完整报告入口」（跳 Web 历史 HTML / 飞书云文档 / App Bot 发文件），不再默认碎成 N 条；保留分片作为降级路径。
- G6：卡片支持交互 action（查看完整报告 / 打开分享图），App Bot 链路可嵌入分享图 PNG。
- G7：富卡片→轻卡片→紧凑卡片→纯文本降级链**保持不变**；结构化渲染失败时回退文本解析，任何路径不丢正文信息。
- G8：`feishu_card.py` 与报告 HTML 的共用解析不漂移；新增结构化渲染逻辑不改动既有 `parse_report_document()` 的输出契约。

### 2.2 非目标

- 不升级到飞书 Card 2.0（`schema` 版卡片）：Webhook 自定义机器人只支持 Card JSON 1.0，为两条链路统一，保持 1.0。
- 不改 LLM Prompt、分析 / 评分 / 买卖建议逻辑、`structured_payload` 的产出字段。
- 不改飞书 Stream Bot 交互回复链路（`bot/platforms/feishu_stream.py`）。
- 不新增第三方依赖；复用 `lark-oapi`（已存在）与现有分享图生成链路。
- 不新增用户可配置的「卡片风格」开关，避免配置面继续膨胀；默认启用新渲染，仅内部实现可回退。
- 不把评分换算成新的多空占比或生成额外分数；只展示报告已有的 0–100 评分与八态动作。

---

## 3. 设计方向

### 3.1 结构化优先渲染

新增一个「结构化信号区」渲染器，输入为归一化后的结构化数据，输出卡片头部若干元素。核心字段与 `share_image.py` 复用同一套解析口径（不复制、只提取）：

- 动作：`action_label` / `operation_advice`（八态之一，映射到固定色板：买入=绿、卖出=红、加仓/持有观察=蓝、减仓/观望=橙/灰）。
- 评分：`sentiment_score` 或 `trend.trend_score`（0–100，分级：≥70 绿 / 50–69 黄 / <50 红）。
- 点位：`dashboard.battle_plan.sniper_points` 的 `ideal_buy` / `stop_loss` / `take_profit`。
- 趋势：`trend` 的趋势方向与温度。
- 大盘：`market_phase_summary.phase` / 市场情绪灯（`share_image.py` 的 `_market_light` 已有解析）。

信号区只放「已有值」的字段；某字段缺失时自动隐藏，不渲染空占位。正文（`content`）仍作为信号区下方的完整内容，保证信息不丢。

### 3.2 卡片组件升级（仅 Card JSON 1.0 官方组件）

- **动作徽章**：用独立 `div` + 分色 `<font>` / `<text_tag>` 渲染成「色块徽章」，与标题栏 `template` 主题色一致。
- **评分**：以大号分色数字呈现（`<font color=...>**58**/100</font>`），不依赖非标准 `progress` 组件；若评审希望真进度条，需评估 Card 2.0，默认不做。
- **点位三列卡**：`column_set` + 三 `column`，每列标题 `🎯 理想买 / 🛑 止损 / 🎯 目标`，值用高对比色。
- **个股摘要网格**：`column_set` 双列，每格「emoji + 名称」行 + 「动作 · 评分」行。
- **标题栏**：`header.template` 按报告类型主题色 + 报告自身 H1 标题 + 摘要副标题（现有逻辑保留，强化 emoji 与主题色映射）。

### 3.3 长内容呈现

按优先级：

1. **摘要卡 + 完整报告入口**（推荐）：卡片只渲染信号区 + 摘要 + 首屏正文，底部 `action` 放「查看完整报告」按钮。
   - 有 Web 历史报告 HTML（`/api/v1/history/{id}/report-html`）时，按钮跳该链接（App Bot 用 `button` + `url`，Webhook 用 `action` 支持 `url`）。
   - 已配置飞书云文档（`FEISHU_APP_ID` + `FEISHU_FOLDER_TOKEN`）时，走云文档链接。
   - App Bot 模式可「发送文件」（`send_feishu_file` 已存在）承载完整 Markdown。
2. **分片降级**：仍超限且无上述入口时，才退回现有分片，但改进分页观感（首页卡注明「共 N 页」，非末页底加「未完待续」）。

### 3.4 交互增强

- 卡片 `action` 增加 `button`：`查看完整报告`、`打开 K 线图`。
- 分享图 PNG 已在 `notification.py` 生成（`image_bytes`），App Bot 模式先 `im.v1.image.create` 拿 `img_key`，再在卡片用 `img` 组件嵌入；Webhook 自定义机器人不支持图片上传，走「外链图片 / 无图」降级。

### 3.5 降级链（保持不变 + 新增一层）

```
结构化富卡片（信号区 + 字段/网格 + action）
  → 富卡片（现有分节 + 语义色 + 字段）
  → 轻卡片（现有）
  → 紧凑卡片（现有）
  → 纯文本兜底（现有）
```

结构化信号区只在「富卡片」一级尝试；任一降级路径都不丢正文信息。

---

## 4. 改动清单

### 4.1 卡片渲染层 `src/notification_sender/feishu_card.py`

- `build_feishu_card()` 新增可选参数 `render_context: Optional[Dict[str, Any]] = None`（或归一化后的 `structured_payload` 子集），**默认 None 时行为与现状逐字节一致**。
- 新增 `_build_signal_zone(payload) -> List[Dict]`：从结构化数据提取动作 / 评分 / 点位 / 趋势 / 大盘阶段，生成信号区元素；字段缺失自动隐藏。
- 新增 `_build_action_buttons(...) -> Optional[Dict]`：生成「查看完整报告 / K 线图」`action`。
- 新增 `_build_stock_summary_grid(...)`：把个股摘要列表渲染成 `column_set` 网格。
- 复用并保持 `parse_report_document()` / `plan_field_grid()` / `_dedupe_notes()` 输出契约不变；`_SECTION_COLOR_RULES`、动作色板、评分分级色板抽成模块级常量供 HTML / 卡片共用（**需同步跑 `tests/test_report_html_renderer.py`**）。

### 4.2 发送层 `src/notification_sender/feishu_sender.py`

- `send_to_feishu()` 把 `render_context` 透传到 `_build_card_body()` → `build_feishu_card()`，不再只取 `route_type`。
- `_send_via_webhook()` / `_send_via_app_bot()` / `_app_send_once()` 同步透传渲染上下文。
- 长内容：在 `_send_via_app_bot` / `_send_via_webhook` 超长分支增加「摘要卡 + 完整报告入口」策略；入口不可用时回退现有 `_send_feishu_chunked` / `_app_send_chunked`。
- App Bot 图片嵌入：`send_to_feishu` 增加可选 `image_bytes`，先 `im.v1.image.create` 上传拿 `img_key` 再嵌入卡片（无图 / 上传失败时静默降级）。

### 4.3 通知编排 `src/notification.py`

- `_send_to_static_channel` 飞书分支除 `render_context` 外，把已生成的 `image_bytes` 一并传给 `send_to_feishu`（当前飞书不在 `channels_needing_image`，不改变其它渠道逻辑）。

### 4.4 格式转换 `src/formatters.py`

- 仅做必要微调（若信号区已结构化渲染，避免正文与信号区重复展示动作/评分行，需在结构化渲染时对正文做「去重行」处理，而非改通用转换）。
- `format_feishu_markdown()` 主逻辑保持兼容，不做破坏性重写。

### 4.5 文档

| 文件 | 改动 |
| --- | --- |
| `docs/notifications.md`「飞书消息卡片排版」节 | 补结构化信号区、长内容入口、交互 action、图片嵌入说明 |
| `docs/report-html.md` | 说明卡片结构化渲染与 HTML 共用解析的新增共用点 |
| `docs/bot/feishu-bot-config.md` | 补「长报告入口 / 图片嵌入」配置与效果说明 |
| `docs/ai/PITFALLS.md` | 更新第 14 条，新增「结构化信号区与动作色板改动需联动 HTML 报告」 |
| `docs/CHANGELOG.md` | `[Unreleased]` 扁平格式追加 `[改进]` / `[文档]` 条目 |
| `docs/ai/CODEMAP.md` | 跑 `python scripts/gen_ai_codemap.py` 重新生成 |

---

## 5. 关键实现约束

- **默认兼容**：`build_feishu_card(content)` 不带 `render_context` 时输出必须与现状逐字节一致（用现有 `tests/test_feishu_card.py` 全量断言守护）。
- **信息不丢**：结构化信号区只做「摘要式增强」，完整正文仍保留在卡片 `div`；任何降级路径都保留正文。
- **单一真源**：动作色板、评分分级、点位标签与 `share_image.py` / `report_language.py` 复用同一份常量与 label，不各自拼色值。
- **HTML 联动**：改 `feishu_card.py` 的章节解析 / 字段布局 / 脚注去重 / 主题映射 / 色板时，必须同步跑 `tests/test_report_html_renderer.py`。
- **只展示已有值**：不凭空生成评分、多空占比或额外点位；`battle_plan.sniper_points` 缺值即隐藏。
- **Webhook / App Bot 能力差异**：Webhook 自定义机器人不支持图片上传与部分 action，需在发送层按模式降级，不报错。
- **不扩配置面**：默认启用新渲染，无新增 `.env` 开关；回退走代码 revert。

---

## 6. 验收标准

### 6.1 结构化信号区（单股）

- 报告含 `action_label` / 评分时，卡片顶部出现「动作徽章 + 评分分级色 + 趋势」，与分享图同源一致。
- 报告含 `battle_plan.sniper_points` 时，出现三列「理想买 / 止损 / 目标」高对比卡。
- 任一字段缺失时对应块自动隐藏，不出现空占位或 `N/A`。

### 6.2 摘要网格与大盘

- 个股摘要列表以 `column_set` 网格呈现「名称 + 动作 + 评分」，替代逐条长文本。
- 大盘复盘卡片顶部出现市场阶段 / 情绪灯摘要（有值时）。

### 6.3 长内容入口

- 有「完整报告入口」时，长报告不再默认碎成 N 条；卡片底部出现「查看完整报告」按钮并可跳转。
- 无入口时仍回退分片，分片带「共 N 页 / 未完待续」提示。

### 6.4 交互与图片

- 卡片 action 按钮可点击跳转完整报告 / K 线图。
- App Bot 模式嵌入分享图 PNG 成功；Webhook 模式无图不报错。

### 6.5 兼容与降级

- `build_feishu_card(content)`（无 context）输出与现状逐字节一致。
- 富卡片→轻卡片→紧凑卡片→纯文本降级链不回归；结构化渲染失败回退文本解析。
- 关键词、加签、分片预算、`send_feishu_file` 不回归。

### 6.6 质量门禁

- `python -m pytest -m "not network"` 通过；`./scripts/ci_gate.sh` 通过。
- `tests/test_report_html_renderer.py` 通过（共用解析不漂移）。
- `python scripts/check_ai_assets.py`、`python scripts/gen_ai_codemap.py --check` 通过。
- PR 描述附 before/after 截图：单股富卡片、大盘卡片、聚合摘要卡、长报告入口、纯文本兜底。截图只放 PR 描述 / 评论 / artifact / 外链，不入库。

---

## 7. 测试矩阵

| 测试文件 | 更新 / 新增 |
| --- | --- |
| `tests/test_feishu_card.py` | 新增：信号区渲染、动作徽章、评分分级、点位三列卡、摘要网格、字段缺失隐藏、长内容入口、`build_feishu_card` 无 context 兼容性 |
| `tests/test_feishu_f1_hotfix.py` | 新增：结构化渲染失败回退文本、分片降级提示、纯文本兜底仍带声明 |
| `tests/test_report_html_renderer.py` | 守护共用解析不漂移；色板 / 动作常量改动后的 HTML 一致性 |
| `tests/test_notification_sender.py` | 新增：`send_to_feishu` 透传 `render_context` / `image_bytes`；Webhook 与 App Bot 能力差异降级 |
| `tests/test_notification.py` | 新增：飞书分支把 `image_bytes` 传入 sender；其它渠道行为不变 |
| `tests/test_formatters.py` | 守护 `format_feishu_markdown` 输出不变（若未改则仅回归） |

---

## 8. 实施顺序

1. **基线锁定**：从最新 `main` 拉分支，记录当前飞书卡片截图作为 before；锁定 `render_context.structured_payload` 实际字段样例。
2. **色板 / 常量单一真源**：抽动作色板、评分分级、点位标签，与 `share_image.py` / `report_language.py` 对齐；跑 HTML 联动测试。
3. **结构化信号区**：`feishu_card.py` 加 `render_context` 入参与 `_build_signal_zone`，先覆盖单股，再大盘 / 聚合。
4. **摘要网格与交互**：`column_set` 摘要网格、action 按钮、App Bot 图片嵌入。
5. **长内容入口**：摘要卡 + 完整报告入口，回退分片保留。
6. **发送层透传**：`feishu_sender.py` 透传 context / image_bytes，`notification.py` 传 image_bytes。
7. **文档与治理**：更新 `notifications.md` / `report-html.md` / `feishu-bot-config.md` / `PITFALLS.md` / `CHANGELOG`，重生成 CODEMAP。
8. **全量验证**：跑 §7 测试矩阵 + `ci_gate.sh`；补 before/after 截图。
9. **提交与 PR**：按 `AGENTS.md` 交付结构写 PR body；未经确认不 `git commit` / `git push` / `gh pr create`。

---

## 9. 分期

- **P1（本次核心，视觉优先）**：结构化信号区（动作徽章 / 评分分级 / 点位三列卡）+ 摘要网格 + 发送层透传。单股 / 大盘 / 聚合三类报告视觉显著提升，零新增依赖、零配置面。
- **P2（长内容）**：摘要卡 + 完整报告入口（Web 历史 HTML / 云文档 / 发文件），分片降级改进。
- **P3（交互 / 图片）**：action 按钮 + App Bot 分享图 PNG 嵌入。

P2 / P3 依赖「完整报告链接 / 云文档 / 图片上传」等既有能力的接线，可独立评审、独立回滚。

---

## 10. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 结构化字段与分享图口径不一致 | 卡片与海报评分 / 动作对不上 | 单一真源抽常量 + 复用 `share_image.py` 解析口径 + 契约测试 |
| 结构化渲染破坏正文信息 | 报告内容缺失 | 信号区只做摘要增强，正文始终保留；降级链不丢信息 |
| `build_feishu_card` 无 context 输出漂移 | HTML 报告与既有飞书卡片回归 | 全量现有断言守护逐字节兼容；HTML 联动测试 |
| Webhook 不支持图片 / 部分 action | 发送报错 | 按模式降级，Webhook 无图 / 简化 action，不报错 |
| 长内容入口不可用（无 Web 历史 / 未配云文档） | 回退分片 | 保留现有分片为最终降级，并改进分页观感 |
| 色板改动导致 HTML 与卡片不一致 | 视觉漂移 | 色板抽常量 + `test_report_html_renderer.py` 守护 |

回滚方式：整体 `revert` 本次 PR 即可。无数据库 schema 变更、无数据迁移、无新增依赖；`build_feishu_card` 保持向后兼容，回滚后飞书与 HTML 均退回现状。

---

## 11. 待确认项

| # | 问题 | 建议默认 | 影响 |
| --- | --- | --- | --- |
| 1 | 评分分级色阈值（≥70 绿 / 50–69 黄 / <50 红）是否与分享图一致 | 与分享图 `_tone_for_score` 一致 | 视觉一致性 |
| 2 | 长报告入口优先级：Web 历史 HTML vs 飞书云文档 vs 发文件 | Web 历史 HTML > 云文档 > 发文件 > 分片 | 决定 P2 接线 |
| 3 | 是否需要真正进度条（依赖 Card 2.0） | 不做，用分色大数字 | 是否引入 2.0 |
| 4 | 动作八态色板具体映射 | 按分享图 `_tone_for_action` 口径，实施前定稿 | 动作徽章配色 |
| 5 | 图片嵌入是否只做 App Bot（Webhook 无图） | 是，Webhook 无图 | 能力边界 |
| 6 | 是否新增「卡片风格」用户开关 | 否，默认启用 | 配置面 |

---

## 12. 交付要求

- PR 标题建议：`feat: 飞书推送消息结构化卡片优化`。
- PR 描述按 `AGENTS.md` 写清：改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式。
- 必须附 before/after 可视证据：单股富卡片、大盘卡片、聚合摘要卡、长报告入口、纯文本兜底。
- 截图只放 PR 描述 / 评论 / Actions artifact / 外链，不入库。
- 未经明确确认，不执行 `git commit`、`git tag`、`git push`、`gh pr create`。
- 实施完成后同步更新 `docs/CHANGELOG.md` 的 `[Unreleased]`，使用扁平格式，例如：
  - `- [改进] 飞书推送升级结构化卡片：信号区（动作徽章/评分分级/点位三列卡）、摘要网格与长报告入口。`
  - `- [文档] 新增飞书消息格式深度优化开发任务书（docs/tasks/feishu-message-redesign.md）。`

---

## 13. P1 落地说明（与任务书的差异）

P1 已按本任务书落地，核心改动：

- `src/notification_sender/feishu_card.py`
  - `build_feishu_card()` 新增 `render_context` 参数；缺省 `None` 时输出与旧版逐字节一致（由 `tests/test_feishu_card_document.py` 三份基线快照守护）。
  - 新增结构化信号区：`_build_signal_zone()` / `_extract_stock_signal()` / `_extract_market_signal()` / `_render_stock_signal_zone()` / `_render_market_signal_zone()` / `_columns_element()`。
    - 个股：`column_set` 并排「操作（八态色）/ 评分（0–100 分级色）/ 趋势」，第二行「🎯 理想买 / 🛑 止损 / 🎯 目标」。
    - 大盘：`market_light` 渲染「市场情绪 / 情绪分 / 温度」。
    - 评分阈值对齐 `share_image.py::_tone_for_score`（>60 绿 / <40 红 / 其余橙）；动作色板对齐买入绿、卖出红、其余灰。
  - 信号区只读结构化字段；字段缺失自动隐藏；结构化渲染异常静默回退文本卡片。
  - 富卡片、轻卡片、紧凑卡片三级都尝试带信号区；紧凑卡片超预算时先退回「无信号区紧凑卡」，最后才由发送层纯文本兜底。
- `src/notification_sender/feishu_sender.py`
  - `render_context` 从 `send_to_feishu()` 透传到 `_send_via_webhook()` / `_send_via_app_bot()` → `_send_feishu_message()` / `_app_send_once()` → `_build_card_body()`。
  - 分片发送时信号区只作用于首片，避免每片重复。
- `tests/test_feishu_card.py`
  - 新增信号区渲染、缺失字段隐藏、评分/动作阈值、大盘信号区、Webhook / App Bot 透传用例；`build_feishu_card` 无 `render_context` 的兼容性由既有 52 个测试与基线快照守护。

与任务书的差异 / 说明：

1. **摘要网格（G4）无需额外改动**：现有 `_render_blocks(summary_mode=True)` 已把 `⚪ **名称**：动作 | 评分 N | 趋势` 解析为 `fields` 双列网格，等价于任务书描述的「名称 + 动作 + 评分」网格，未重复造轮子。
2. **P2 / P3 未在本次落地**：长内容「摘要卡 + 完整报告入口」需要 `render_context` 携带历史报告 URL / 云文档 token，分享图 PNG 嵌入需要把飞书加入图片生成链路并接 `im.v1.image.create`，均涉及更广的接线，按任务书分期留待独立 PR。
3. **信号区不删除正文重复行**：为保证「任何降级路径不丢正文信息」，正文仍保留完整内容；信号区定位为顶部「一眼结论」，与正文详情并存。
4. **仅 Card JSON 1.0 组件**：使用 `column_set` / `column` / `div` / `text_tag` / `font`，未引入 Card 2.0 或 `progress` 等非标准组件。


---

## 14. P1.1 迭代：原生表格 + UTF-8 请求体（2026-11-23）

线上反馈「卡片仍像一大段文字、疑似没渲染成功」，排查结论与改动：

- **根因 1：组件数超限**。3 只股票的真实报告生成 64 个元素，超过旧的 `_MAX_ELEMENTS=60`，富卡片直接降级为「轻卡片」（按 `##` 合并成 4 块纯文字），视觉上就是文字墙。现在数值内容改用原生 `table` 后，同一份报告降到 40 余个元素，`_MAX_ELEMENTS` 放宽到 80，最终仍以 `max_bytes` 的真实 JSON 体积为准。
- **根因 2：Webhook 请求体被转义**。`requests.post(json=...)` 默认把中文转义为 `\uXXXX`（6 字节/字符），卡片真实体积比预算口径大约 1.4 倍，容易被飞书 20 KB 限制拒绝后回退纯文本。现在统一走 `_encode_webhook_request_body()`（`ensure_ascii=False` + UTF-8 bytes），大小计量与发送使用同一口径，并为外层 `msg_type/card/timestamp/sign` 预留 512 字节。
- **表格方案**：Card JSON 1.0 原生 `table`（飞书 7.4+，不需要 Card 2.0）：
  - 多标的摘要 → 「标的 / 动作 / 评分 / 趋势」四列表。
  - 个股数值 → 「分类 / 指标 / 数值」三列表，放在股票标题下方；按「点位 → 行情 → 技术 → 盘中」排序，同名指标去重，同分类只在首行写分类。
  - 单卡最多 5 张表；超出时后续板块回退 `fields`。长策略、观察条件、来源说明等长文本不进表。
  - 表格只放根节点（飞书限制：不能嵌套）。
- **兼容性**：`table` 在飞书 < 7.4 客户端显示「请升级客户端」占位图，其余组件不受影响；`lark_md` / `markdown` 类型的表格单元格需要更高版本，因此全部使用 `text` 类型，并在入表前去掉 lark_md 标记。
- **回归**：`tests/test_feishu_card.py` 新增摘要表、指标表、去重、长文本不进表、5 表上限、单元格纯文本断言；`tests/test_feishu_f1_hotfix.py` 覆盖 UTF-8 请求体与 App Bot 超限不发送 `null`。
