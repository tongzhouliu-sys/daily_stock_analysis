# -*- coding: utf-8 -*-
"""T31 alert semantics: edge-triggered price alerts, session gating, volume baseline."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from src.config import Config
from src.core.trading_calendar import MarketPhase
from src.services.alert_indicators import _drop_partial_today
from src.services.alert_service import AlertService
from src.services.alert_worker import AlertWorker
from src.storage import DatabaseManager


def _quote_prices(*prices: float) -> AsyncMock:
    return AsyncMock(side_effect=[SimpleNamespace(price=value) for value in prices])


class AlertSemanticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "t31_alert.db"
        self.env_path.write_text(
            "\n".join([
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={self.db_path}",
            ])
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.service = AlertService()
        # Keep the session gate out of the price-cross scenarios; it has its
        # own dedicated test below.
        self._session_patch = patch.object(
            AlertWorker, "_is_non_trading_session", staticmethod(lambda _code: False)
        )
        self._session_patch.start()

    def tearDown(self) -> None:
        self._session_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _config(self) -> SimpleNamespace:
        return SimpleNamespace(
            agent_event_monitor_enabled=True,
            agent_event_alert_rules_json="",
            trading_day_check_enabled=False,
        )

    def _create_price_rule(self, **overrides) -> dict:
        payload = {
            "name": "Moutai breakout",
            "target_scope": "single_symbol",
            "target": "600519",
            "alert_type": "price_cross",
            "parameters": {"direction": "above", "price": 1800},
            "severity": "warning",
            "enabled": True,
        }
        payload.update(overrides)
        return self.service.create_rule(payload)

    def _worker(self) -> AlertWorker:
        return AlertWorker(
            config_provider=lambda: self._config(),
            service=self.service,
            notifier=MagicMock(),
        )

    def _trigger_count(self) -> int:
        return len(self.service.list_triggers(page_size=100, status="triggered")["items"])

    def test_price_above_threshold_three_times_triggers_once(self) -> None:
        self._create_price_rule()
        worker = self._worker()

        with patch(
            "src.agent.events.EventMonitor._get_realtime_quote",
            new=_quote_prices(1810.0, 1815.0, 1820.0),
        ):
            first = worker.run_once()
            second = worker.run_once()
            third = worker.run_once()

        self.assertEqual(first["triggered"], 1)
        self.assertEqual(second["triggered"], 0)
        self.assertEqual(third["triggered"], 0)
        self.assertEqual(self._trigger_count(), 1)

    def test_price_dipping_below_and_back_triggers_twice(self) -> None:
        self._create_price_rule()
        worker = self._worker()

        with patch(
            "src.agent.events.EventMonitor._get_realtime_quote",
            new=_quote_prices(1810.0, 1790.0, 1810.0),
        ):
            worker.run_once()
            worker.run_once()
            third = worker.run_once()

        self.assertEqual(third["triggered"], 1)
        self.assertEqual(self._trigger_count(), 2)

    def test_price_state_is_persisted_across_worker_restarts(self) -> None:
        self._create_price_rule()

        with patch(
            "src.agent.events.EventMonitor._get_realtime_quote",
            new=_quote_prices(1810.0),
        ):
            first = self._worker().run_once()

        # A fresh worker must still remember the price already sits above the
        # level, otherwise every process restart re-sends the same alert.
        with patch(
            "src.agent.events.EventMonitor._get_realtime_quote",
            new=_quote_prices(1810.0),
        ):
            second = self._worker().run_once()

        self.assertEqual(first["triggered"], 1)
        self.assertEqual(second["triggered"], 0)
        self.assertEqual(self._trigger_count(), 1)

    def test_non_trading_session_skips_price_alert(self) -> None:
        rule = self._create_price_rule()
        self._session_patch.stop()
        try:
            with patch(
                "src.services.alert_worker.infer_market_phase",
                return_value=MarketPhase.NON_TRADING,
            ), patch(
                "src.agent.events.EventMonitor._get_realtime_quote",
                new=_quote_prices(1810.0),
            ):
                stats = self._worker().run_once()
        finally:
            self._session_patch.start()

        self.assertEqual(stats["triggered"], 0)
        self.assertEqual(stats["skipped"], 1)
        triggers = self.service.list_triggers(page_size=100, rule_id=rule["id"], status="skipped")["items"]
        self.assertEqual(len(triggers), 1)
        self.assertIn("not in a trading session", triggers[0]["diagnostics"])

    def test_deleting_rule_removes_persisted_price_state(self) -> None:
        rule = self._create_price_rule()
        with patch(
            "src.agent.events.EventMonitor._get_realtime_quote",
            new=_quote_prices(1810.0),
        ):
            self._worker().run_once()

        state_key = f"rule:{rule['id']}:600519"
        self.assertIsNotNone(self.service.repo.get_rule_state(state_key))
        self.assertTrue(self.service.delete_rule(rule["id"]))
        self.assertIsNone(self.service.repo.get_rule_state(state_key))

    def test_volume_baseline_excludes_current_bar(self) -> None:
        self._create_price_rule(
            name="Volume",
            target="000858",
            alert_type="volume_spike",
            parameters={"multiplier": 2.0},
        )
        manager = MagicMock()
        manager.get_daily_data.return_value = (
            pd.DataFrame(
                {
                    "date": [date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)],
                    "volume": [1000, 1000, 5000],
                }
            ),
            "test_source",
        )

        async def _run_inline(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("data_provider.DataFetcherManager", return_value=manager), \
             patch("src.services.alert_service.asyncio.to_thread", new=_run_inline):
            worker = self._worker()
            worker.run_once()

        triggers = self.service.list_triggers(page_size=100, status="triggered")["items"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["observed_value"], 5000.0)
        # mean(1000, 1000) * 2.0 -- the spike bar must not inflate its own baseline.
        self.assertAlmostEqual(triggers[0]["threshold"], 2000.0)


class PartialBarSessionGateTestCase(unittest.TestCase):
    def _frame(self, bar_date: date) -> pd.DataFrame:
        return pd.DataFrame({"date": [bar_date], "close": [10.0]})

    def test_open_session_drops_same_day_bar(self) -> None:
        now = datetime(2026, 5, 15, 22, 0)
        with (
            patch("src.services.alert_indicators.get_market_for_stock", return_value="us"),
            patch("src.services.alert_indicators.infer_market_phase", return_value=MarketPhase.INTRADAY),
            patch(
                "src.services.alert_indicators.get_market_now",
                return_value=datetime(2026, 5, 15, 10, 0),
            ),
        ):
            result = _drop_partial_today(self._frame(date(2026, 5, 15)), now=now, stock_code="AAPL")

        self.assertTrue(result.empty)

    def test_closed_session_keeps_same_day_bar(self) -> None:
        now = datetime(2026, 5, 16, 5, 0)
        with (
            patch("src.services.alert_indicators.get_market_for_stock", return_value="us"),
            patch("src.services.alert_indicators.infer_market_phase", return_value=MarketPhase.POSTMARKET),
            patch(
                "src.services.alert_indicators.get_market_now",
                return_value=datetime(2026, 5, 15, 17, 0),
            ),
        ):
            result = _drop_partial_today(self._frame(date(2026, 5, 15)), now=now, stock_code="AAPL")

        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
