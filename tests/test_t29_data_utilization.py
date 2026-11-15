"""T29: 把已有数据用起来的回归测试。

覆盖四处此前"抓到了但没用"的数据：
1. 个股资金流历史里的近 5/10 日主力净流入；
2. 日线窗口不足 60 根时不再用 MA20 冒充 MA60；
3. history_loader 的缓存命中阈值（80%）；
4. 业绩预告/快报、机构与十大股东变动、龙虎榜进入提示词，以及新闻窗口口径。
"""

from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import pandas as pd

from data_provider.fundamental_adapter import _sum_recent_net_inflow


class CapitalFlowWindowTest(unittest.TestCase):
    def _frame(self, nets):
        dates = pd.date_range("2026-01-05", periods=len(nets), freq="D")
        return pd.DataFrame(
            {
                "日期": dates.strftime("%Y-%m-%d"),
                "主力净流入-净额": nets,
            }
        )

    def test_sums_five_and_ten_day_windows(self):
        nets = [float(i) for i in range(1, 13)]
        df = self._frame(nets)
        dates = pd.to_datetime(df["日期"], errors="coerce")
        inflow_5d, inflow_10d = _sum_recent_net_inflow(df, dates, float(nets[-1]))

        # 最新 5 行为 8..12 之和
        self.assertAlmostEqual(inflow_5d, 50.0)
        # 最新 10 行为 3..12 之和
        self.assertAlmostEqual(inflow_10d, 75.0)

    def test_partial_window_returns_none_instead_of_partial_sum(self):
        nets = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        df = self._frame(nets)
        dates = pd.to_datetime(df["日期"], errors="coerce")
        inflow_5d, inflow_10d = _sum_recent_net_inflow(df, dates, 7.0)

        self.assertAlmostEqual(inflow_5d, 3.0 + 4.0 + 5.0 + 6.0 + 7.0)
        self.assertIsNone(inflow_10d)

    def test_latest_net_inflow_overrides_missing_cell(self):
        df = self._frame([1.0, 2.0, 3.0, 4.0, None])
        dates = pd.to_datetime(df["日期"], errors="coerce")
        inflow_5d, inflow_10d = _sum_recent_net_inflow(df, dates, 10.0)

        self.assertAlmostEqual(inflow_5d, 1.0 + 2.0 + 3.0 + 4.0 + 10.0)
        self.assertIsNone(inflow_10d)

    def test_missing_column_returns_none(self):
        df = pd.DataFrame({"日期": ["2026-01-05"], "other": [1.0]})
        dates = pd.to_datetime(df["日期"], errors="coerce")
        self.assertEqual(_sum_recent_net_inflow(df, dates, 1.0), (None, None))

    def test_duplicate_dates_do_not_double_count(self):
        dates = pd.to_datetime(
            ["2026-01-09", "2026-01-09", "2026-01-08", "2026-01-07", "2026-01-06", "2026-01-05"],
            errors="coerce",
        )
        df = pd.DataFrame({"主力净流入-净额": [5.0, 5.0, 4.0, 3.0, 2.0, 1.0]})
        inflow_5d, _inflow_10d = _sum_recent_net_inflow(df, dates, 5.0)
        self.assertAlmostEqual(inflow_5d, 5.0 + 4.0 + 3.0 + 2.0 + 1.0)


class Ma60NoSubstitutionTest(unittest.TestCase):
    def test_short_history_leaves_ma60_nan(self):
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer.__new__(StockTrendAnalyzer)
        df = pd.DataFrame({"close": [10.0 + i for i in range(30)]})
        out = StockTrendAnalyzer._calculate_mas(analyzer, df)

        self.assertIn("MA60", out.columns)
        self.assertTrue(out["MA60"].isna().all())

    def test_long_history_computes_ma60(self):
        from src.stock_analyzer import StockTrendAnalyzer

        analyzer = StockTrendAnalyzer.__new__(StockTrendAnalyzer)
        df = pd.DataFrame({"close": [10.0 + i for i in range(80)]})
        out = StockTrendAnalyzer._calculate_mas(analyzer, df)

        tail = out["MA60"].iloc[-1]
        self.assertTrue(math.isfinite(tail))
        expected = sum(10.0 + i for i in range(20, 80)) / 60.0
        self.assertAlmostEqual(tail, expected)


class PromptEventsTest(unittest.TestCase):
    def _context(self):
        return {
            "code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1500.0,
            "fundamental_context": {
                "growth": {
                    "status": "ok",
                    "data": {
                        "revenue_yoy": 12.5,
                        "net_profit_yoy": 15.1,
                        "roe": None,
                        "gross_margin": 91.2,
                    },
                },
                "earnings": {
                    "status": "ok",
                    "data": {
                        "forecast_summary": "预计净利润同比增长 15%",
                        "quick_report_summary": None,
                    },
                },
                "institution": {
                    "status": "ok",
                    "data": {
                        "institution_holding_change": 1234.5,
                        "top10_holder_change": None,
                    },
                },
                "dragon_tiger": {
                    "status": "ok",
                    "data": {"is_on_list": True, "recent_count": 2, "latest_date": "2026-11-06"},
                },
            },
        }

    def test_rows_only_include_present_values(self):
        from src.analyzer import _build_earnings_and_chip_event_rows

        rows = _build_earnings_and_chip_event_rows(self._context()["fundamental_context"])
        labels = {row[1] for row in rows}

        self.assertIn("营收同比%", labels)
        self.assertIn("净利润同比%", labels)
        self.assertIn("毛利率%", labels)
        self.assertIn("业绩预告", labels)
        self.assertIn("机构持仓变动", labels)
        self.assertIn("龙虎榜（次数/最近日期）", labels)
        # 缺失项不产生占位行
        self.assertNotIn("净资产收益率%", labels)
        self.assertNotIn("业绩快报", labels)
        self.assertNotIn("十大股东变动", labels)

    def test_rows_empty_without_events(self):
        from src.analyzer import _build_earnings_and_chip_event_rows

        self.assertEqual(_build_earnings_and_chip_event_rows(None), [])
        self.assertEqual(_build_earnings_and_chip_event_rows({}), [])
        self.assertEqual(
            _build_earnings_and_chip_event_rows(
                {"dragon_tiger": {"status": "ok", "data": {"is_on_list": False, "recent_count": 0}}}
            ),
            [],
        )

    def test_prompt_contains_event_table(self):
        from src.analyzer import GeminiAnalyzer

        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        prompt = GeminiAnalyzer._format_prompt(analyzer, self._context(), "贵州茅台")

        self.assertIn("业绩与筹码事件（事件驱动参考）", prompt)
        self.assertIn("营收同比%", prompt)
        self.assertIn("龙虎榜（次数/最近日期）", prompt)

    def test_prompt_event_table_absent_when_no_events(self):
        from src.analyzer import GeminiAnalyzer

        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {"code": "600519", "stock_name": "贵州茅台", "current_price": 1500.0}
        prompt = GeminiAnalyzer._format_prompt(analyzer, context, "贵州茅台")

        self.assertNotIn("业绩与筹码事件（事件驱动参考）", prompt)


class NewsWindowAlignmentTest(unittest.TestCase):
    def test_analytical_window_constant_is_shared(self):
        from src.config import ANALYTICAL_INTEL_LOOKBACK_DAYS
        from src.search_service import SearchService

        self.assertEqual(ANALYTICAL_INTEL_LOOKBACK_DAYS, 180)
        self.assertEqual(SearchService.ANALYTICAL_INTEL_LOOKBACK_DAYS, 180)

    def test_prompt_uses_both_windows(self):
        from src.analyzer import GeminiAnalyzer

        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            "code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1500.0,
            "news_window_days": 3,
        }
        prompt = GeminiAnalyzer._format_prompt(
            analyzer,
            context,
            "贵州茅台",
            news_context="2026-11-10 某机构增持 1.2%",
        )

        self.assertIn("近3日", prompt)
        self.assertIn("近180日", prompt)
        # 不再对长周期情报做"一律忽略"的硬性丢弃
        self.assertNotIn("时间未知、无法确定发布日期的新闻一律忽略", prompt)


class HistoryLoaderThresholdTest(unittest.TestCase):
    def test_cache_hit_ratio_is_eighty_percent(self):
        from src.services import history_loader

        self.assertAlmostEqual(history_loader._CACHE_HIT_RATIO, 0.8)


if __name__ == "__main__":
    unittest.main()
