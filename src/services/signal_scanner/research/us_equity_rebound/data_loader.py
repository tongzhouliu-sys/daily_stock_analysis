"""Data loader for market data and universe."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from .adjustments import load_splits


def load_market_data(data_dir: str) -> Dict[str, Dict[str, List[float]]]:
    """Load market data from CSV files.

    Expected CSV format:
    date,security_id,open,high,low,close,volume

    Series are grouped per security and keep their own date coverage, so
    mid-window listings and delistings are representable (E4).
    """
    data_path = Path(data_dir)
    market_csv = data_path / "market_data.csv"

    if not market_csv.exists():
        raise FileNotFoundError(f"Market data file not found: {market_csv}")

    security_data: Dict[str, Dict[str, List[float]]] = {}

    with open(market_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            security_id = row["security_id"]

            if security_id not in security_data:
                security_data[security_id] = {
                    "date": [],
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                }

            security_data[security_id]["date"].append(row["date"])
            security_data[security_id]["open"].append(float(row["open"]))
            security_data[security_id]["high"].append(float(row["high"]))
            security_data[security_id]["low"].append(float(row["low"]))
            security_data[security_id]["close"].append(float(row["close"]))
            security_data[security_id]["volume"].append(float(row["volume"]))

    return security_data


def load_spy_data(data_dir: str) -> Dict[str, List[float]]:
    """Load SPY data from CSV."""
    data_path = Path(data_dir)
    spy_csv = data_path / "spy_data.csv"

    if not spy_csv.exists():
        raise FileNotFoundError(f"SPY data file not found: {spy_csv}")

    spy_data: Dict[str, List[float]] = {
        "date": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
    }

    with open(spy_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            spy_data["date"].append(row["date"])
            spy_data["open"].append(float(row["open"]))
            spy_data["high"].append(float(row["high"]))
            spy_data["low"].append(float(row["low"]))
            spy_data["close"].append(float(row["close"]))
            spy_data["volume"].append(float(row["volume"]))

    return spy_data


def load_universe_metadata(data_dir: str) -> Dict[str, Any]:
    """Load the universe definition and its audit metadata when available."""
    data_path = Path(data_dir)
    universe_file = data_path / "universe.json"

    if not universe_file.exists():
        return {}

    with open(universe_file, "r") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return {"securities": payload}

    metadata = dict(payload)
    metadata.setdefault("securities", [])
    return metadata


def load_universe(data_dir: str) -> List[str]:
    """Load the candidate universe of security IDs.

    The universe is a point-in-time liquidity screen, not a period-end
    survivor list. When no historical index membership is available the file
    must name the actual definition (for example ``us_all_listed``) instead of
    claiming S&P 500 membership.
    """
    payload = load_universe_metadata(data_dir)
    if not payload:
        raise FileNotFoundError(f"Universe file not found: {Path(data_dir) / 'universe.json'}")
    return list(payload.get("securities") or [])


def load_corporate_actions(data_dir: str) -> List[Dict[str, Any]]:
    """Load corporate actions.

    ``corporate_actions.json`` is authoritative. When it is absent or empty
    and a ``splits.csv`` / ``splits_new.csv`` archive exists, split events are
    derived from it instead of silently running without corporate actions.
    """
    data_path = Path(data_dir)
    actions: List[Dict[str, Any]] = []
    actions_file = data_path / "corporate_actions.json"
    if actions_file.exists():
        with open(actions_file, "r") as f:
            payload = json.load(f)
        if isinstance(payload, list):
            actions = payload

    if actions:
        return actions

    for event in load_splits(data_path):
        actions.append(
            {
                "security_id": event["ticker"],
                "type": "SPLIT",
                "effective_date": event["execution_date"],
                "split_ratio": event["split_ratio"],
            }
        )
    return actions


def load_trading_calendar(data_dir: str) -> List[str]:
    """Load trading calendar."""
    data_path = Path(data_dir)
    calendar_file = data_path / "trading_calendar.json"

    if not calendar_file.exists():
        raise FileNotFoundError(f"Calendar file not found: {calendar_file}")

    with open(calendar_file, "r") as f:
        calendar = json.load(f)

    return calendar["trading_dates"]


def _validate_series_dates(
    security_id: str,
    dates: List[str],
    calendar_set: set[str],
) -> None:
    if not dates:
        raise ValueError(f"Security {security_id} has no bars")

    if dates != sorted(dates):
        raise ValueError(f"Security {security_id} dates are not ascending")

    if len(set(dates)) != len(dates):
        raise ValueError(f"Security {security_id} has duplicate dates")

    outside = [day for day in dates if day not in calendar_set]
    if outside:
        raise ValueError(
            f"Security {security_id} has {len(outside)} bar(s) outside the trading calendar "
            f"(first: {outside[0]})"
        )


def validate_data_consistency(
    market_data: Dict[str, Dict[str, List[float]]],
    spy_data: Dict[str, List[float]],
    trading_dates: List[str],
    universe: List[str],
):
    """Validate inputs while allowing partial per-security histories.

    Raises ``ValueError`` on structural problems (bad calendar, unsorted or
    duplicated dates, missing values). A security that lists or delists in the
    middle of the window is valid and required; forcing every series to cover
    the full calendar is what produced the survivorship bias in the old run.
    """
    if not trading_dates:
        raise ValueError("Trading calendar is empty")

    if trading_dates != sorted(trading_dates):
        raise ValueError("Trading calendar is not ascending")

    calendar_set = set(trading_dates)

    if not spy_data.get("date"):
        raise ValueError("SPY data is empty")

    _validate_series_dates("SPY", list(spy_data["date"]), calendar_set)
    if len(spy_data["date"]) != len(trading_dates):
        raise ValueError(
            f"SPY data length ({len(spy_data['date'])}) != calendar length "
            f"({len(trading_dates)})"
        )

    available = 0
    partial = 0
    for security_id in universe:
        if security_id not in market_data:
            # A listed universe member may have no bars in this window; the
            # engine simply ignores it instead of failing the whole run.
            continue

        data = market_data[security_id]
        series = data.get("date") or []
        lengths = {len(data.get(field) or []) for field in ("open", "high", "low", "close", "volume")}
        if lengths != {len(series)}:
            raise ValueError(f"Security {security_id} has misaligned OHLCV columns")

        _validate_series_dates(security_id, list(series), calendar_set)
        available += 1
        if len(series) < len(trading_dates):
            partial += 1

    if available == 0:
        raise ValueError("No universe security has market data")

    print(
        f"✓ Data validation passed: {len(universe)} universe members, "
        f"{available} with bars ({partial} partial history), "
        f"{len(trading_dates)} trading days"
    )
