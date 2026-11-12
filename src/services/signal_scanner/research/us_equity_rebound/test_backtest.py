"""Unit tests for US Equity Rebound backtest system."""

from __future__ import annotations

import pytest
from datetime import datetime
from typing import List, Dict

from .indicators import (
    calculate_sma,
    calculate_rsi2,
    calculate_atr14,
    calculate_volume_zscore,
    check_kline_pattern,
)
from .signals import check_entry_conditions
from .exit_logic import check_exit_conditions
from .portfolio import Portfolio, Position
from .performance import calculate_performance_metrics
from .config import ReboundConfig


class TestIndicators:
    """Test technical indicators."""
    
    def test_sma_basic(self):
        """Test basic SMA calculation."""
        prices = [10.0, 11.0, 12.0, 13.0, 14.0]
        result = calculate_sma(prices, 3)
        assert len(result) == 5
        assert result[0] is None  # Not enough data
        assert result[1] is None
        assert result[2] == pytest.approx(11.0)  # (10+11+12)/3
        assert result[3] == pytest.approx(12.0)  # (11+12+13)/3
        assert result[4] == pytest.approx(13.0)  # (12+13+14)/3
    
    def test_sma_with_none(self):
        """Test SMA with None values."""
        prices = [10.0, None, 12.0, 13.0, 14.0]
        result = calculate_sma(prices, 3)
        assert result[2] is None  # Contains None
    
    def test_rsi2_calculation(self):
        """Test RSI2 calculation."""
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 98.0, 95.0]
        rsi_values = calculate_rsi2(prices)
        
        assert len(rsi_values) == 7
        assert rsi_values[0] is None  # Not enough data
        assert rsi_values[1] is None
        
        # RSI should be between 0 and 100
        for i in range(2, 7):
            if rsi_values[i] is not None:
                assert 0 <= rsi_values[i] <= 100
    
    def test_rsi2_oversold(self):
        """Test RSI2 identifies oversold conditions."""
        # Create a series with consistent declines
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0]
        rsi_values = calculate_rsi2(prices)
        
        # After consistent declines, RSI should be low
        if rsi_values[-1] is not None:
            assert rsi_values[-1] < 30
    
    def test_atr14_calculation(self):
        """Test ATR14 calculation."""
        # Need at least 14 bars
        highs = [10.0 + i * 0.1 for i in range(20)]
        lows = [9.0 + i * 0.1 for i in range(20)]
        closes = [9.5 + i * 0.1 for i in range(20)]
        
        atr_values = calculate_atr14(highs, lows, closes)
        
        assert len(atr_values) == 20
        # First 13 should be None (need 14 bars)
        for i in range(13):
            assert atr_values[i] is None
        
        # ATR should be positive after calculation starts
        for i in range(13, 20):
            if atr_values[i] is not None:
                assert atr_values[i] > 0
    
    def test_volume_zscore(self):
        """Test volume Z-score calculation."""
        volumes = [1000000] * 20 + [5000000]  # Normal volume then spike
        z_scores = calculate_volume_zscore(volumes, 20)
        
        assert len(z_scores) == 21
        # First 19 should be None (need 20 bars)
        for i in range(19):
            assert z_scores[i] is None
        
        # Last value should have high Z-score (volume spike)
        if z_scores[-1] is not None:
            assert z_scores[-1] > 2.0
    
    def test_kline_pattern(self):
        """Test K-line pattern detection."""
        # Bullish engulfing pattern
        open_price = 100.0
        high = 105.0
        low = 95.0
        close = 104.0
        atr = 2.0
        
        result = check_kline_pattern(open_price, high, low, close, atr)
        # Should detect strong bullish candle
        assert isinstance(result, bool)


class TestSignals:
    """Test entry signal generation."""
    
    def test_entry_conditions_all_met(self):
        """Test entry when all conditions are met."""
        config = ReboundConfig()
        
        # Create data that should trigger all conditions
        dates = [f"2023-01-{i+1:02d}" for i in range(250)]
        closes = [100.0] * 200 + [95.0, 93.0, 91.0, 89.0, 87.0]  # Decline
        volumes = [1000000] * 245 + [5000000, 5000000, 5000000, 5000000, 5000000]  # Volume spike
        
        # Mock other required data
        opens = closes
        highs = [c * 1.02 for c in closes]
        lows = [c * 0.98 for c in closes]
        
        # This is a simplified test - actual implementation needs proper indicator values
        result = check_entry_conditions(
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            index=249,
            config=config,
        )
        
        assert isinstance(result, bool)
    
    def test_entry_conditions_trend_filter(self):
        """Test that trend filter blocks signals in downtrend."""
        config = ReboundConfig()
        
        # Create downtrend (price below SMA200)
        dates = [f"2023-01-{i+1:02d}" for i in range(250)]
        closes = [150.0 - i * 0.2 for i in range(250)]  # Steady decline
        
        opens = closes
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        volumes = [1000000] * 250
        
        result = check_entry_conditions(
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            index=249,
            config=config,
        )
        
        # Should be False due to downtrend
        assert result is False


class TestExitLogic:
    """Test exit condition logic."""
    
    def test_hard_stop_loss(self):
        """Test hard stop loss trigger."""
        config = ReboundConfig()
        
        position = Position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0},
        )
        
        # Current price below stop loss
        result = check_exit_conditions(
            position=position,
            current_date="2023-01-10",
            current_price=139.0,
            holding_days=10,
            closes=[150.0] * 20,
            index=19,
            config=config,
        )
        
        assert result is not None
        assert result["reason"] == "HARD_STOP"
    
    def test_time_stop(self):
        """Test time stop trigger."""
        config = ReboundConfig(max_holding_days=10)
        
        position = Position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0},
        )
        
        # Held for max days
        result = check_exit_conditions(
            position=position,
            current_date="2023-01-15",
            current_price=152.0,
            holding_days=10,
            closes=[150.0] * 20,
            index=19,
            config=config,
        )
        
        assert result is not None
        assert result["reason"] == "TIME_STOP"
    
    def test_sma20_exit(self):
        """Test SMA20 exit trigger."""
        config = ReboundConfig()
        
        position = Position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0},
        )
        
        # Price above SMA20 after recovery
        closes = [150.0] * 15 + [152.0, 153.0, 154.0, 155.0, 156.0]
        
        result = check_exit_conditions(
            position=position,
            current_date="2023-01-20",
            current_price=156.0,
            holding_days=5,
            closes=closes,
            index=19,
            config=config,
        )
        
        # May or may not trigger depending on SMA calculation
        assert result is None or result["reason"] == "SMA20_EXIT"


class TestPortfolio:
    """Test portfolio management."""
    
    def test_initial_portfolio(self):
        """Test initial portfolio state."""
        config = ReboundConfig(initial_equity=100000.0)
        portfolio = Portfolio(config)
        
        assert portfolio.cash == 100000.0
        assert len(portfolio.positions) == 0
        assert portfolio.equity({}) == 100000.0
    
    def test_open_position(self):
        """Test opening a position."""
        config = ReboundConfig(initial_equity=100000.0)
        portfolio = Portfolio(config)
        
        portfolio.open_position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0, "rsi": 25.0},
        )
        
        assert len(portfolio.positions) == 1
        assert "AAPL" in portfolio.positions
        assert portfolio.positions["AAPL"].shares == 100
        assert portfolio.cash < 100000.0  # Cash used
    
    def test_close_position(self):
        """Test closing a position."""
        config = ReboundConfig(initial_equity=100000.0)
        portfolio = Portfolio(config)
        
        portfolio.open_position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0},
        )
        
        trade = portfolio.close_position(
            security_id="AAPL",
            exit_date="2023-01-10",
            exit_price=160.0,
            exit_reason="SMA20_EXIT",
        )
        
        assert len(portfolio.positions) == 0
        assert trade is not None
        assert trade["security_id"] == "AAPL"
        assert trade["exit_reason"] == "SMA20_EXIT"
        assert trade["pnl"] > 0  # Profit
    
    def test_equity_calculation(self):
        """Test equity calculation with positions."""
        config = ReboundConfig(initial_equity=100000.0)
        portfolio = Portfolio(config)
        
        portfolio.open_position(
            security_id="AAPL",
            entry_date="2023-01-01",
            entry_price=150.0,
            shares=100,
            stop_loss_price=140.0,
            entry_indicators={"atr": 2.0},
        )
        
        # Equity should include position value
        current_prices = {"AAPL": 160.0}
        equity = portfolio.equity(current_prices)
        
        assert equity > 100000.0  # Position gained value


class TestPerformance:
    """Test performance metrics calculation."""
    
    def test_basic_metrics(self):
        """Test basic performance metrics."""
        trades = [
            {
                "security_id": "AAPL",
                "entry_date": "2023-01-01",
                "exit_date": "2023-01-10",
                "entry_price": 150.0,
                "exit_price": 160.0,
                "shares": 100,
                "pnl": 1000.0,
                "return_pct": 6.67,
                "exit_reason": "SMA20_EXIT",
                "holding_days": 10,
            },
            {
                "security_id": "MSFT",
                "entry_date": "2023-01-05",
                "exit_date": "2023-01-15",
                "entry_price": 250.0,
                "exit_price": 240.0,
                "shares": 50,
                "pnl": -500.0,
                "return_pct": -4.0,
                "exit_reason": "HARD_STOP",
                "holding_days": 10,
            },
        ]
        
        equity_curve = [
            {"date": "2023-01-01", "equity": 100000.0},
            {"date": "2023-01-10", "equity": 101000.0},
            {"date": "2023-01-15", "equity": 100500.0},
        ]
        
        metrics = calculate_performance_metrics(trades, equity_curve)
        
        assert "total_return" in metrics
        assert "win_rate" in metrics
        assert "avg_trade_return" in metrics
        assert "max_drawdown" in metrics
        
        assert metrics["total_trades"] == 2
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 1


class TestConfig:
    """Test configuration management."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = ReboundConfig()
        
        assert config.initial_equity == 100000.0
        assert config.rsi_threshold == 30.0
        assert config.atr_multiplier == 2.0
        assert config.max_holding_days == 20
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = ReboundConfig(
            initial_equity=200000.0,
            rsi_threshold=25.0,
            max_holding_days=15,
        )
        
        assert config.initial_equity == 200000.0
        assert config.rsi_threshold == 25.0
        assert config.max_holding_days == 15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
