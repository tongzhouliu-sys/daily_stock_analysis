"""Split adjustment helpers for the rebound research data set.

``data10`` daily bars are raw (unadjusted) prices. A 4:1 split therefore
appears as a ~75% one-day crash and would fire the oversold entry condition for
every split in the window. The converter must back-adjust prices and share
volume before the replay. These helpers stay importable so the adjustment can
be tested without touching the raw archive.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def parse_split_ratio(row: Dict[str, str]) -> Optional[float]:
    """Return ``split_to / split_from`` for a splits.csv row."""
    try:
        split_from = float(row["split_from"])
        split_to = float(row["split_to"])
    except (KeyError, TypeError, ValueError):
        return None
    if split_from <= 0 or split_to <= 0:
        return None
    return split_to / split_from


def load_splits(data_dir: str | Path) -> List[Dict[str, Any]]:
    """Load split events from ``splits.csv`` / ``splits_new.csv`` if present.

    Returns a list of ``{ticker, execution_date, split_ratio}`` dictionaries.
    Duplicate (ticker, date, ratio) rows are de-duplicated; conflicting ratios
    for the same (ticker, date) keep the first occurrence in file order.
    """
    base = Path(data_dir)
    events: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    conflicts: set[tuple[str, str]] = set()

    for filename in ("splits.csv", "splits_new.csv"):
        path = base / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                ticker = (row.get("ticker") or "").strip().upper()
                day = (row.get("execution_date") or "").strip()
                ratio = parse_split_ratio(row)
                if not ticker or not day or ratio is None:
                    continue
                key = (ticker, day)
                if key in conflicts:
                    continue
                identity = (ticker, day, ratio)
                if identity in seen:
                    continue
                if any(key == (t, d) for t, d, _ in seen):
                    # Same split reported with a different ratio: keep the
                    # first and skip the conflicting duplicate.
                    conflicts.add(key)
                    continue
                seen.add(identity)
                events.append(
                    {"ticker": ticker, "execution_date": day, "split_ratio": ratio}
                )

    events.sort(key=lambda event: (event["ticker"], event["execution_date"]))
    return events


def _splits_by_ticker(splits: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in splits:
        ticker = str(event["ticker"]).upper()
        grouped.setdefault(ticker, []).append(
            {
                "execution_date": str(event["execution_date"]),
                "split_ratio": float(event["split_ratio"]),
            }
        )
    for ticker in grouped:
        grouped[ticker].sort(key=lambda item: item["execution_date"])
    return grouped


def back_adjust_bars(
    rows: List[Dict[str, Any]],
    splits: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Back-adjust raw bars so prices are expressed in the latest share unit.

    For every split effective on ``d``, bars strictly before ``d`` have prices
    divided by the ratio and share volume multiplied by it. ``rows`` must be
    chronological per ticker and include ``ticker`` and ``date`` keys.
    """
    grouped = _splits_by_ticker(splits)
    if not grouped:
        return rows

    adjusted: List[Dict[str, Any]] = []
    for row in rows:
        ticker = str(row["ticker"]).upper()
        day = str(row["date"])
        factor = 1.0
        for event in grouped.get(ticker, []):
            if day < event["execution_date"]:
                factor *= event["split_ratio"]

        if factor == 1.0:
            adjusted.append(row)
            continue

        changed = dict(row)
        for field in ("open", "high", "low", "close"):
            if changed.get(field) is not None:
                changed[field] = float(changed[field]) / factor
        if changed.get("volume") is not None:
            changed["volume"] = float(changed["volume"]) * factor
        adjusted.append(changed)

    return adjusted


def split_events_in_range(
    splits: Iterable[Dict[str, Any]],
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Build ``corporate_actions`` events effective inside the replay window.

    Only needed when the backtest consumes raw (unadjusted) prices; when the
    converter back-adjusts the series these events must stay unused to avoid
    adjusting the same split twice.
    """
    events: List[Dict[str, Any]] = []
    for event in splits:
        day = str(event["execution_date"])
        if not (start_date <= day <= end_date):
            continue
        events.append(
            {
                "security_id": str(event["ticker"]).upper(),
                "type": "SPLIT",
                "effective_date": day,
                "split_ratio": float(event["split_ratio"]),
            }
        )
    return events
