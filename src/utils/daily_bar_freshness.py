# -*- coding: utf-8 -*-
"""Daily-bar freshness helpers shared by fetch, storage and prompt layers.

Two related audit findings live here (C11/C12):

* Intraday runs can receive a *partial* bar for the current session from daily
  providers. Persisting it as a finished daily bar makes later same-day runs
  skip the fetch ("today already exists") and leaves the partial values behind.
* When the network fails, the pipeline falls back to whatever the database
  holds. Those rows may be days old, so the prompt must state the real data
  date instead of presenting them as today's quote.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if hasattr(value, "date") and not isinstance(value, date):
        try:
            return value.date()
        except (TypeError, ValueError):
            return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def drop_unclosed_daily_bars(
    df: Optional[pd.DataFrame],
    last_completed_session: Optional[date],
    *,
    code: Optional[str] = None,
) -> Tuple[Optional[pd.DataFrame], int]:
    """Remove rows newer than the last completed trading session.

    A row whose date is after ``last_completed_session`` can only be the
    in-progress session, so its OHLCV values are accumulated, not final.
    Returns ``(filtered_df, dropped_rows)``.
    """
    if df is None or df.empty or last_completed_session is None:
        return df, 0
    if "date" not in df.columns:
        return df, 0

    parsed = pd.to_datetime(df["date"], errors="coerce")
    keep = parsed.notna() & (parsed.dt.date <= last_completed_session)
    dropped = int(len(df) - int(keep.sum()))
    if dropped <= 0:
        return df, 0

    logger.info(
        "%s dropped %d unclosed daily bar(s) after %s",
        code or "daily-bar",
        dropped,
        last_completed_session,
    )
    return df.loc[keep].reset_index(drop=True), dropped


def count_trading_sessions_between(
    market: Optional[str],
    start_exclusive: date,
    end_inclusive: date,
) -> Optional[int]:
    """Count sessions in ``(start_exclusive, end_inclusive]``.

    Returns ``None`` when the exchange calendar is unavailable so callers can
    fall back to a weekday approximation instead of inventing precision.
    """
    if end_inclusive <= start_exclusive:
        return 0
    try:
        from src.core.trading_calendar import get_trading_dates

        sessions = get_trading_dates(
            market or "",
            start_exclusive + timedelta(days=1),
            end_inclusive,
        )
    except Exception as exc:  # pragma: no cover - calendar guard
        logger.debug("trading session count unavailable: %s", exc)
        sessions = None
    if sessions is None:
        return None
    return int(len(sessions))


def evaluate_daily_bar_freshness(
    *,
    market: Optional[str],
    data_as_of: Any,
    expected_as_of: Any,
) -> Dict[str, Any]:
    """Describe how far the stored daily bars lag the expected session.

    Returns an empty dict when either date is unknown, so callers can keep the
    existing context untouched. Otherwise returns ``data_as_of`` /
    ``data_expected_as_of`` / ``data_stale`` / ``data_stale_sessions`` plus
    localized notes for prompts.
    """
    as_of = _coerce_date(data_as_of)
    expected = _coerce_date(expected_as_of)
    if as_of is None or expected is None:
        return {}

    payload: Dict[str, Any] = {
        "data_as_of": as_of.isoformat(),
        "data_expected_as_of": expected.isoformat(),
        "data_stale": as_of < expected,
        "data_stale_sessions": 0,
    }
    if as_of >= expected:
        return payload

    sessions = count_trading_sessions_between(market, as_of, expected)
    if sessions is None:
        sessions = int(np.busday_count(as_of + timedelta(days=1), expected + timedelta(days=1)))
        payload["data_stale_sessions_estimated"] = True
    payload["data_stale_sessions"] = max(0, int(sessions))
    payload["data_freshness_note_zh"] = (
        f"日线数据截至 {as_of.isoformat()}，落后最新交易日 {expected.isoformat()} "
        f"{payload['data_stale_sessions']} 个交易日，不能当作今日行情"
    )
    payload["data_freshness_note_en"] = (
        f"Daily bars end at {as_of.isoformat()}, {payload['data_stale_sessions']} trading "
        f"session(s) behind {expected.isoformat()}; they are not today's quote"
    )
    return payload


def attach_daily_bar_freshness(
    context: Optional[Dict[str, Any]],
    *,
    market: Optional[str],
    expected_as_of: Any,
) -> Optional[Dict[str, Any]]:
    """Add freshness metadata to an analysis context dict (in place copy)."""
    if not isinstance(context, dict) or not context:
        return context
    if context.get("data_missing"):
        return context
    freshness = evaluate_daily_bar_freshness(
        market=market,
        data_as_of=context.get("date"),
        expected_as_of=expected_as_of,
    )
    if not freshness:
        return context
    enriched = dict(context)
    enriched.update(freshness)
    return enriched
