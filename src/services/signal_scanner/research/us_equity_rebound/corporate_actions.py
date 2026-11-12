"""Corporate actions processing."""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import date


def apply_split_adjustment(
    positions: Dict[str, Any],
    split_event: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply stock split adjustment to positions.
    
    Args:
        positions: Current positions dict
        split_event: Split event with fields:
            - security_id
            - effective_date
            - split_ratio (e.g., 2.0 for 2:1 split)
    
    Returns:
        Updated positions dict
    """
    security_id = split_event["security_id"]
    split_ratio = split_event["split_ratio"]
    
    if security_id not in positions:
        return positions
    
    pos = positions[security_id]
    
    # Adjust shares
    pos["shares"] = pos["shares"] * split_ratio
    
    # Adjust prices (entry, stop)
    pos["entry_price"] = pos["entry_price"] / split_ratio
    pos["stop_price"] = pos["stop_price"] / split_ratio
    pos["current_price"] = pos.get("current_price", pos["entry_price"]) / split_ratio
    
    # Adjust ATR
    if "atr_at_entry" in pos:
        pos["atr_at_entry"] = pos["atr_at_entry"] / split_ratio
    
    return positions


def apply_dividend_adjustment(
    cash: float,
    positions: Dict[str, Any],
    dividend_event: Dict[str, Any],
) -> tuple[float, Dict[str, Any]]:
    """Apply cash dividend adjustment.
    
    Args:
        cash: Current cash balance
        positions: Current positions dict
        dividend_event: Dividend event with fields:
            - security_id
            - ex_date
            - dividend_per_share
    
    Returns:
        Updated (cash, positions) tuple
    """
    security_id = dividend_event["security_id"]
    dividend_per_share = dividend_event["dividend_per_share"]
    
    if security_id not in positions:
        return cash, positions
    
    pos = positions[security_id]
    
    # Calculate dividend payment
    dividend_payment = pos["shares"] * dividend_per_share
    
    # Add to cash
    cash += dividend_payment
    
    # Adjust price (ex-dividend drop)
    if "current_price" in pos:
        pos["current_price"] = pos["current_price"] - dividend_per_share
    
    return cash, positions


def process_corporate_actions(
    cash: float,
    positions: Dict[str, Any],
    events: List[Dict[str, Any]],
    current_date: str,
) -> tuple[float, Dict[str, Any]]:
    """Process all corporate actions effective on current date.
    
    Args:
        cash: Current cash balance
        positions: Current positions dict
        events: List of corporate action events
        current_date: Current trading date (YYYY-MM-DD)
    
    Returns:
        Updated (cash, positions) tuple
    """
    for event in events:
        effective_date = event.get("effective_date")
        
        if effective_date != current_date:
            continue
        
        event_type = event.get("type")
        
        if event_type == "SPLIT":
            positions = apply_split_adjustment(positions, event)
        elif event_type == "DIVIDEND":
            cash, positions = apply_dividend_adjustment(cash, positions, event)
    
    return cash, positions


def get_corporate_actions_for_date(
    all_events: List[Dict[str, Any]],
    target_date: str,
) -> List[Dict[str, Any]]:
    """Filter corporate actions effective on target date.
    
    Args:
        all_events: All corporate action events
        target_date: Target date (YYYY-MM-DD)
    
    Returns:
        List of events effective on target date
    """
    return [
        event for event in all_events
        if event.get("effective_date") == target_date
    ]
