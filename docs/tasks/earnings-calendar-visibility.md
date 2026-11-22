# 开发任务书：财报日历「可见性」落地（分析页卡片 + 问股徽章 + 通知/飞书）

> 状态：已实施（P1）
> 基线：`origin/main` @ `60d4594a`（财报日历数据层已在 `d4b67bc7` 合入，其后 main 又前进）
> 任务类型：`feat`
> 影响面：API schema / report_builder / data_processing / 通知渲染 / 问股 answer_meta / Web 报告页与聊天页 / i18n / tests / docs
> 背景依据：`docs/tasks/earnings-calendar.md`、`src/utils/data_processing.py`、`api/v1/report_builder.py`、`api/v1/schemas/history.py`、`src/notification.py`、`src/notification_sender/feishu_card.py`、`src/agent/answer_editor.py`、`api/v1/endpoints/agent.py`

---

## 1. 背景与问题

### 1.1 现状（数据层已合入，但不可见）

上一任务已把「下一次财报披露日期」标准化为 `earnings.calendar`（`next_earnings_date` / `days_to_next` / `events` / `coverage` / `as_of`），但只进入两个非 UI 消费面：

| 消费面 | 代码锚点 | 是否用户可见 |
| --- | --- | --- |
| 分析 LLM Prompt「业绩与筹码事件」表 | `src/analyzer.py::_build_earnings_and_chip_event_rows` | 否 |
| 问股工具返回 | `src/agent/tools/data_tools.py::get_stock_info` → `earnings.calendar` | 否 |

Web / API / 通知侧没有任何结构化呈现：

- `extract_fundamental_detail_fields` 只抽 `financial_report` / `dividend`。
- `ReportDetails` schema 无 calendar；`ReportDetails.tsx` 只渲染 raw JSON / 快照。
- 通知 `_get_fundamental_blocks` 只抽 financial_report / growth / dividend / boards，无 calendar。
- 问股 `AnswerMeta` 无 calendar；`ChatAnswer` 只渲染正文与来源引用。

### 1.2 用户可见症状

分析页、问股页、通知/飞书卡片都看不到「下次财报披露日期」；A股在财报季间隙（如 600519：三季报已披露、年报预约未发布）时 `next_earnings_date=None`，即使数据层也无内容。

### 1.3 目标

- **G1** 分析报告页新增结构化「财报日历」卡片，三态（有未来日期 / 仅已披露 / 空）。
- **G2** 问股回答新增「下次财报」徽章，来自 `get_stock_info` 工具结果。
- **G3** API `ReportDetails.earnings_calendar` 三处一致。
- **G4** 通知与飞书卡片展示「下次财报披露日期」。
- **G5** 纯呈现 + 最小透传；不改数据层、不落库、不加配置；空值口径按「展示最近已披露」处理。

### 1.4 非目标

- 不改 `earnings.calendar` 取数/标准化。
- 不新增表/迁移；不把日期纳入评分/仓位。
- 不做「预计披露窗口估算」（P3）；不新增独立页面。
- 不把原始 payload / DataFrame / 内网信息写进 API、日志、通知或前端。

---

## 2. 契约

### 2.1 API：`ReportDetails.earnings_calendar`

透传 `fundamental_context.earnings.data.calendar`（5 个低敏字段）。抽取点 `extract_fundamental_detail_fields`，schema `history.py::ReportDetails`，组装 `report_builder.py`。空/非 dict → `None`。

### 2.2 Web 分析页

`types/analysis.ts` 增 `EarningsCalendar` / `EarningsCalendarEvent`；新增 `ReportEarningsCalendar.tsx`（有值/仅已披露/空三态）；`ReportSummary.tsx` 在 `ReportOverview` 后插入；文案 `reportLanguage.ts` zh/en/ko。

### 2.3 问股 answer_meta 徽章

`runner.py` 从 `get_stock_info` 结果提取受限快照 → `AnswerMeta.earnings_calendar`（`schemas/agent_intake.py`）→ `_public_answer_meta` 白名单 → `ChatAnswer` 徽章 + `agentIntake.ts` 类型。

### 2.4 通知 + 飞书卡片

- `src/notification.py::_get_fundamental_blocks` 增 `earnings_calendar`（读 `ctx["earnings"]["data"]["calendar"]`）。
- `_append_fundamental_blocks` 增 `_append_earnings_calendar`，渲染 `### 📅 财报日历` 块（下次披露日期 + 距今 + 「预计」；无下次但有已披露时显示「最近披露 + 暂无预约」；无数据不渲染）。
- `report_language.py` 增 label（zh/en/ko）。
- **飞书卡片不改**：`feishu_card.py` 是通用 Markdown→卡片解析器（`_parse_sections` 按 `###` 分 section），通知 Markdown 新增块会被自动渲染为卡片 section；`feishu_sender.py` 复用同一份通知 Markdown。仅在必要时补飞书卡片单测（若已有 section 级渲染快照需同步）。
- 覆盖路径：`generate_dashboard_report`（1608）、`generate_single_stock_report`（2149）走 `_append_fundamental_blocks`；微信 dashboard/summary 若走独立渲染，列为 P2 补充。

### 2.5 A股空值口径

`next_earnings_date=None` 且 `events` 仅含 `disclosed`：显示「最近披露 YYYY-MM-DD（已披露）；暂无已发布的预约披露日期」。完全无 calendar：不渲染。

---

## 3. 实现清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `src/utils/data_processing.py` | `extract_fundamental_detail_fields` 增 `earnings_calendar` |
| 2 | `api/v1/schemas/history.py` | `ReportDetails.earnings_calendar` |
| 3 | `api/v1/report_builder.py` | 传 `earnings_calendar` |
| 4 | `src/notification.py` | `_get_fundamental_blocks` 增 calendar；`_append_earnings_calendar` |
| 5 | `src/report_language.py` | 通知 label（zh/en/ko） |
| 6 | `src/agent/runner.py` | `get_stock_info` 结果提取受限快照 |
| 7 | `src/schemas/agent_intake.py` | `AnswerMeta.earnings_calendar` |
| 8 | `src/agent/chat_executor.py`、`orchestrator.py` | 把快照写入 `AnswerMeta` |
| 9 | `api/v1/endpoints/agent.py` | `_public_answer_meta` 白名单增 `earnings_calendar` |
| 10 | `apps/dsa-web/src/types/analysis.ts` | `EarningsCalendar` 类型 |
| 11 | `apps/dsa-web/src/components/report/ReportEarningsCalendar.tsx`（新） | 卡片组件 |
| 12 | `apps/dsa-web/src/components/report/ReportSummary.tsx` | 插入卡片 |
| 13 | `apps/dsa-web/src/utils/reportLanguage.ts` | zh/en/ko label |
| 14 | `apps/dsa-web/src/api/agentIntake.ts` | `AnswerMeta.earnings_calendar` 类型 |
| 15 | `apps/dsa-web/src/components/chat/ChatAnswer.tsx` | 「下次财报」徽章 |
| 16 | `tests/` | 见 §5 |
| 17 | `docs/ai/DATA-FLOW.md`、`docs/CHANGELOG.md` | 可见性说明 |

---

## 4. 验收用例

1. 分析 `AAPL`：报告页「财报日历」卡片显示 `2027-01-28（预计 · 67 天）`，来源 yfinance。
2. 分析 `600519`：卡片显示「最近披露 2026-10-31（已披露）；暂无已发布的预约披露日期」，不伪造未来日期。
3. 港股/ETF/无基本面：不渲染卡片，报告正常。
4. 问股「AAPL 什么时候财报」：回答下方「下次财报」徽章；历史消息仍显示。
5. 问股未调 `get_stock_info` 或无日历：不渲染徽章。
6. 历史/同步/任务状态三处 `details.earnings_calendar` 一致。
7. 通知（dashboard/单股）出现「📅 财报日历」块；飞书卡片出现对应 section；无数据不出现。
8. 不泄露原始 payload/内网/密钥；旧记录无字段正常读取。

---

## 5. 验证矩阵

- 后端：`pytest tests/test_history_service.py tests/test_analysis_api_contract.py tests/test_notification*.py tests/test_agent_chat_executor.py` + 新增 `test_earnings_calendar_visibility.py`；`py_compile` / `flake8`。
- 前端：`cd apps/dsa-web && npm run lint && npm run build`；新增 `ReportEarningsCalendar.test.tsx` 与 `ChatAnswer` 徽章测试。
- 三语核对；完整 `./scripts/ci_gate.sh`。

---

## 6. 兼容性与回滚

纯追加字段/组件；旧记录缺失→不渲染。不改数据层/配置/迁移语义。回滚 revert 本 PR。

---

## 7. 分期

- P1（本次全部）：API + 分析页卡片 + 问股徽章 + 通知/飞书 + i18n + 测试。
- P2（后续）：微信 dashboard/summary 财报日历（若走独立渲染）；A股「预计披露窗口」估算。

---

## 8. 已确认范围（用户拍板）

1. 问股徽章与 P1 一起做 ✅
2. A股空值展示「最近已披露」一行 ✅
3. 通知/飞书卡片一起做 ✅（通知 Markdown 新增块，飞书解析器自动跟随，不改 feishu_card.py）
4. 服务器已部署含数据层的最新 main ✅
