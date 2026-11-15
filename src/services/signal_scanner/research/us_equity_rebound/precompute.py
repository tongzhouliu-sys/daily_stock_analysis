"""Precomputed indicator series for the US equity rebound backtest.

The original replay loop rebuilt list slices and recomputed SMA/RSI/ATR for
every security on every trading day. With a realistic universe (tens of
thousands of tickers over several years) that is both slow and memory hungry.
This module computes the indicator series once per security, aligned with the
security's own bar dates so that mid-window listings and delistings stay valid
(no synthetic bars, no forward fill).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


@dataclass
class SecuritySeries:
    """Indicator series for one security, aligned with its own bar dates."""

    security_id: str
    dates: List[str]
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    rsi: np.ndarray
    atr: np.ndarray
    sma200: np.ndarray
    sma20: np.ndarray
    # Liquidity window includes the current bar (20 sessions ending at t).
    avg_volume20: np.ndarray
    avg_dollar_volume20: np.ndarray
    # Signal volume baseline excludes the current bar (t-20 .. t-1).
    vol_mean_prev20: np.ndarray
    vol_std_prev20: np.ndarray
    date_pos: Dict[str, int]

    def pos(self, date: str) -> Optional[int]:
        return self.date_pos.get(date)


def _build_date_pos(dates: List[str]) -> Dict[str, int]:
    positions: Dict[str, int] = {}
    for index, day in enumerate(dates):
        positions.setdefault(day, index)
    return positions


def _rsi2_series(closes: np.ndarray) -> np.ndarray:
    """Wilder RSI(2) series mirroring ``indicators.rsi2_wilder``."""
    size = len(closes)
    out = np.full(size, np.nan, dtype=np.float32)
    if size < 3:
        return out

    changes = np.diff(closes[:3])
    gains = np.maximum(0.0, changes)
    losses = np.maximum(0.0, -changes)
    avg_gain = float(gains.sum() / 2.0)
    avg_loss = float(losses.sum() / 2.0)

    for index in range(2, size):
        if index > 2:
            change = float(closes[index] - closes[index - 1])
            current_gain = max(0.0, change)
            current_loss = max(0.0, -change)
            avg_gain = (avg_gain + current_gain) / 2.0
            avg_loss = (avg_loss + current_loss) / 2.0

        if avg_gain == 0 and avg_loss == 0:
            rsi = 50.0
        elif avg_loss == 0:
            rsi = 100.0
        elif avg_gain == 0:
            rsi = 0.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        out[index] = rsi

    return out


def _atr14_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Wilder ATR(14) series mirroring ``indicators.atr14_wilder``."""
    size = len(closes)
    out = np.full(size, np.nan, dtype=np.float32)
    if size < 2:
        return out

    true_ranges = np.zeros(size, dtype=np.float64)
    for index in range(1, size):
        high_low = float(highs[index] - lows[index])
        high_prev_close = abs(float(highs[index] - closes[index - 1]))
        low_prev_close = abs(float(lows[index] - closes[index - 1]))
        true_ranges[index] = max(high_low, high_prev_close, low_prev_close)

    atr: Optional[float] = None
    for index in range(1, size):
        if atr is None:
            if index >= 14:
                atr = float(true_ranges[1:15].sum() / 14.0)
            else:
                atr = float(true_ranges[index])
        else:
            atr = (atr * 13.0 + float(true_ranges[index])) / 14.0
        out[index] = atr

    return out


def _rolling_mean(values: np.ndarray, window: int, *, shift: int = 0) -> np.ndarray:
    """Trailing mean over ``window`` sessions ending at ``index - shift``."""
    size = len(values)
    out = np.full(size, np.nan, dtype=np.float32)
    if size < window + shift:
        return out

    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    for index in range(window + shift - 1, size):
        end = index - shift + 1
        start = end - window
        out[index] = (cumulative[end] - cumulative[start]) / window
    return out


def _rolling_std(values: np.ndarray, window: int, *, shift: int = 0) -> np.ndarray:
    """Trailing population std over ``window`` sessions ending at ``index - shift``."""
    size = len(values)
    out = np.full(size, np.nan, dtype=np.float32)
    if size < window + shift:
        return out

    for index in range(window + shift - 1, size):
        end = index - shift + 1
        out[index] = float(values[end - window:end].std())

    return out


def _sma(closes: np.ndarray, window: int) -> np.ndarray:
    return _rolling_mean(closes, window)


def build_series(security_id: str, data: Dict[str, List[float]]) -> Optional[SecuritySeries]:
    """Build one security's indicator series, or ``None`` when unusable."""
    dates = data.get("date") or []
    closes_list = data.get("close") or []
    if not dates or len(dates) != len(closes_list) or len(dates) < 20:
        return None

    opens = np.asarray(data["open"], dtype=np.float64)
    highs = np.asarray(data["high"], dtype=np.float64)
    lows = np.asarray(data["low"], dtype=np.float64)
    closes = np.asarray(closes_list, dtype=np.float64)
    volumes = np.asarray(data.get("volume") or [], dtype=np.float64)
    if len(volumes) != len(closes):
        return None
    if not np.isfinite(closes).all() or (closes <= 0).any():
        return None

    dollar_volume = closes * volumes

    return SecuritySeries(
        security_id=security_id,
        dates=list(dates),
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        rsi=_rsi2_series(closes),
        atr=_atr14_series(highs, lows, closes),
        sma200=_sma(closes, 200),
        sma20=_sma(closes, 20),
        avg_volume20=_rolling_mean(volumes, 20),
        avg_dollar_volume20=_rolling_mean(dollar_volume, 20),
        vol_mean_prev20=_rolling_mean(volumes, 20, shift=1),
        vol_std_prev20=_rolling_std(volumes, 20, shift=1),
        date_pos=_build_date_pos(dates),
    )


def build_universe_series(
    universe: List[str],
    market_data: Dict[str, Dict[str, List[float]]],
) -> Dict[str, SecuritySeries]:
    """Build series for every universe member that has usable bars."""
    series: Dict[str, SecuritySeries] = {}
    for security_id in universe:
        data = market_data.get(security_id)
        if not data:
            continue
        built = build_series(security_id, data)
        if built is not None:
            series[security_id] = built
    return series
