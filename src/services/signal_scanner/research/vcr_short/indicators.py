# -*- coding: utf-8 -*-
"""Technical indicators for VCR Short strategy.

Implements SMA, ATR (Wilder smoothing), and other technical calculations
needed for signal generation.
"""

from typing import List, Optional, Tuple


def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average.

    Args:
        values: List of price values
        period: Lookback period

    Returns:
        List of SMA values, None where insufficient data
    """
    result = [None] * len(values)
    if len(values) < period:
        return result

    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if None in window:
            continue
        result[i] = sum(window) / period

    return result


def true_range(
    high: List[float],
    low: List[float],
    prev_close: List[Optional[float]]
) -> List[Optional[float]]:
    """Calculate True Range for each bar.

    TR = max(H - L, abs(H - prev_C), abs(L - prev_C))

    Args:
        high: High prices
        low: Low prices
        prev_close: Previous close prices (None for first bar)

    Returns:
        List of true range values
    """
    result = [None] * len(high)

    for i in range(len(high)):
        if high[i] is None or low[i] is None:
            continue

        if prev_close[i] is None:
            # First bar or no previous close
            result[i] = high[i] - low[i]
        else:
            tr = max(
                high[i] - low[i],
                abs(high[i] - prev_close[i]),
                abs(low[i] - prev_close[i])
            )
            result[i] = tr

    return result


def atr_wilder(
    high: List[float],
    low: List[float],
    close: List[float],
    period: int = 14
) -> List[Optional[float]]:
    """Average True Range with Wilder smoothing.

    Uses first `period` true ranges to seed, then applies Wilder smoothing:
    ATR[i] = (ATR[i-1] * (period-1) + TR[i]) / period

    Args:
        high: High prices
        low: Low prices
        close: Close prices
        period: ATR period (default 14)

    Returns:
        List of ATR values
    """
    if len(high) < period:
        return [None] * len(high)

    # Build previous close array
    prev_close = [None] + close[:-1]

    # Calculate true ranges
    tr_values = true_range(high, low, prev_close)

    # Initialize ATR array
    atr_values = [None] * len(high)

    # Find first valid TR
    first_valid = None
    for i, tr in enumerate(tr_values):
        if tr is not None:
            first_valid = i
            break

    if first_valid is None or first_valid + period > len(high):
        return atr_values

    # Seed with simple average of first `period` valid TRs
    seed_trs = []
    for i in range(first_valid, first_valid + period):
        if tr_values[i] is not None:
            seed_trs.append(tr_values[i])

    if len(seed_trs) < period:
        return atr_values

    atr_values[first_valid + period - 1] = sum(seed_trs) / period

    # Apply Wilder smoothing
    for i in range(first_valid + period, len(high)):
        if tr_values[i] is not None and atr_values[i - 1] is not None:
            atr_values[i] = (atr_values[i - 1] * (period - 1) + tr_values[i]) / period

    return atr_values


def rolling_max(values: List[float], period: int) -> List[Optional[float]]:
    """Rolling maximum over a window.

    Args:
        values: List of values
        period: Lookback period

    Returns:
        List of rolling max values
    """
    result = [None] * len(values)

    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if None in window:
            continue
        result[i] = max(window)

    return result


def rolling_min(values: List[float], period: int) -> List[Optional[float]]:
    """Rolling minimum over a window.

    Args:
        values: List of values
        period: Lookback period

    Returns:
        List of rolling min values
    """
    result = [None] * len(values)

    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if None in window:
            continue
        result[i] = min(window)

    return result


def find_lowest_close_index(close: List[float], lookback_end: int, lookback_period: int) -> Optional[int]:
    """Find index of lowest close price in lookback window.

    Args:
        close: Close prices
        lookback_end: End index (inclusive)
        lookback_period: Number of bars to look back

    Returns:
        Index of lowest close, or None if insufficient data
    """
    start = lookback_end - lookback_period + 1
    if start < 0:
        return None

    window = close[start:lookback_end + 1]
    if None in window:
        return None

    min_val = min(window)
    # Return earliest index if tied
    for i, val in enumerate(window):
        if val == min_val:
            return start + i

    return None


def calculate_dollar_volume(
    close: List[float],
    volume: List[float]
) -> List[Optional[float]]:
    """Calculate dollar volume (close * volume).

    Args:
        close: Close prices
        volume: Volume values

    Returns:
        List of dollar volumes
    """
    result = []
    for c, v in zip(close, volume):
        if c is None or v is None:
            result.append(None)
        else:
            result.append(c * v)
    return result
