# -*- coding: utf-8 -*-
"""Tests for Issue #1381 daily market context decision guardrail."""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.analyzer import AnalysisResult
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.services.daily_market_context import DailyMarketContextService


def _result() -> AnalysisResult:
    return AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=82,
        trend_prediction="看多",
        operation_advice="立即买入并积极加仓",
        decision_type="buy",
        confidence_level="高",
        analysis_summary="个股信号强势",
        dashboard={
            "operation_advice": "立即买入并积极加仓",
            "decision_type": "buy",
            "core_conclusion": {
                "one_sentence": "立即买入并积极加仓",
                "position_advice": {
                    "no_position": "立即买入并积极加仓",
                    "has_position": "继续加仓",
                },
            },
            "battle_plan": {
                "position_strategy": {
                    "suggested_position": "满仓买入",
                    "entry_plan": "突破后立即买入",
                    "risk_control": "回踩继续加仓",
                },
            },
            "phase_decision": {
                "data_limitations": [],
                "confidence_reason": "趋势强",
            },
        },
    )


def test_conservative_market_context_softens_aggressive_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "观望"
    assert len(result.operation_advice) <= 20
    assert result.confidence_level == "中"
    assert result.sentiment_score == 52
    assert result.dashboard["operation_advice"] == result.operation_advice
    assert result.dashboard["decision_type"] == "hold"
    assert result.dashboard["sentiment_score"] == 52
    core = result.dashboard["core_conclusion"]
    assert core["one_sentence"] == result.operation_advice
    assert core["position_advice"] == {
        "no_position": "大盘环境偏谨慎，暂不开新仓，等待风险缓解或确认信号。",
        "has_position": "仅保留小仓观察，暂不扩大仓位；若跌破风控位优先降低仓位。",
    }
    position_strategy = result.dashboard["battle_plan"]["position_strategy"]
    assert position_strategy == {
        "suggested_position": "小仓/低仓位",
        "entry_plan": "大盘环境偏谨慎，暂不开新仓，等待风险缓解或确认信号。",
        "risk_control": "大盘风险未缓解前不扩大仓位，严格控制回撤。",
    }
    phase_decision = result.dashboard["phase_decision"]
    assert any("大盘环境" in item for item in phase_decision["data_limitations"])
    assert "大盘环境" in phase_decision["confidence_reason"]


def test_position_cap_only_market_context_softens_aggressive_buy() -> None:
    cases = [
        ("zh", "市场震荡，仓位不超过30%。", "立即买入并积极加仓", "高", "观望"),
        ("en", "Major indices are mixed. Position limit 30%.", "Buy now and add aggressively.", "High", "Watch"),
    ]
    for language, summary, advice, confidence, expected_advice in cases:
        result = _result()
        result.operation_advice = advice
        result.confidence_level = confidence

        adjustments = apply_daily_market_context_guardrail(
            result,
            daily_market_context={
                "region": "us" if language == "en" else "cn",
                "trade_date": "2026-06-06",
                "summary": summary,
                "risk_tags": [],
                "position_cap": "30%",
            },
            report_language=language,
        )

        assert "daily_market_context_buy_softened" in adjustments
        assert result.decision_type == "hold"
        assert result.operation_advice == expected_advice


def test_neutral_market_context_leaves_hold_unchanged() -> None:
    result = _result()
    result.decision_type = "hold"
    result.operation_advice = "持有观察"
    result.confidence_level = "中"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "市场震荡，结构分化。",
            "risk_tags": [],
        },
        report_language="zh",
    )

    assert adjustments == []
    assert result.decision_type == "hold"
    assert result.operation_advice == "持有观察"


def test_conservative_market_context_does_not_soften_negative_buy_language() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "暂不加仓，继续持有观察。"
    result.confidence_level = "高"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "暂不加仓，继续持有观察。"


def test_conservative_market_context_does_not_soften_no_action_in_english() -> None:
    result = _result()
    result.decision_type = "hold"
    result.operation_advice = "No add now; keep watching for confirmation."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "us",
            "trade_date": "2026-06-06",
            "summary": "Market cooling and elevated risk. Cautious on new positions."
        },
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "hold"
    assert result.operation_advice == "No add now; keep watching for confirmation."


def test_conservative_market_context_does_not_soften_explicit_negative_add_position() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "不建议加仓，等待窗口更清晰。"
    result.confidence_level = "高"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "不建议加仓，等待窗口更清晰。"


def test_conservative_market_context_softens_generic_buy_advice_phrase() -> None:
    result = _result()
    result.operation_advice = "回踩买入，强支撑上攻。"
    result.confidence_level = "高"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "观望"


def test_conservative_market_context_softens_when_risk_warning_then_recommend_buy() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "风险不能忽视，但建议买入等待确认信号。"
    result.confidence_level = "高"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "观望"


def test_conservative_market_context_softens_when_negated_chase_then_recommend_buy() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "不建议追高，但建议分批买入。"
    result.confidence_level = "高"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "观望"


def test_conservative_market_context_does_not_soften_buy_when_negated_explicitly_in_english() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "No buy now; avoid adding."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-06-06",
            "summary": "大盘退潮，高风险，建议观望，仓位上限30%。",
            "risk_tags": ["high_risk", "low_position_cap"],
        },
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "No buy now; avoid adding."


def test_conservative_market_context_does_not_soften_do_not_buy_in_english() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "Do not buy now; sell into strength."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "us",
            "trade_date": "2026-06-06",
            "summary": "Market cooling and elevated risk. Cautious on new positions.",
        },
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "Do not buy now; sell into strength."


# =============================================================================
# T13: 只有明确风险状态才改写结论
# =============================================================================


def test_mildly_positive_summary_does_not_soften_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-11-14",
            "summary": "市场谨慎乐观，量能温和放大。",
            "risk_tags": [],
        },
        report_language="zh",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "立即买入并积极加仓"
    assert "daily_market_context_hint" not in result.dashboard


def test_fading_watch_sentiment_does_not_soften_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-11-14",
            "summary": "观望情绪消退，资金回流。",
            "risk_tags": [],
        },
        report_language="zh",
    )

    assert adjustments == []
    assert result.decision_type == "buy"


def test_conservative_tag_only_adds_hint_without_rewriting() -> None:
    result = _result()
    original_advice = result.operation_advice

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-11-14",
            "summary": "建议观望，等待方向确认。",
            "risk_tags": ["conservative"],
        },
        report_language="zh",
    )

    assert adjustments == ["daily_market_context_conservative_hint"]
    assert result.decision_type == "buy"
    assert result.operation_advice == original_advice
    assert result.confidence_level == "高"
    assert result.sentiment_score == 82
    phase_decision = result.dashboard["phase_decision"]
    assert any("大盘环境偏谨慎" in item for item in phase_decision["data_limitations"])


def test_high_risk_tag_still_downgrades_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-11-14",
            "summary": "市场高风险，情绪退潮。",
            "risk_tags": ["high_risk", "market_cooling"],
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"


def test_position_cap_still_downgrades_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-11-14",
            "summary": "仓位上限三成。",
            "risk_tags": ["low_position_cap"],
            "position_cap": "三成",
        },
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"


def _context_from_summary(monkeypatch, summary: str) -> dict:
    """Run the real summary -> extraction -> context path from R17."""
    record = SimpleNamespace(
        id=17,
        query_id="market-review-r17",
        code="MARKET",
        report_type="market_review",
        analysis_summary=summary,
        news_content=summary,
        raw_result=json.dumps({"raw_response": summary}, ensure_ascii=False),
        context_snapshot=json.dumps(
            {
                "report_kind": "market_review",
                "market_review_region": "cn",
                "market_review_payload": {
                    "kind": "market_review",
                    "region": "cn",
                    "markdown_report": summary,
                    "sections": [{"key": "overview", "title": "概览", "markdown": summary}],
                },
                "report_language": "zh",
            },
            ensure_ascii=False,
        ),
        created_at=datetime(2026, 11, 14, 9, 30),
    )
    db = MagicMock()
    db.get_analysis_history.return_value = [record]
    service = DailyMarketContextService(db_manager=db, today_fn=lambda: date(2026, 11, 14))
    with patch("src.services.daily_market_context.run_market_review"):
        context = service.get_context(
            region="cn",
            config=SimpleNamespace(report_language="zh"),
            notifier=MagicMock(),
            analyzer=MagicMock(),
            search_service=MagicMock(),
            allow_generate=False,
        )
    assert context is not None
    return context.to_safe_dict()


@pytest.mark.parametrize(
    ("summary", "expected_decision", "expect_downgrade", "expected_cap"),
    [
        ("市场谨慎乐观，量能温和放大。", "buy", False, None),
        ("观望情绪消退，资金回流。", "buy", False, None),
        ("建议观望，等待方向确认。", "buy", False, None),
        ("市场高风险，情绪退潮。", "hold", True, None),
        ("仓位上限三成。", "hold", True, "30%"),
        ("不建议轻仓试错，趋势向好。", "buy", False, None),
        ("情绪降温后企稳回升。", "buy", False, None),
        ("시장 고위험, 관망 권장.", "hold", True, None),
        ("仓位不超过 40%，保持谨慎。", "hold", True, "40%"),
    ],
)
def test_summary_text_round_trip_drives_guardrail(
    monkeypatch,
    summary: str,
    expected_decision: str,
    expect_downgrade: bool,
    expected_cap: str | None,
) -> None:
    context = _context_from_summary(monkeypatch, summary)
    if expected_cap is not None:
        assert context.get("position_cap") == expected_cap
    else:
        assert not context.get("position_cap")

    result = _result()
    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=context,
        report_language="zh",
    )

    assert result.decision_type == expected_decision
    assert ("daily_market_context_buy_softened" in adjustments) is expect_downgrade
