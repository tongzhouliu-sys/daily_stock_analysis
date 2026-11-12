"""Market regime detection (SPY trend filter)."""

from __future__ import annotations

from typing import List, Optional
from .indicators import sma


def check_market_on(
    spy_closes: List[float],
    index: int,
) -> bool:
    """Check if market is ON (SPY > SMA200).
    
    Returns True if market should be active, False otherwise.
    Market is ON when SPY close > 200-day SMA.
    """
    sma200 = sma(spy_closes, 200, index)
    if sma200 is None:
        return False
    return spy_closes[index] > sma200
