"""DB-first K-line history loader for Agent tools.

Provides:
- ContextVar-based frozen target_date propagation across threads
- ``load_history_df``: read from DB first, DataFetcherManager fallback
- Market-aware cache freshness (trading-calendar based)
- Concurrent dedup + 60-second in-memory cache for network fetches
- Optional DB write-back after network fetch

Fixes #1066 – eliminates 45+ redundant HTTP requests per stock in Agent mode.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from threading import Lock, RLock
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_MIN_RECORDS = 30
# In-memory cache TTL for network-fetched DataFrames (seconds).
_MEM_CACHE_TTL_SECONDS = 60
# Maximum number of entries in the in-memory cache.
_MEM_CACHE_MAX_ENTRIES = 64
# Network fetch window buckets (trading-day rounding).
_WINDOW_SMALL = 120
_WINDOW_LARGE = 365


class HistoryIdentityError(ValueError):
    """A strict tool identity cannot be preserved by the history read path."""


# ---------------------------------------------------------------------------
# Frozen target date (ContextVar) – set once per stock in pipeline, read by
# all agent tool threads via copy_context().run().
# ---------------------------------------------------------------------------
_frozen_target_date: contextvars.ContextVar[Optional[date]] = contextvars.ContextVar(
    "_frozen_target_date", default=None,
)


def set_frozen_target_date(d: date) -> contextvars.Token:
    return _frozen_target_date.set(d)


def get_frozen_target_date() -> Optional[date]:
    return _frozen_target_date.get()


def reset_frozen_target_date(token: contextvars.Token) -> None:
    _frozen_target_date.reset(token)


# ---------------------------------------------------------------------------
# Internal DataFetcherManager singleton (fallback only)
# ---------------------------------------------------------------------------
_fetcher_singleton = None
_fetcher_lock = Lock()


def _get_fetcher_manager():
    global _fetcher_singleton
    if _fetcher_singleton is None:
        with _fetcher_lock:
            if _fetcher_singleton is None:
                from data_provider import DataFetcherManager
                _fetcher_singleton = DataFetcherManager()
    return _fetcher_singleton


# ---------------------------------------------------------------------------
# Market-aware cache freshness
# ---------------------------------------------------------------------------
def _resolve_cache_end_date(
    stock_code: str,
    target_date: Optional[date],
) -> date:
    """Resolve the date that the cache must cover to be considered fresh.

    Priority:
    1. Explicit ``target_date`` (caller override)
    2. Frozen target date (pipeline ContextVar)
    3. Market-aware trading-calendar rule
    """
    if target_date is not None:
        return target_date
    frozen = get_frozen_target_date()
    if frozen is not None:
        return frozen
    try:
        from src.core.trading_calendar import get_required_daily_bar_date
        return get_required_daily_bar_date(stock_code)
    except Exception as exc:
        logger.debug("Cache end-date resolution failed, falling back to today: %s", exc)
        return date.today()


# ---------------------------------------------------------------------------
# In-memory dedup cache for network fetches
# ---------------------------------------------------------------------------
class _NetworkCache:
    """Thread-safe, time-bounded in-memory cache for network-fetched DataFrames.

    Concurrent callers for the same (code, end_date, window) key share a single
    in-flight fetch.  Results are cached for ``_MEM_CACHE_TTL_SECONDS`` and
    evicted LRU when the entry count exceeds ``_MEM_CACHE_MAX_ENTRIES``.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: OrderedDict[str, Tuple[float, pd.DataFrame, str]] = OrderedDict()
        self._inflight: Dict[str, Tuple[threading.Event, Optional[pd.DataFrame], Optional[str], Optional[Exception]]] = {}

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired_keys = [k for k, (ts, _, _) in self._entries.items() if now - ts > _MEM_CACHE_TTL_SECONDS]
        for k in expired_keys:
            del self._entries[k]
        while len(self._entries) > _MEM_CACHE_MAX_ENTRIES:
            self._entries.popitem(last=False)

    def get(self, key: str) -> Optional[Tuple[pd.DataFrame, str]]:
        with self._lock:
            self._evict_expired_locked()
            entry = self._entries.get(key)
            if entry is None:
                return None
            _ts, df, source = entry
            # Return a copy so callers cannot mutate the cached frame.
            return df.copy(), source

    def put(self, key: str, df: pd.DataFrame, source: str) -> None:
        with self._lock:
            self._evict_expired_locked()
            self._entries[key] = (time.monotonic(), df.copy(), source)

    def get_inflight(self, key: str) -> Optional[Tuple[threading.Event, Optional[pd.DataFrame], Optional[str], Optional[Exception]]]:
        with self._lock:
            return self._inflight.get(key)

    def start_inflight(self, key: str) -> Tuple[threading.Event, bool]:
        """Register an in-flight fetch. Returns (event, is_owner).

        ``is_owner`` is True when the caller should perform the fetch;
        False when another caller is already fetching and this caller should
        wait on the returned event.
        """
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return existing[0], False
            event = threading.Event()
            self._inflight[key] = (event, None, None, None)
            return event, True

    def finish_inflight(self, key: str, df: Optional[pd.DataFrame], source: Optional[str], error: Optional[Exception]) -> None:
        with self._lock:
            record = self._inflight.get(key)
            if record is None:
                return
            event, _, _, _ = record
            self._inflight[key] = (event, df, source, error)
            event.set()

    def wait_inflight(self, key: str, event: threading.Event, timeout: float = 120.0) -> Tuple[Optional[pd.DataFrame], Optional[str], Optional[Exception]]:
        event.wait(timeout=timeout)
        with self._lock:
            record = self._inflight.pop(key, None)
            if record is None:
                return None, None, Exception("inflight record vanished")
            _, df, source, error = record
            return df, source, error

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._inflight.clear()


_network_cache = _NetworkCache()


def _network_cache_key(normalized_code: str, end: date, window: int) -> str:
    return f"{normalized_code}|{end.isoformat()}|{window}"


def _round_fetch_window(days: int) -> int:
    """Round requested days up to one of two fetch-window buckets."""
    if days <= _WINDOW_SMALL:
        return _WINDOW_SMALL
    return _WINDOW_LARGE


# ---------------------------------------------------------------------------
# DB-first history loader
# ---------------------------------------------------------------------------
def _history_code_candidates(stock_code: str, stock_identity=None) -> Tuple[List[str], str]:
    from data_provider.base import canonical_stock_code, normalize_stock_code
    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    raw_code = str(stock_code or "").strip()
    if stock_identity is not None:
        if raw_code != stock_identity.stock_code:
            raise HistoryIdentityError("History identity does not match the bound tool code")
        return [stock_identity.stock_code], stock_identity.stock_code
    target = parse_analysis_target(raw_code)
    if target.asset_type == ParseStatus.INDEX:
        # Explicit index identities keep their canonical bucket (``sh000016``
        # / ``csi930955``) so index bars never land in the colliding stock
        # bucket (Story 1.5).
        return [target.canonical_id], target.canonical_id
    normalized_code = canonical_stock_code(normalize_stock_code(raw_code))
    candidates: List[str] = []
    for candidate in (canonical_stock_code(raw_code), normalized_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates, normalized_code


def _coerce_bar_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return date.min
    if hasattr(value, "date"):
        try:
            coerced = value.date()
            return coerced if isinstance(coerced, date) else date.min
        except Exception:
            return date.min
    return date.min


def _bar_date(bar: Any) -> date:
    row_date = _coerce_bar_date(getattr(bar, "date", None))
    if row_date != date.min:
        return row_date
    if hasattr(bar, "to_dict"):
        try:
            return _coerce_bar_date((bar.to_dict() or {}).get("date"))
        except Exception:
            return date.min
    return date.min


def _select_best_bars(db, stock_code: str, start: date, end: date, stock_identity=None) -> Tuple[Optional[str], list]:
    candidates, normalized_code = _history_code_candidates(stock_code, stock_identity)
    best_code = None
    best_bars = []
    best_key = None

    for candidate in candidates:
        bars = list(db.get_data_range(candidate, start, end) or [])
        if not bars:
            continue
        latest_date = max(_bar_date(bar) for bar in bars)
        key = (latest_date, len(bars), candidate == normalized_code)
        if best_key is None or key > best_key:
            best_key = key
            best_code = candidate
            best_bars = bars

    return best_code, best_bars


def _write_back_to_db(df: pd.DataFrame, stock_code: str, source: str) -> None:
    """Persist a network-fetched DataFrame to DB. Failures are logged, not raised."""
    if df is None or df.empty:
        return
    try:
        from src.storage import get_db
        _, normalized_code = _history_code_candidates(stock_code)
        db = get_db()
        saved = db.save_daily_data(df, normalized_code, source)
        logger.info(
            "load_history_df(%s): wrote back %d records from %s",
            stock_code, saved, source,
        )
    except Exception as exc:
        logger.warning("load_history_df(%s): DB write-back failed: %s", stock_code, exc)


def _fetch_from_network(
    stock_code: str,
    days: int,
    identity: Any,
    analysis_target: Any,
    end: date,
    write_back: bool,
) -> Tuple[Optional[pd.DataFrame], str]:
    """Fetch from network with in-memory dedup and optional DB write-back."""
    _, normalized_code = _history_code_candidates(stock_code, identity)
    window = _round_fetch_window(days)
    cache_key = _network_cache_key(normalized_code, end, window)

    # 1. Check in-memory cache first.
    cached = _network_cache.get(cache_key)
    if cached is not None:
        df_cached, src_cached = cached
        logger.debug(
            "load_history_df(%s): in-memory cache hit (window=%d)",
            stock_code, window,
        )
        # Truncate to caller's requested days.
        if len(df_cached) > days:
            df_cached = df_cached.tail(days).copy()
        return df_cached, src_cached

    # 2. Concurrent dedup: if another thread is already fetching, wait.
    event, is_owner = _network_cache.start_inflight(cache_key)
    if not is_owner:
        df_wait, src_wait, err_wait = _network_cache.wait_inflight(cache_key, event)
        if err_wait is not None:
            logger.warning("load_history_df(%s): inflight fetch failed: %s", stock_code, err_wait)
            return None, "none"
        if df_wait is not None and not df_wait.empty:
            result = df_wait.tail(days).copy() if len(df_wait) > days else df_wait.copy()
            return result, src_wait or "network"
        return None, "none"

    # 3. We are the owner — perform the actual fetch.
    try:
        fallback_kwargs: Dict[str, Any] = {}
        if identity is not None:
            fallback_kwargs["analysis_target"] = analysis_target
        # Fetch the rounded window, not the exact ``days``, so that concurrent
        # callers with similar requests share the same fetch.
        fetch_days = window
        manager = _get_fetcher_manager()
        df, source = manager.get_daily_data(stock_code, days=fetch_days, **fallback_kwargs)
        if df is not None and not df.empty:
            _network_cache.put(cache_key, df, source)
            if write_back:
                _write_back_to_db(df, stock_code, source)
            _network_cache.finish_inflight(cache_key, df, source, None)
            result = df.tail(days).copy() if len(df) > days else df.copy()
            return result, source
        _network_cache.finish_inflight(cache_key, None, None, None)
        return None, "none"
    except Exception as exc:
        logger.warning("load_history_df(%s): DataFetcherManager failed: %s", stock_code, exc)
        _network_cache.finish_inflight(cache_key, None, None, exc)
        return None, "none"


def load_history_df(
    stock_code: str,
    days: int = 60,
    target_date: Optional[date] = None,
    *,
    write_back: bool = False,
) -> Tuple[Optional[pd.DataFrame], str]:
    """Load K-line history, DB first with DataFetcherManager fallback.

    Returns ``(df, source)`` where *source* is ``"db_cache"`` on DB hit,
    ``"mem_cache"`` on in-memory cache hit, or the actual provider name on
    network fallback.  Returns ``(None, "none")`` when both paths fail.

    Parameters
    ----------
    stock_code : str
        Stock code (e.g. ``"600519"``, ``"AAPL"``).
    days : int
        Number of trading days requested.
    target_date : date, optional
        Explicit end date override.
    write_back : bool
        When True, network-fetched data is written back to DB.  Agent tools
        should set this; screening callers should leave it False.
    """
    from src.storage import get_db
    from src.agent.tools.execution import get_tool_stock_identity, get_tool_analysis_target

    # Resolve before the cache/provider exception boundaries. A lost strict
    # binding is not a cache miss and must never enter untyped fallback.
    identity = get_tool_stock_identity(stock_code)
    analysis_target = get_tool_analysis_target(stock_code)

    # Resolve effective end date using market-aware freshness rule.
    end = _resolve_cache_end_date(stock_code, target_date)

    # Calendar-day buffer: ~1.8x trading days + margin for long holidays
    start = end - timedelta(days=int(days * 1.8) + 10)

    # --- 1. DB lookup (canonical code, then prefix-stripped fallback) ------
    try:
        db = get_db()
        _code, bars = _select_best_bars(db, stock_code, start, end, identity)
        required_records = max(min(days, _CACHE_MIN_RECORDS), 1)
        latest_date = max((_bar_date(bar) for bar in bars), default=date.min)
        if bars and latest_date >= end and len(bars) >= required_records:
            df = pd.DataFrame([b.to_dict() for b in bars])
            logger.debug(
                "load_history_df(%s): %d bars from DB (requested %d, end=%s)",
                stock_code, len(df), days, end,
            )
            return df, "db_cache"
    except HistoryIdentityError:
        raise
    except Exception as e:
        logger.debug("load_history_df(%s): DB read failed: %s", stock_code, e)

    # --- 2. Network fallback with in-memory dedup + optional write-back ---
    return _fetch_from_network(stock_code, days, identity, analysis_target, end, write_back)
