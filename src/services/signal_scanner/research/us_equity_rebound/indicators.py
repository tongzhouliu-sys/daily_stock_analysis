"""Technical indicators with Wilder smoothing and exact spec formulas."""

from __future__ import annotations

from typing import List, Optional, Tuple


def sma(values: List[float], window: int, index: int) -> Optional[float]:
    """Simple moving average at index with given window.

    Returns None if insufficient data or any None in window.
    """
    if index < window - 1:
        return None
    window_values = values[index - window + 1 : index + 1]
    if any(v is None for v in window_values):
        return None
    return sum(window_values) / window


def rsi2_wilder(
    closes: List[float],
    index: int,
    prev_avg_gain: Optional[float],
    prev_avg_loss: Optional[float],
) -> Tuple[Optional[float], float, float]:
    """RSI(2) using Wilder smoothing.

    Returns (rsi, avg_gain, avg_loss) at index.
    Uses two-period Wilder smoothing as specified.
    """
    if index < 2:
        return None, prev_avg_gain or 0.0, prev_avg_loss or 0.0

    # Calculate current change
    change = closes[index] - closes[index - 1]
    current_gain = max(0.0, change)
    current_loss = max(0.0, -change)

    # Initialize or update Wilder averages
    if prev_avg_gain is None or prev_avg_loss is None:
        # Seed with first two changes
        if index >= 2:
            changes = [closes[i] - closes[i - 1] for i in range(1, 3)]
            gains = [max(0.0, c) for c in changes]
            losses = [max(0.0, -c) for c in changes]
            avg_gain = sum(gains) / 2
            avg_loss = sum(losses) / 2
        else:
            avg_gain = current_gain
            avg_loss = current_loss
    else:
        # Wilder smoothing: (prev * (n-1) + current) / n
        # For n=2: (prev + current) / 2
        avg_gain = (prev_avg_gain + current_gain) / 2
        avg_loss = (prev_avg_loss + current_loss) / 2

    # Calculate RSI
    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0
    elif avg_loss == 0:
        rsi = 100.0
    elif avg_gain == 0:
        rsi = 0.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    return rsi, avg_gain, avg_loss


def atr14_wilder(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    index: int,
    prev_atr: Optional[float],
) -> Tuple[Optional[float], float]:
    """ATR(14) using Wilder smoothing.

    Returns (atr, true_range) at index.
    """
    if index < 1:
        return None, 0.0

    # Calculate true range
    high_low = highs[index] - lows[index]
    high_prev_close = abs(highs[index] - closes[index - 1])
    low_prev_close = abs(lows[index] - closes[index - 1])
    true_range = max(high_low, high_prev_close, low_prev_close)

    # Initialize or update ATR
    if prev_atr is None:
        # Seed with first 14 true ranges
        if index >= 14:
            trs = []
            for i in range(1, 15):
                hl = highs[i] - lows[i]
                hpc = abs(highs[i] - closes[i - 1])
                lpc = abs(lows[i] - closes[i - 1])
                trs.append(max(hl, hpc, lpc))
            atr = sum(trs) / 14
        else:
            atr = true_range
    else:
        # Wilder smoothing: (prev * 13 + current) / 14
        atr = (prev_atr * 13 + true_range) / 14

    return atr, true_range


def volume_zscore(
    volumes: List[float], index: int, window: int = 20
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Volume Z-score and statistics over trailing window.

    Returns (z_score, mean, std) at index.
    Window excludes current bar (index-20 to index-1).
    """
    if index < window:
        return None, None, None

    # Get trailing window (excluding current)
    window_values = volumes[index - window : index]
    if any(v is None for v in window_values):
        return None, None, None

    mean = sum(window_values) / window
    if mean <= 0:
        return None, mean, None

    # Population standard deviation (ddof=0)
    variance = sum((v - mean) ** 2 for v in window_values) / window
    std = variance ** 0.5

    current_volume = volumes[index]
    if std == 0:
        z_score = None  # Only check multiple condition
    else:
        z_score = (current_volume - mean) / std

    return z_score, mean, std


def check_kline_stop(
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: Optional[float],
) -> bool:
    """Check if bar satisfies stop-loss K-line conditions.

    Conditions:
    - range = high - low > 0
    - min(open, close) - low >= 0.60 * range
    - min(open, close) - low >= 0.50 * atr
    - abs(close - open) <= 0.30 * range
    """
    if atr is None or atr <= 0:
        return False

    range_val = high - low
    if range_val <= 0:
        return False

    body_bottom = min(open_price, close) - low
    body_size = abs(close - open_price)

    return (
        body_bottom >= 0.60 * range_val
        and body_bottom >= 0.50 * atr
        and body_size <= 0.30 * range_val
    )
