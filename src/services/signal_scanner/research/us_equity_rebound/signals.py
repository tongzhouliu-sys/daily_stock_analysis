"""Entry signal generation for oversold rebound strategy."""

from __future__ import annotations

from typing import List, Optional, Dict, Any
from .indicators import sma, rsi2_wilder, atr14_wilder, volume_zscore, check_kline_stop
from .config import ReboundConfig


def check_trend_condition(
    closes: List[float],
    index: int,
) -> bool:
    """Condition A: Close[t] > SMA200[t]."""
    sma200 = sma(closes, 200, index)
    if sma200 is None:
        return False
    return closes[index] > sma200


def check_crash_condition(
    closes: List[float],
    index: int,
    rsi: Optional[float],
    config: ReboundConfig,
) -> bool:
    """Condition B: Oversold crash (any one of three).
    
    - RSI2[t] < rsi_threshold
    - Close[t] / Close[t-3] - 1 <= -0.08 (3-day decline >= 8%)
    - Close[t] / Close[t-1] - 1 <= -0.05 (1-day decline >= 5%)
    """
    # RSI condition
    if rsi is not None and rsi < config.rsi_threshold:
        return True
    
    # 3-day decline condition
    if index >= 3:
        decline_3d = closes[index] / closes[index - 3] - 1
        if decline_3d <= -0.08:
            return True
    
    # 1-day decline condition
    if index >= 1:
        decline_1d = closes[index] / closes[index - 1] - 1
        if decline_1d <= -0.05:
            return True
    
    return False


def check_volume_condition(
    volumes: List[float],
    index: int,
    config: ReboundConfig,
) -> bool:
    """Condition C: Volume surge (any one of two).
    
    - Volume[t] >= volume_multiple * mean(Volume[t-20:t-1])
    - Z > 2 (fixed threshold)
    """
    z_score, mean, std = volume_zscore(volumes, index, window=20)
    
    if mean is None or mean <= 0:
        return False
    
    current_volume = volumes[index]
    
    # Multiple condition
    if current_volume >= config.volume_multiple * mean:
        return True
    
    # Z-score condition (fixed threshold of 2)
    if z_score is not None and z_score > 2.0:
        return True
    
    return False


def check_kline_condition(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    index: int,
    atr: Optional[float],
) -> bool:
    """Condition D: Stop-loss K-line pattern."""
    return check_kline_stop(
        opens[index],
        highs[index],
        lows[index],
        closes[index],
        atr,
    )


def generate_entry_signal(
    security_id: str,
    date: str,
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    index: int,
    rsi_state: tuple,
    atr_state: tuple,
    config: ReboundConfig,
) -> Optional[Dict[str, Any]]:
    """Generate entry signal if all four conditions are met.
    
    Returns signal dict with diagnostic info, or None if no signal.
    """
    # Extract state
    prev_rsi_avg_gain, prev_rsi_avg_loss = rsi_state
    prev_atr = atr_state
    
    # Calculate indicators at current index
    rsi, avg_gain, avg_loss = rsi2_wilder(
        closes, index, prev_rsi_avg_gain, prev_rsi_avg_loss
    )
    atr, true_range = atr14_wilder(highs, lows, closes, index, prev_atr)
    
    # Check all four conditions
    trend_ok = check_trend_condition(closes, index)
    crash_ok = check_crash_condition(closes, index, rsi, config)
    volume_ok = check_volume_condition(volumes, index, config)
    kline_ok = check_kline_condition(opens, highs, lows, closes, index, atr)
    
    # All conditions must be satisfied
    if not (trend_ok and crash_ok and volume_ok and kline_ok):
        return None
    
    # Calculate SMA200 for reference
    sma200 = sma(closes, 200, index)
    
    return {
        "security_id": security_id,
        "signal_date": date,
        "signal_index": index,
        "rsi": rsi,
        "atr": atr,
        "sma200": sma200,
        "close": closes[index],
        "volume": volumes[index],
        "diagnostics": {
            "trend": trend_ok,
            "crash": crash_ok,
            "volume": volume_ok,
            "kline": kline_ok,
        },
    }
