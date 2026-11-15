"""Corporate actions processing.

Supports both the legacy dict payloads and the ``Position`` dataclasses used by
the replay engine. Split adjustments keep positions share-compatible; cash
dividends are credited to the portfolio cash balance (long-only strategy).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _get(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)


def _set(container: Any, key: str, value: Any) -> None:
    if isinstance(container, dict):
        container[key] = value
    else:
        setattr(container, key, value)


def apply_split_adjustment(
    positions: Dict[str, Any],
    split_event: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a stock split (ratio = split_to / split_from) to open positions."""
    security_id = split_event["security_id"]
    split_ratio = float(split_event["split_ratio"])

    if security_id not in positions or split_ratio <= 0:
        return positions

    pos = positions[security_id]

    _set(pos, "shares", int(round(_get(pos, "shares", 0) * split_ratio)))

    for field in ("entry_price", "stop_price", "atr_at_entry", "last_price"):
        value = _get(pos, field)
        if value:
            _set(pos, field, float(value) / split_ratio)

    return positions


def apply_dividend_adjustment(
    cash: float,
    positions: Dict[str, Any],
    dividend_event: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    """Credit a cash dividend to the portfolio for shares held."""
    security_id = dividend_event["security_id"]
    dividend_per_share = float(dividend_event["dividend_per_share"])

    if security_id not in positions:
        return cash, positions

    shares = int(_get(positions[security_id], "shares", 0) or 0)
    if shares <= 0 or dividend_per_share <= 0:
        return cash, positions

    return cash + shares * dividend_per_share, positions


def process_corporate_actions(
    cash: float,
    positions: Dict[str, Any],
    events: List[Dict[str, Any]],
    current_date: str,
) -> Tuple[float, Dict[str, Any]]:
    """Process all corporate actions effective on ``current_date``."""
    for event in events:
        if event.get("effective_date") != current_date:
            continue

        event_type = str(event.get("type") or "").upper()
        if event_type == "SPLIT":
            positions = apply_split_adjustment(positions, event)
        elif event_type == "DIVIDEND":
            cash, positions = apply_dividend_adjustment(cash, positions, event)

    return cash, positions


def get_corporate_actions_for_date(
    all_events: List[Dict[str, Any]],
    target_date: str,
) -> List[Dict[str, Any]]:
    """Filter corporate actions effective on target date."""
    return [event for event in all_events if event.get("effective_date") == target_date]
