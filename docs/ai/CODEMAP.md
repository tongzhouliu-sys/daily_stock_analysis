# CODEMAP · 代码地图

> 本文件由 `python scripts/gen_ai_codemap.py` 生成，请勿手改。
> 重新生成：`python scripts/gen_ai_codemap.py`；校验：`python scripts/gen_ai_codemap.py --check`（CI 已接入）。
> 只统计静态结构（路径 / 行数 / 顶层声明 / import 引用次数），不执行任何模块。

## 目录职责

行数按 py/ts/tsx/js/css/md/json/yml/sh/sql 等文本文件统计，不含 `node_modules`、`dist`、`local`、`static`。
`docs/` 不在此表统计：文档会随着每次改动增减，纳入统计会让本文件自身永远「过期」（本文件也在 `docs/` 下）。

| 目录 | 职责 | 文件数 | 行数 |
| --- | --- | --- | --- |
| `src/core/` | 主流程编排：pipeline、配置装配、调度入口 | 14 | 14162 |
| `src/services/` | 业务服务层：分析、筛选、通知、系统配置、任务与诊断 | 208 | 84719 |
| `src/repositories/` | 数据访问层：数据库读写封装 | 11 | 3934 |
| `src/schemas/` | API / 报告数据结构定义 | 10 | 2107 |
| `src/agent/` | 问股 Agent：意图解析、编排、工具与流式事件 | 64 | 26598 |
| `src/llm/` | LLM 调用封装与本地 CLI 生成后端 | 12 | 6466 |
| `data_provider/` | 多数据源适配、优先级与 fallback | 22 | 19244 |
| `api/` | FastAPI 应用与 v1 路由 | 44 | 13123 |
| `bot/` | IM / 机器人接入 | 22 | 5524 |
| `apps/dsa-web/src/` | Web 前端（React + Vite） | 336 | 111495 |
| `apps/dsa-desktop/` | Electron 桌面端壳与打包配置 | 8 | 8469 |
| `scripts/` | 本地与 CI 脚本 | 52 | 10789 |
| `.github/scripts/` | GitHub Actions 专用脚本 | 2 | 652 |
| `tests/` | pytest 测试 | 374 | 190133 |

## 大文件（Python，≥400 行）

`引用` 指其他 Python 文件通过 `import` 引用该模块或其顶层符号的次数；0 表示只被入口脚本、反射或测试间接使用，改动前先自行搜索。

| 文件 | 行数 | 关键顶层声明（行号） | 引用 |
| --- | --- | --- | --- |
| `src/services/system_config_service.py` | 6088 | `ConfigValidationError`:123, `ConfigConflictError`:131, `ConfigImportError`:139, `_LLMDiagnostic`:148, `SystemConfigService`:158 | 31 |
| `src/analyzer.py` | 6085 | `_describe_llm_result_model`:115, `_localized_text`:129, `_normalize_risk_warning_values`:139, `_today_has_realtime_overlay`:164, `_today_looks_complete_daily_bar`:177, `_phase_aware_quote_labels`:210 | 241 |
| `src/storage.py` | 5515 | `utc_naive_now`:81, `to_utc_naive_datetime`:86, `to_utc_isoformat`:93, `DatabaseSchemaMigration`:111, `StockDaily`:121, `NewsIntel`:201 | 373 |
| `src/core/config_registry.py` | 5335 | `get_category_definitions`:5159, `get_registered_field_keys`:5164, `_extract_option_values`:5169, `get_field_definition`:5183, `build_schema_response`:5218, `_is_sensitive_key`:5241 | 24 |
| `data_provider/base.py` | 5198 | `_resolve_fundamental_timeout_worker_limit`:52, `interactive_mode`:76, `unwrap_exception`:93, `summarize_exception`:110, `normalize_stock_code`:120, `_is_us_market`:205 | 243 |
| `src/search_service.py` | 4962 | `_terminate_search_process`:56, `_search_topic_news_process_worker`:67, `_call_topic_news_in_subprocess`:95, `_post_with_retry`:186, `_get_with_retry`:198, `fetch_url_content`:205 | 72 |
| `src/services/screening_service.py` | 4384 | `_safe_float`:97, `_utc_now_iso`:109, `_resolve_screening_data_dir`:113, `_screening_hotspot_cache_path`:120, `_screening_hotspot_history_path`:126, `_screening_hotspot_detail_cache_dir`:132 | 23 |
| `src/core/pipeline.py` | 4270 | `_share_image_payload`:132, `_coerce_context_date`:146, `_supports_explicit_keyword`:160, `_symbol_scope_lookup_values`:175, `_measure_fetch_step`:246, `_format_fetch_timings`:261 | 71 |
| `src/config.py` | 3996 | `ConfigIssue`:81, `_has_ntfy_topic_endpoint`:146, `_has_gotify_base_url`:157, `normalize_tickflow_kline_adjust`:171, `parse_prompt_cache_diagnostics_level`:183, `AgentContextCompressionPreset`:211 | 356 |
| `src/notification.py` | 3308 | `_share_image_payload`:94, `_structured_payload_for`:108, `_safe_float`:116, `_format_strategy_skill_items`:136, `_append_strategy_synthesis_block`:156, `NotificationChannel`:207 | 85 |
| `src/llm/local_cli_backend.py` | 2966 | `_diagnostic_field_name_pattern`:256, `_diagnostic_field_assignment_pattern`:286, `_diagnostic_json_assignment_pattern`:301, `_diagnostic_line_field_pattern`:315, `_diagnostic_double_quoted_yaml_line_pattern`:337, `_diagnostic_yaml_explicit_key_pattern`:357 | 25 |
| `data_provider/akshare_fetcher.py` | 2673 | `_lots_to_shares`:65, `_is_etf_code`:121, `_is_hk_code`:140, `_normalize_tencent_volume`:172, `_normalize_cn_daily_volume`:204, `_parse_tencent_amount`:241 | 52 |
| `src/agent/orchestrator.py` | 2612 | `_stage_trajectory_snapshot`:93, `_skipped_stage_trajectory`:124, `OrchestratorResult`:139, `PreparedOrchestratorChatTurn`:159, `AgentOrchestrator`:169, `_is_denied_ticker_candidate`:2404 | 76 |
| `src/market_analyzer.py` | 2219 | `MarketIndex`:62, `MarketOverview`:94, `MarketLightReviewResult`:114, `MarketAnalyzer`:123 | 165 |
| `main.py` | 2188 | `_get_active_env_path`:137, `_log_startup_auth_state`:144, `_resolve_web_service_bind`:151, `_read_active_env_values`:158, `_bootstrap_environment`:186, `_setup_bootstrap_logging`:210 | 14 |
| `src/share_image.py` | 2184 | `Table`:158, `ShareImageBranding`:165, `share_image_branding_from_config`:184, `StockPoster`:205, `MarketPoster`:230, `MarketSegment`:254 | 19 |
| `src/agent/web_intent_resolver.py` | 2176 | `_dedup_stocks`:218, `_stock_payload`:225, `_stock_from_payload`:229, `_payload_stocks`:239, `_group_payload`:243, `_group_codes`:255 | 30 |
| `src/services/portfolio_service.py` | 1846 | `_portfolio_limitations_for_market`:52, `_merge_portfolio_limitations`:64, `PortfolioConflictError`:75, `PortfolioOversellError`:79, `_AvgState`:103, `_ResolvedPositionPrice`:109 | 30 |
| `src/services/screening/hotspot.py` | 1741 | `HotspotSummary`:40, `HotspotStock`:69, `HotspotTopicResolution`:88, `TimelineEvent`:98, `HotspotRouteItem`:111, `HotspotDetail`:124 | 1 |
| `src/services/history_service.py` | 1618 | `MarkdownReportGenerationError`:75, `HistoryService`:84 | 29 |
| `src/services/run_diagnostics.py` | 1536 | `build_trace_id`:81, `sanitize_diagnostic_text`:86, `safe_diagnostic_key`:106, `sanitize_diagnostic_metadata`:112, `ProviderRun`:143, `LLMRun`:182 | 94 |
| `src/services/run_flow.py` | 1504 | `build_task_run_flow_snapshot`:77, `build_history_run_flow_snapshot`:183, `_append_provider_runs`:371, `_append_context_blocks`:474, `_append_llm_runs`:536, `_append_history_runs`:611 | 7 |
| `api/v1/endpoints/analysis.py` | 1472 | `_get_task_trace_id`:90, `_task_asset_type`:100, `_market_review_lock_path`:126, `_build_market_review_runtime`:130, `_with_request_report_language`:134, `_run_market_review_background`:145 | 17 |
| `data_provider/tushare_fetcher.py` | 1432 | `_is_etf_code`:51, `_is_us_code`:63, `_resolve_tushare_http_url`:75, `_TushareHttpClient`:97, `TushareFetcher`:135 | 14 |
| `data_provider/efinance_fetcher.py` | 1419 | `EfinanceRealtimeQuote`:80, `_lots_to_shares`:124, `_is_etf_code`:165, `_build_eastmoney_etf_secid`:182, `_is_us_code`:194, `_ef_call_with_timeout`:206 | 15 |
| `api/v1/endpoints/agent.py` | 1403 | `ChatRequest`:73, `_intake_status_code`:120, `_public_answer_meta`:124, `_needs_object_intake`:135, `_reuse_session_brief`:142, `_build_agent_chat_context`:159 | 25 |
| `src/services/decision_signal_service.py` | 1388 | `DecisionSignalNotFoundError`:75, `DecisionSignalStorageError`:79, `DecisionSignalWriteOutcome`:87, `DecisionSignalService`:110 | 35 |
| `src/services/alert_service.py` | 1372 | `AlertServiceError`:87, `AlertNotFoundError`:93, `UnsupportedAlertTypeError`:99, `AlertService`:105 | 15 |
| `src/agent/llm_adapter.py` | 1354 | `_resolve_litellm_exception`:64, `ToolCall`:82, `LLMResponse`:92, `_split_provider_model`:190, `_object_to_dict`:200, `_provider_specific_fields_from`:224 | 122 |
| `src/services/screening/daily.py` | 1345 | `enrich_daily_features`:63, `fetch_daily_history`:170, `_normalize_daily_code`:341, `_normalize_daily_source`:353, `_normalize_max_workers`:357, `_call_daily_wrapper`:363 | 8 |
| …另外 126 个 ≥400 行的文件 | — | — | — |

## 大文件（前端，≥400 行）

| 文件 | 行数 |
| --- | --- |
| `apps/dsa-web/src/i18n/uiText.ts` | 3067 |
| `apps/dsa-web/src/components/settings/LLMChannelEditor.tsx` | 2845 |
| `apps/dsa-web/src/locales/settingsHelp.ts` | 2558 |
| `apps/dsa-web/src/pages/StockScreeningPage.tsx` | 2354 |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 2252 |
| `apps/dsa-web/src/pages/HomePage.tsx` | 1940 |
| `apps/dsa-web/src/pages/SettingsPage.tsx` | 1738 |
| `apps/dsa-web/src/pages/DecisionSignalsPage.tsx` | 1633 |
| `apps/dsa-web/src/pages/ChatPage.tsx` | 1535 |
| `apps/dsa-web/src/stores/stockPoolStore.ts` | 1153 |
| `apps/dsa-web/src/stores/agentChatStore.ts` | 1130 |
| `apps/dsa-web/src/components/scanner/pages/Screen.tsx` | 931 |

## 规模快照

- Python 源码（不含测试）：498 个文件，231266 行
- Python 测试：368 个文件，189954 行
- 前端 TS/TSX：328 个文件，102982 行
- ≥400 行的 Python 文件：156 个

规模数字只用于判断改动影响面，不是质量指标。
