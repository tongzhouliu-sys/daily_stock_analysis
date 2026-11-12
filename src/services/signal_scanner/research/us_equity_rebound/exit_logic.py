"""Exit logic implementation."""

from __future__ import annotations

from typing import Optional, Dict, Any
from .indicators import sma
from .config import ReboundConfig


def check_hard_stop(
    close_price: float,
    stop_price: float,
) -> bool:
    """Check if hard stop-loss is triggered.
    
    Condition: Close <= stop_price
    """
    return close_price <= stop_price


def check_time_stop(
    holding_sessions: int,
    max_sessions: int,
) -> bool:
    """Check if time stop is triggered.
    
    Condition: holding_sessions >= max_sessions
    Entry day counts as day 1.
    """
    return holding_sessions >= max_sessions


def check_sma20_exit(
    closes: list[float],
    index: int,
) -> bool:
    """Check if SMA20 exit is triggered.
    
    Condition: Close >= SMA20
    SMA20 includes current day.
    """
    sma20 = sma(closes, 20, index)
    if sma20 is None:
        return False
    return closes[index] >= sma20


def determine_exit_reason(
    close_price: float,
    stop_price: float,
    holding_sessions: int,
    closes: list[float],
    index: int,
    config: ReboundConfig,
) -> Optional[str]:
    """Determine exit reason based on priority.
    
    Priority order:
    1. HARD_STOP
    2. TIME_STOP
    3. SMA20_EXIT
    
    Returns exit reason code or None if no exit.
    """
    # Priority 1: Hard stop
    if check_hard_stop(close_price, stop_price):
        return "HARD_STOP"
    
    # Priority 2: Time stop
    if check_time_stop(holding_sessions, config.max_holding_sessions):
        return "TIME_STOP"
    
    # Priority 3: SMA20 exit
    if check_sma20_exit(closes, index):
        return "SMA20_EXIT"
    
    return None
