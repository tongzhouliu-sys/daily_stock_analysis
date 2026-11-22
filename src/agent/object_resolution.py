# -*- coding: utf-8 -*-
"""Resolve the object of a first question into an intake resolution.

This is the "do the homework before asking" step: the same local stock index
decision the chat turn uses (``decide_chat_object``) now runs while the intake
draft is being created, so the UI can start immediately when the target is
unambiguous and only ask one question when it is not.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from src.agent.chat_object_decision import decide_chat_object
from src.agent.stock_scope import StockIdentity
from src.schemas.agent_intake import ResolutionScope, ResolutionStatus

_MARKET_LABELS = {
    "CN": ("A股", "A-shares"),
    "HK": ("港股", "Hong Kong"),
    "US": ("美股", "US stocks"),
    "JP": ("日股", "Japan"),
    "KR": ("韩股", "Korea"),
    "TW": ("台股", "Taiwan"),
    "UNKNOWN": ("未知市场", "Unknown market"),
}


def market_for(canonical_id: str, asset_type: str = "stock") -> str:
    """Derive the coarse market code from a canonical id."""
    canonical = (canonical_id or "").lower()
    if canonical.startswith("hk"):
        return "HK"
    if canonical.startswith("csi"):
        return "CN"
    if canonical.startswith(("sh", "sz", "bj")):
        return "CN"
    if asset_type == "index":
        return "CN"
    if canonical and canonical[0].isalpha():
        return "US"
    return "UNKNOWN"


def market_label(market: str, language: str = "zh") -> str:
    zh, en = _MARKET_LABELS.get(market, _MARKET_LABELS["UNKNOWN"])
    return en if str(language).lower().startswith("en") else zh


def _identity_payload(identity, lookup: dict, registry) -> Dict[str, Any]:
    # Imported lazily: the session service owns the name lookup, and importing
    # it at module import time would pull the whole chat stack into intake.
    from src.services.agent_chat_session_service import _stock_record

    record = _stock_record(identity, lookup, registry)
    market = market_for(record["canonical_id"], record.get("asset_type", "stock"))
    return {
        "stock_code": record["stock_code"],
        "stock_name": record.get("stock_name"),
        "canonical_id": record["canonical_id"],
        "asset_type": record.get("asset_type", "stock"),
        "market": market,
        "market_label": market_label(market),
    }


def _recommend(candidates: List[Dict[str, Any]], current: Optional[Dict[str, Any]]):
    from src.services.agent_chat_session_service import _pick_recommended

    return _pick_recommended(candidates, current)


_MAX_ALIAS_CANDIDATES = 6

#: Whole-market questions do not need a single stock object.
_MARKET_SCOPE_PATTERNS = (
    r"大盘", r"指数", r"两市", r"沪指", r"深成指", r"创业板指", r"市场情绪",
)


def _scan_alias_identities(message: str, registry, lookup: dict) -> list:
    """Find stock identities whose index alias appears verbatim in the text."""
    text = unicodedata.normalize("NFKC", message or "").casefold()
    if not text:
        return []
    found: Dict[tuple, Any] = {}
    for alias, rows in lookup.get("names", {}).items():
        if len(alias) < 2 or alias not in text:
            continue
        for code, _name, kind in rows:
            try:
                identity = StockIdentity.from_code(code, registry)
            except Exception:  # noqa: BLE001 - an unparsable row is not a hit
                continue
            if kind != identity.asset_type:
                continue
            found[(identity.canonical_id, identity.asset_type)] = identity
            if len(found) > _MAX_ALIAS_CANDIDATES:
                return []
    return list(found.values())


def resolve_intake_object(
    message: str,
    registry: Any,
    lookup: dict,
    *,
    concept_question: bool = False,
) -> Dict[str, Any]:
    """Return ``{status, scope, identity, candidates, recommended, reason}``."""
    if concept_question:
        return {
            "status": ResolutionStatus.NOT_REQUIRED.value,
            "scope": ResolutionScope.UNKNOWN.value,
            "identity": None,
            "candidates": [],
            "recommended": None,
            "reason": "concept_question",
        }

    decision = decide_chat_object(message, None, registry, lookup)

    if decision.kind == "confirm" and decision.target is not None:
        identity = _identity_payload(decision.target.identity, lookup, registry)
        return {
            "status": ResolutionStatus.RESOLVED.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": identity,
            "candidates": [],
            "recommended": identity,
            "reason": "unique_object",
        }

    if decision.kind == "compare" and decision.participants:
        participants = [
            _identity_payload(identity, lookup, registry)
            for identity in dict.fromkeys(decision.participants)
        ]
        return {
            "status": ResolutionStatus.RESOLVED.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": None,
            "candidates": participants,
            "recommended": None,
            "reason": "comparison",
        }

    if decision.candidates:
        candidates = [
            _identity_payload(identity, lookup, registry)
            for identity in dict.fromkeys(decision.candidates)
        ]
        recommended = _recommend(candidates, None)
        return {
            "status": ResolutionStatus.AMBIGUOUS.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": None,
            "candidates": candidates,
            "recommended": recommended,
            "reason": "ambiguous_object",
        }

    if any(re.search(pattern, message, re.IGNORECASE) for pattern in _MARKET_SCOPE_PATTERNS):
        return {
            "status": ResolutionStatus.NOT_REQUIRED.value,
            "scope": ResolutionScope.MARKET.value,
            "identity": None,
            "candidates": [],
            "recommended": None,
            "reason": "market_scope",
        }

    # First questions often name the object inside prose
    # ("我持有茅台被套了，长期怎么看") without the verb phrases the chat
    # resolver needs. Scan the index aliases; one hit binds, a few become
    # candidates, and noise stays "missing".
    aliases = _scan_alias_identities(message, registry, lookup)
    if len(aliases) == 1:
        identity_payload = _identity_payload(next(iter(aliases)), lookup, registry)
        return {
            "status": ResolutionStatus.RESOLVED.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": identity_payload,
            "candidates": [],
            "recommended": identity_payload,
            "reason": "unique_mention",
        }
    if 2 <= len(aliases) <= 6:
        candidates = [
            _identity_payload(identity, lookup, registry)
            for identity in aliases
        ]
        return {
            "status": ResolutionStatus.AMBIGUOUS.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": None,
            "candidates": candidates,
            "recommended": _recommend(candidates, None),
            "reason": "ambiguous_mention",
        }

    return {
        "status": ResolutionStatus.MISSING.value,
        "scope": ResolutionScope.STOCK.value,
        "identity": None,
        "candidates": [],
        "recommended": None,
        "reason": "missing_object",
    }


__all__ = ["resolve_intake_object", "market_for", "market_label"]
