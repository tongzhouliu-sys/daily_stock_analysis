# FLOWS · 三条主链路的步骤与代码锚点

> 手写维护。改了链路顺序、入口或落库点时同步更新本文件。
> 行号会漂移，锚点以「文件 + 函数名」为准，行号只作快速跳转提示。

## 1. 单股分析（CLI / 定时任务 / 服务内触发）

```
main.py::main → StockAnalysisPipeline.run → fetch_and_save_stock_data
            → Analyzer.analyze → 报告文本 → _send_notifications → 落库/推送
```

| 步骤 | 代码锚点 | 说明 |
| --- | --- | --- |
| 1. 启动与参数 | `main.py:1740 main()` | 解析 `--stocks`、`--debug`、`--dry-run`、`--market-review`、`--schedule`、`--serve`；`--dry-run` 只跑分析不发通知 |
| 2. 装配依赖 | `src/core/pipeline.py:269 class StockAnalysisPipeline` | 持有 `DataFetcherManager`、`StockAnalyzer`、`NotificationService`、`DatabaseManager` |
| 3. 单股主循环 | `src/core/pipeline.py:3646 run()` | 遍历股票列表；单股失败只记录并继续，不拖垮批次 |
| 4. 取数并落库 | `src/core/pipeline.py:419 fetch_and_save_stock_data()` | 走 `data_provider` failover；盘中未收盘的当日 K 线不落库 |
| 5. 分析 | `src/analyzer.py:4196 analyze()` | 组装上下文（行情 / 历史 / 新闻 / 基本面）→ LLM → 结构化结果 |
| 6. 报告组装 | `src/core/pipeline.py:4229 _generate_aggregate_report()` | 单股直接使用结果；批量时拼聚合报告；报告语言由 `REPORT_LANGUAGE` 决定 |
| 7. 通知 | `src/core/pipeline.py:4055 _send_notifications()` → `src/notification.py NotificationService.send_with_results()` | 渠道分支只在 `notification.py`；单渠道失败记录诊断后继续 |
| 8. 历史落库 | `src/storage.py:3156 save_analysis_history()` | Web 历史页与报告详情读的是这条记录 |

调度模式下（`--schedule`）第 3 步由调度器按 `SCHEDULE_TIME` 触发；保存调度配置只重建调度线程，不杀正在跑的分析。

## 2. 问股一次提问（Web / 桌面端）

```
ChatPage 发送 → POST /api/v1/agent/chat/stream (SSE)
            → 会话状态与澄清 → AgentOrchestrator.chat
            → 工具调用事件流 → 前端进度区/消息区 → 会话落库
```

| 步骤 | 代码锚点 | 说明 |
| --- | --- | --- |
| 1. 输入与发送 | `apps/dsa-web/src/pages/ChatPage.tsx`、`apps/dsa-web/src/components/chat/ChatInputArea.tsx` | Enter 发送，输入法组合中不发送；发送前可用 `skills` 指定策略 |
| 2. 请求 | `apps/dsa-web/src/api/agent.ts`（`/api/v1/agent/chat/stream`、`/chat/send`） | 流式入口用于进度展示；`/chat/send` 为非流式兼容入口 |
| 3. 端点 | `api/v1/endpoints/agent.py:849 agent_chat_stream()` | SSE 事件类型：`thinking`、`stage_start`、`stage_done`、`tool_start`、`tool_done`、`generating`、`pipeline_timeout`、`pipeline_budget_skipped`、`done`、`error` |
| 4. 请求准备 | `api/v1/endpoints/agent.py:261 _prepare_chat_request()` | 绑定/解析标的、加载会话、技能白名单 |
| 5. 标的澄清 | `api/v1/endpoints/agent.py:309 _clarification_result()` | 名称歧义时先返回澄清卡片，不直接开始研究 |
| 6. 引导首问 | `api/v1/endpoints/agent.py:1121 /chat/intake` | 新会话首问生成 1–5 个受控选项，用户确认后才真正开跑 |
| 7. 编排 | `src/agent/orchestrator.py:443 AgentOrchestrator.chat()` | 阶段推进、工具调用、预算控制；`progress_callback` 产出 SSE 事件 |
| 8. 前端渲染 | `ChatLiveProgress`（进行中）、`ChatMessageItem`（历史消息）、`ChatThinkingBlock`（思考分组） | 消息项记忆化，打字不重渲染历史消息 |
| 9. 落库 | `src/services/agent_chat_session_service.py` | 会话与消息分组保存；`/chat/sessions` 供侧栏读取 |

## 3. 配置保存（Web 设置页）

```
SettingsPage → PUT /api/v1/system-config/config
            → SystemConfigService.update → 校验/掩码/版本冲突
            → 写 .env → 重新加载 Config → 影响运行中组件
```

| 步骤 | 代码锚点 | 说明 |
| --- | --- | --- |
| 1. 表单 | `apps/dsa-web/src/pages/SettingsPage.tsx` | 按 `config_registry` 的分组与字段渲染；敏感项显示掩码 |
| 2. 请求 | `apps/dsa-web/src/api/systemConfig.ts`（`PUT /api/v1/system-config/config`） | 携带 `config_version`，用于乐观并发控制 |
| 3. 端点 | `api/v1/endpoints/system_config.py:376 update_system_config()` | 400 校验失败、409 版本冲突、500 内部错误 |
| 4. 服务 | `src/services/system_config_service.py:2077 SystemConfigService.update()` | 合并提交项、校验、写 `.env`、可选立即 reload |
| 5. 校验 | `src/services/system_config_service.py:536 validate()`、`:2994 _validate_numeric_range()` | 类型 / 范围 / 枚举 / 跨字段规则 |
| 6. 生效 | `src/config.py get_config()` 重新加载；部分组件按需 `apply_simple_updates()` | 调度类配置只重建调度线程，不中断正在跑的分析 |

## 4. 相关但独立的两条链路

- 选股 / 筛选：`api/v1/endpoints/screening.py` → `src/services/screening_service.py`，热点与日线在 `src/services/screening/`。
- 回测：`api/v1/endpoints/backtest.py` → `src/services/backtest_service.py`，输入转换脚本 `scripts/signal_scanner/build_rebound_input.py`，报告写 `local/backtests/`。
