# -*- coding: utf-8 -*-
"""Load and validate a persisted stock report for an Agent Chat follow-up.

The service is the only place that turns a client-provided history record id
into model-visible material.  It reuses the same history-detail projection as
the history API, then keeps only the fields the Chat prompt needs.  Raw context
snapshots, diagnostics and provider configuration never leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.agent.stock_scope import StockIdentity
from src.services.history_service import HistoryService
from src.utils.data_processing import (
    extract_market_structure_detail_field,
    extract_realtime_detail_fields,
)

REPORT_CONTEXT_UNAVAILABLE = "report_context_unavailable"
REPORT_CONTEXT_INVALID = "report_context_invalid"

_UNAVAILABLE_MESSAGE = "未能载入分析报告，请重试或移除报告后按普通问股发送。"
_INVALID_MESSAGE = "报告引用无效，请从原报告重新发起追问。"
_SUPPORTED_REPORT_TYPES = {"simple", "detailed", "full", "brief"}

_MAX_TEXT_LENGTHS = {
    "analysis_summary": 2000,
    "key_points": 1200,
    "risk_warning": 1000,
    "operation_advice": 400,
    "trend_prediction": 400,
    "buy_reason": 800,
    "action_label": 120,
}
_STRATEGY_FIELDS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")


class AgentReportContextError(Exception):
    """A recoverable error raised before the server accepts a Chat turn."""

    def __init__(
        self,
        message: str = _UNAVAILABLE_MESSAGE,
        *,
        code: str = REPORT_CONTEXT_UNAVAILABLE,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ChatReportReference:
    """Tri-state client update for the session report pointer."""

    explicit: bool = False
    record_id: Optional[int] = None


def parse_report_record_id(value: Any) -> ChatReportReference:
    """Validate ``context.report_record_id`` without accepting strings or bools."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AgentReportContextError(_INVALID_MESSAGE, code=REPORT_CONTEXT_INVALID)
    return ChatReportReference(explicit=True, record_id=value)


@dataclass(frozen=True)
class LoadedReportContext:
    """Validated report projection plus the identity used by Chat scope rules."""

    identity: StockIdentity
    payload: Dict[str, Any]

    @property
    def record_id(self) -> int:
        return int(self.payload["record_id"])

    @property
    def public(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "status": "available",
            "stock_code": self.payload["stock_code"],
            "stock_name": self.payload.get("stock_name"),
            "created_at": self.payload.get("created_at"),
        }


def unavailable_report_context(record_id: int) -> Dict[str, Any]:
    """Return the public status for a pointer that no longer resolves."""
    return {
        "record_id": int(record_id),
        "status": "unavailable",
        "stock_code": None,
        "stock_name": None,
        "created_at": None,
    }


def load_agent_report_context(
    record_id: int,
    *,
    registry: Any,
    lookup: Dict[str, Any],
    hint_code: Optional[str] = None,
    history_service: Optional[HistoryService] = None,
) -> LoadedReportContext:
    """Load one record by primary key and project only Chat-safe fields.

    All not-found, access and content failures intentionally share one public
    error so the endpoint does not become a report-existence oracle.
    """
    try:
        reference = parse_report_record_id(record_id)
        assert reference.record_id is not None
    except AgentReportContextError:
        raise

    detail = (history_service or HistoryService()).get_history_detail_by_id(reference.record_id)
    if not isinstance(detail, dict):
        raise AgentReportContextError()

    report_type = str(detail.get("report_type") or "").strip().lower()
    if report_type not in _SUPPORTED_REPORT_TYPES:
        raise AgentReportContextError()

    identity = _resolve_report_identity(detail, registry, lookup)
    if identity is None:
        raise AgentReportContextError()
    if hint_code and not _hint_matches_identity(hint_code, identity, registry, lookup):
        raise AgentReportContextError()

    projection = _project_report(detail, identity)
    if not _has_usable_content(projection):
        raise AgentReportContextError()
    return LoadedReportContext(identity=identity, payload=projection)


def try_load_agent_report_context(
    record_id: Optional[int],
    *,
    registry: Any,
    lookup: Dict[str, Any],
    history_service: Optional[HistoryService] = None,
) -> Optional[LoadedReportContext]:
    """Best-effort load for an already persisted pointer; never raises."""
    if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
        return None
    try:
        return load_agent_report_context(
            record_id,
            registry=registry,
            lookup=lookup,
            history_service=history_service,
        )
    except AgentReportContextError:
        return None
    except Exception:
        return None


def _resolve_report_identity(
    detail: Dict[str, Any],
    registry: Any,
    lookup: Dict[str, Any],
) -> Optional[StockIdentity]:
    candidates = [
        detail.get("storage_stock_code"),
        detail.get("stock_code"),
    ]
    for token in candidates:
        if not isinstance(token, str) or not token.strip():
            continue
        try:
            return StockIdentity.from_code(token.strip(), registry)
        except Exception:
            continue
    # A display code may not be enough to disambiguate a cross-market alias;
    # an unresolved identity is not allowed to become a discussion object.
    return None


def _hint_matches_identity(
    hint_code: str,
    identity: StockIdentity,
    registry: Any,
    lookup: Dict[str, Any],
) -> bool:
    if not isinstance(hint_code, str) or not hint_code.strip():
        return True
    try:
        hint = StockIdentity.from_code(hint_code.strip(), registry)
    except Exception:
        return False
    return hint == identity


def _project_report(detail: Dict[str, Any], identity: StockIdentity) -> Dict[str, Any]:
    raw_result = detail.get("raw_result")
    raw = raw_result if isinstance(raw_result, dict) else {}
    context_snapshot = detail.get("context_snapshot")
    realtime = extract_realtime_detail_fields(context_snapshot)
    market_structure = extract_market_structure_detail_field(context_snapshot, raw_result)
    strategy = {
        field: _safe_number_or_text(detail.get(field))
        for field in _STRATEGY_FIELDS
    }
    projection: Dict[str, Any] = {
        "record_id": int(detail.get("id")),
        "query_id": _safe_text(detail.get("query_id"), 128),
        "report_type": str(detail.get("report_type") or "").strip().lower(),
        "created_at": _safe_text(detail.get("created_at"), 64),
        "stock_code": identity.stock_code,
        "stock_name": _safe_text(detail.get("stock_name"), 80),
        "canonical_id": identity.canonical_id,
        "asset_type": identity.asset_type,
        "analysis_summary": _safe_text(detail.get("analysis_summary"), _MAX_TEXT_LENGTHS["analysis_summary"]),
        "key_points": _safe_text(raw.get("key_points"), _MAX_TEXT_LENGTHS["key_points"]),
        "risk_warning": _safe_text(raw.get("risk_warning"), _MAX_TEXT_LENGTHS["risk_warning"]),
        "operation_advice": _safe_text(detail.get("operation_advice"), _MAX_TEXT_LENGTHS["operation_advice"]),
        "action_label": _safe_text(detail.get("action_label") or raw.get("action_label"), _MAX_TEXT_LENGTHS["action_label"]),
        "trend_prediction": _safe_text(detail.get("trend_prediction"), _MAX_TEXT_LENGTHS["trend_prediction"]),
        "buy_reason": _safe_text(raw.get("buy_reason"), _MAX_TEXT_LENGTHS["buy_reason"]),
        "sentiment_score": _safe_number(detail.get("sentiment_score")),
        "strategy": strategy,
        "price": _safe_number(realtime.get("current_price")),
        "change_pct": _safe_number(realtime.get("change_pct")),
        "market_structure": market_structure if isinstance(market_structure, dict) else None,
    }
    projection["truncated_fields"] = _truncated_fields(detail, raw, projection)
    return projection


def _truncated_fields(detail: Dict[str, Any], raw: Dict[str, Any], projection: Dict[str, Any]) -> list[str]:
    checks = {
        "analysis_summary": (detail.get("analysis_summary"), _MAX_TEXT_LENGTHS["analysis_summary"]),
        "key_points": (raw.get("key_points"), _MAX_TEXT_LENGTHS["key_points"]),
        "risk_warning": (raw.get("risk_warning"), _MAX_TEXT_LENGTHS["risk_warning"]),
        "operation_advice": (detail.get("operation_advice"), _MAX_TEXT_LENGTHS["operation_advice"]),
        "trend_prediction": (detail.get("trend_prediction"), _MAX_TEXT_LENGTHS["trend_prediction"]),
        "buy_reason": (raw.get("buy_reason"), _MAX_TEXT_LENGTHS["buy_reason"]),
        "action_label": (detail.get("action_label") or raw.get("action_label"), _MAX_TEXT_LENGTHS["action_label"]),
    }
    result = []
    for key, (value, limit) in checks.items():
        if isinstance(value, str) and len(value.strip()) > limit and projection.get(key):
            result.append(key)
    return result


def _has_usable_content(projection: Dict[str, Any]) -> bool:
    if any(projection.get(field) for field in (
        "analysis_summary", "key_points", "risk_warning", "operation_advice",
        "trend_prediction", "buy_reason", "action_label",
    )):
        return True
    if any(projection["strategy"].get(field) is not None for field in _STRATEGY_FIELDS):
        return True
    return isinstance(projection.get("market_structure"), dict) and bool(projection["market_structure"])


def _safe_text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _safe_number(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_number_or_text(value: Any) -> Any:
    """Keep zero and persisted display strings exactly as the history API shows them."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text or text in {"-", "—", "N/A"}:
        return None
    return text[:120]
