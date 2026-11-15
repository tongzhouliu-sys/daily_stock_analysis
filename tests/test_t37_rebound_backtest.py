"""Regression tests for the T37 backtest engine fixes (E1-E9)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.signal_scanner.research.us_equity_rebound.adjustments import (
    back_adjust_bars,
    split_events_in_range,
)
from src.services.signal_scanner.research.us_equity_rebound.config import ReboundConfig
from src.services.signal_scanner.research.us_equity_rebound.data_loader import (
    validate_data_consistency,
)
from src.services.signal_scanner.research.us_equity_rebound.engine import BacktestEngine
from src.services.signal_scanner.research.us_equity_rebound.portfolio import Portfolio
from src.services.signal_scanner.research.us_equity_rebound.precompute import build_series
from src.services.signal_scanner.research.vcr_short.config import DefaultConfig
from src.services.signal_scanner.research.vcr_short.matching import OrderMatcher
from src.services.signal_scanner.research.vcr_short.performance import PerformanceCalculator
from src.services.signal_scanner.research.vcr_short.portfolio import (
    ExitReason,
    Portfolio as ShortPortfolio,
    Position as ShortPosition,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _sessions(count: int) -> list[str]:
    start = date(2020, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def _rising_series(sessions: list[str], *, skip: set[str] | None = None):
    """260 sessions: long uptrend, flat top, then one oversold crash day."""
    skip = skip or set()
    data = {"date": [], "open": [], "high": [], "low": [], "close": [], "volume": []}

    for index, day in enumerate(sessions):
        if index < 210:
            close = 40.0 + (100.0 - 40.0) * index / 209.0
            open_price = close
            high = close
            low = close
            volume = 1_000_000.0
        elif index < 211:
            close = open_price = high = low = 100.0
            volume = 1_000_000.0
        elif index == 211:
            # First leg down: RSI2 reaches 0 but volume stays normal.
            close = 96.0
            open_price, high = 99.0, 99.5
            low = 95.5
            volume = 1_000_000.0
        elif index == 212:
            # Crash day that satisfies all four entry conditions.
            close = 90.4
            open_price, high, low = 90.6, 91.0, 89.0
            volume = 3_000_000.0
        else:
            close = open_price = high = low = 90.4
            volume = 1_000_000.0

        if day in skip:
            continue

        data["date"].append(day)
        data["open"].append(open_price)
        data["high"].append(high)
        data["low"].append(low)
        data["close"].append(close)
        data["volume"].append(volume)

    return data


def _spy(sessions: list[str]):
    spy = {"date": list(sessions), "open": [], "high": [], "low": [], "close": [], "volume": []}
    for index in range(len(sessions)):
        close = 100.0 + index * 0.5
        spy["open"].append(close)
        spy["high"].append(close)
        spy["low"].append(close)
        spy["close"].append(close)
        spy["volume"].append(10_000_000.0)
    return spy


def _run_engine(sessions, market_data, universe, start_index=210, **kwargs):
    config = ReboundConfig(
        start_date=sessions[start_index],
        end_date=sessions[-1],
        initial_equity=100_000.0,
        max_positions=kwargs.pop("max_positions", 5),
        **kwargs,
    )
    engine = BacktestEngine(config)
    results = engine.run(
        trading_dates=sessions,
        market_data=market_data,
        spy_data=_spy(sessions),
        universe=universe,
        universe_name="test-universe",
    )
    return engine, results


def _short_position(shares=100, entry=100.0, risk=5.0) -> ShortPosition:
    return ShortPosition(
        instrument_id="TEST",
        signal_date="2020-01-01",
        entry_date="2020-01-02",
        entry_price=entry,
        shares=shares,
        initial_risk_per_share=risk,
        initial_stop_loss=entry + risk,
        current_stop_loss=entry + risk,
        take_profit_1=entry - risk,
        take_profit_2=entry - 2 * risk,
        shares_remaining=shares,
        borrow_rate=0.0,
        commission_paid=10.0,
    )


# --------------------------------------------------------------------------
# E1 - unknown industry must not collide with the industry cap
# --------------------------------------------------------------------------

def test_unknown_industry_skips_industry_cap():
    config = ReboundConfig(max_positions=5, max_per_industry=2)
    portfolio = Portfolio(config)

    for index in range(4):
        assert portfolio.execute_buy(
            security_id=f"UNK{index}",
            date="2020-01-02",
            index=index,
            open_price=10.0,
            atr=1.0,
            industry="UNKNOWN",
            equity=100_000.0,
        )

    assert portfolio.num_positions() == 4


def test_known_industry_still_enforces_cap():
    config = ReboundConfig(max_positions=5, max_per_industry=2)
    portfolio = Portfolio(config)

    assert portfolio.execute_buy("A", "2020-01-02", 0, 10.0, 1.0, "Tech", 100_000.0)
    assert portfolio.execute_buy("B", "2020-01-02", 0, 10.0, 1.0, "Tech", 100_000.0)
    assert not portfolio.execute_buy("C", "2020-01-02", 0, 10.0, 1.0, "Tech", 100_000.0)


# --------------------------------------------------------------------------
# E2 - a buy order is only valid for the open of the next session
# --------------------------------------------------------------------------

def test_stale_buy_order_never_fills_later():
    sessions = _sessions(260)
    market_data = {"CRASH": _rising_series(sessions, skip={sessions[213], sessions[214]})}

    engine, results = _run_engine(sessions, market_data, ["CRASH"])

    assert results["trades"] == []
    assert results["performance"]["open_positions"] == 0
    assert engine.pending_buy_orders == []


def test_buy_order_fills_on_next_session_open():
    sessions = _sessions(260)
    market_data = {"CRASH": _rising_series(sessions)}

    _, results = _run_engine(sessions, market_data, ["CRASH"])

    assert len(results["trades"]) == 1
    assert results["trades"][0]["entry_date"] == sessions[213]


# --------------------------------------------------------------------------
# E3 - split adjustment
# --------------------------------------------------------------------------

def test_back_adjust_bars_applies_split_factor():
    rows = [
        {"ticker": "AAPL", "date": "2020-08-28", "open": 400.0, "high": 404.0,
         "low": 396.0, "close": 400.0, "volume": 100.0},
        {"ticker": "AAPL", "date": "2020-08-31", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 400.0},
    ]
    splits = [{"ticker": "AAPL", "execution_date": "2020-08-31", "split_ratio": 4.0}]

    adjusted = back_adjust_bars(rows, splits)

    assert adjusted[0]["close"] == pytest.approx(100.0)
    assert adjusted[0]["volume"] == pytest.approx(400.0)
    assert adjusted[1]["close"] == pytest.approx(100.0)
    assert adjusted[1]["volume"] == pytest.approx(400.0)


def test_split_events_in_range_builds_engine_events():
    splits = [
        {"ticker": "MSFT", "execution_date": "2019-01-01", "split_ratio": 2.0},
        {"ticker": "MSFT", "execution_date": "2021-06-01", "split_ratio": 2.0},
    ]
    events = split_events_in_range(splits, "2020-01-01", "2023-12-31")

    assert events == [
        {
            "security_id": "MSFT",
            "type": "SPLIT",
            "effective_date": "2021-06-01",
            "split_ratio": 2.0,
        }
    ]


def test_engine_split_action_adjusts_open_position():
    sessions = ["2020-01-02", "2020-01-03", "2020-01-06"]
    config = ReboundConfig(start_date=sessions[0], end_date=sessions[2])
    portfolio = Portfolio(config)
    portfolio.execute_buy("AAA", sessions[0], 0, 100.0, 5.0, "Tech", 100_000.0)
    position = portfolio.positions["AAA"]
    shares_before = position.shares
    entry_before = position.entry_price
    stop_before = position.stop_price

    from src.services.signal_scanner.research.us_equity_rebound.corporate_actions import (
        process_corporate_actions,
    )

    _, positions = process_corporate_actions(
        portfolio.cash,
        portfolio.positions,
        [{"security_id": "AAA", "type": "SPLIT", "effective_date": sessions[1], "split_ratio": 2.0}],
        sessions[1],
    )

    assert positions["AAA"].shares == shares_before * 2
    assert positions["AAA"].entry_price == pytest.approx(entry_before / 2)
    assert positions["AAA"].stop_price == pytest.approx(stop_before / 2)


# --------------------------------------------------------------------------
# E4 - ragged histories are valid, malformed series are not
# --------------------------------------------------------------------------

def test_validation_allows_mid_window_listing_and_delisting():
    sessions = _sessions(30)
    market_data = {
        "EARLY": {
            "date": sessions[:10],
            "open": [1.0] * 10,
            "high": [1.0] * 10,
            "low": [1.0] * 10,
            "close": [1.0] * 10,
            "volume": [1_000_000.0] * 10,
        },
        "LATE": {
            "date": sessions[20:],
            "open": [1.0] * 10,
            "high": [1.0] * 10,
            "low": [1.0] * 10,
            "close": [1.0] * 10,
            "volume": [1_000_000.0] * 10,
        },
    }
    spy = {"date": sessions, "close": [1.0] * 30}

    validate_data_consistency(market_data, spy, sessions, ["EARLY", "LATE"])


def test_validation_rejects_unsorted_security_dates():
    sessions = _sessions(5)
    market_data = {
        "BAD": {
            "date": [sessions[2], sessions[1]],
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1.0, 1.0],
        }
    }
    spy = {"date": sessions, "close": [1.0] * 5}

    with pytest.raises(ValueError, match="not ascending"):
        validate_data_consistency(market_data, spy, sessions, ["BAD"])


# --------------------------------------------------------------------------
# E5 - warmup consumes pre-start history, not the replay window
# --------------------------------------------------------------------------

def test_warmup_uses_history_before_start_date():
    sessions = _sessions(260)
    market_data = {"CRASH": _rising_series(sessions)}

    _, results = _run_engine(sessions, market_data, ["CRASH"], start_index=210)

    assert results["daily_records"][0]["date"] == sessions[210]
    assert results["data_info"]["warmup_sessions_before_start"] == 210
    # A signal on the third replay session only works if SMA200 was already
    # warm from pre-start data.
    assert results["trades"][0]["entry_date"] == sessions[213]


# --------------------------------------------------------------------------
# E6 - short portfolio equity must not double count sale proceeds
# --------------------------------------------------------------------------

def test_short_equity_subtracts_current_market_value():
    config = DefaultConfig
    portfolio = ShortPortfolio(config=config, cash=config.initial_equity)
    position = _short_position()
    portfolio.open_position(position)

    equity_at_entry = portfolio.current_equity({"TEST": 100.0})
    assert equity_at_entry == pytest.approx(config.initial_equity - position.commission_paid)

    equity_after_drop = portfolio.current_equity({"TEST": 90.0})
    assert equity_after_drop == pytest.approx(equity_at_entry + 1000.0)


# --------------------------------------------------------------------------
# E7 - win rate reads the P&L field the trade log actually wrote
# --------------------------------------------------------------------------

def test_win_rate_uses_net_pnl_when_realized_pnl_missing():
    calculator = PerformanceCalculator(DefaultConfig)
    trades = [
        {"net_pnl": 100.0},
        {"net_pnl": -50.0},
        {"net_pnl": 25.0},
    ]

    stats = calculator.calculate_trade_statistics(trades)

    assert stats["winning_trades"] == 2
    assert stats["losing_trades"] == 1
    assert stats["win_rate"] == pytest.approx(2 / 3)


# --------------------------------------------------------------------------
# E8 - trailing/breakeven stops use the previous session only
# --------------------------------------------------------------------------

def test_breakeven_stop_uses_previous_session_low():
    matcher = OrderMatcher(DefaultConfig)
    position = _short_position(entry=100.0, risk=5.0)

    # Current-day low would have activated breakeven, but it is not known
    # before the current open: only the previous low counts.
    matcher.update_trailing_stop(position, prev_low=96.0, prev_high=100.0)
    assert position.breakeven_activated is False

    matcher.update_trailing_stop(position, prev_low=94.0, prev_high=100.0)
    assert position.breakeven_activated is True
    assert position.current_stop_loss == pytest.approx(100.0)


# --------------------------------------------------------------------------
# E9 - multi-tranche P&L and point-in-time liquidity
# --------------------------------------------------------------------------

def test_partial_exit_pnl_accumulates_across_tranches():
    config = DefaultConfig
    portfolio = ShortPortfolio(config=config, cash=config.initial_equity)
    position = _short_position(shares=100, entry=100.0, risk=5.0)
    portfolio.open_position(position)

    portfolio.close_position("TEST", "2020-01-06", 90.0, 50, ExitReason.TAKE_PROFIT_1)
    closed = portfolio.close_position("TEST", "2020-01-07", 80.0, 50, ExitReason.TAKE_PROFIT_2)

    assert closed is not None
    # (100-90)*50 + (100-80)*50 = 1500 gross, 10 commission charged once.
    assert closed.gross_realized == pytest.approx(1500.0)
    assert closed.realized_pnl == pytest.approx(1490.0)


def test_liquidity_uses_only_trailing_volume():
    sessions = _sessions(60)
    data = {
        "date": list(sessions),
        "open": [10.0] * 60,
        "high": [10.0] * 60,
        "low": [10.0] * 60,
        "close": [10.0] * 60,
        "volume": [100_000.0] * 40 + [50_000_000.0] * 20,
    }

    series = build_series("LATE_SPIKE", data)

    assert series is not None
    assert series.avg_volume20[30] == pytest.approx(100_000.0)
    assert series.avg_volume20[59] == pytest.approx(50_000_000.0)
