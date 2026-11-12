"""Data loader for market data and universe."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime


def load_market_data(data_dir: str) -> Dict[str, Dict[str, List[float]]]:
    """Load market data from CSV files.
    
    Expected CSV format:
    date,security_id,open,high,low,close,volume
    
    Args:
        data_dir: Directory containing market data CSV
    
    Returns:
        Dict of security_id -> {open: [], high: [], low: [], close: [], volume: []}
    """
    data_path = Path(data_dir)
    market_csv = data_path / "market_data.csv"
    
    if not market_csv.exists():
        raise FileNotFoundError(f"Market data file not found: {market_csv}")
    
    # Organize by security
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
    """Load SPY data from CSV.
    
    Args:
        data_dir: Directory containing SPY CSV
    
    Returns:
        Dict with SPY OHLCV data
    """
    data_path = Path(data_dir)
    spy_csv = data_path / "spy_data.csv"
    
    if not spy_csv.exists():
        raise FileNotFoundError(f"SPY data file not found: {spy_csv}")
    
    spy_data = {
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


def load_universe(data_dir: str) -> List[str]:
    """Load universe of security IDs.
    
    Args:
        data_dir: Directory containing universe file
    
    Returns:
        List of security IDs
    """
    data_path = Path(data_dir)
    universe_file = data_path / "universe.json"
    
    if not universe_file.exists():
        raise FileNotFoundError(f"Universe file not found: {universe_file}")
    
    with open(universe_file, "r") as f:
        universe = json.load(f)
    
    return universe["securities"]


def load_corporate_actions(data_dir: str) -> List[Dict[str, Any]]:
    """Load corporate actions from JSON.
    
    Args:
        data_dir: Directory containing corporate actions file
    
    Returns:
        List of corporate action events
    """
    data_path = Path(data_dir)
    actions_file = data_path / "corporate_actions.json"
    
    if not actions_file.exists():
        return []
    
    with open(actions_file, "r") as f:
        actions = json.load(f)
    
    return actions


def load_trading_calendar(data_dir: str) -> List[str]:
    """Load trading calendar.
    
    Args:
        data_dir: Directory containing calendar file
    
    Returns:
        List of trading dates (YYYY-MM-DD)
    """
    data_path = Path(data_dir)
    calendar_file = data_path / "trading_calendar.json"
    
    if not calendar_file.exists():
        raise FileNotFoundError(f"Calendar file not found: {calendar_file}")
    
    with open(calendar_file, "r") as f:
        calendar = json.load(f)
    
    return calendar["trading_dates"]


def validate_data_consistency(
    market_data: Dict[str, Dict[str, List[float]]],
    spy_data: Dict[str, List[float]],
    trading_dates: List[str],
    universe: List[str],
):
    """Validate data consistency across all inputs.
    
    Args:
        market_data: Market data dict
        spy_data: SPY data dict
        trading_dates: Trading calendar
        universe: Security universe
    
    Raises:
        ValueError if inconsistencies found
    """
    # Check SPY dates match calendar
    if len(spy_data["date"]) != len(trading_dates):
        raise ValueError(
            f"SPY data length ({len(spy_data['date'])}) != calendar length ({len(trading_dates)})"
        )
    
    for i, (spy_date, cal_date) in enumerate(zip(spy_data["date"], trading_dates)):
        if spy_date != cal_date:
            raise ValueError(
                f"Date mismatch at index {i}: SPY={spy_date}, Calendar={cal_date}"
            )
    
    # Check all securities have data
    for security_id in universe:
        if security_id not in market_data:
            raise ValueError(f"Security {security_id} not found in market data")
        
        if len(market_data[security_id]["date"]) != len(trading_dates):
            raise ValueError(
                f"Security {security_id} data length != calendar length"
            )
    
    print(f"✓ Data validation passed: {len(universe)} securities, {len(trading_dates)} trading days")
