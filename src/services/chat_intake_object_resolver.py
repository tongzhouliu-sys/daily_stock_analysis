"""v2: Deterministic object resolution for smart intake.

The resolver answers one question before the intake flow selects questions:
*what, if anything, is the user asking about?*  It reads only the local stock
index and the caller's session snapshot; it never calls the network or a model.

Resolution order (requirements R1 / 9.1):

1. explicit code tokens (bare, prefixed, suffixed, ticker)
2. exact names and aliases from ``get_chat_stock_lookup()``
3. compare expressions with at least two participants
4. the current session object for follow-up pronouns
5. the active report object
6. market / portfolio / watchlist scope
7. concept questions
8. local fuzzy name matching (one candidate still requires confirmation)
9. missing

The public return type is the Pydantic ``ObjectResolution`` from
``src.schemas.agent_intake``.  The previous dataclass duplicate was removed in
the T1/T2 convergence; callers must not maintain a second contract.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.agent.chat_object_decision import object_mentions
from src.agent.stock_scope import (
    _CHOICE_COMPARE_PATTERN,
    _STRONG_COMPARE_PATTERN,
    _WEAK_COMPARE_HINT_PATTERN,
)
from src.schemas.agent_intake import (
    ObjectIdentity,
    ObjectResolution,
    ResolutionEvidence,
    ResolutionScope,
    ResolutionStatus,
)
from src.services.stock_code_utils import normalize_code
from src.services.stock_list_parser import (
    AnalysisTarget,
    IndexRegistry,
    ParseStatus,
    default_index_registry,
    parse_analysis_target,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MAX_CANDIDATES = 5
_SUPPORTED_MARKETS = frozenset({"CN", "HK", "US", "JP", "KR", "TW"})

_MARKET_SHORT_LABELS = {
    "CN": "A股",
    "HK": "港股",
    "US": "美股",
    "JP": "日股",
    "KR": "韩股",
    "TW": "台股",
}

_EXCHANGE_NAMES = {
    "SH": "上交所",
    "SZ": "深交所",
    "BJ": "北交所",
    "HK": "港交所",
}

_MARKET_PATTERN = re.compile(
    r"大盘|市场|A股|港股|美股|日股|韩股|台股|上证|深证|恒生|纳指|标普|道指"
    r"|\bmarket\b|\ba-share\b",
    re.IGNORECASE,
)
_PORTFOLIO_PATTERN = re.compile(r"持仓|组合|我的持仓|\bportfolio\b|\bholdings?\b", re.IGNORECASE)
_WATCHLIST_PATTERN = re.compile(r"自选|我的自选|\bwatchlist\b", re.IGNORECASE)
_CONCEPT_PATTERN = re.compile(
    r"什么是|什么叫|是什么意思|怎么理解|如何理解|解释一下|请解释"
    r"|\bwhat\s+is\b|\bexplain\b",
    re.IGNORECASE,
)
_REPORT_REFERENCE_PATTERN = re.compile(
    r"这份报告|报告里|报告中|报告上|上面提到|刚才的报告|该报告|\bthis\s+report\b",
    re.IGNORECASE,
)
_SESSION_REFERENCE_PATTERN = re.compile(
    r"继续|再看看|接着看|那现在|它(?:最近|现在|今天|这|的|还|又|怎么|走势)?|"
    r"这只|该股|这家公司",
)
_PRONOUN_PATTERN = re.compile(r"它|这只|该股|这家公司")
_COMPARE_CONJUNCTION_PATTERN = re.compile(r"和|与|跟")

# Tokens that look like tickers but are finance/analysis jargon or exchange
# codes.  This is intentionally small and topical; it is not a stock list.
_CODE_STOPWORDS = frozenset({
    "A", "AI", "API", "BJ", "BOLL", "CAGR", "CEO", "CFO", "CN", "CPI", "CTO",
    "DCF", "EMA", "EPS", "ETF", "FCF", "GDP", "HK", "HKD", "IPO", "KDJ",
    "LLM", "MA", "MACD", "ML", "NAV", "OK", "PB", "PE", "PMI", "PPI", "QOQ",
    "ROA", "ROE", "ROI", "RSI", "SH", "SMA", "SS", "SZ", "TTM", "UI", "US",
    "USD", "UX", "VS", "VWAP", "YOY", "YTD",
})

_SUPPORTED_SUFFIXES = frozenset({
    "SH", "SZ", "SS", "BJ", "HK", "US", "T", "KS", "KQ", "TW", "CSI",
})

_CODE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"[A-Za-z]{1,6}\d{2,6}(?:\.[A-Za-z]{1,3})?"   # hk700 / SH600519
    r"|\d{1,6}\.[A-Za-z]{1,3}"                     # 0700.HK / 000300.SH
    r"|[A-Za-z]{2,6}\.[A-Za-z]{1,3}"               # AAPL.US / BRK.B
    r"|\d{2,6}"                                     # 600519 / 00700
    r"|[A-Za-z]{2,6}"                               # AAPL / SHOP
    r")"
    r"(?![A-Za-z0-9])"
)
_SUFFIX_RE = re.compile(r"\.([A-Za-z]{1,3})$")
_LEADING_INTENT_RE = re.compile(
    r"^\s*(?:请|请问|帮我|帮忙|麻烦)?\s*"
    r"(?:分析一下|分析下|分析|看看|研究一下|研究|诊断一下|诊断|了解一下|了解|看一下|看下)"
    r"\s*"
)
_FUZZY_TRAILING_RE = re.compile(
    r"(?:最近|目前|现在|今天|明天|这周|本周|走势|行情|股价|怎么样|怎么|如何|"
    r"分析|看看|一下|的|吗|呢|啊|吧|呀).*$"
)


# ─────────────────────────────────────────────────────────────────────────────
# Internal value objects
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Participant:
    """One object mention in the input, before the final decision."""

    start: int
    end: int
    text: str
    status: ResolutionStatus
    identities: Tuple[ObjectIdentity, ...] = ()
    reason: Optional[str] = None
    evidence_kind: str = "code_token"
    strength: str = "strong"


# ─────────────────────────────────────────────────────────────────────────────
# Cached local index
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _default_registry() -> IndexRegistry:
    return default_index_registry()


def _get_registry(registry: Optional[IndexRegistry]) -> IndexRegistry:
    return registry if registry is not None else _default_registry()


def _get_lookup(lookup: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if lookup is not None:
        return lookup
    from src.data.stock_index_loader import get_chat_stock_lookup

    return get_chat_stock_lookup()


# ─────────────────────────────────────────────────────────────────────────────
# Small normalization helpers
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _fold(text: str) -> str:
    return _normalize_text(text).casefold()


def _normalize_market(value: Optional[str], default: str = "CN") -> str:
    first = str(value or "").strip().split(",", 1)[0].strip().upper()
    return first if first in _SUPPORTED_MARKETS else default


def _market_from_exchange(exchange: Optional[str]) -> str:
    return {
        "SH": "CN", "SZ": "CN", "BJ": "CN", "SS": "CN", "CSI": "CN",
        "HK": "HK", "US": "US", "NASDAQ": "US", "NYSE": "US", "AMEX": "US",
        "T": "JP", "JP": "JP", "KS": "KR", "KQ": "KR", "KR": "KR",
        "TW": "TW",
    }.get((exchange or "").strip().upper(), "UNKNOWN")


def _market_from_code(stock_code: str, canonical_id: Optional[str] = None) -> str:
    for value in (stock_code, canonical_id or ""):
        upper = str(value or "").strip().upper()
        if not upper:
            continue
        if upper.startswith("HK") or upper.endswith(".HK"):
            return "HK"
        if (upper.startswith(("SH", "SZ", "BJ", "CSI"))
                or upper.endswith((".SH", ".SZ", ".BJ", ".CSI"))):
            return "CN"
        if upper.endswith(".T"):
            return "JP"
        if upper.endswith((".KS", ".KQ")):
            return "KR"
        if upper.endswith(".TW"):
            return "TW"
    code = str(stock_code or "").strip()
    if re.fullmatch(r"\d{6}", code):
        return "CN"
    if re.fullmatch(r"\d{5}", code):
        return "HK"
    if re.fullmatch(r"[A-Za-z][A-Za-z.]*", code):
        return "US"
    return "UNKNOWN"


def _exchange_from_code(stock_code: str, canonical_id: Optional[str] = None) -> Optional[str]:
    for value in (stock_code, canonical_id or ""):
        upper = str(value or "").strip().upper()
        if not upper:
            continue
        if upper.startswith("SH"):
            return "SH"
        if upper.startswith("SZ"):
            return "SZ"
        if upper.startswith("BJ"):
            return "BJ"
        if upper.startswith("HK"):
            return "HK"
        if upper.startswith("CSI"):
            return "CSI"
        if upper.endswith(".SH"):
            return "SH"
        if upper.endswith(".SZ"):
            return "SZ"
        if upper.endswith(".BJ"):
            return "BJ"
        if upper.endswith(".HK"):
            return "HK"
        if upper.endswith(".CSI"):
            return "CSI"
    return None


def _market_label(market: str, exchange: Optional[str] = None) -> str:
    short = _MARKET_SHORT_LABELS.get(market)
    if short is None:
        return "未知市场"
    exchange_name = _EXCHANGE_NAMES.get((exchange or "").upper())
    return f"{short} · {exchange_name}" if exchange_name else short


def _market_short_label(market: str) -> str:
    return _MARKET_SHORT_LABELS.get(market, "未知市场")


def _scope_for_asset_type(asset_type: str) -> ResolutionScope:
    return {
        "stock": ResolutionScope.STOCK,
        "index": ResolutionScope.INDEX,
        "etf": ResolutionScope.ETF,
        "fund": ResolutionScope.ETF,
    }.get(asset_type, ResolutionScope.UNKNOWN)


def _dedupe_identities(identities: Iterable[ObjectIdentity]) -> Tuple[ObjectIdentity, ...]:
    seen = set()
    result: List[ObjectIdentity] = []
    for identity in identities:
        key = (identity.stock_code, identity.canonical_id, identity.asset_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(identity)
    return tuple(result)


def _bare_code(stock_code: str) -> str:
    upper = stock_code.upper()
    if upper.startswith("HK") and len(stock_code) > 2:
        return stock_code[2:]
    if upper.startswith(("SH", "SZ", "BJ")) and len(stock_code) > 2:
        return stock_code[2:]
    return stock_code


def _option_id(identity: ObjectIdentity) -> str:
    """Stable identity option id, e.g. ``CN_600519`` / ``HK_00700``."""
    if identity.asset_type == "index":
        return f"{identity.market}_{identity.canonical_id}"
    return f"{identity.market}_{_bare_code(identity.stock_code)}"


def _identity_keys(identity: ObjectIdentity) -> set[str]:
    keys = {
        identity.stock_code.upper(),
        identity.canonical_id.upper(),
        _bare_code(identity.stock_code).upper(),
    }
    normalized = normalize_code(identity.stock_code)
    if normalized:
        keys.add(normalized.upper())
    normalized = normalize_code(identity.canonical_id)
    if normalized:
        keys.add(normalized.upper())
    return keys


def _code_in(identity: ObjectIdentity, codes: Sequence[str]) -> bool:
    wanted = set()
    for code in codes:
        text = str(code or "").strip()
        if not text:
            continue
        wanted.add(text.upper())
        wanted.add(_bare_code(text).upper())
        normalized = normalize_code(text)
        if normalized:
            wanted.add(normalized.upper())
    return bool(wanted & _identity_keys(identity))


# ─────────────────────────────────────────────────────────────────────────────
# Lookup row → ObjectIdentity
# ─────────────────────────────────────────────────────────────────────────────


def _identity_from_row(row: Tuple[str, str, str]) -> Optional[ObjectIdentity]:
    code, label, kind = row
    code = str(code or "").strip()
    label = str(label or "").strip()
    kind = "index" if kind == "index" else ("etf" if kind == "etf" else "stock")
    if not code:
        return None
    upper = code.upper()

    if kind == "index":
        market = _market_from_code(code)
        exchange = _exchange_from_code(code)
        return ObjectIdentity(
            stock_code=code,
            stock_name=label or None,
            canonical_id=code,
            asset_type="index",
            market=market,
            market_label=_market_label(market, exchange),
        )

    if upper.endswith(".HK"):
        bare = code[:-3]
        stock_code = f"HK{bare}"
        market, canonical = "HK", stock_code
    elif upper.endswith((".SH", ".SZ", ".BJ")):
        stock_code, market, canonical = code[:-3], "CN", code
    elif upper.endswith(".T"):
        stock_code, market, canonical = code, "JP", code
    elif upper.endswith((".KS", ".KQ")):
        stock_code, market, canonical = code, "KR", code
    elif upper.endswith(".TW"):
        stock_code, market, canonical = code, "TW", code
    elif upper.endswith(".US"):
        stock_code, market, canonical = code[:-3], "US", code[:-3]
    else:
        # Unsuffixed rows are US tickers (including share classes such as
        # ``BRK.B``); other markets carry an exchange suffix in the index.
        stock_code, market, canonical = code, "US", code

    exchange = _exchange_from_code(code, canonical)
    return ObjectIdentity(
        stock_code=stock_code,
        stock_name=label or None,
        canonical_id=canonical,
        asset_type=kind,
        market=market,
        market_label=_market_label(market, exchange),
    )


def _rows_for_code_token(token: str, lookup: Dict[str, Any]) -> Tuple[Tuple[str, str, str], ...]:
    codes = lookup.get("codes") or {}
    text = str(token or "").strip()
    if not text:
        return ()
    candidates = [text, text.upper()]
    normalized = normalize_code(text)
    if normalized:
        candidates.extend([normalized, normalized.upper()])
        if re.fullmatch(r"\d{5}", normalized):
            candidates.extend([f"{normalized}.HK", f"HK{normalized}"])
        if re.fullmatch(r"\d{6}", normalized):
            candidates.extend(f"{normalized}.{suffix}" for suffix in ("SH", "SZ", "BJ"))
    if text.upper().endswith(".US"):
        candidates.append(text[:-3])
    for key in candidates:
        rows = codes.get(key)
        if rows:
            return tuple(rows)
    return ()


def _pick_row(
    rows: Sequence[Tuple[str, str, str]], asset_type: str,
) -> Optional[Tuple[str, str, str]]:
    for row in rows:
        if row[2] == asset_type:
            return row
    return rows[0] if rows else None


def _identity_from_stock_identity(
    identity: Any, lookup: Dict[str, Any],
) -> Optional[ObjectIdentity]:
    rows = (_rows_for_code_token(identity.stock_code, lookup)
            or _rows_for_code_token(identity.canonical_id, lookup))
    row = _pick_row(rows, identity.asset_type)
    if row is not None:
        return _identity_from_row(row)

    market = _market_from_code(identity.stock_code, identity.canonical_id)
    if market == "UNKNOWN":
        return None
    return ObjectIdentity(
        stock_code=identity.stock_code,
        stock_name=getattr(identity, "stock_name", None),
        canonical_id=identity.canonical_id,
        asset_type=identity.asset_type,
        market=market,
        market_label=_market_label(
            market, _exchange_from_code(identity.stock_code, identity.canonical_id),
        ),
    )


def _identity_from_target(target: AnalysisTarget, lookup: Dict[str, Any]) -> ObjectIdentity:
    market = _market_from_exchange(target.exchange)
    if market == "UNKNOWN":
        market = _market_from_code(target.display_code, target.canonical_id)

    if target.asset_type == ParseStatus.INDEX:
        matched = target.matched_index
        name = matched.display_name if matched is not None else target.display_code
        stock_code = target.canonical_id or target.display_code
        return ObjectIdentity(
            stock_code=stock_code,
            stock_name=name or None,
            canonical_id=stock_code,
            asset_type="index",
            market=market,
            market_label=_market_label(market, target.exchange or _exchange_from_code(stock_code)),
        )

    rows = (_rows_for_code_token(target.display_code, lookup)
            or _rows_for_code_token(target.canonical_id, lookup))
    row = _pick_row(rows, target.asset_type)
    if row is not None:
        return _identity_from_row(row)  # type: ignore[return-value]

    stock_code = target.display_code or target.canonical_id
    canonical = target.canonical_id or stock_code
    return ObjectIdentity(
        stock_code=stock_code,
        stock_name=None,
        canonical_id=canonical,
        asset_type=target.asset_type or "stock",
        market=market,
        market_label=_market_label(market, target.exchange or _exchange_from_code(canonical)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Explicit code tokens
# ─────────────────────────────────────────────────────────────────────────────


def _code_tokens(text: str) -> List[Tuple[str, int, int]]:
    return [(match.group(0), match.start(), match.end())
            for match in _CODE_TOKEN_RE.finditer(text)]


def _is_code_stopword(token: str) -> bool:
    return token.strip().upper() in _CODE_STOPWORDS


def _has_unsupported_suffix(token: str) -> bool:
    match = _SUFFIX_RE.search(token)
    if match is None:
        return False
    suffix = match.group(1).upper()
    if suffix in _SUPPORTED_SUFFIXES:
        return False
    # ``BRK.B`` is a US share class, not an exchange suffix.  A single
    # unsupported letter such as ``VOD.L`` is treated as an unsupported market.
    if suffix in {"A", "B", "C"} and re.fullmatch(r"[A-Za-z]{1,5}\.[A-Za-z]", token):
        return False
    return True


def _code_shape(token: str) -> str:
    if re.fullmatch(r"\d{6}", token):
        return "bare6"
    if re.fullmatch(r"\d{5}", token):
        return "bare5"
    if re.fullmatch(r"\d+", token):
        return "bare_other"
    if re.fullmatch(r"(?i)(?:sh|sz|bj|hk|us)[A-Za-z0-9.]{1,8}", token):
        return "prefixed"
    if re.search(r"\.", token):
        return "suffixed"
    if re.fullmatch(r"[A-Za-z]{2,6}", token):
        return "ticker"
    return "other"


def _code_evidence_kind(token: str, shape: str, target: Optional[AnalysisTarget]) -> str:
    if target is not None and target.asset_type == ParseStatus.INDEX:
        return "index_code"
    return {
        "bare6": "bare_cn_code",
        "bare5": "hk_code",
        "prefixed": "prefixed_code",
        "suffixed": "suffix_code",
        "ticker": "us_ticker",
    }.get(shape, "code_token")


def _resolve_code_token(
    token: str, registry: IndexRegistry, lookup: Dict[str, Any],
) -> Optional[_Participant]:
    shape = _code_shape(token)
    evidence_kind = _code_evidence_kind(token, shape, None)
    strength = "strong" if shape in {"bare5", "bare6", "prefixed", "suffixed"} else "weak"

    if _has_unsupported_suffix(token):
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.UNSUPPORTED,
            reason="unsupported_market",
            evidence_kind="unsupported_code",
            strength="strong",
        )

    rows = _rows_for_code_token(token, lookup)
    if rows:
        identities = tuple(
            identity for row in rows
            for identity in (_identity_from_row(row),)
            if identity is not None
        )
        identities = _dedupe_identities(identities)
        if len(identities) == 1:
            return _Participant(
                start=0, end=0, text=token,
                status=ResolutionStatus.RESOLVED,
                identities=identities,
                reason="unique_code",
                evidence_kind=evidence_kind,
            )
        if identities:
            return _Participant(
                start=0, end=0, text=token,
                status=ResolutionStatus.AMBIGUOUS,
                identities=identities[:_MAX_CANDIDATES],
                reason="cross_market_listing",
                evidence_kind=evidence_kind,
            )

    # A five-digit bare code is a Hong Kong code by shape.  If the local index
    # does not contain it we must not let parse_analysis_target() turn it into
    # an A-share/BJ code just because it starts with 9/4/8.
    if shape == "bare5" or shape == "bare_other":
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.MISSING,
            reason="invalid_code",
            evidence_kind=evidence_kind,
            strength=strength,
        )

    try:
        target = parse_analysis_target(token, registry)
    except Exception:
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.MISSING,
            reason="invalid_code",
            evidence_kind=evidence_kind,
            strength=strength,
        )

    if target.asset_type == ParseStatus.INDEX and target.matched_index is not None:
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.RESOLVED,
            identities=(_identity_from_target(target, lookup),),
            reason="unique_code",
            evidence_kind="index_code",
        )
    if target.asset_type == ParseStatus.UNSUPPORTED:
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.MISSING,
            reason="invalid_code",
            evidence_kind=evidence_kind,
            strength=strength,
        )

    market = _market_from_exchange(target.exchange)
    if market == "UNKNOWN":
        market = _market_from_code(target.display_code, target.canonical_id)

    # ``target.normalized_code`` rescues suffix/prefixed forms that the lookup
    # only stores under their bare code (0700.HK → 00700).
    normalized = normalize_code(target.normalized_code or target.display_code or "")
    if normalized:
        rows = _rows_for_code_token(normalized, lookup)
        if rows:
            row = _pick_row(rows, target.asset_type)
            if row is not None:
                identity = _identity_from_row(row)
                if identity is not None:
                    return _Participant(
                        start=0, end=0, text=token,
                        status=ResolutionStatus.RESOLVED,
                        identities=(identity,),
                        reason="unique_code",
                        evidence_kind=_code_evidence_kind(token, shape, target),
                    )

    if market in {"JP", "KR", "TW"}:
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.RESOLVED,
            identities=(_identity_from_target(target, lookup),),
            reason="unique_code",
            evidence_kind=_code_evidence_kind(token, shape, target),
        )
    if market in {"CN", "HK", "US"}:
        return _Participant(
            start=0, end=0, text=token,
            status=ResolutionStatus.MISSING,
            reason="invalid_code",
            evidence_kind=evidence_kind,
            strength=strength,
        )
    return _Participant(
        start=0, end=0, text=token,
        status=ResolutionStatus.UNSUPPORTED,
        reason="unsupported_market",
        evidence_kind="unsupported_code",
        strength="strong",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exact names and fuzzy names
# ─────────────────────────────────────────────────────────────────────────────


def _exact_name_participants(
    text: str, registry: IndexRegistry, lookup: Dict[str, Any],
) -> List[_Participant]:
    # ``object_mentions`` scans every registered name with a regex.  Narrow the
    # name table to the names that actually occur in this message first; the
    # membership test is a plain substring check and stays O(names) with a much
    # smaller constant.  The boundary/overlap logic below is still the shared
    # Chat implementation.
    folded = text.casefold()
    names = lookup.get("names") or {}
    matched_names = {name: rows for name, rows in names.items() if name in folded}
    if not matched_names:
        return []
    scoped_lookup = dict(lookup)
    scoped_lookup["names"] = matched_names

    try:
        mentions = object_mentions(text, registry, scoped_lookup)
    except Exception:
        logger.debug("[对象解析] 名称扫描失败", exc_info=True)
        return []

    participants: List[_Participant] = []
    for mention in mentions:
        if mention.kind != "name" or not mention.identities:
            continue
        identities = tuple(
            identity for item in mention.identities
            for identity in (_identity_from_stock_identity(item, lookup),)
            if identity is not None
        )
        identities = _dedupe_identities(identities)
        if not identities:
            continue
        matched = _fold(mention.raw)
        exact = any(
            identity.stock_name is not None and _fold(identity.stock_name) == matched
            for identity in identities
        )
        participants.append(_Participant(
            start=mention.start,
            end=mention.end,
            text=text[mention.start:mention.end],
            status=(ResolutionStatus.RESOLVED if len(identities) == 1
                    else ResolutionStatus.AMBIGUOUS),
            identities=identities,
            reason=("unique_name" if len(identities) == 1 else "cross_market_listing"),
            evidence_kind=("name_match" if exact else "alias_match"),
        ))
    return participants


def _fuzzy_seed(text: str) -> str:
    seed = _LEADING_INTENT_RE.sub("", text)
    seed = _FUZZY_TRAILING_RE.sub("", seed)
    seed = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff.]+", " ", seed).strip()
    return seed


def _fuzzy_candidates(text: str, lookup: Dict[str, Any]) -> Tuple[ObjectIdentity, ...]:
    seed = _fuzzy_seed(text)
    if not seed:
        return ()
    folded = seed.casefold()
    if folded.isascii() and len(folded) < 3:
        return ()
    if not folded.isascii() and len(folded) < 2:
        return ()

    identities: List[ObjectIdentity] = []
    names = lookup.get("names") or {}
    for name, rows in names.items():
        if folded not in name:
            continue
        for row in rows:
            identity = _identity_from_row(row)
            if identity is not None:
                identities.append(identity)
        if len(_dedupe_identities(identities)) >= _MAX_CANDIDATES:
            break
    return _dedupe_identities(identities)[:_MAX_CANDIDATES]


# ─────────────────────────────────────────────────────────────────────────────
# Session / report context
# ─────────────────────────────────────────────────────────────────────────────


def _identity_from_context(
    context: Optional[Dict[str, Any]], lookup: Dict[str, Any],
) -> Optional[ObjectIdentity]:
    if not context:
        return None
    stock_code = str(context.get("stock_code") or "").strip()
    if not stock_code:
        return None
    canonical_id = str(context.get("canonical_id") or stock_code)
    asset_type = str(context.get("asset_type") or "stock")
    rows = (_rows_for_code_token(stock_code, lookup)
            or _rows_for_code_token(canonical_id, lookup))
    row = _pick_row(rows, asset_type)
    if row is not None:
        identity = _identity_from_row(row)
        if identity is not None:
            if context.get("stock_name") and not identity.stock_name:
                identity = identity.model_copy(update={"stock_name": context["stock_name"]})
            return identity

    market = _normalize_market(context.get("market"), default="")
    if not market:
        market = _market_from_code(stock_code, canonical_id)
    if market == "UNKNOWN":
        return None
    return ObjectIdentity(
        stock_code=stock_code,
        stock_name=context.get("stock_name") or None,
        canonical_id=canonical_id,
        asset_type=asset_type,
        market=market,
        market_label=_market_label(
            market, _exchange_from_code(stock_code, canonical_id),
        ),
    )


def _active_market(active_stock: Optional[Dict[str, Any]]) -> Optional[str]:
    if not active_stock:
        return None
    market = _normalize_market(active_stock.get("market"), default="")
    if market:
        return market
    derived = _market_from_code(
        str(active_stock.get("stock_code") or ""),
        str(active_stock.get("canonical_id") or ""),
    )
    return derived if derived in _SUPPORTED_MARKETS else None


# ─────────────────────────────────────────────────────────────────────────────
# Candidate ordering / recommendation
# ─────────────────────────────────────────────────────────────────────────────


def _candidate_sort_key(
    identity: ObjectIdentity,
    *,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
    matched_text: str,
) -> tuple:
    exact = bool(
        identity.stock_name
        and _fold(identity.stock_name) == _fold(matched_text)
    )
    return (
        0 if active_market and identity.market == active_market else 1,
        0 if _code_in(identity, watchlist) else 1,
        0 if _code_in(identity, recent_codes) else 1,
        0 if exact else 1,
        0 if identity.asset_type == "stock" else 1,
        0 if identity.market == default_market else 1,
        identity.canonical_id.casefold(),
    )


def _recommendation_reason(
    identity: ObjectIdentity,
    *,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
    matched_text: str,
    single_fuzzy: bool = False,
) -> str:
    if single_fuzzy:
        return "名称相近，请确认"
    if active_market and identity.market == active_market:
        return f"与你当前会话的 {_market_short_label(active_market)} 标的一致"
    if _code_in(identity, watchlist):
        return "在你的自选列表中"
    if _code_in(identity, recent_codes):
        return "你最近分析过这只"
    if identity.market == default_market:
        return f"{_market_short_label(default_market)}主市场默认推荐"
    if identity.stock_name and _fold(identity.stock_name) == _fold(matched_text):
        return "名称完全匹配"
    return "代码精确匹配"


def _rank_candidates(
    identities: Sequence[ObjectIdentity],
    *,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
    matched_text: str,
) -> Tuple[Tuple[ObjectIdentity, ...], Optional[str], Optional[str]]:
    ordered = sorted(
        _dedupe_identities(identities),
        key=lambda identity: _candidate_sort_key(
            identity,
            active_market=active_market,
            watchlist=watchlist,
            recent_codes=recent_codes,
            default_market=default_market,
            matched_text=matched_text,
        ),
    )[:_MAX_CANDIDATES]
    if not ordered:
        return (), None, None
    top = ordered[0]
    return (
        tuple(ordered),
        _option_id(top),
        _recommendation_reason(
            top,
            active_market=active_market,
            watchlist=watchlist,
            recent_codes=recent_codes,
            default_market=default_market,
            matched_text=matched_text,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Participant collection
# ─────────────────────────────────────────────────────────────────────────────


def _participant_identity_key(participant: _Participant) -> frozenset:
    return frozenset(
        (identity.stock_code, identity.canonical_id, identity.asset_type)
        for identity in participant.identities
    )


def _merge_adjacent(participants: List[_Participant], text: str) -> List[_Participant]:
    ordered = sorted(participants, key=lambda item: (item.start, item.end))
    merged: List[_Participant] = []
    for participant in ordered:
        if not merged:
            merged.append(participant)
            continue
        previous = merged[-1]
        gap = text[previous.end:participant.start] if previous.end <= participant.start else ""
        original = text[previous.start:participant.end]
        sets_intersect = bool(
            _participant_identity_key(previous) & _participant_identity_key(participant)
        )
        if (gap is not None and re.fullmatch(r"[\s,，、和与跟:：()（）\-]*", gap)
                and sets_intersect):
            # Prefer the resolved code when a name and its code sit together
            # (``中芯国际 00981``); otherwise keep the earliest evidence.
            chosen = previous
            if participant.status == ResolutionStatus.RESOLVED:
                chosen = participant
            elif previous.status != ResolutionStatus.RESOLVED:
                chosen = previous
            merged[-1] = _Participant(
                start=previous.start,
                end=max(previous.end, participant.end),
                text=original,
                status=chosen.status,
                identities=chosen.identities,
                reason=chosen.reason,
                evidence_kind=chosen.evidence_kind,
                strength=chosen.strength,
            )
            continue
        merged.append(participant)
    return merged


def _collect_participants(
    text: str,
    registry: IndexRegistry,
    lookup: Dict[str, Any],
    active_stock: Optional[Dict[str, Any]],
    *,
    include_pronouns: bool,
) -> List[_Participant]:
    participants: List[_Participant] = []
    for token, start, end in _code_tokens(text):
        if _is_code_stopword(token):
            continue
        resolved = _resolve_code_token(token, registry, lookup)
        if resolved is None:
            continue
        participants.append(_Participant(
            start=start,
            end=end,
            text=token,
            status=resolved.status,
            identities=resolved.identities,
            reason=resolved.reason,
            evidence_kind=resolved.evidence_kind,
            strength=resolved.strength,
        ))

    participants.extend(_exact_name_participants(text, registry, lookup))

    if include_pronouns:
        for match in _PRONOUN_PATTERN.finditer(text):
            identity = _identity_from_context(active_stock, lookup)
            if identity is None:
                participants.append(_Participant(
                    start=match.start(), end=match.end(), text=match.group(),
                    status=ResolutionStatus.MISSING, reason="no_object",
                    evidence_kind="session_inherited",
                ))
            else:
                participants.append(_Participant(
                    start=match.start(), end=match.end(), text=match.group(),
                    status=ResolutionStatus.RESOLVED, identities=(identity,),
                    reason="session_inherited", evidence_kind="session_inherited",
                ))

    return _merge_adjacent(participants, text)


def _dedupe_participants(participants: Sequence[_Participant]) -> List[_Participant]:
    result: List[_Participant] = []
    seen = set()
    for participant in participants:
        key = (
            participant.status,
            tuple((i.stock_code, i.canonical_id, i.asset_type) for i in participant.identities),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(participant)
    return result


def _has_compare_intent(text: str, participant_count: int) -> bool:
    if (_STRONG_COMPARE_PATTERN.search(text)
            or _WEAK_COMPARE_HINT_PATTERN.search(text)
            or _CHOICE_COMPARE_PATTERN.search(text)):
        return True
    return participant_count >= 2 and bool(_COMPARE_CONJUNCTION_PATTERN.search(text))


# ─────────────────────────────────────────────────────────────────────────────
# Resolution constructors
# ─────────────────────────────────────────────────────────────────────────────


def _evidence(participant: _Participant) -> ResolutionEvidence:
    return ResolutionEvidence(
        kind=participant.evidence_kind,
        text=participant.text,
        start=participant.start,
        end=participant.end,
    )


def _resolved(
    identity: ObjectIdentity,
    *,
    reason: str,
    evidence: Optional[ResolutionEvidence] = None,
    scope: Optional[ResolutionScope] = None,
) -> ObjectResolution:
    return ObjectResolution(
        status=ResolutionStatus.RESOLVED,
        scope=scope or _scope_for_asset_type(identity.asset_type),
        identity=identity,
        evidence=evidence,
        reason=reason,
    )


def _ambiguous(
    identity: ObjectIdentity,
    participants: Sequence[_Participant],
    *,
    reason: str,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
    scope: Optional[ResolutionScope] = None,
    objects: Sequence[ObjectIdentity] = (),
    single_fuzzy: bool = False,
) -> ObjectResolution:
    candidates, recommended, recommendation_reason = _rank_candidates(
        identity.identities,
        active_market=active_market,
        watchlist=watchlist,
        recent_codes=recent_codes,
        default_market=default_market,
        matched_text=identity.text,
    )
    if single_fuzzy and recommended is not None:
        top = next((item for item in candidates if _option_id(item) == recommended), None)
        if top is not None:
            recommendation_reason = _recommendation_reason(
                top,
                active_market=active_market,
                watchlist=watchlist,
                recent_codes=recent_codes,
                default_market=default_market,
                matched_text=identity.text,
                single_fuzzy=True,
            )
    return ObjectResolution(
        status=ResolutionStatus.AMBIGUOUS,
        scope=scope or (_scope_for_asset_type(candidates[0].asset_type) if candidates else ResolutionScope.UNKNOWN),
        objects=list(objects),
        candidates=list(candidates),
        recommended_option_id=recommended,
        recommendation_reason=recommendation_reason,
        evidence=_evidence(identity),
        reason=reason,
    )


def _single_object_resolution(
    participants: Sequence[_Participant],
    *,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
) -> Optional[ObjectResolution]:
    if not participants:
        return None

    # A resolved/ambiguous object always wins over incidental unresolved
    # tokens such as a year (``2026``) or an unresolved lowercase word.
    concrete = [item for item in participants if item.status != ResolutionStatus.MISSING]
    if concrete:
        selected = concrete
    else:
        strong = [item for item in participants if item.strength == "strong"]
        selected = strong or list(participants)

    unique = _dedupe_participants(selected)
    if len(unique) == 1:
        participant = unique[0]
        if participant.status == ResolutionStatus.RESOLVED:
            return _resolved(
                participant.identities[0],
                reason=participant.reason or "unique_code",
                evidence=_evidence(participant),
            )
        if participant.status == ResolutionStatus.AMBIGUOUS:
            return _ambiguous(
                participant,
                unique,
                reason=participant.reason or "cross_market_listing",
                active_market=active_market,
                watchlist=watchlist,
                recent_codes=recent_codes,
                default_market=default_market,
            )
        return ObjectResolution(
            status=(
                ResolutionStatus.UNSUPPORTED
                if participant.status == ResolutionStatus.UNSUPPORTED
                else ResolutionStatus.MISSING
            ),
            scope=ResolutionScope.UNKNOWN,
            evidence=_evidence(participant),
            reason=participant.reason or "no_object",
        )

    identities = tuple(
        identity for participant in unique for identity in participant.identities
    )
    candidates, recommended, recommendation_reason = _rank_candidates(
        identities,
        active_market=active_market,
        watchlist=watchlist,
        recent_codes=recent_codes,
        default_market=default_market,
        matched_text=unique[0].text,
    )
    if not candidates:
        first = unique[0]
        return ObjectResolution(
            status=ResolutionStatus.MISSING,
            scope=ResolutionScope.UNKNOWN,
            evidence=_evidence(first),
            reason=first.reason or "no_object",
        )
    return ObjectResolution(
        status=ResolutionStatus.AMBIGUOUS,
        scope=(_scope_for_asset_type(candidates[0].asset_type)
               if len({candidate.asset_type for candidate in candidates}) == 1
               else ResolutionScope.UNKNOWN),
        candidates=list(candidates),
        recommended_option_id=recommended,
        recommendation_reason=recommendation_reason,
        evidence=_evidence(unique[0]),
        reason="multiple_objects",
    )


def _compare_resolution(
    participants: Sequence[_Participant],
    *,
    active_market: Optional[str],
    watchlist: Sequence[str],
    recent_codes: Sequence[str],
    default_market: str,
) -> ObjectResolution:
    strong = [item for item in participants if item.strength == "strong"]
    selected = strong or list(participants)
    selected = _dedupe_participants(selected)

    if len(selected) < 2:
        first = selected[0] if selected else None
        return ObjectResolution(
            status=ResolutionStatus.MISSING,
            scope=ResolutionScope.COMPARE,
            evidence=_evidence(first) if first else None,
            reason="compare_incomplete",
        )

    resolved_objects = _dedupe_identities(
        identity for participant in selected
        if participant.status == ResolutionStatus.RESOLVED
        for identity in participant.identities
    )
    ambiguous = [item for item in selected if item.status == ResolutionStatus.AMBIGUOUS]
    missing = [item for item in selected if item.status == ResolutionStatus.MISSING]

    if ambiguous:
        candidates = _dedupe_identities(
            identity for participant in ambiguous for identity in participant.identities
        )
        ranked, recommended, recommendation_reason = _rank_candidates(
            candidates,
            active_market=active_market,
            watchlist=watchlist,
            recent_codes=recent_codes,
            default_market=default_market,
            matched_text=ambiguous[0].text,
        )
        return ObjectResolution(
            status=ResolutionStatus.AMBIGUOUS,
            scope=ResolutionScope.COMPARE,
            objects=list(resolved_objects),
            candidates=list(ranked),
            recommended_option_id=recommended,
            recommendation_reason=recommendation_reason,
            evidence=_evidence(ambiguous[0]),
            reason="compare_incomplete",
        )
    if missing or len(resolved_objects) < 2:
        first = missing[0] if missing else selected[0]
        return ObjectResolution(
            status=ResolutionStatus.MISSING,
            scope=ResolutionScope.COMPARE,
            objects=list(resolved_objects),
            evidence=_evidence(first),
            reason="compare_incomplete",
        )
    return ObjectResolution(
        status=ResolutionStatus.RESOLVED,
        scope=ResolutionScope.COMPARE,
        objects=list(resolved_objects),
        reason="multiple_objects",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
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
) -> ObjectResolution:
    """Resolve ``message`` to a concrete object identity or a blocker.

    The function is deterministic and side-effect free.  Index/lookup loading
    is cached; a malformed lookup or parser never raises to the API caller.
    """
    raw = (message or "").strip()
    if not raw:
        return ObjectResolution(
            status=ResolutionStatus.MISSING,
            scope=ResolutionScope.UNKNOWN,
            reason="no_object",
        )

    text = _normalize_text(raw)
    registry_obj = _get_registry(registry)
    lookup_obj = _get_lookup(lookup)
    market_default = _normalize_market(default_market)
    active_market = _active_market(active_stock)

    # Compare intent needs the pronoun participant even when the message has
    # no conventional code/name mention (``比较 它和 AAPL``).
    preliminary = _collect_participants(
        text, registry_obj, lookup_obj, active_stock, include_pronouns=False,
    )
    compare_intent = _has_compare_intent(text, len(preliminary))

    if compare_intent:
        participants = _collect_participants(
            text, registry_obj, lookup_obj, active_stock, include_pronouns=True,
        )
        return _compare_resolution(
            participants,
            active_market=active_market,
            watchlist=watchlist,
            recent_codes=recent_codes,
            default_market=market_default,
        )

    single = _single_object_resolution(
        preliminary,
        active_market=active_market,
        watchlist=watchlist,
        recent_codes=recent_codes,
        default_market=market_default,
    )
    if single is not None:
        return single

    # Follow-up pronouns inherit the current session object.
    if _SESSION_REFERENCE_PATTERN.search(text):
        identity = _identity_from_context(active_stock, lookup_obj)
        if identity is not None:
            return _resolved(
                identity,
                reason="session_inherited",
                evidence=ResolutionEvidence(
                    kind="session_inherited", text=text,
                ),
            )

    # Report follow-ups inherit the report object only when one is available.
    if _REPORT_REFERENCE_PATTERN.search(text):
        identity = _identity_from_context(report_context, lookup_obj)
        if identity is not None:
            return _resolved(
                identity,
                reason="report_context",
                evidence=ResolutionEvidence(kind="report_context", text=text),
            )
        return ObjectResolution(
            status=ResolutionStatus.MISSING,
            scope=ResolutionScope.REPORT,
            evidence=ResolutionEvidence(kind="report_reference", text=text),
            reason="no_object",
        )

    for pattern, scope in (
        (_MARKET_PATTERN, ResolutionScope.MARKET),
        (_PORTFOLIO_PATTERN, ResolutionScope.PORTFOLIO),
        (_WATCHLIST_PATTERN, ResolutionScope.WATCHLIST),
        (_CONCEPT_PATTERN, ResolutionScope.CONCEPT),
    ):
        match = pattern.search(text)
        if match is not None:
            return ObjectResolution(
                status=ResolutionStatus.NOT_REQUIRED,
                scope=scope,
                evidence=ResolutionEvidence(
                    kind="scope_keyword",
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                ),
                reason="no_object",
            )

    fuzzy = _fuzzy_candidates(text, lookup_obj)
    if fuzzy:
        candidates, recommended, recommendation_reason = _rank_candidates(
            fuzzy,
            active_market=active_market,
            watchlist=watchlist,
            recent_codes=recent_codes,
            default_market=market_default,
            matched_text=_fuzzy_seed(text),
        )
        single_fuzzy = len(candidates) == 1
        if single_fuzzy and recommended is not None:
            top = next((item for item in candidates if _option_id(item) == recommended), None)
            if top is not None:
                recommendation_reason = _recommendation_reason(
                    top,
                    active_market=active_market,
                    watchlist=watchlist,
                    recent_codes=recent_codes,
                    default_market=market_default,
                    matched_text=_fuzzy_seed(text),
                    single_fuzzy=True,
                )
        return ObjectResolution(
            status=ResolutionStatus.AMBIGUOUS,
            scope=_scope_for_asset_type(candidates[0].asset_type),
            candidates=list(candidates),
            recommended_option_id=recommended,
            recommendation_reason=recommendation_reason,
            evidence=ResolutionEvidence(
                kind="fuzzy_name", text=_fuzzy_seed(text) or text,
            ),
            reason="single_fuzzy_candidate" if single_fuzzy else "fuzzy_match",
        )

    return ObjectResolution(
        status=ResolutionStatus.MISSING,
        scope=ResolutionScope.UNKNOWN,
        reason="no_object",
    )


__all__ = [
    "resolve_intake_object",
]
