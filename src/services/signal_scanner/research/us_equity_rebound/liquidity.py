"""Liquidity filtering and ranking."""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from .config import ReboundConfig


def calculate_liquidity_metrics(
    security_id: str,
    closes: List[float],
    volumes: List[float],
    index: int,
    window: int = 20,
) -> Dict[str, Any]:
    """Calculate liquidity metrics for a security.
    
    Returns:
        - avg_dollar_volume: Average dollar volume over window
        - avg_volume: Average share volume over window
        - is_qualified: Whether security meets liquidity criteria
    """
    if index < window - 1:
        return {
            "avg_dollar_volume": 0.0,
            "avg_volume": 0.0,
            "is_qualified": False,
        }
    
    # Calculate trailing window metrics
    window_closes = closes[index - window + 1:index + 1]
    window_volumes = volumes[index - window + 1:index + 1]
    
    # Dollar volume = close * volume for each day
    dollar_volumes = [c * v for c, v in zip(window_closes, window_volumes)]
    avg_dollar_volume = sum(dollar_volumes) / window
    avg_volume = sum(window_volumes) / window
    
    return {
        "avg_dollar_volume": avg_dollar_volume,
        "avg_volume": avg_volume,
        "is_qualified": True,
    }


def filter_by_liquidity(
    candidates: List[Dict[str, Any]],
    config: ReboundConfig,
) -> List[Dict[str, Any]]:
    """Filter candidates by liquidity criteria.
    
    1. Rank by avg_dollar_volume descending
    2. Take top liquidity_fraction of universe
    3. Filter by min_avg_volume
    
    Args:
        candidates: List of candidate dicts with liquidity metrics
        config: Backtest configuration
    
    Returns:
        Filtered and ranked list of candidates
    """
    # Filter by minimum volume
    qualified = [
        c for c in candidates
        if c["liquidity"]["avg_volume"] >= config.min_avg_volume
    ]
    
    if not qualified:
        return []
    
    # Sort by dollar volume descending, then by security_id ascending for ties
    qualified.sort(
        key=lambda c: (-c["liquidity"]["avg_dollar_volume"], c["security_id"])
    )
    
    # Take top fraction
    top_count = int(len(qualified) * config.liquidity_fraction)
    top_count = max(1, top_count)  # At least 1 if any qualified
    
    return qualified[:top_count]


def rank_candidates(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rank candidates by signal quality.
    
    Ranking criteria (in order):
    1. RSI ascending (lower is more oversold)
    2. security_id ascending (for tie-breaking)
    
    Args:
        candidates: List of candidates with signals
    
    Returns:
        Ranked list of candidates
    """
    # Sort by RSI ascending, then security_id ascending
    ranked = sorted(
        candidates,
        key=lambda c: (c["signal"]["rsi"], c["security_id"])
    )
    
    # Add rank field
    for i, candidate in enumerate(ranked):
        candidate["rank"] = i + 1
    
    return ranked
