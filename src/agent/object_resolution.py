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

from src.agent.chat_object_decision import decide_chat_object, validate_chat_identity
from src.agent.stock_scope import StockIdentity, _has_ascii_token_boundaries
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
#: A sector word followed by a 股/板块/行业/概念/赛道 suffix (白酒股, 半导体板块…)
#: is a stock-picking intent, not a specific stock.
_SECTOR_WORDS = (
    r"(?:白酒|医药|医疗|生物|科技|芯片|半导体|新能源|光伏|储能|军工|券商|证券|银行|保险"
    r"|金融|地产|房地产|汽车|消费|有色|钢铁|煤炭|航空|旅游|游戏|传媒|互联网|计算机"
    r"|通信|电子|家电|食品|饮料|农业|化工|机械|电力|环保|教育|基建|建筑|港口|航运"
    r"|黄金|油气|石油)"
)
_MARKET_SCOPE_PATTERNS = (
    r"大盘", r"指数", r"两市", r"沪指", r"深成指", r"创业板指", r"市场情绪",
    r"行情", r"板块", r"行业", r"赛道", r"题材", r"热点", r"风口",
    r"选股", r"选哪只", r"选什么", r"买什么", r"买哪只", r"哪只(?:股票|股)?(?:能|可以|值得|好)",
    r"推荐", r"荐股", r"挖掘", r"机会在哪里", r"有什么机会", r"哪些股票",
    _SECTOR_WORDS + r"(?:股|板块|行业|概念|赛道)",
)


def _scan_alias_identities(message: str, registry, lookup: dict) -> list:
    """Find stock identities whose index alias appears verbatim in the text."""
    text = unicodedata.normalize("NFKC", message or "").casefold()
    if not text:
        return []
    found: Dict[tuple, Any] = {}
    for alias, rows in lookup.get("names", {}).items():
        if len(alias) < 2:
            continue
        for code, _name, kind in rows:
            try:
                identity = StockIdentity.from_code(code, registry)
            except Exception:  # noqa: BLE001 - an unparsable row is not a hit
                continue
            if kind != identity.asset_type:
                continue
            # ASCII aliases must be whole words ("aapl" must not match the
            # name "AAP" inside it); CJK aliases keep the substring scan.
            if alias.isascii():
                for occurrence in re.finditer(re.escape(alias), text):
                    start, end = occurrence.start(), occurrence.end()
                    if not _has_ascii_token_boundaries(text, start, end):
                        continue
                    found[(identity.canonical_id, identity.asset_type)] = identity
                    if len(found) > _MAX_ALIAS_CANDIDATES:
                        return []
                continue
            if alias not in text:
                continue
            found[(identity.canonical_id, identity.asset_type)] = identity
            if len(found) > _MAX_ALIAS_CANDIDATES:
                return []
    return list(found.values())


#: Whole-word lowercase ASCII ticker, mirroring ``stock_scope._LOWERCASE_TICKER_PATTERN``.
_LOWERCASE_TICKER_RE = re.compile(r"(?<![a-zA-Z.])([a-z]{2,5}(?:\.[a-z]{1,2})?)(?![a-zA-Z0-9])")
_ANALYZE_VERB_PREFIX = r"(?:分析|看看|看下|研究|诊断|查一下|帮我看看|帮我分析|看一下|换成|改看|切换到)"


def _indexed_identity(identity, lookup: dict, registry) -> bool:
    """True when ``identity`` resolves to a row in the stock index.

    ``decide_chat_object`` classifies a code by its shape (``parse_analysis_target``)
    without checking that a real index row backs it, so ``123456`` becomes a
    "CN stock" identity that no lookup key references. Re-deriving each candidate
    code and comparing identities is the accurate existence check.
    """
    for key in {identity.stock_code.upper(), identity.canonical_id.upper()}:
        for code, _name, kind in lookup.get("codes", {}).get(key, ()):
            if kind != identity.asset_type:
                continue
            try:
                if StockIdentity.from_code(code, registry) == identity:
                    return True
            except Exception:  # noqa: BLE001 - an unparsable row is not a hit
                continue
    return False


def _lowercase_code_identity(message: str, lookup: dict, registry):
    """Resolve a bare lowercase ASCII ticker that equals an index code.

    ``decide_chat_object`` only lowers its guard for lowercase tickers when a
    switch/analysis verb is present, so ``aapl`` / ``tsla`` / ``nvda怎么样``
    fall through to ``maintain``. One whole-word token that matches exactly one
    indexed stock (case-insensitive, outside the common-word list) binds.
    """
    from src.agent.orchestrator import _COMMON_WORDS

    text = unicodedata.normalize("NFKC", message or "").casefold()
    matched = {}
    for match in _LOWERCASE_TICKER_RE.finditer(text):
        token = match.group(1).upper()
        if token in _COMMON_WORDS:
            continue
        for code, _name, kind in lookup.get("codes", {}).get(token, ()):
            if kind != "stock":
                continue
            try:
                identity = StockIdentity.from_code(code, registry)
            except Exception:  # noqa: BLE001 - an unparsable row is not a hit
                continue
            if identity.asset_type == "stock":
                matched[(identity.canonical_id, identity.asset_type)] = identity
    if len(matched) == 1:
        return next(iter(matched.values()))
    return None


def _hk_short_code(message: str, lookup: dict, registry):
    """Resolve a 1-4 digit bare HK short code to its padded stock, if unique."""
    stripped = unicodedata.normalize("NFKC", message or "").strip()
    match = re.fullmatch(r"(\d{1,4})", stripped)
    if match is None:
        match = re.fullmatch(_ANALYZE_VERB_PREFIX + r"\s*(\d{1,4})", stripped)
        if match is None:
            return None
    padded = match.group(1).zfill(5)
    for key in (padded, f"HK{padded}"):
        for code, _name, kind in lookup.get("codes", {}).get(key, ()):
            if kind != "stock":
                continue
            try:
                identity = StockIdentity.from_code(code, registry)
            except Exception:  # noqa: BLE001 - an unparsable row is not a hit
                continue
            if identity.asset_type == "stock" and identity.canonical_id.lower().startswith("hk"):
                return identity
    return None


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
        # A code-shaped token can classify cleanly ("123456" → a CN stock)
        # without any index row backing it. Such a token must not auto-start;
        # it becomes the same unknown-code confirmation an ETF/fund gets.
        if not _indexed_identity(decision.target.identity, lookup, registry):
            identity_payload = _identity_payload(decision.target.identity, lookup, registry)
            return {
                "status": ResolutionStatus.AMBIGUOUS.value,
                "scope": ResolutionScope.STOCK.value,
                "identity": None,
                "candidates": [identity_payload],
                "recommended": None,
                "reason": "unknown_code",
            }
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

    # No name/alias bound the question. A bare lowercase ticker that equals one
    # index code binds ("aapl" / "tsla" / "nvda怎么样"); a bare HK short code
    # ("700") becomes a confirm-question with its padded candidate.
    identity = _lowercase_code_identity(message, lookup, registry)
    if identity is not None:
        identity_payload = _identity_payload(identity, lookup, registry)
        return {
            "status": ResolutionStatus.RESOLVED.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": identity_payload,
            "candidates": [],
            "recommended": identity_payload,
            "reason": "lowercase_code",
        }

    short_identity = _hk_short_code(message, lookup, registry)
    if short_identity is not None:
        identity_payload = _identity_payload(short_identity, lookup, registry)
        return {
            "status": ResolutionStatus.AMBIGUOUS.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": None,
            "candidates": [identity_payload],
            "recommended": identity_payload,
            "reason": "short_code",
        }

    # A code-shaped token with no index row must not auto-start; it becomes a
    # confirm-question so the user can still analyze an ETF/fund/new listing.
    for match in re.finditer(r"(?<![a-zA-Z0-9])([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?|\d{1,6})(?![a-zA-Z0-9])", unicodedata.normalize("NFKC", message)):
        token = match.group(1)
        if not token:
            continue
        upper = token.upper()
        if re.fullmatch(r"(?:SH|SZ|BJ|HK|US|SS|CSI)", upper):
            continue
        try:
            identity = validate_chat_identity(token, registry, lookup)
        except ValueError:
            continue
        if _indexed_identity(identity, lookup, registry):
            continue
        from src.agent.orchestrator import _COMMON_WORDS

        if upper in _COMMON_WORDS:
            continue
        identity_payload = _identity_payload(identity, lookup, registry)
        return {
            "status": ResolutionStatus.AMBIGUOUS.value,
            "scope": ResolutionScope.STOCK.value,
            "identity": None,
            "candidates": [identity_payload],
            "recommended": None,
            "reason": "unknown_code",
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
