# CODEMAP · 代码地图

> 本文件由 `python scripts/gen_ai_codemap.py` 生成，请勿手改。
> 重新生成：`python scripts/gen_ai_codemap.py`；校验：`python scripts/gen_ai_codemap.py --check`（CI 已接入）。
> 只统计静态结构（路径 / 行数 / 顶层声明 / import 引用次数），不执行任何模块。

## 目录职责

行数按 py/ts/tsx/js/css/md/json/yml/sh/sql 等文本文件统计，不含 `node_modules`、`dist`、`local`、`static`。
`docs/` 不在此表统计：文档会随着每次改动增减，纳入统计会让本文件自身永远「过期」（本文件也在 `docs/` 下）。

| 目录 | 职责 | 文件数 | 行数 |
| --- | --- | --- | --- |
| `src/core/` | 主流程编排：pipeline、配置装配、调度入口 | 14 | 14270 |
| `src/services/` | 业务服务层：分析、筛选、通知、系统配置、任务与诊断 | 217 | 86117 |
| `src/repositories/` | 数据访问层：数据库读写封装 | 13 | 4177 |
| `src/schemas/` | API / 报告数据结构定义 | 10 | 2114 |
| `src/agent/` | 问股 Agent：意图解析、编排、工具与流式事件 | 66 | 26887 |
| `src/llm/` | LLM 调用封装与本地 CLI 生成后端 | 12 | 6466 |
| `data_provider/` | 多数据源适配、优先级与 fallback | 25 | 20413 |
| `api/` | FastAPI 应用与 v1 路由 | 47 | 13822 |
| `bot/` | IM / 机器人接入 | 22 | 5507 |
| `apps/dsa-web/src/` | Web 前端（React + Vite） | 354 | 115706 |
| `apps/dsa-desktop/` | Electron 桌面端壳与打包配置 | 8 | 8519 |
| `scripts/` | 本地与 CI 脚本 | 52 | 10795 |
| `.github/scripts/` | GitHub Actions 专用脚本 | 2 | 652 |
| `tests/` | pytest 测试 | 397 | 195011 |

## 大文件（Python，≥400 行）

`引用` 指其他 Python 文件通过 `import` 引用该模块或其顶层符号的次数；0 表示只被入口脚本、反射或测试间接使用，改动前先自行搜索。

| 文件 | 行数 | 关键顶层声明（行号） | 引用 |
| --- | --- | --- | --- |
| `src/analyzer.py` | 6115 | `_describe_llm_result_model`:115, `_localized_text`:129, `_normalize_risk_warning_values`:139, `_today_has_realtime_overlay`:164, `_today_looks_complete_daily_bar`:177, `_phase_aware_quote_labels`:210 | 241 |
| `src/services/system_config_service.py` | 6088 | `ConfigValidationError`:123, `ConfigConflictError`:131, `ConfigImportError`:139, `_LLMDiagnostic`:148, `SystemConfigService`:158 | 31 |
| `src/storage.py` | 5639 | `utc_naive_now`:81, `to_utc_naive_datetime`:86, `to_utc_isoformat`:93, `DatabaseSchemaMigration`:111, `StockDaily`:121, `NewsIntel`:201 | 395 |
| `src/core/config_registry.py` | 5371 | `get_category_definitions`:5195, `get_registered_field_keys`:5200, `_extract_option_values`:5205, `get_field_definition`:5219, `build_schema_response`:5254, `_is_sensitive_key`:5277 | 24 |
| `src/search_service.py` | 5269 | `_terminate_search_process`:61, `_search_topic_news_process_worker`:72, `_call_topic_news_in_subprocess`:100, `_post_with_retry`:191, `_get_with_retry`:203, `_is_public_http_url`:210 | 73 |
| `data_provider/base.py` | 5221 | `_resolve_fundamental_timeout_worker_limit`:58, `interactive_mode`:82, `unwrap_exception`:99, `summarize_exception`:116, `normalize_stock_code`:126, `_is_us_market`:211 | 245 |
| `src/services/screening_service.py` | 4384 | `_safe_float`:97, `_utc_now_iso`:109, `_resolve_screening_data_dir`:113, `_screening_hotspot_cache_path`:120, `_screening_hotspot_history_path`:126, `_screening_hotspot_detail_cache_dir`:132 | 23 |
| `src/core/pipeline.py` | 4327 | `_share_image_payload`:137, `_coerce_context_date`:151, `_supports_explicit_keyword`:165, `_symbol_scope_lookup_values`:180, `_measure_fetch_step`:251, `_format_fetch_timings`:266 | 71 |
| `src/config.py` | 4059 | `ConfigIssue`:81, `_has_ntfy_topic_endpoint`:146, `_has_gotify_base_url`:157, `normalize_tickflow_kline_adjust`:171, `parse_prompt_cache_diagnostics_level`:183, `AgentContextCompressionPreset`:211 | 360 |
| `src/notification.py` | 3375 | `_share_image_payload`:95, `_structured_payload_for`:109, `_safe_float`:117, `_format_strategy_skill_items`:137, `_append_strategy_synthesis_block`:157, `NotificationChannel`:208 | 87 |
| `src/llm/local_cli_backend.py` | 2966 | `_diagnostic_field_name_pattern`:256, `_diagnostic_field_assignment_pattern`:286, `_diagnostic_json_assignment_pattern`:301, `_diagnostic_line_field_pattern`:315, `_diagnostic_double_quoted_yaml_line_pattern`:337, `_diagnostic_yaml_explicit_key_pattern`:357 | 25 |
| `data_provider/akshare_fetcher.py` | 2640 | `_lots_to_shares`:64, `_is_etf_code`:120, `_is_hk_code`:139, `_normalize_tencent_volume`:171, `_normalize_cn_daily_volume`:203, `_parse_tencent_amount`:240 | 52 |
| `src/agent/orchestrator.py` | 2626 | `_stage_trajectory_snapshot`:93, `_skipped_stage_trajectory`:124, `OrchestratorResult`:139, `PreparedOrchestratorChatTurn`:159, `AgentOrchestrator`:169, `_is_denied_ticker_candidate`:2418 | 78 |
| `src/market_analyzer.py` | 2259 | `MarketIndex`:61, `MarketOverview`:93, `MarketLightReviewResult`:113, `MarketAnalyzer`:122 | 165 |
| `main.py` | 2186 | `_get_active_env_path`:137, `_log_startup_auth_state`:144, `_resolve_web_service_bind`:151, `_read_active_env_values`:158, `_bootstrap_environment`:186, `_setup_bootstrap_logging`:210 | 14 |
| `src/agent/web_intent_resolver.py` | 2176 | `_dedup_stocks`:218, `_stock_payload`:225, `_stock_from_payload`:229, `_payload_stocks`:239, `_group_payload`:243, `_group_codes`:255 | 30 |
| `src/share_image.py` | 2067 | `Table`:146, `StockPoster`:153, `MarketPoster`:178, `MarketSegment`:202, `_plain`:207, `_clean_value`:217 | 10 |
| `src/services/portfolio_service.py` | 1846 | `_portfolio_limitations_for_market`:52, `_merge_portfolio_limitations`:64, `PortfolioConflictError`:75, `PortfolioOversellError`:79, `_AvgState`:103, `_ResolvedPositionPrice`:109 | 30 |
| `src/services/screening/hotspot.py` | 1741 | `HotspotSummary`:40, `HotspotStock`:69, `HotspotTopicResolution`:88, `TimelineEvent`:98, `HotspotRouteItem`:111, `HotspotDetail`:124 | 1 |
| `src/services/history_service.py` | 1629 | `MarkdownReportGenerationError`:75, `HistoryService`:84 | 29 |
| `src/services/run_diagnostics.py` | 1536 | `build_trace_id`:81, `sanitize_diagnostic_text`:86, `safe_diagnostic_key`:106, `sanitize_diagnostic_metadata`:112, `ProviderRun`:143, `LLMRun`:182 | 95 |
| `api/v1/endpoints/analysis.py` | 1506 | `_get_task_trace_id`:96, `_task_asset_type`:106, `_task_stage`:132, `_task_progress_updated_at`:142, `_market_review_lock_path`:153, `_build_market_review_runtime`:155 | 17 |
| `src/services/run_flow.py` | 1504 | `build_task_run_flow_snapshot`:77, `build_history_run_flow_snapshot`:183, `_append_provider_runs`:371, `_append_context_blocks`:474, `_append_llm_runs`:536, `_append_history_runs`:611 | 7 |
| `api/v1/endpoints/agent.py` | 1476 | `ChatRequest`:74, `_intake_status_code`:121, `_public_answer_meta`:125, `_needs_object_intake`:136, `_reuse_session_brief`:143, `_build_agent_chat_context`:160 | 29 |
| `data_provider/efinance_fetcher.py` | 1443 | `EfinanceRealtimeQuote`:95, `_lots_to_shares`:139, `_is_etf_code`:180, `_build_eastmoney_etf_secid`:197, `_is_us_code`:209, `_get_ef_executor`:221 | 16 |
| `data_provider/tushare_fetcher.py` | 1427 | `_is_etf_code`:51, `_is_us_code`:63, `_resolve_tushare_http_url`:75, `_TushareHttpClient`:97, `TushareFetcher`:135 | 14 |
| `src/report_language.py` | 1424 | `_normalized_disclaimer_candidate`:379, `is_report_disclaimer_line`:386, `strip_report_disclaimers`:399, `ensure_report_disclaimer`:415, `_strip_decision_negation_connectors`:884, `normalize_report_language`:898 | 184 |
| `src/services/decision_signal_service.py` | 1388 | `DecisionSignalNotFoundError`:75, `DecisionSignalStorageError`:79, `DecisionSignalWriteOutcome`:87, `DecisionSignalService`:110 | 35 |
| `src/services/alert_service.py` | 1372 | `AlertServiceError`:87, `AlertNotFoundError`:93, `UnsupportedAlertTypeError`:99, `AlertService`:105 | 15 |
| `src/agent/llm_adapter.py` | 1345 | `_resolve_litellm_exception`:64, `ToolCall`:82, `LLMResponse`:92, `_split_provider_model`:190, `_object_to_dict`:200, `_provider_specific_fields_from`:224 | 122 |
| …另外 130 个 ≥400 行的文件 | — | — | — |

## 大文件（前端，≥400 行）

| 文件 | 行数 |
| --- | --- |
| `apps/dsa-web/src/i18n/uiText.ts` | 3385 |
| `apps/dsa-web/src/components/settings/LLMChannelEditor.tsx` | 2845 |
| `apps/dsa-web/src/locales/settingsHelp.ts` | 2574 |
| `apps/dsa-web/src/pages/StockScreeningPage.tsx` | 2354 |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 2252 |
| `apps/dsa-web/src/pages/HomePage.tsx` | 2072 |
| `apps/dsa-web/src/pages/ChatPage.tsx` | 1782 |
| `apps/dsa-web/src/pages/SettingsPage.tsx` | 1742 |
| `apps/dsa-web/src/pages/DecisionSignalsPage.tsx` | 1633 |
| `apps/dsa-web/src/stores/agentChatStore.ts` | 1293 |
| `apps/dsa-web/src/stores/stockPoolStore.ts` | 1184 |
| `apps/dsa-web/src/components/scanner/pages/Screen.tsx` | 931 |

## 规模快照

- Python 源码（不含测试）：516 个文件，236194 行
- Python 测试：388 个文件，194621 行
- 前端 TS/TSX：346 个文件，106651 行
- ≥400 行的 Python 文件：160 个

规模数字只用于判断改动影响面，不是质量指标。
