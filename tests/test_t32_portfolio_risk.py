# -*- coding: utf-8 -*-
"""T32: portfolio valuation and risk semantics (E17 / E18 / E19).

These tests pin the numeric behaviour that the audit asked for:

* E17 - external deposits/withdrawals must not manufacture a drawdown, and the
  alert must follow the *current* drawdown rather than the historical peak.
* E18 - a position whose price is missing is valued at cost, and it must not be
  read as a -100% stop-loss nor distort concentration weights.
* E19 - a missing FX rate must stay visible instead of silently converting 1:1.
"""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import FX_SOURCE_MISSING, PortfolioService


class _FakeRepo:
    def __init__(self, *, snapshots=None, ledger=None):
        self.snapshots = snapshots or []
        self.ledger = ledger or []
        self.ledger_calls = []

    def list_daily_snapshots_for_risk(self, **_kwargs):
        return list(self.snapshots)

    def list_cash_ledger_window(self, **kwargs):
        self.ledger_calls.append(kwargs)
        return list(self.ledger)

    def get_latest_fx_rate(self, **_kwargs):
        # No FX rate is on file: every cross-currency conversion must stay
        # visibly unconverted instead of falling back to 1:1.
        return None


class _FakePortfolioService:
    """Identity converter: every amount is already CNY."""

    def __init__(self, *, missing_rates=()):
        self.missing_rates = {str(code).upper() for code in missing_rates}

    def convert_amount(self, *, amount, from_currency, to_currency, as_of_date):
        if str(from_currency).upper() in self.missing_rates:
            return 0.0, True, FX_SOURCE_MISSING
        return float(amount), False, "direct"


def _snapshot_row(day: int, equity: float, currency: str = "CNY", fx_stale: bool = False):
    return SimpleNamespace(
        snapshot_date=date(2026, 1, day),
        total_equity=equity,
        base_currency=currency,
        fx_stale=fx_stale,
    )


def _ledger_row(day: int, *, direction: str, amount: float, currency: str = "CNY"):
    return SimpleNamespace(
        event_date=date(2026, 1, day),
        direction=direction,
        amount=amount,
        currency=currency,
    )


def _service(*, snapshots=None, ledger=None, missing_rates=()):
    return PortfolioRiskService(
        repo=_FakeRepo(snapshots=snapshots, ledger=ledger),
        portfolio_service=_FakePortfolioService(missing_rates=missing_rates),
    )


class E17CashFlowAdjustedDrawdownTestCase(unittest.TestCase):
    def test_withdrawal_does_not_manufacture_drawdown(self):
        """1,000,000 -> 700,000 caused by a 300,000 withdrawal is a 0% drawdown."""

        service = _service(
            snapshots=[
                _snapshot_row(2, 1_000_000.0),
                _snapshot_row(3, 700_000.0),
            ],
            ledger=[_ledger_row(3, direction="out", amount=300_000.0)],
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 5),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertEqual(result["series_points"], 2)
        self.assertAlmostEqual(result["max_drawdown_pct"], 0.0, places=4)
        self.assertAlmostEqual(result["current_drawdown_pct"], 0.0, places=4)
        self.assertFalse(result["alert"])
        self.assertTrue(result["cash_flow_adjusted"])

    def test_deposit_does_not_hide_or_fake_drawdown(self):
        """1,000,000 -> 800,000 while depositing 100,000 is a real 20% loss."""

        service = _service(
            snapshots=[
                _snapshot_row(2, 1_000_000.0),
                _snapshot_row(3, 900_000.0),
            ],
            ledger=[_ledger_row(3, direction="in", amount=100_000.0)],
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 5),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        # equity/base = 900,000 / 1,100,000 = 0.818181... -> 18.1818% drawdown
        self.assertAlmostEqual(result["max_drawdown_pct"], 18.1818, places=3)
        self.assertAlmostEqual(result["current_drawdown_pct"], 18.1818, places=3)
        self.assertTrue(result["alert"])

    def test_alert_tracks_current_not_historical_drawdown(self):
        """A recovered portfolio must stop alerting on the old peak-to-trough value."""

        service = _service(
            snapshots=[
                _snapshot_row(2, 100_000.0),
                _snapshot_row(3, 70_000.0),  # -30% trough
                _snapshot_row(4, 99_000.0),  # -1% from peak
            ],
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 5),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertAlmostEqual(result["max_drawdown_pct"], 30.0, places=4)
        self.assertAlmostEqual(result["current_drawdown_pct"], 1.0, places=4)
        self.assertFalse(result["alert"])

    def test_ledger_is_queried_for_the_snapshot_window(self):
        repo = _FakeRepo(
            snapshots=[_snapshot_row(2, 100.0)],
            ledger=[_ledger_row(2, direction="out", amount=10.0)],
        )
        service = PortfolioRiskService(
            repo=repo,
            portfolio_service=_FakePortfolioService(),
        )
        service._build_drawdown(
            account_id=7,
            as_of_date=date(2026, 1, 5),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertEqual(len(repo.ledger_calls), 1)
        call = repo.ledger_calls[0]
        self.assertEqual(call["account_id"], 7)
        self.assertEqual(call["end_date"], date(2026, 1, 5))
        self.assertEqual(call["start_date"], date(2025, 12, 6))

    def test_missing_fx_snapshot_is_excluded_and_flagged(self):
        """A USD snapshot without an FX rate must not be chained at par."""

        service = _service(
            snapshots=[
                _snapshot_row(2, 1_000_000.0),
                _snapshot_row(3, 700_000.0, currency="USD"),
            ],
            missing_rates={"USD"},
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 5),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertTrue(result["fx_missing"])
        self.assertEqual(result["series_points"], 1)
        self.assertAlmostEqual(result["current_drawdown_pct"], 0.0, places=4)


class E18MissingPriceTestCase(unittest.TestCase):
    @staticmethod
    def _snapshot(*positions):
        return {
            "currency": "CNY",
            "total_market_value": 100_000.0,
            "accounts": [
                {
                    "account_id": 1,
                    "base_currency": "CNY",
                    "positions": list(positions),
                }
            ],
        }

    def test_missing_price_position_is_cost_valued_not_zero(self):
        service = _service()
        snapshot = self._snapshot(
            {
                "symbol": "AAPL",
                "avg_cost": 50.0,
                "last_price": 0.0,
                "market_value_base": 5_000.0,
                "price_available": False,
                "valuation_basis": "cost",
            }
        )

        stop_loss = service._build_stop_loss(snapshot, {"stop_loss_alert_pct": 10.0, "stop_loss_near_ratio": 0.8})

        self.assertEqual(stop_loss["items"], [])
        self.assertEqual(stop_loss["triggered_count"], 0)
        self.assertFalse(stop_loss["near_alert"])
        self.assertEqual(stop_loss["excluded_missing_price_count"], 1)

    def test_missing_price_does_not_distort_concentration(self):
        service = _service()
        snapshot = self._snapshot(
            {
                "symbol": "AAPL",
                "market_value_base": 5_000.0,
                "price_available": False,
                "valuation_basis": "cost",
            },
            {
                "symbol": "MSFT",
                "market_value_base": 20_000.0,
                "price_available": True,
            },
        )
        snapshot["total_market_value"] = 25_000.0

        concentration = service._build_concentration(snapshot, 50.0, as_of_date=date(2026, 1, 5))

        symbols = [row["symbol"] for row in concentration["top_positions"]]
        self.assertEqual(symbols, ["MSFT"])
        # 20,000 / (25,000 - 5,000) = 100%
        self.assertAlmostEqual(concentration["top_positions"][0]["weight_pct"], 100.0, places=4)
        self.assertAlmostEqual(concentration["total_market_value"], 20_000.0, places=4)
        self.assertEqual(concentration["excluded_missing_price_count"], 1)

    def test_priced_position_still_triggers_stop_loss(self):
        service = _service()
        snapshot = self._snapshot(
            {
                "symbol": "AAPL",
                "avg_cost": 100.0,
                "last_price": 85.0,
                "market_value_base": 8_500.0,
                "price_available": True,
            }
        )

        stop_loss = service._build_stop_loss(snapshot, {"stop_loss_alert_pct": 10.0, "stop_loss_near_ratio": 0.8})

        self.assertEqual(stop_loss["triggered_count"], 1)
        self.assertEqual(stop_loss["items"][0]["symbol"], "AAPL")
        self.assertAlmostEqual(stop_loss["items"][0]["loss_pct"], 15.0, places=4)


class E19MissingFxTestCase(unittest.TestCase):
    def test_missing_rate_returns_zero_and_flag(self):
        service = PortfolioService(repo=_FakeRepo())

        amount, stale, source = service._convert_amount(
            amount=100_000.0,
            from_currency="USD",
            to_currency="CNY",
            as_of_date=date(2026, 1, 5),
        )

        self.assertEqual(amount, 0.0)
        self.assertTrue(stale)
        self.assertEqual(source, FX_SOURCE_MISSING)

    def test_converted_amount_is_not_par_for_missing_currency(self):
        """USD 100k must never be reported as CNY 100k."""

        service = PortfolioService(repo=_FakeRepo())
        converted, _, source = service._convert_amount(
            amount=100_000.0,
            from_currency="USD",
            to_currency="CNY",
            as_of_date=date(2026, 1, 5),
        )

        self.assertNotAlmostEqual(converted, 100_000.0)
        self.assertEqual(converted, 0.0)
        self.assertEqual(source, FX_SOURCE_MISSING)


if __name__ == "__main__":
    unittest.main()


class R19SparseCashFlowTestCase(unittest.TestCase):
    def test_withdrawal_between_snapshots_is_charged_to_the_later_snapshot(self):
        """Friday 1,000,000 -> Monday 700,000 with a Saturday 300,000 withdrawal is 0%."""

        service = _service(
            snapshots=[
                _snapshot_row(16, 1_000_000.0),  # Friday
                _snapshot_row(19, 700_000.0),  # Monday, no snapshot on the weekend
            ],
            ledger=[_ledger_row(17, direction="out", amount=300_000.0)],
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 20),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertEqual(result["series_points"], 2)
        self.assertAlmostEqual(result["max_drawdown_pct"], 0.0, places=4)
        self.assertAlmostEqual(result["current_drawdown_pct"], 0.0, places=4)
        self.assertFalse(result["alert"])

    def test_deposit_between_snapshots_does_not_create_a_gain(self):
        service = _service(
            snapshots=[
                _snapshot_row(16, 1_000_000.0),
                _snapshot_row(19, 1_300_000.0),
            ],
            ledger=[_ledger_row(17, direction="in", amount=300_000.0)],
        )

        result = service._build_drawdown(
            account_id=1,
            as_of_date=date(2026, 1, 20),
            cost_method="fifo",
            threshold_pct=10.0,
            lookback_days=30,
        )

        self.assertAlmostEqual(result["max_drawdown_pct"], 0.0, places=4)
        self.assertAlmostEqual(result["current_drawdown_pct"], 0.0, places=4)


class _FxPortfolioService:
    """USD -> CNY at 7.2; CNY direct."""

    def convert_amount(self, *, amount, from_currency, to_currency, as_of_date):
        currency = str(from_currency).upper()
        if currency == "USD":
            return float(amount) * 7.2, False, "fx"
        return float(amount), False, "direct"


class R19ConcentrationFxTestCase(unittest.TestCase):
    def test_missing_price_exclusion_uses_summary_currency_rate(self):
        service = PortfolioRiskService(
            repo=_FakeRepo(),
            portfolio_service=_FxPortfolioService(),
        )
        snapshot = {
            "currency": "CNY",
            # 10,000 USD priced + 10,000 USD cost-valued, both at 7.2
            "total_market_value": 144_000.0,
            "accounts": [
                {
                    "account_id": 1,
                    "base_currency": "USD",
                    "positions": [
                        {
                            "symbol": "AAPL",
                            "market_value_base": 10_000.0,
                            "valuation_currency": "USD",
                            "price_available": True,
                        },
                        {
                            "symbol": "MSFT",
                            "market_value_base": 10_000.0,
                            "valuation_currency": "USD",
                            "price_available": False,
                            "valuation_basis": "cost",
                        },
                    ],
                }
            ],
        }

        concentration = service._build_concentration(snapshot, 50.0, as_of_date=date(2026, 1, 5))

        self.assertEqual([row["symbol"] for row in concentration["top_positions"]], ["AAPL"])
        self.assertAlmostEqual(concentration["top_positions"][0]["weight_pct"], 100.0, places=4)
        self.assertAlmostEqual(concentration["total_market_value"], 72_000.0, places=4)
        self.assertEqual(concentration["excluded_missing_price_count"], 1)
