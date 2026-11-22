# -*- coding: utf-8 -*-
"""Tests for prediction-market ingestion, storage, service and agent tool."""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from data_provider.prediction_market_fetcher import (
    PredictionMarketFetcher,
    _normalize_probability,
)
from src.repositories.prediction_market_repo import PredictionMarketRepository
from src.services.prediction_market_service import PredictionMarketService
from src.storage import DatabaseManager, PredictionMarketEvent, utc_naive_now


def _config(**overrides):
    base = dict(
        prediction_market_enabled=True,
        prediction_market_auto_fetch_enabled=False,
        prediction_market_refresh_cooldown_min=60,
        prediction_market_fetch_timeout_sec=10.0,
        prediction_market_retention_days=30,
        prediction_market_max_items_per_fetch=200,
        prediction_market_politics_allowed=False,
        kalshi_api_base_url="https://api.elections.kalshi.com/trade-api/v2",
        kalshi_api_key=None,
        kalshi_series_tickers="KXFED,KXCPI",
        polymarket_api_base_url="https://gamma-api.polymarket.com",
        polymarket_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestNormalization(unittest.TestCase):
    def test_normalize_probability_handles_cents_and_invalid(self):
        self.assertEqual(_normalize_probability(62), 0.62)
        self.assertEqual(_normalize_probability("0.4"), 0.4)
        self.assertIsNone(_normalize_probability(-1))
        self.assertIsNone(_normalize_probability(101))
        self.assertIsNone(_normalize_probability("nan"))
        self.assertIsNone(_normalize_probability(None))

    def test_kalshi_market_normalization(self):
        fetcher = PredictionMarketFetcher(config=_config())
        markets = [
            {
                "ticker": "KXFED-26DEC",
                "event_ticker": "KXFED-26",
                "title": "Fed cuts rates in December?",
                "yes_bid": 61,
                "yes_ask": 63,
                "last_price": 62,
                "volume_24h": 420000,
                "open_interest": 1000,
                "status": "open",
                "close_time": "2026-12-31T00:00:00Z",
            }
        ]
        items = fetcher._normalize_kalshi_markets(markets, max_items=10)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source"], "kalshi")
        self.assertEqual(item["market_id"], "KXFED-26DEC")
        self.assertEqual(item["implied_probability"], 0.62)
        self.assertEqual(item["category"], "macro")
        self.assertIn("kalshi.com", item["market_url"])

    def test_kalshi_dollar_fields_and_other_filter(self):
        fetcher = PredictionMarketFetcher(config=_config())
        markets = [
            {
                "ticker": "KXFED-26DEC",
                "title": "Fed cuts rates in December?",
                "last_price_dollars": "0.62",
                "yes_bid_dollars": "0.61",
                "yes_ask_dollars": "0.63",
                "volume_fp": "1000",
                "open_interest_fp": "120",
            },
            {
                "ticker": "KXSPORT-1",
                "title": "Oklahoma State vs Arizona State",
                "last_price_dollars": "0.55",
            },
        ]
        items = fetcher._normalize_kalshi_markets(markets, max_items=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["implied_probability"], 0.62)
        self.assertEqual(items[0]["volume_24h"], 1000.0)
        self.assertEqual(items[0]["open_interest"], 120.0)

    def test_polymarket_other_category_is_filtered(self):
        fetcher = PredictionMarketFetcher(config=_config())
        markets = [
            {
                "id": "0xsport",
                "question": "Oklahoma State vs. Arizona State",
                "outcomes": '["Oklahoma State", "Arizona State"]',
                "outcomePrices": '["0.9995", "0.0005"]',
                "active": True,
            }
        ]
        self.assertEqual(fetcher._normalize_polymarket_markets(markets, max_items=10), [])

    def test_kalshi_series_allowlist_path(self):
        fetcher = PredictionMarketFetcher(config=_config(kalshi_series_tickers="KXFED,KXCPI"))
        calls = []

        def fake_get_json(url, headers=None):
            calls.append(url)
            if "KXFED" in url:
                return {
                    "markets": [
                        {
                            "ticker": "KXFED-1",
                            "title": "Fed cuts rates in December?",
                            "last_price_dollars": "0.62",
                        }
                    ]
                }
            return {
                "markets": [
                    {
                        "ticker": "KXCPI-1",
                        "title": "Will CPI rise more than 1%?",
                        "last_price_dollars": "0.31",
                    }
                ]
            }

        fetcher._get_json = fake_get_json  # type: ignore[assignment]
        items = fetcher.fetch_kalshi()
        self.assertEqual(len(items), 2)
        self.assertTrue(len(calls) == 2 and all("series_ticker=" in url for url in calls))

    def test_kalshi_politics_is_excluded_by_default(self):
        fetcher = PredictionMarketFetcher(config=_config())
        markets = [
            {
                "ticker": "KXPOL-26",
                "title": "Will Trump win the 2026 election?",
                "yes_bid": 50,
                "yes_ask": 52,
            }
        ]
        self.assertEqual(fetcher._normalize_kalshi_markets(markets, max_items=10), [])
        allowed = PredictionMarketFetcher(config=_config(prediction_market_politics_allowed=True))
        allowed_items = allowed._normalize_kalshi_markets(markets, max_items=10)
        self.assertEqual(len(allowed_items), 1)
        self.assertEqual(allowed_items[0]["category"], "politics")

    def test_polymarket_market_normalization(self):
        fetcher = PredictionMarketFetcher(config=_config())
        markets = [
            {
                "id": "0xabc",
                "question": "Will Nvidia beat quarterly earnings?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.92", "0.08"]',
                "volume24hr": 34000,
                "liquidity": 5000,
                "active": True,
                "category": "Equity",
                "slug": "nvidia-earnings",
                "endDate": "2026-11-20",
            }
        ]
        items = fetcher._normalize_polymarket_markets(markets, max_items=10)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source"], "polymarket")
        self.assertEqual(item["implied_probability"], 0.92)
        self.assertEqual(item["category"], "equity")
        self.assertIn("polymarket.com", item["market_url"])


class TestRepositoryAndService(unittest.TestCase):
    def setUp(self):
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        self.repo = PredictionMarketRepository(db_manager=self.db)

    def tearDown(self):
        DatabaseManager.reset_instance()

    @staticmethod
    def _event(**overrides):
        base = {
            "source": "kalshi",
            "market_id": "KXFED-26DEC",
            "event_id": "KXFED-26",
            "question": "Fed cuts rates in December?",
            "outcome": "yes",
            "implied_probability": 0.62,
            "volume_24h": 420000.0,
            "status": "open",
            "category": "macro",
            "symbol": None,
            "market": "global",
            "close_time": "2026-12-31T00:00:00Z",
            "market_url": "https://kalshi.com/markets/KXFED-26DEC",
        }
        base.update(overrides)
        return base

    def test_upsert_dedups_and_refreshes_probability(self):
        self.assertEqual(self.repo.upsert_events([self._event()]), 1)
        refreshed = self.repo.upsert_events([self._event(implied_probability=0.71)])
        self.assertEqual(refreshed, 0)
        rows = self.repo.list_events(status="open", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].implied_probability, 0.71)

    def test_retention_deletes_old_rows(self):
        self.repo.upsert_events([self._event()])
        with self.db.get_session() as session:
            row = session.query(PredictionMarketEvent).first()
            row.fetched_at = utc_naive_now() - timedelta(days=40)
            session.commit()
        self.assertEqual(self.repo.apply_retention(30), 1)
        self.assertEqual(self.repo.list_events(limit=10), [])

    def test_service_query_and_context(self):
        self.repo.upsert_events(
            [
                self._event(),
                self._event(
                    source="polymarket",
                    market_id="0xnvda",
                    question="Will Nvidia beat quarterly earnings?",
                    implied_probability=0.92,
                    category="equity",
                    symbol="NVDA",
                    market="us",
                ),
            ]
        )
        service = PredictionMarketService(repository=self.repo, config=_config())
        self.assertTrue(service.is_enabled())
        items = service.query_events(symbol="NVDA", status="open", limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["implied_probability"], 0.92)

        text = service.build_evidence_context(symbol="NVDA", stock_name="NVIDIA", market="us", limit=5)
        self.assertIsNotNone(text)
        self.assertIn("预测市场现状", text)
        self.assertIn("92%", text)
        self.assertIn("非投资建议", text)

    def test_fetch_persists_normalized_items(self):
        self.repo.upsert_events([self._event()])
        service = PredictionMarketService(repository=self.repo, config=_config())
        payload = {
            "items": [self._event(market_id="KXNEW-26", implied_probability=0.55)],
            "sources": {"kalshi": {"ok": True, "count": 1}},
            "errors": [],
        }
        with patch.object(PredictionMarketFetcher, "fetch_all", return_value=payload):
            result = service.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(result["saved"], 1)
        self.assertEqual(len(self.repo.list_events(limit=10)), 2)

    def test_market_context_uses_macro_events(self):
        self.repo.upsert_events([self._event()])
        service = PredictionMarketService(repository=self.repo, config=_config())
        text = service.build_market_context(market="global", limit=5)
        self.assertIsNotNone(text)
        self.assertIn("宏观事件", text)
        self.assertIn("62%", text)

    def test_disabled_service_is_inert(self):
        self.repo.upsert_events([self._event()])
        service = PredictionMarketService(
            repository=self.repo, config=_config(prediction_market_enabled=False)
        )
        self.assertFalse(service.is_enabled())
        self.assertEqual(service.query_events(status="open", limit=10), [])
        self.assertIsNone(service.build_evidence_context(symbol="NVDA", market="us"))
        result = service.fetch()
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "disabled")


class TestAgentTool(unittest.TestCase):
    def test_tool_definition_is_read_only(self):
        from src.agent.tools.prediction_market_tools import search_prediction_markets_tool

        self.assertEqual(search_prediction_markets_tool.name, "search_prediction_markets")
        self.assertEqual(search_prediction_markets_tool.category, "market")
        self.assertTrue(search_prediction_markets_tool.policy.read_only)
        param_names = {param.name for param in search_prediction_markets_tool.parameters}
        self.assertTrue({"query", "symbol", "market", "category", "status", "limit"} <= param_names)
        self.assertFalse(search_prediction_markets_tool.policy.scope_dimensions)

    def test_disabled_handler_returns_disabled(self):
        from src.agent.tools.prediction_market_tools import _handle_search_prediction_markets

        with patch(
            "src.services.prediction_market_service.PredictionMarketService.is_enabled",
            return_value=False,
        ):
            result = _handle_search_prediction_markets()
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
