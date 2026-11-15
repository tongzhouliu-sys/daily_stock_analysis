# -*- coding: utf-8 -*-
"""Unit tests for src.services.history_loader (Issue #1066)."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


class HistoryLoaderTestCase(unittest.TestCase):
    """Tests for load_history_df and frozen target date ContextVar."""

    # ------------------------------------------------------------------
    # ContextVar lifecycle
    # ------------------------------------------------------------------
    def test_frozen_target_date_lifecycle(self):
        from src.services.history_loader import (
            get_frozen_target_date,
            reset_frozen_target_date,
            set_frozen_target_date,
        )

        self.assertIsNone(get_frozen_target_date())
        d = date(2026, 4, 18)
        token = set_frozen_target_date(d)
        self.assertEqual(get_frozen_target_date(), d)
        reset_frozen_target_date(token)
        self.assertIsNone(get_frozen_target_date())

    # ------------------------------------------------------------------
    # DB hit path
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_returns_db_data_when_sufficient(self, mock_get_db):
        from src.services.history_loader import load_history_df

        fake_bar = MagicMock()
        fake_bar.to_dict.return_value = {
            "date": "2026-04-18",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        mock_db = MagicMock()
        # T29: 命中门槛是请求根数的八成（days=60 → 48 根）
        mock_db.get_data_range.return_value = [fake_bar] * 50
        mock_get_db.return_value = mock_db

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNotNone(df)
        self.assertEqual(source, "db_cache")
        self.assertEqual(len(df), 50)
        mock_db.get_data_range.assert_called_once()

    @patch("src.services.history_loader._get_fetcher_manager")
    @patch("src.storage.get_db")
    def test_db_hit_requires_eighty_percent_of_requested(self, mock_get_db, mock_get_fm):
        """T29: 库内不足请求根数的八成时视为未命中，不再把 30 根当作 120 根。"""
        from src.services.history_loader import _network_cache, load_history_df

        _network_cache.clear()
        fake_bar = MagicMock()
        fake_bar.to_dict.return_value = {"date": "2026-04-18", "close": 10}
        mock_db = MagicMock()
        mock_db.get_data_range.return_value = [fake_bar] * 30
        mock_get_db.return_value = mock_db

        mock_fm = MagicMock()
        mock_fm.get_daily_data.return_value = (None, "none")
        mock_get_fm.return_value = mock_fm

        _df, source = load_history_df("600519", days=120, target_date=date(2026, 4, 18))

        self.assertNotEqual(source, "db_cache")
        mock_fm.get_daily_data.assert_called_once()

    # ------------------------------------------------------------------
    # DB miss → DFM fallback
    # ------------------------------------------------------------------
    @patch("src.services.history_loader._get_fetcher_manager")
    @patch("src.storage.get_db")
    def test_falls_back_to_dfm_when_db_empty(self, mock_get_db, mock_get_fm):
        from src.services.history_loader import _network_cache, load_history_df

        _network_cache.clear()

        mock_db = MagicMock()
        mock_db.get_data_range.return_value = []
        mock_get_db.return_value = mock_db

        fake_df = pd.DataFrame({"close": [1, 2, 3]})
        mock_fm = MagicMock()
        mock_fm.get_daily_data.return_value = (fake_df, "eastmoney")
        mock_get_fm.return_value = mock_fm

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNotNone(df)
        self.assertEqual(source, "eastmoney")
        # 网络取数按窗口桶取整（60 → 120），并发请求可共享同一次拉取
        mock_fm.get_daily_data.assert_called_once_with("600519", days=120)

    # ------------------------------------------------------------------
    # ContextVar integration
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_uses_frozen_target_date_from_contextvar(self, mock_get_db):
        from src.services.history_loader import (
            load_history_df,
            reset_frozen_target_date,
            set_frozen_target_date,
        )

        frozen_date = date(2026, 4, 15)
        token = set_frozen_target_date(frozen_date)
        try:
            mock_db = MagicMock()
            fake_bar = MagicMock()
            fake_bar.to_dict.return_value = {"date": "2026-04-15", "close": 10}
            mock_db.get_data_range.return_value = [fake_bar] * 30
            mock_get_db.return_value = mock_db

            df, source = load_history_df("600519", days=30)

            self.assertEqual(source, "db_cache")
            call_args = mock_db.get_data_range.call_args
            _code, _start, end = call_args[0]
            self.assertEqual(end, frozen_date)
        finally:
            reset_frozen_target_date(token)

    # ------------------------------------------------------------------
    # normalize_stock_code fallback for prefixed codes
    # ------------------------------------------------------------------
    @patch("src.storage.get_db")
    def test_uses_normalize_fallback_for_prefixed_code(self, mock_get_db):
        from src.services.history_loader import load_history_df

        fake_bar = MagicMock()
        fake_bar.to_dict.return_value = {"date": "2026-04-18", "close": 10}

        mock_db = MagicMock()

        def side_effect(code, start, end):
            if code == "SH600519":
                return []
            return [fake_bar] * 30

        mock_db.get_data_range.side_effect = side_effect
        mock_get_db.return_value = mock_db

        df, source = load_history_df("SH600519", days=30, target_date=date(2026, 4, 18))

        self.assertEqual(source, "db_cache")
        self.assertEqual(mock_db.get_data_range.call_count, 2)

    # ------------------------------------------------------------------
    # Both paths fail gracefully
    # ------------------------------------------------------------------
    @patch("src.services.history_loader._get_fetcher_manager")
    @patch("src.storage.get_db")
    def test_graceful_when_both_fail(self, mock_get_db, mock_get_fm):
        from src.services.history_loader import _network_cache, load_history_df

        _network_cache.clear()

        mock_db = MagicMock()
        mock_db.get_data_range.side_effect = Exception("DB down")
        mock_get_db.return_value = mock_db

        mock_fm = MagicMock()
        mock_fm.get_daily_data.side_effect = Exception("API down")
        mock_get_fm.return_value = mock_fm

        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))

        self.assertIsNone(df)
        self.assertEqual(source, "none")


if __name__ == "__main__":
    unittest.main()
