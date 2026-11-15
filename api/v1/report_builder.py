# -*- coding: utf-8 -*-
"""Single constructor for the public analysis report contract.

Before T39 three code paths built ``AnalysisReport`` by hand:

* the sync analyze response (``_build_analysis_report`` in ``analysis.py``),
* the ``/analysis/status/{task_id}`` database fallback,
* ``/history/{record_id}``.

They diverged in localized fields, strategy defaults and details assembly, so
the same record could return different reports depending on the endpoint. This
module is the only place that turns a stored record or an in-memory payload
into ``AnalysisReport``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from api.v1.schemas.history import (
    AnalysisReport,
    ReportDetails,
    ReportMeta,
    ReportStrategy,
    ReportSummary,
)
from src.analysis_context_pack_overview import (
    extract_analysis_context_pack_overview,
    sanitize_context_snapshot_for_api,
)
from src.config import Config
from src.data.stock_index_loader import resolve_index_stock_code
from src.market_phase_summary import rebuild_market_phase_summary_for_stock_code
from src.report_language import (
    get_localized_stock_name,
    get_sentiment_label,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.schemas.decision_action import build_action_fields
from src.services.analysis_service import asset_type_from_canonical_code
from src.services.empty_news import empty_news_disclosure_from_stored
from src.utils.data_processing import (
    extract_board_detail_fields,
    extract_fundamental_detail_fields,
    extract_market_structure_detail_field,
    extract_realtime_detail_fields,
    normalize_model_used,
)

logger = logging.getLogger(__name__)


def stringify_report_strategy_value(value: Any) -> Optional[str]:
    """Strategy fields are strings in the contract but legacy rows store numbers."""

    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def display_stock_code_from_index(stock_code: Any) -> str:
    code = str(stock_code or "").strip()
    if not code:
        return code
    return resolve_index_stock_code(code) or code


def display_market_phase_summary(stock_code: Any, context_snapshot: Any) -> Any:
    return rebuild_market_phase_summary_for_stock_code(
        display_stock_code_from_index(stock_code),
        context_snapshot,
    )


def first_non_empty_report_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def extract_guardrail_reason(raw_result: Any) -> Optional[str]:
    if not isinstance(raw_result, dict):
        return None
    for reason in (
        raw_result.get("guardrail_reason"),
        raw_result.get("downgrade_reason"),
        raw_result.get("decision_score_guardrail_reason"),
    ):
        if reason is not None:
            text = str(reason).strip()
            if text:
                return text

    metadata = raw_result.get("metadata")
    if isinstance(metadata, dict):
        metadata_reason = metadata.get("guardrail_reason") or metadata.get("downgrade_reason")
        if metadata_reason is not None:
            text = str(metadata_reason).strip()
            if text:
                return text
    return None


def _coerce_raw_result(
    source: Mapping[str, Any],
    fallback_raw_result_payload: Optional[Any],
) -> Dict[str, Any]:
    raw_result = source.get("raw_result")
    if isinstance(raw_result, dict) and raw_result:
        return raw_result

    if isinstance(fallback_raw_result_payload, dict):
        nested = fallback_raw_result_payload.get("raw_result")
        if isinstance(nested, dict):
            return nested
        if looks_like_raw_result_payload(fallback_raw_result_payload):
            return fallback_raw_result_payload

    details = source.get("details")
    if isinstance(details, dict) and details:
        return details
    return raw_result if isinstance(raw_result, dict) else {}


def looks_like_raw_result_payload(candidate: Any) -> bool:
    return isinstance(candidate, dict) and (
        "analysis_summary" in candidate
        or "operation_advice" in candidate
        or "trend_prediction" in candidate
        or "sentiment_score" in candidate
        or "market_structure_context" in candidate
        or "model_used" in candidate
        or "dashboard" in candidate
        or "action" in candidate
    )


def build_analysis_report(
    source: Mapping[str, Any],
    *,
    query_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    context_snapshot: Optional[Any] = None,
    fallback_fundamental_payload: Optional[Dict[str, Any]] = None,
    fallback_raw_result_payload: Optional[Any] = None,
    default_report_type: Optional[str] = "detailed",
    localize_summary: bool = False,
    always_include_strategy: bool = False,
) -> AnalysisReport:
    """Build ``AnalysisReport`` from one normalized source mapping.

    ``source`` may come from an in-memory analysis payload, a database row or
    ``HistoryService``; all keys are optional. ``localize_summary`` controls
    whether advice/trend/sentiment labels are localized for the report
    language, and ``always_include_strategy`` keeps historical rows that expose
    an all-empty strategy block instead of ``null``.
    """

    raw_result = _coerce_raw_result(source, fallback_raw_result_payload)
    report_language = normalize_report_language(
        source.get("report_language")
        or raw_result.get("report_language")
        or (context_snapshot if isinstance(context_snapshot, dict) else {}).get("report_language")
        or getattr(Config.get_instance(), "report_language", "zh")
    )

    raw_meta_code = first_non_empty_report_value(
        source.get("storage_stock_code"),
        source.get("stock_code"),
        stock_code,
    )
    display_stock_code = display_stock_code_from_index(
        first_non_empty_report_value(source.get("stock_code"), stock_code) or ""
    )
    localized_stock_name = get_localized_stock_name(
        first_non_empty_report_value(source.get("stock_name"), stock_name),
        display_stock_code,
        report_language,
    )

    realtime_fields = extract_realtime_detail_fields(context_snapshot)
    current_price = first_non_empty_report_value(
        source.get("current_price"),
        realtime_fields.get("current_price"),
    )
    change_pct = first_non_empty_report_value(
        source.get("change_pct"),
        realtime_fields.get("change_pct"),
    )

    market_phase_summary = display_market_phase_summary(raw_meta_code, context_snapshot)
    if market_phase_summary is None and source.get("market_phase_summary") is not None:
        market_phase_summary = display_market_phase_summary(
            raw_meta_code,
            {"market_phase_summary": source.get("market_phase_summary")},
        )

    report_type = source.get("report_type") or default_report_type
    action_fields = build_action_fields(
        operation_advice=first_non_empty_report_value(
            raw_result.get("operation_advice"),
            source.get("operation_advice"),
            source.get("details", {}).get("operation_advice")
            if isinstance(source.get("details"), dict)
            else None,
        ),
        explicit_action=first_non_empty_report_value(
            raw_result.get("action"),
            source.get("action"),
            source.get("details", {}).get("action")
            if isinstance(source.get("details"), dict)
            else None,
        ),
        report_type=report_type,
        report_language=report_language,
        sentiment_score=first_non_empty_report_value(
            source.get("sentiment_score"),
            raw_result.get("sentiment_score"),
            source.get("details", {}).get("sentiment_score")
            if isinstance(source.get("details"), dict)
            else None,
        ),
        guardrail_reason=extract_guardrail_reason(raw_result),
        align_with_score=True,
    )

    operation_advice = source.get("operation_advice")
    trend_prediction = source.get("trend_prediction")
    sentiment_score = first_non_empty_report_value(
        source.get("sentiment_score"),
        raw_result.get("sentiment_score"),
    )
    sentiment_label = source.get("sentiment_label")
    if localize_summary:
        operation_advice = localize_operation_advice(operation_advice, report_language)
        trend_prediction = localize_trend_prediction(trend_prediction, report_language)
    # `sentiment_label` is derived from the score for every entry point so the
    # same record never returns a label on one endpoint and null on another.
    if sentiment_score is not None and sentiment_label is None:
        sentiment_label = get_sentiment_label(int(sentiment_score), report_language)

    summary = ReportSummary(
        analysis_summary=source.get("analysis_summary"),
        operation_advice=operation_advice,
        action=action_fields["action"],
        action_label=action_fields["action_label"],
        trend_prediction=trend_prediction,
        sentiment_score=sentiment_score,
        sentiment_label=sentiment_label,
    )

    strategy_values = {
        "ideal_buy": stringify_report_strategy_value(source.get("ideal_buy")),
        "secondary_buy": stringify_report_strategy_value(source.get("secondary_buy")),
        "stop_loss": stringify_report_strategy_value(source.get("stop_loss")),
        "take_profit": stringify_report_strategy_value(source.get("take_profit")),
    }
    if always_include_strategy or any(value is not None for value in strategy_values.values()):
        strategy = ReportStrategy(**strategy_values)
    else:
        strategy = None

    extracted_fundamental = extract_fundamental_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    extracted_boards = extract_board_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    market_structure = None
    for raw_candidate in (
        fallback_raw_result_payload,
        raw_result,
        source.get("details"),
    ):
        if raw_candidate is None:
            continue
        market_structure = extract_market_structure_detail_field(
            context_snapshot,
            raw_candidate,
        )
        if market_structure is not None:
            break

    analysis_context_pack_overview = extract_analysis_context_pack_overview(context_snapshot)
    api_context_snapshot = sanitize_context_snapshot_for_api(context_snapshot)
    news_disclosure = empty_news_disclosure_from_stored(
        raw_result,
        context_snapshot,
        report_language,
    )
    if news_disclosure is None:
        news_disclosure = source.get("empty_news_disclosure")

    details_data = source.get("details") if isinstance(source.get("details"), dict) else {}
    news_content = first_non_empty_report_value(
        source.get("news_content"),
        details_data.get("news_content"),
        details_data.get("news_summary"),
    )
    has_board_details = (
        bool(extracted_boards.get("belong_boards"))
        or extracted_boards.get("sector_rankings") is not None
        or extracted_boards.get("concept_rankings") is not None
    )
    details = None
    if (
        source.get("details_present")
        or details_data
        or bool(raw_result)
        or any(extracted_fundamental.values())
        or has_board_details
        or market_structure is not None
        or context_snapshot is not None
        or analysis_context_pack_overview is not None
        or news_disclosure is not None
        or news_content is not None
    ):
        details = ReportDetails(
            news_content=news_content,
            empty_news_disclosure=news_disclosure,
            raw_result=raw_result or None,
            context_snapshot=api_context_snapshot,
            analysis_context_pack_overview=analysis_context_pack_overview,
            financial_report=extracted_fundamental.get("financial_report"),
            dividend_metrics=extracted_fundamental.get("dividend_metrics"),
            belong_boards=extracted_boards.get("belong_boards"),
            sector_rankings=extracted_boards.get("sector_rankings"),
            concept_rankings=extracted_boards.get("concept_rankings"),
            market_structure=market_structure,
        )

    meta = ReportMeta(
        id=source.get("id"),
        query_id=first_non_empty_report_value(source.get("query_id"), query_id),
        stock_code=display_stock_code,
        stock_name=localized_stock_name,
        report_type=report_type,
        report_language=report_language,
        created_at=first_non_empty_report_value(
            source.get("created_at"),
            datetime.now().isoformat(),
        ),
        model_used=normalize_model_used(source.get("model_used")),
        current_price=current_price,
        change_pct=change_pct,
        market_phase_summary=market_phase_summary,
        asset_type=asset_type_from_canonical_code(raw_meta_code or display_stock_code),
    )

    return AnalysisReport(
        meta=meta,
        summary=summary,
        strategy=strategy,
        details=details,
    )


__all__ = [
    "build_analysis_report",
    "display_market_phase_summary",
    "display_stock_code_from_index",
    "extract_guardrail_reason",
    "first_non_empty_report_value",
    "looks_like_raw_result_payload",
    "stringify_report_strategy_value",
]
