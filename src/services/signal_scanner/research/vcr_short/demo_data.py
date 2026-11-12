# -*- coding: utf-8 -*-
"""Demo data generator for VCR Short backtest.

Generates synthetic stock data with realistic patterns including
volume climax reversals for testing purposes.

Uses only Python standard library (random, math) for portability.
Returns data as plain Python dicts/lists for compatibility.
"""

import random
import math
from typing import Dict, List, Any
from datetime import datetime, timedelta


class DemoDataGenerator:
    """Generates synthetic stock data for backtest testing.

    Data format (per instrument):
    {
        "dates": [...],
        "open": [...],
        "high": [...],
        "low": [...],
        "close": [...],
        "volume": [...]
    }
    """

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def _normal(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Generate normal random variable using Box-Muller transform."""
        u1 = self.rng.random()
        u2 = self.rng.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mu + sigma * z

    def _lognormal(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Generate lognormal random variable."""
        return math.exp(self._normal(mu, sigma))

    def generate_trading_dates(
        self,
        start_date: str,
        end_date: str
    ) -> List[str]:
        """Generate list of trading dates (weekdays only)."""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        dates = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # Skip weekends
                dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates

    def generate_stock_data(
        self,
        dates: List[str],
        initial_price: float = 100.0,
        volatility: float = 0.02,
        drift: float = 0.0001,
        volume_base: float = 1_000_000,
        volume_volatility: float = 0.5
    ) -> Dict[str, List[Any]]:
        """Generate synthetic OHLCV data for a single stock."""
        n = len(dates)
        closes = [0.0] * n
        opens = [0.0] * n
        highs = [0.0] * n
        lows = [0.0] * n
        volumes = [0] * n

        closes[0] = initial_price
        opens[0] = initial_price

        for i in range(1, n):
            ret = self._normal(drift, volatility)
            closes[i] = closes[i - 1] * (1 + ret)
            opens[i] = closes[i - 1] * (1 + self._normal(0, volatility * 0.3))

        for i in range(n):
            intraday_vol = abs(self._normal(0, volatility * 0.5))
            highs[i] = max(opens[i], closes[i]) * (1 + intraday_vol)
            lows[i] = min(opens[i], closes[i]) * (1 - intraday_vol)
            volumes[i] = int(volume_base * self._lognormal(0, volume_volatility))

        return {
            "dates": dates,
            "open": [round(x, 2) for x in opens],
            "high": [round(x, 2) for x in highs],
            "low": [round(x, 2) for x in lows],
            "close": [round(x, 2) for x in closes],
            "volume": volumes,
        }

    def inject_volume_climax_pattern(
        self,
        data: Dict[str, List[Any]],
        pattern_date_idx: int,
        intensity: float = 2.0
    ) -> Dict[str, List[Any]]:
        """Inject a volume climax reversal pattern at specific date."""
        # Deep copy
        data = {k: list(v) for k, v in data.items()}
        n = len(data["dates"])

        if pattern_date_idx < 20 or pattern_date_idx >= n - 5:
            return data

        # Create uptrend before pattern (20 days)
        for i in range(20):
            idx = pattern_date_idx - 20 + i
            if idx >= 0:
                increase_factor = 1 + (i / 20) * 0.3 * intensity
                data["close"][idx] *= increase_factor
                data["high"][idx] *= increase_factor
                data["low"][idx] *= increase_factor
                data["open"][idx] *= increase_factor

        # Volume climax at pattern date
        avg_vol = sum(data["volume"][pattern_date_idx - 5:pattern_date_idx]) / 5
        data["volume"][pattern_date_idx] = int(avg_vol * 3 * intensity)

        # Price spike at climax
        spike_factor = 1 + 0.1 * intensity
        data["close"][pattern_date_idx] *= spike_factor
        data["high"][pattern_date_idx] *= spike_factor * 1.02
        data["low"][pattern_date_idx] *= spike_factor * 0.98

        # Potential reversal after (5 days)
        for i in range(1, 6):
            idx = pattern_date_idx + i
            if idx < n:
                decline_factor = 1 - (i / 6) * 0.15 * intensity
                data["close"][idx] *= decline_factor
                data["high"][idx] *= decline_factor
                data["low"][idx] *= decline_factor
                data["open"][idx] *= decline_factor

        return data

    def generate(
        self,
        num_stocks: int = 50,
        start_date: str = "2010-01-01",
        end_date: str = "2025-12-31",
        pattern_probability: float = 0.3
    ) -> Dict[str, Dict[str, List[Any]]]:
        """Generate synthetic data for multiple stocks.

        Returns:
            Dictionary of instrument_id -> {dates, open, high, low, close, volume}
        """
        dates = self.generate_trading_dates(start_date, end_date)
        data = {}

        for i in range(num_stocks):
            instrument_id = f"DEMO{i + 1:04d}"

            initial_price = self.rng.uniform(20, 200)
            volatility = self.rng.uniform(0.015, 0.035)
            drift = self.rng.uniform(-0.0002, 0.0003)
            volume_base = self.rng.uniform(500_000, 5_000_000)

            stock_data = self.generate_stock_data(
                dates=dates,
                initial_price=initial_price,
                volatility=volatility,
                drift=drift,
                volume_base=volume_base
            )

            # Inject VCR patterns with given probability
            if self.rng.random() < pattern_probability:
                num_patterns = self.rng.randint(1, 3)
                max_idx = len(dates) - 50
                if max_idx > 100:
                    pattern_indices = self.rng.sample(range(100, max_idx), min(num_patterns, max_idx - 100))
                    for idx in pattern_indices:
                        intensity = self.rng.uniform(1.5, 3.0)
                        stock_data = self.inject_volume_climax_pattern(stock_data, idx, intensity)

            data[instrument_id] = stock_data

        return data

    def generate_borrow_rates(
        self,
        instrument_ids: List[str],
        default_rate: float = 0.10
    ) -> Dict[str, float]:
        """Generate synthetic borrow rates."""
        rates = {}
        for iid in instrument_ids:
            if self.rng.random() < 0.8:
                rate = self.rng.uniform(0.03, 0.10)
            else:
                rate = self.rng.uniform(0.10, 0.25)
            rates[iid] = rate
        return rates

    def generate_dividend_data(
        self,
        instrument_ids: List[str],
        dates: List[str],
        dividend_probability: float = 0.5
    ) -> Dict[str, List[Dict]]:
        """Generate synthetic dividend data."""
        dividend_data = {}

        for iid in instrument_ids:
            if self.rng.random() < dividend_probability:
                events = []
                for i in range(0, len(dates), 63):  # ~63 trading days per quarter
                    if self.rng.random() < 0.8:
                        div_amount = self.rng.uniform(0.20, 1.50)
                        events.append({
                            "date": dates[i],
                            "amount": round(div_amount, 2)
                        })
                dividend_data[iid] = events

        return dividend_data
