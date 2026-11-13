"""v2: Deterministic object resolution for smart intake.

Resolves user input to concrete stock identities using a priority-based
resolution chain: explicit codes → exact names → session context → report
context → market/portfolio scope → concept → fuzzy match → missing.

This service reuses existing parsing capabilities:
- parse_analysis_target() for code/name parsing
- get_chat_stock_lookup() for name/code lookup
- IndexRegistry for index resolution

T2: Object resolution service.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.schemas.agent_intake import (
    ObjectIdentity,
    ObjectResolution,
    ResolutionEvidence,
    ResolutionScope,
    ResolutionStatus,
)
from src.services.stock_list_parser import (
    AnalysisTarget,
    IndexRegistry,
    parse_analysis_target,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Patterns for scope detection
MARKET_PATTERNS = [
    r"大盘", r"市场", r"A股", r"港股", r"美股",
    r"上证", r"深证", r"恒生", r"纳指", r"标普",
    r"market", r"a-share", r"hk", r"us",
]

PORTFOLIO_PATTERNS = [
    r"持仓", r"组合", r"我的持仓",
    r"portfolio", r"holdings",
]

WATCHLIST_PATTERNS = [
    r"自选", r"关注", r"我的自选",
    r"watchlist",
]

CONCEPT_PATTERNS = [
    r"什么是", r"怎么理解", r"解释", r"什么叫", r"是什么意思",
    r"what is", r"how to understand", r"explain",
]

# Patterns for session inheritance (pronouns/references)
SESSION_INHERIT_PATTERNS = [
    r"继续", r"它", r"这只", r"这个", r"那现在",
    r"再看看", r"接着看",
]

# Maximum candidates for fuzzy match
MAX_CANDIDATES = 5


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntakeObjectResolution:
    """Result of object resolution for intake."""

    status: str  # resolved | ambiguous | missing | unsupported | not_required
    scope: str  # stock | index | etf | market | portfolio | watchlist | report | concept | unknown
    identity: Optional[ObjectIdentity] = None
    candidates: Tuple[ObjectIdentity, ...] = field(default_factory=tuple)
    recommended_option_id: Optional[str] = None
    recommendation_reason: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None  # unique_code | unique_name | session_inherited | etc.


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    """Normalize text using NFKC + casefold for matching."""
    return unicodedata.normalize("NFKC", text).casefold()


def _extract_tokens(text: str) -> List[Tuple[str, int, int]]:
    """Extract potential stock tokens from text with positions.

    Returns list of (token, start, end) tuples.
    """
    tokens = []

    # Split by whitespace and punctuation, keeping positions
    # Match: alphanumeric sequences, Chinese characters, code-like patterns
    pattern = re.compile(
        r"([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)"  # Tickers with optional suffix
        r"|(\d{1,6}(?:\.[A-Za-z]{1,2})?)"  # Numeric codes with optional suffix
        r"|([A-Za-z]{1,2}\d{1,6})"  # Prefix codes like SH600519
        r"|([\u4e00-\u9fff]+)"  # Chinese characters
    )

    for match in pattern.finditer(text):
        token = match.group(0)
        start, end = match.span()
        tokens.append((token, start, end))

    return tokens


def _get_market_from_exchange(exchange: Optional[str]) -> str:
    """Derive market code from exchange."""
    if not exchange:
        return "UNKNOWN"

    exchange_upper = exchange.upper()

    if exchange_upper in ("SH", "SZ", "BJ"):
        return "CN"
    elif exchange_upper == "HK":
        return "HK"
    elif exchange_upper in ("US", "NASDAQ", "NYSE", "AMEX"):
        return "US"
    elif exchange_upper == "JP":
        return "JP"
    elif exchange_upper == "KR":
        return "KR"
    elif exchange_upper == "TW":
        return "TW"

    return "UNKNOWN"


def _get_market_label(market: str, exchange: Optional[str] = None) -> str:
    """Generate user-readable market label."""
    labels = {
        "CN": "A股",
        "HK": "港股",
        "US": "美股",
        "JP": "日股",
        "KR": "韩股",
        "TW": "台股",
    }

    base_label = labels.get(market, market)

    # Add exchange info if available
    if exchange and exchange in ("SH", "SZ", "BJ", "HK"):
        exchange_names = {
            "SH": "上交所",
            "SZ": "深交所",
            "BJ": "北交所",
            "HK": "港交所",
        }
        exchange_name = exchange_names.get(exchange, exchange)
        return f"{base_label} · {exchange_name}"

    return base_label


def _target_to_identity(target: AnalysisTarget) -> Optional[ObjectIdentity]:
    """Convert AnalysisTarget to ObjectIdentity."""
    if not target or not target.code:
        return None

    market = _get_market_from_exchange(target.exchange)
    market_label = _get_market_label(market, target.exchange)

    return ObjectIdentity(
        stock_code=target.code,
        stock_name=target.name,
        canonical_id=target.canonical_id or target.code,
        asset_type=target.asset_type or "stock",
        market=market,
        market_label=market_label,
    )


def _lookup_to_identity(
    entry: Dict[str, Any],
    lookup: Dict[str, Any],
) -> Optional[ObjectIdentity]:
    """Convert lookup entry to ObjectIdentity."""
    if not entry:
        return None

    stock_code = entry.get("code", "")
    stock_name = entry.get("name", "")
    market = entry.get("market", "UNKNOWN")

    # Build canonical ID
    canonical_id = stock_code
    if market == "CN" and len(stock_code) == 6:
        # A-share: add exchange suffix
        if stock_code.startswith("6"):
            canonical_id = f"{stock_code}.SH"
        elif stock_code.startswith(("0", "3")):
            canonical_id = f"{stock_code}.SZ"
        elif stock_code.startswith(("4", "8")):
            canonical_id = f"{stock_code}.BJ"
    elif market == "HK":
        canonical_id = f"HK{stock_code}"

    market_label = _get_market_label(market)

    return ObjectIdentity(
        stock_code=stock_code,
        stock_name=stock_name,
        canonical_id=canonical_id,
        asset_type=entry.get("assetType", "stock"),
        market=market,
        market_label=market_label,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main resolution function
# ─────────────────────────────────────────────────────────────────────────────


def resolve_intake_object(
    message: str,
    *,
    active_stock: Optional[Dict[str, Any]] = None,
    report_context: Optional[Dict[str, Any]] = None,
    watchlist: Sequence[str] = (),
    recent_codes: Sequence[str] = (),
    registry: Optional[IndexRegistry] = None,
    lookup: Optional[Dict[str, Any]] = None,
    default_market: str = "CN",
) -> IntakeObjectResolution:
    """Resolve user input to concrete object identity.

    Priority chain (per requirements R1):
    1. Explicit codes (600519, 00700, AAPL, etc.)
    2. Exact names/aliases (贵州茅台, 腾讯, etc.)
    3. Current session stock (for follow-up questions)
    4. Report context stock
    5. Market/portfolio/watchlist scope
    6. Concept/method questions
    7. Fuzzy name match
    8. Missing

    Args:
        message: User input message
        active_stock: Current session's active stock context
        report_context: Active report context
        watchlist: User's watchlist codes
        recent_codes: Recently analyzed stock codes
        registry: Index registry for index resolution
        lookup: Stock name/code lookup dict
        default_market: Default market for ambiguous cases

    Returns:
        IntakeObjectResolution with status, identity, candidates, etc.
    """
    if not message or not message.strip():
        return IntakeObjectResolution(
            status="missing",
            scope="unknown",
            reason="no_object",
        )

    text = message.strip()
    normalized_text = _normalize_text(text)

    # Initialize registry and lookup if not provided
    if registry is None:
        try:
            registry = IndexRegistry()
        except Exception as exc:
            logger.warning("[对象解析] 初始化 IndexRegistry 失败: %s", exc)
            registry = IndexRegistry(rows=[], validated=True)

    if lookup is None:
        try:
            from src.data.stock_index_loader import get_chat_stock_lookup
            lookup = get_chat_stock_lookup()
        except Exception as exc:
            logger.warning("[对象解析] 初始化 lookup 失败: %s", exc)
            lookup = {"codes": {}, "names": {}, "aliases": {}}

    # ─────────────────────────────────────────────────────────────────
    # Step 1: Check for scope patterns (market/portfolio/concept)
    # ─────────────────────────────────────────────────────────────────

    # Check concept patterns first (no object needed)
    for pattern in CONCEPT_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            return IntakeObjectResolution(
                status="not_required",
                scope="concept",
                reason="no_object",
            )

    # Check market patterns
    for pattern in MARKET_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            return IntakeObjectResolution(
                status="not_required",
                scope="market",
                reason="no_object",
            )

    # Check portfolio patterns
    for pattern in PORTFOLIO_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            return IntakeObjectResolution(
                status="not_required",
                scope="portfolio",
                reason="no_object",
            )

    # Check watchlist patterns
    for pattern in WATCHLIST_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            return IntakeObjectResolution(
                status="not_required",
                scope="watchlist",
                reason="no_object",
            )

    # ─────────────────────────────────────────────────────────────────
    # Step 2: Try explicit code parsing
    # ─────────────────────────────────────────────────────────────────

    tokens = _extract_tokens(text)

    for token, start, end in tokens:
        try:
            target = parse_analysis_target(token, registry=registry)
            if target and target.code:
                identity = _target_to_identity(target)
                if identity:
                    return IntakeObjectResolution(
                        status="resolved",
                        scope=target.asset_type or "stock",
                        identity=identity,
                        evidence={
                            "kind": "explicit_code",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="unique_code",
                    )
        except Exception as exc:
            logger.debug("[对象解析] 解析 token 失败 %s: %s", token, exc)
            continue

    # ─────────────────────────────────────────────────────────────────
    # Step 3: Try exact name/alias match
    # ─────────────────────────────────────────────────────────────────

    names_map = lookup.get("names", {})
    aliases_map = lookup.get("aliases", {})

    # Try exact name match
    for token, start, end in tokens:
        normalized_token = _normalize_text(token)

        # Check exact name match
        if normalized_token in names_map:
            entries = names_map[normalized_token]
            if len(entries) == 1:
                identity = _lookup_to_identity(entries[0], lookup)
                if identity:
                    return IntakeObjectResolution(
                        status="resolved",
                        scope=identity.asset_type,
                        identity=identity,
                        evidence={
                            "kind": "unique_name",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="unique_name",
                    )
            elif len(entries) > 1:
                # Multiple matches - ambiguous
                candidates = []
                for entry in entries[:MAX_CANDIDATES]:
                    identity = _lookup_to_identity(entry, lookup)
                    if identity:
                        candidates.append(identity)

                if candidates:
                    # Generate recommendation
                    recommended = _recommend_candidate(
                        candidates,
                        active_stock=active_stock,
                        watchlist=watchlist,
                        recent_codes=recent_codes,
                        default_market=default_market,
                    )

                    return IntakeObjectResolution(
                        status="ambiguous",
                        scope=candidates[0].asset_type,
                        candidates=tuple(candidates),
                        recommended_option_id=recommended[0] if recommended else None,
                        recommendation_reason=recommended[1] if recommended else None,
                        evidence={
                            "kind": "ambiguous_name",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="cross_market_listing",
                    )

        # Check alias match
        if normalized_token in aliases_map:
            entries = aliases_map[normalized_token]
            if len(entries) == 1:
                identity = _lookup_to_identity(entries[0], lookup)
                if identity:
                    return IntakeObjectResolution(
                        status="resolved",
                        scope=identity.asset_type,
                        identity=identity,
                        evidence={
                            "kind": "unique_alias",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="unique_name",
                    )

    # ─────────────────────────────────────────────────────────────────
    # Step 4: Check session inheritance
    # ─────────────────────────────────────────────────────────────────

    for pattern in SESSION_INHERIT_PATTERNS:
        if re.search(pattern, normalized_text, re.IGNORECASE):
            if active_stock:
                # Build identity from active stock
                stock_code = active_stock.get("stock_code", "")
                stock_name = active_stock.get("stock_name", "")
                canonical_id = active_stock.get("canonical_id", stock_code)
                asset_type = active_stock.get("asset_type", "stock")
                market = active_stock.get("market", "UNKNOWN")

                if stock_code:
                    identity = ObjectIdentity(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        canonical_id=canonical_id,
                        asset_type=asset_type,
                        market=market,
                        market_label=_get_market_label(market),
                    )
                    return IntakeObjectResolution(
                        status="resolved",
                        scope=asset_type,
                        identity=identity,
                        evidence={
                            "kind": "session_inherited",
                            "text": text,
                        },
                        reason="session_inherited",
                    )

    # ─────────────────────────────────────────────────────────────────
    # Step 5: Check report context
    # ─────────────────────────────────────────────────────────────────

    if report_context:
        report_stock = report_context.get("stock_code")
        if report_stock:
            identity = ObjectIdentity(
                stock_code=report_stock,
                stock_name=report_context.get("stock_name", ""),
                canonical_id=report_context.get("canonical_id", report_stock),
                asset_type=report_context.get("asset_type", "stock"),
                market=report_context.get("market", "UNKNOWN"),
                market_label=_get_market_label(report_context.get("market", "UNKNOWN")),
            )
            return IntakeObjectResolution(
                status="resolved",
                scope=identity.asset_type,
                identity=identity,
                evidence={
                    "kind": "report_context",
                    "text": text,
                },
                reason="report_context",
            )

    # ─────────────────────────────────────────────────────────────────
    # Step 6: Fuzzy name match (prefix/substring)
    # ─────────────────────────────────────────────────────────────────

    for token, start, end in tokens:
        if len(token) < 2:
            continue

        normalized_token = _normalize_text(token)
        matches = []

        # Search in names
        for name, entries in names_map.items():
            if normalized_token in name or name.startswith(normalized_token):
                for entry in entries:
                    matches.append(entry)

        # Search in aliases
        for alias, entries in aliases_map.items():
            if normalized_token in alias or alias.startswith(normalized_token):
                for entry in entries:
                    if entry not in matches:
                        matches.append(entry)

        if matches:
            # Limit to MAX_CANDIDATES
            matches = matches[:MAX_CANDIDATES]

            if len(matches) == 1:
                # Single match - resolved
                identity = _lookup_to_identity(matches[0], lookup)
                if identity:
                    return IntakeObjectResolution(
                        status="resolved",
                        scope=identity.asset_type,
                        identity=identity,
                        evidence={
                            "kind": "fuzzy_match",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="fuzzy_match",
                    )
            else:
                # Multiple matches - ambiguous
                candidates = []
                for entry in matches:
                    identity = _lookup_to_identity(entry, lookup)
                    if identity:
                        candidates.append(identity)

                if candidates:
                    recommended = _recommend_candidate(
                        candidates,
                        active_stock=active_stock,
                        watchlist=watchlist,
                        recent_codes=recent_codes,
                        default_market=default_market,
                    )

                    return IntakeObjectResolution(
                        status="ambiguous",
                        scope=candidates[0].asset_type,
                        candidates=tuple(candidates),
                        recommended_option_id=recommended[0] if recommended else None,
                        recommendation_reason=recommended[1] if recommended else None,
                        evidence={
                            "kind": "fuzzy_match_multiple",
                            "text": token,
                            "start": start,
                            "end": end,
                        },
                        reason="fuzzy_match",
                    )

    # ─────────────────────────────────────────────────────────────────
    # Step 7: Missing - no object found
    # ─────────────────────────────────────────────────────────────────

    return IntakeObjectResolution(
        status="missing",
        scope="unknown",
        reason="no_object",
    )


def _recommend_candidate(
    candidates: List[ObjectIdentity],
    *,
    active_stock: Optional[Dict[str, Any]] = None,
    watchlist: Sequence[str] = (),
    recent_codes: Sequence[str] = (),
    default_market: str = "CN",
) -> Optional[Tuple[str, str]]:
    """Recommend a candidate based on context.

    Returns (option_id, reason) or None.
    """
    if not candidates:
        return None

    # Priority 1: Match active stock market
    if active_stock:
        active_market = active_stock.get("market")
        if active_market:
            for candidate in candidates:
                if candidate.market == active_market:
                    option_id = f"{candidate.market}_{candidate.stock_code}"
                    reason = f"与你当前会话的 {_get_market_label(active_market)} 标的一致"
                    return (option_id, reason)

    # Priority 2: Match watchlist
    for candidate in candidates:
        if candidate.stock_code in watchlist:
            option_id = f"{candidate.market}_{candidate.stock_code}"
            reason = "在你的自选列表中"
            return (option_id, reason)

    # Priority 3: Match recent analysis
    for candidate in candidates:
        if candidate.stock_code in recent_codes:
            option_id = f"{candidate.market}_{candidate.stock_code}"
            reason = "你最近分析过这只"
            return (option_id, reason)

    # Priority 4: Default market
    for candidate in candidates:
        if candidate.market == default_market:
            option_id = f"{candidate.market}_{candidate.stock_code}"
            reason = f"{_get_market_label(default_market)}主市场默认推荐"
            return (option_id, reason)

    # Fallback: First candidate
    first = candidates[0]
    option_id = f"{first.market}_{first.stock_code}"
    reason = "名称完全匹配"
    return (option_id, reason)


__all__ = [
    "IntakeObjectResolution",
    "resolve_intake_object",
]
