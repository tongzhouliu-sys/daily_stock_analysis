# -*- coding: utf-8 -*-
"""Process-wide runtime status cache for on-demand data providers.

The cache is intentionally tiny and side-effect free: fetchers record the
result of real requests, and the background probe records periodic light
requests.  The data-center API only reads this cache, so opening the page never
triggers an external request or consumes provider quota.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

_FETCHER_PROVIDER_MAP = {
    "EfinanceFetcher": "efinance",
    "AkshareFetcher": "akshare",
    "TencentFetcher": "tencent",
    "YfinanceFetcher": "yfinance",
    "PytdxFetcher": "pytdx",
    "BaostockFetcher": "baostock",
    "TushareFetcher": "tushare",
    "TickPlusFetcher": "tickplus",
    "TickFlowFetcher": "tickflow",
    "LongbridgeFetcher": "longbridge",
    "FutuFetcher": "futu",
    "FinnhubFetcher": "finnhub",
    "AlphaVantageFetcher": "alphavantage",
}

_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s'\"<>]+")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:apikey|api_key|token|access_token|key|auth)=)[^&\s]+"
)
_QUOTA_MARKERS = (
    "rate limit",
    "ratelimited",
    "quota",
    "daily limit",
    "api call frequency",
    "calls per day",
    "calls per minute",
    "too many requests",
    "premium plan",
    "upgrade to premium",
    "run out of",
    "limit reached",
    "429",
)

_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.RLock()


def provider_token_for_fetcher(fetcher_or_name: Any) -> Optional[str]:
    """Return the stable provider token for a fetcher instance or class name."""

    if fetcher_or_name is None:
        return None
    name = fetcher_or_name if isinstance(fetcher_or_name, str) else getattr(fetcher_or_name, "name", "")
    return _FETCHER_PROVIDER_MAP.get(str(name or "").strip())


def sanitize_error_summary(error: Any, *, secrets: Iterable[str] = ()) -> str:
    """Build a short, non-sensitive error summary.

    URLs are removed entirely and common credential query parameters are
    redacted.  Callers may also pass the actual key pool values so that a key
    that happens to appear in an exception message is masked before the summary
    reaches the API or logs.
    """

    if isinstance(error, str):
        text = error
    else:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            reason = str(getattr(response, "reason", "") or "").strip()
            text = f"HTTP {status_code} {reason}".strip()
        else:
            message = " ".join(str(error).split())
            text = f"{type(error).__name__}: {message}" if message else type(error).__name__

    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "***")
    text = _URL_PATTERN.sub("<url>", text)
    text = _QUERY_SECRET_PATTERN.sub(r"\1***", text)
    text = " ".join(text.split())
    return text[:300]


def is_quota_limited_error(error: Any) -> bool:
    """Return True for provider rate-limit / exhausted-quota failures.

    Authentication / permission failures deliberately stay False so they can be
    reported as ``unavailable`` instead of being masked as a quota warning.
    """

    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    text = str(error or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _QUOTA_MARKERS)


def record_runtime_result(
    provider: Any,
    *,
    ok: bool,
    error: Any = None,
    quota_limited: bool = False,
    checked_at: Optional[float] = None,
    source: str = "runtime",
    secrets: Iterable[str] = (),
) -> Optional[Dict[str, Any]]:
    """Record one provider result and return the stored snapshot."""

    token = provider if isinstance(provider, str) else provider_token_for_fetcher(provider)
    if not token:
        return None

    timestamp = float(checked_at if checked_at is not None else time.time())
    warnings: List[str] = ["quota_limited"] if quota_limited else []
    entry: Dict[str, Any] = {
        "provider": token,
        "status": "ok" if ok or quota_limited else "unavailable",
        "checked_at": timestamp,
        "error": None if ok or quota_limited else sanitize_error_summary(error, secrets=secrets),
        "warnings": warnings,
        "source": source,
    }
    with _cache_lock:
        _cache[token] = entry
    return dict(entry)


def get_runtime_result(provider: Any) -> Optional[Dict[str, Any]]:
    """Return a copy of the last runtime result for a provider, if any."""

    token = provider if isinstance(provider, str) else provider_token_for_fetcher(provider)
    if not token:
        return None
    with _cache_lock:
        entry = _cache.get(token)
        if entry is None:
            return None
        snapshot = dict(entry)
        snapshot["warnings"] = list(entry.get("warnings") or [])
        return snapshot


def clear_runtime_results(provider: Optional[Any] = None) -> None:
    """Clear one provider entry, or the whole cache when provider is omitted."""

    with _cache_lock:
        if provider is None:
            _cache.clear()
            return
        token = provider if isinstance(provider, str) else provider_token_for_fetcher(provider)
        if token:
            _cache.pop(token, None)
