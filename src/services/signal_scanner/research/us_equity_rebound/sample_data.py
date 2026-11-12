"""Generate synthetic market data for testing."""

from __future__ import annotations

import json
import csv
import random
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta


def generate_trading_calendar(start_date: str, end_date: str) -> List[str]:
    """Generate trading calendar (weekdays only, excluding major holidays)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    trading_dates = []
    current = start
    
    while current <= end:
        # Skip weekends
        if current.weekday() < 5:
            trading_dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    return trading_dates


def generate_market_data(
    trading_dates: List[str],
    securities: List[str],
    initial_prices: Dict[str, float],
    seed: int = 42,
) -> Dict[str, Dict[str, List[float]]]:
    """Generate synthetic OHLCV data for securities."""
    random.seed(seed)
    
    market_data = {}
    
    for security_id in securities:
        price = initial_prices.get(security_id, 100.0)
        
        dates = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []
        
        for date in trading_dates:
            # Random walk with drift
            daily_return = random.gauss(0.0005, 0.02)
            open_price = price
            close_price = price * (1 + daily_return)
            
            # Generate high and low
            intraday_vol = abs(daily_return) + random.uniform(0.005, 0.02)
            high_price = max(open_price, close_price) * (1 + intraday_vol * random.random())
            low_price = min(open_price, close_price) * (1 - intraday_vol * random.random())
            
            # Volume with some randomness
            base_volume = random.randint(500000, 5000000)
            volume = int(base_volume * (1 + abs(daily_return) * 10))
            
            dates.append(date)
            opens.append(round(open_price, 2))
            highs.append(round(high_price, 2))
            lows.append(round(low_price, 2))
            closes.append(round(close_price, 2))
            volumes.append(volume)
            
            price = close_price
        
        market_data[security_id] = {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    
    return market_data


def generate_spy_data(trading_dates: List[str], seed: int = 42) -> Dict[str, List[float]]:
    """Generate SPY data with realistic market behavior."""
    random.seed(seed)
    
    price = 100.0
    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    for date in trading_dates:
        # Market has lower volatility than individual stocks
        daily_return = random.gauss(0.0003, 0.01)
        open_price = price
        close_price = price * (1 + daily_return)
        
        intraday_vol = abs(daily_return) + random.uniform(0.003, 0.01)
        high_price = max(open_price, close_price) * (1 + intraday_vol * random.random())
        low_price = min(open_price, close_price) * (1 - intraday_vol * random.random())
        
        volume = random.randint(50000000, 150000000)
        
        dates.append(date)
        opens.append(round(open_price, 2))
        highs.append(round(high_price, 2))
        lows.append(round(low_price, 2))
        closes.append(round(close_price, 2))
        volumes.append(volume)
        
        price = close_price
    
    return {
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


def save_market_data(market_data: Dict, output_dir: str):
    """Save market data to CSV."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    csv_path = output_path / "market_data.csv"
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "security_id", "open", "high", "low", "close", "volume"])
        
        for security_id, data in market_data.items():
            for i in range(len(data["date"])):
                writer.writerow([
                    data["date"][i],
                    security_id,
                    data["open"][i],
                    data["high"][i],
                    data["low"][i],
                    data["close"][i],
                    data["volume"][i],
                ])
    
    print(f"✓ Saved market data to {csv_path}")


def save_spy_data(spy_data: Dict, output_dir: str):
    """Save SPY data to CSV."""
    output_path = Path(output_dir)
    csv_path = output_path / "spy_data.csv"
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        
        for i in range(len(spy_data["date"])):
            writer.writerow([
                spy_data["date"][i],
                spy_data["open"][i],
                spy_data["high"][i],
                spy_data["low"][i],
                spy_data["close"][i],
                spy_data["volume"][i],
            ])
    
    print(f"✓ Saved SPY data to {csv_path}")


def save_universe(securities: List[str], output_dir: str):
    """Save universe to JSON."""
    output_path = Path(output_dir)
    json_path = output_path / "universe.json"
    
    with open(json_path, "w") as f:
        json.dump({"securities": securities}, f, indent=2)
    
    print(f"✓ Saved universe to {json_path}")


def save_trading_calendar(trading_dates: List[str], output_dir: str):
    """Save trading calendar to JSON."""
    output_path = Path(output_dir)
    json_path = output_path / "trading_calendar.json"
    
    with open(json_path, "w") as f:
        json.dump({"trading_dates": trading_dates}, f, indent=2)
    
    print(f"✓ Saved trading calendar to {json_path}")


def save_corporate_actions(actions: List[Dict], output_dir: str):
    """Save corporate actions to JSON."""
    output_path = Path(output_dir)
    json_path = output_path / "corporate_actions.json"
    
    with open(json_path, "w") as f:
        json.dump(actions, f, indent=2)
    
    print(f"✓ Saved corporate actions to {json_path}")


def generate_sample_data(output_dir: str, start_date: str = "2020-01-01", end_date: str = "2023-12-31"):
    """Generate complete sample dataset for testing."""
    print(f"Generating sample data from {start_date} to {end_date}...")
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Generate trading calendar
    trading_dates = generate_trading_calendar(start_date, end_date)
    print(f"  Generated {len(trading_dates)} trading days")
    
    # Define universe (S&P 500 sample)
    securities = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "BRK.B", "JPM", "V",
        "JNJ", "WMT", "PG", "MA", "UNH",
        "HD", "BAC", "XOM", "PFE", "CSCO",
    ]
    print(f"  Universe: {len(securities)} securities")
    
    # Generate initial prices
    initial_prices = {
        "AAPL": 300.0, "MSFT": 250.0, "GOOGL": 140.0, "AMZN": 180.0, "NVDA": 200.0,
        "META": 200.0, "TSLA": 250.0, "BRK.B": 350.0, "JPM": 150.0, "V": 220.0,
        "JNJ": 170.0, "WMT": 140.0, "PG": 150.0, "MA": 350.0, "UNH": 500.0,
        "HD": 300.0, "BAC": 35.0, "XOM": 90.0, "PFE": 40.0, "CSCO": 50.0,
    }
    
    # Generate market data
    print("  Generating market data...")
    market_data = generate_market_data(trading_dates, securities, initial_prices)
    
    # Generate SPY data
    print("  Generating SPY data...")
    spy_data = generate_spy_data(trading_dates)
    
    # Generate some corporate actions (splits and dividends)
    corporate_actions = [
        {
            "type": "SPLIT",
            "security_id": "AAPL",
            "effective_date": "2021-08-30",
            "split_ratio": 4.0,
        },
        {
            "type": "DIVIDEND",
            "security_id": "MSFT",
            "effective_date": "2022-03-10",
            "dividend_per_share": 0.62,
        },
        {
            "type": "DIVIDEND",
            "security_id": "AAPL",
            "effective_date": "2022-02-10",
            "dividend_per_share": 0.22,
        },
    ]
    
    # Save all data
    save_trading_calendar(trading_dates, output_dir)
    save_universe(securities, output_dir)
    save_market_data(market_data, output_dir)
    save_spy_data(spy_data, output_dir)
    save_corporate_actions(corporate_actions, output_dir)
    
    print(f"\n✓ Sample data generated in {output_dir}")
    print(f"  - {len(trading_dates)} trading days")
    print(f"  - {len(securities)} securities")
    print(f"  - {len(corporate_actions)} corporate actions")
