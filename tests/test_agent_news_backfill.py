# -*- coding: utf-8 -*-
"""Agent 模式新闻证据回填与置信度理由回归测试。"""

from unittest.mock import MagicMock, patch

from src.agent.news_evidence import record_news_evidence


def _build_pipeline():
    with patch('src.core.pipeline.get_config') as mock_config, \
         patch('src.core.pipeline.get_db'), \
         patch('src.core.pipeline.DataFetcherManager'), \
         patch('src.core.pipeline.GeminiAnalyzer'), \
         patch('src.core.pipeline.NotificationService'), \
         patch('src.core.pipeline.SearchService'):

        mock_cfg = MagicMock()
        mock_cfg.max_workers = 2
        mock_cfg.agent_mode = True
        mock_cfg.agent_max_steps = 10
        mock_cfg.agent_skills = []
        mock_cfg.bocha_api_keys = []
        mock_cfg.tavily_api_keys = []
        mock_cfg.brave_api_keys = []
        mock_cfg.serpapi_keys = []
        mock_cfg.searxng_base_urls = []
        mock_cfg.searxng_public_instances_enabled = False
        mock_cfg.news_max_age_days = 7
        mock_cfg.enable_realtime_quote = True
        mock_cfg.enable_chip_distribution = True
        mock_cfg.realtime_source_priority = []
        mock_cfg.save_context_snapshot = False
        mock_cfg.report_integrity_enabled = False
        mock_cfg.report_language = "zh"
        mock_config.return_value = mock_cfg

        from src.core.pipeline import StockAnalysisPipeline

        pipeline = StockAnalysisPipeline(config=mock_cfg)

    pipeline.search_service.is_available = True
    post_hoc = MagicMock()
    post_hoc.success = False
    post_hoc.results = []
    pipeline.search_service.search_stock_news.return_value = post_hoc
    pipeline.db.save_analysis_history.return_value = 4321
    pipeline._load_persisted_intelligence_context = MagicMock(return_value=None)
    pipeline._load_prediction_market_evidence = MagicMock(return_value=(None, []))
    pipeline._ensure_agent_history = MagicMock()
    pipeline._load_agent_analysis_context = MagicMock(return_value={})
    pipeline._extract_decision_signal_after_history_save = MagicMock()
    return pipeline


def _news_block(overview):
    for block in overview["blocks"]:
        if block["key"] == "news":
            return block
    raise AssertionError("overview 缺少 news 数据块")


def _run_agent(pipeline, dashboard, *, news_items=None):
    from src.agent.executor import AgentResult
    from src.enums import ReportType

    agent_result = AgentResult(
        success=True,
        content="{}",
        dashboard=dashboard,
        provider="stub",
    )

    def _executor_run(*args, **kwargs):
        if news_items:
            record_news_evidence(len(news_items), news_items)
        return agent_result

    mock_executor = MagicMock()
    mock_executor.run.side_effect = _executor_run
    with patch('src.agent.factory.build_agent_executor', return_value=mock_executor):
        return pipeline._analyze_with_agent(
            code="600519",
            report_type=ReportType.SIMPLE,
            query_id="q-news-backfill",
            stock_name="贵州茅台",
            realtime_quote=None,
            chip_data=None,
        )


def _dashboard(confidence_reason):
    return {
        "stock_name": "贵州茅台",
        "sentiment_score": 70,
        "trend_prediction": "震荡偏多",
        "operation_advice": "持有",
        "decision_type": "hold",
        "analysis_summary": "新闻与行情共同支持当前判断。",
        "phase_decision": {
            "phase_context": "intraday",
            "confidence_reason": confidence_reason,
        },
    }


def test_agent_news_evidence_backfills_pack_quality_and_history():
    pipeline = _build_pipeline()
    news_items = [
        {
            "title": "公司发布季度财报",
            "snippet": "营收同比增长 18%",
            "url": "https://news.example.com/a1",
            "source": "测试资讯",
            "published_date": "2026-11-23",
            "dimension": "latest_news",
        },
        {
            "title": "机构上调目标价",
            "snippet": "多家机构维持买入评级",
            "url": "https://news.example.com/a2",
            "source": "测试资讯",
            "published_date": "2026-11-22",
            "dimension": "market_analysis",
        },
        {
            "title": "行业政策边际改善",
            "snippet": "监管口径趋于稳定",
            "url": "https://news.example.com/a3",
            "source": "测试资讯",
            "published_date": "2026-11-22",
            "dimension": "industry",
        },
    ]

    result = _run_agent(
        pipeline,
        _dashboard("实时行情可用，但新闻数据块缺失，置信度受限。"),
        news_items=news_items,
    )

    assert result is not None
    overview = result.analysis_context_pack_overview
    assert overview["metadata"]["news_result_count"] == 3
    news_block = _news_block(overview)
    assert news_block["status"] == "available"
    assert "测试资讯" in (news_block["source"] or "")
    assert overview["data_quality"]["block_scores"]["news"] == 100

    confidence_reason = result.dashboard["phase_decision"]["confidence_reason"]
    assert "新闻数据块缺失" not in confidence_reason

    saved = pipeline.db.save_analysis_history.call_args.kwargs
    assert saved["news_content"]
    assert "公司发布季度财报" in saved["news_content"]
    assert "测试资讯" in saved["news_content"]
    assert "来源：https://news.example.com/a1" in saved["news_content"]
    assert saved["context_snapshot"]["news_result_count"] == 3


def test_agent_count_only_evidence_still_backfills_news_block():
    """工具只登记计数、未保留条目时，新闻块也不能继续显示缺失。"""
    from src.agent.executor import AgentResult
    from src.enums import ReportType

    pipeline = _build_pipeline()
    agent_result = AgentResult(
        success=True,
        content="{}",
        provider="stub",
        dashboard=_dashboard("实时行情可用，但新闻数据块缺失。"),
    )

    def _count_only_run(*args, **kwargs):
        record_news_evidence(2)
        return agent_result

    mock_executor = MagicMock()
    mock_executor.run.side_effect = _count_only_run
    with patch('src.agent.factory.build_agent_executor', return_value=mock_executor):
        result = pipeline._analyze_with_agent(
            code="600519",
            report_type=ReportType.SIMPLE,
            query_id="q-news-count-only",
            stock_name="贵州茅台",
            realtime_quote=None,
            chip_data=None,
        )

    assert result is not None
    overview = result.analysis_context_pack_overview
    assert overview["metadata"]["news_result_count"] == 2
    news_block = _news_block(overview)
    assert news_block["status"] == "available"
    assert news_block["source"] == "agent:search_tools"
    assert "新闻数据块缺失" not in result.dashboard["phase_decision"]["confidence_reason"]
    assert pipeline.db.save_analysis_history.call_args.kwargs["news_content"]


def test_agent_without_news_evidence_reports_missing_in_confidence_reason():
    pipeline = _build_pipeline()

    result = _run_agent(
        pipeline,
        _dashboard("实时行情与日线数据可用。"),
        news_items=None,
    )

    assert result is not None
    assert result.news_result_count == 0
    confidence_reason = result.dashboard["phase_decision"]["confidence_reason"]
    assert "新闻数据缺失或不足" in confidence_reason

    overview = result.analysis_context_pack_overview
    assert overview["metadata"]["news_result_count"] == 0
    assert _news_block(overview)["status"] == "missing"
    assert overview["data_quality"]["block_scores"]["news"] == 35
