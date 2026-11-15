# -*- coding: utf-8 -*-
"""Regression tests for T36 daily-bar freshness (C11/C12)."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pandas as pd

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    sys.modules["litellm"] = MagicMock()

from src.utils.daily_bar_freshness import (
    attach_daily_bar_freshness,
    drop_unclosed_daily_bars,
    evaluate_daily_bar_freshness,
)


def _bars(*dates: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": list(dates),
            "open": [1.0] * len(dates),
            "close": [1.0] * len(dates),
            "high": [1.0] * len(dates),
            "low": [1.0] * len(dates),
            "volume": [100] * len(dates),
        }
    )


class DropUnclosedBarTestCase(unittest.TestCase):
    def test_drops_intraday_partial_bar(self):
        df = _bars("2026-11-11", "2026-11-12", "2026-11-13")
        filtered, dropped = drop_unclosed_daily_bars(df, date(2026, 11, 12), code="600519")

        self.assertEqual(dropped, 1)
        self.assertEqual(
            list(pd.to_datetime(filtered["date"]).dt.date),
            [date(2026, 11, 11), date(2026, 11, 12)],
        )

    def test_keeps_bar_on_completed_session(self):
        df = _bars("2026-11-12", "2026-11-13")
        filtered, dropped = drop_unclosed_daily_bars(df, date(2026, 11, 13))

        self.assertEqual(dropped, 0)
        self.assertIs(filtered, df)

    def test_noop_without_dates(self):
        df = pd.DataFrame({"close": [1.0]})
        filtered, dropped = drop_unclosed_daily_bars(df, date(2026, 11, 13))
        self.assertEqual(dropped, 0)
        self.assertIs(filtered, df)


class FreshnessEvaluationTestCase(unittest.TestCase):
    def test_stale_when_bars_lag_expected_session(self):
        payload = evaluate_daily_bar_freshness(
            market="cn",
            data_as_of="2026-11-06",
            expected_as_of="2026-11-13",
        )

        self.assertTrue(payload["data_stale"])
        self.assertEqual(payload["data_as_of"], "2026-11-06")
        self.assertEqual(payload["data_expected_as_of"], "2026-11-13")
        self.assertGreaterEqual(payload["data_stale_sessions"], 1)
        self.assertIn("2026-11-06", payload["data_freshness_note_zh"])
        self.assertIn("2026-11-06", payload["data_freshness_note_en"])

    def test_fresh_when_bar_reaches_expected_session(self):
        payload = evaluate_daily_bar_freshness(
            market="cn",
            data_as_of="2026-11-13",
            expected_as_of="2026-11-13",
        )

        self.assertFalse(payload["data_stale"])
        self.assertEqual(payload["data_stale_sessions"], 0)
        self.assertNotIn("data_freshness_note_zh", payload)

    def test_unknown_dates_return_empty_payload(self):
        self.assertEqual(
            evaluate_daily_bar_freshness(market="cn", data_as_of=None, expected_as_of="2026-11-13"),
            {},
        )

    def test_attach_keeps_context_when_missing(self):
        context = {"code": "600519", "data_missing": True, "today": {}, "yesterday": {}}
        self.assertIs(
            attach_daily_bar_freshness(context, market="cn", expected_as_of=date(2026, 11, 13)),
            context,
        )


class ContextPackStaleTestCase(unittest.TestCase):
    def _artifacts(self, base_context: Dict[str, Any]):
        from src.services.analysis_context_builder import PipelineAnalysisArtifacts

        return PipelineAnalysisArtifacts(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            phase={"phase": "postmarket"},
            base_context=base_context,
            enhanced_context={},
            realtime_quote=None,
            trend_result=None,
            chip_data=None,
            fundamental_context=None,
            news_context=None,
            news_result_count=0,
            metadata={},
        )

    def _context(self, **extra) -> Dict[str, Any]:
        context = {
            "code": "600519",
            "date": "2026-11-06",
            "today": {"close": 1400.0, "date": "2026-11-06"},
            "yesterday": {"close": 1390.0, "date": "2026-11-05"},
        }
        context.update(extra)
        return context

    def test_stale_bars_downgrade_block_and_surface_date(self):
        from src.schemas.analysis_context_pack import ContextFieldStatus
        from src.services.analysis_context_builder import AnalysisContextBuilder

        freshness = evaluate_daily_bar_freshness(
            market="cn", data_as_of="2026-11-06", expected_as_of="2026-11-13"
        )
        pack = AnalysisContextBuilder.build(self._artifacts(self._context(**freshness)))

        block = pack.blocks["daily_bars"]
        self.assertIs(block.status, ContextFieldStatus.STALE)
        self.assertTrue(any("2026-11-06" in warning for warning in block.warnings))
        self.assertIn("daily_bars: stale", pack.data_quality.limitations)
        self.assertLess(pack.data_quality.overall_score, 100)
        self.assertGreater(pack.data_quality.overall_score, 0)

    def test_fresh_bars_keep_available_status(self):
        from src.schemas.analysis_context_pack import ContextFieldStatus
        from src.services.analysis_context_builder import AnalysisContextBuilder

        freshness = evaluate_daily_bar_freshness(
            market="cn", data_as_of="2026-11-13", expected_as_of="2026-11-13"
        )
        pack = AnalysisContextBuilder.build(self._artifacts(self._context(date="2026-11-13", **freshness)))

        self.assertIs(pack.blocks["daily_bars"].status, ContextFieldStatus.AVAILABLE)
        self.assertEqual(pack.blocks["daily_bars"].warnings, [])


class AnalyzerPromptStaleTestCase(unittest.TestCase):
    def test_prompt_states_data_as_of(self):
        from src.analyzer import GeminiAnalyzer

        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "date": "2026-11-06",
            "today": {"close": 1400.0, "date": "2026-11-06"},
            "yesterday": {"close": 1390.0, "date": "2026-11-05"},
            "data_stale": True,
            "data_as_of": "2026-11-06",
            "data_expected_as_of": "2026-11-13",
            "data_stale_sessions": 5,
        }
        prompt = GeminiAnalyzer._format_prompt(analyzer, context, "贵州茅台")

        self.assertIn("数据截至", prompt)
        self.assertIn("2026-11-06", prompt)
        self.assertIn("5 个交易日", prompt)
        self.assertIn("最新可得行情", prompt)
        # The stale note itself says "不是今日行情", so assert on the section
        # heading label rather than the raw phrase.
        self.assertIn("### 最新可得行情", prompt)
        self.assertNotIn("### 今日行情", prompt)


class PipelinePersistenceTestCase(unittest.TestCase):
    """C11: the pipeline must not persist an in-progress session bar."""

    def test_fetch_and_save_drops_partial_bar(self):
        from src.core.pipeline import StockAnalysisPipeline
        from tests.support import make_pipeline

        pipeline = make_pipeline()
        pipeline.config = MagicMock()
        pipeline.fetcher_manager = MagicMock()
        pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
        pipeline.fetcher_manager.get_daily_data.return_value = (
            _bars("2026-11-12", "2026-11-13", "2026-11-16"),
            "TestFetcher",
        )
        pipeline.db = MagicMock()
        pipeline.db.has_today_data.return_value = False
        pipeline.db.save_daily_data.return_value = 2

        with patch.object(
            StockAnalysisPipeline,
            "_resolve_resume_target_date",
            return_value=date(2026, 11, 13),
        ):
            success, error = pipeline.fetch_and_save_stock_data("600519")

        self.assertTrue(success)
        self.assertIsNone(error)
        saved_df = pipeline.db.save_daily_data.call_args.args[0]
        self.assertEqual(
            list(pd.to_datetime(saved_df["date"]).dt.date),
            [date(2026, 11, 12), date(2026, 11, 13)],
        )


if __name__ == "__main__":
    unittest.main()
