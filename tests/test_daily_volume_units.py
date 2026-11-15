# -*- coding: utf-8 -*-
"""R26: daily A-share/ETF volume must be normalized to shares."""

import os
import sys
import tempfile
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.akshare_fetcher import (
    AkshareFetcher,
    _normalize_cn_daily_volume,
)
from data_provider.efinance_fetcher import EfinanceFetcher
from src.storage import (
    DAILY_VOLUME_SHARES_MIGRATION_VERSION,
    DatabaseManager,
    DatabaseSchemaMigration,
    StockDaily,
)


class DailyVolumeNormalizationTest(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._temp_dir.name, "volume.db")
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url=f"sqlite:///{self.db_path}")

    def tearDown(self):
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_efinance_daily_lots_are_converted_to_shares(self):
        raw = pd.DataFrame({
            "日期": ["2026-11-13"],
            "开盘": [1404.0],
            "收盘": [1435.26],
            "最高": [1437.69],
            "最低": [1394.0],
            "成交量": [41780],
            "成交额": [5954834100.0],
            "股票代码": ["600519"],
            "股票名称": ["贵州茅台"],
        })

        normalized = EfinanceFetcher()._normalize_data(raw, "600519")

        self.assertEqual(int(normalized.iloc[0]["volume"]), 4_178_000)

    def test_akshare_eastmoney_daily_lots_are_converted_to_shares(self):
        raw = pd.DataFrame({
            "日期": ["2026-11-13"],
            "开盘": [11.76],
            "收盘": [12.07],
            "最高": [12.1],
            "最低": [11.7],
            "成交量": [2189430],
            "成交额": [2624481900.0],
            "涨跌幅": [1.0],
        })

        normalized = _normalize_cn_daily_volume(raw, assume_lots=True)

        self.assertEqual(int(normalized.iloc[0]["成交量"]), 218_943_000)

    def test_akshare_sina_daily_shares_are_left_untouched(self):
        raw = pd.DataFrame({
            "日期": ["2026-11-13"],
            "开盘": [1404.0],
            "收盘": [1435.26],
            "最高": [1437.69],
            "最低": [1394.0],
            "成交量": [4_177_984],
            "成交额": [5_954_834_000.0],
        })

        normalized = _normalize_cn_daily_volume(raw, assume_lots=False)

        self.assertEqual(int(normalized.iloc[0]["成交量"]), 4_177_984)

    def test_akshare_tencent_daily_mixed_units_use_amount_ratio(self):
        raw = pd.DataFrame({
            "日期": ["2026-11-13", "2026-11-13"],
            "收盘": [1435.26, 12.07],
            "成交量": [4_178_000, 2_189_430],
            "成交额": [5_954_834_100.0, 2_624_481_900.0],
        })

        normalized = _normalize_cn_daily_volume(raw, assume_lots=False)

        self.assertEqual(int(normalized.iloc[0]["成交量"]), 4_178_000)
        self.assertEqual(int(normalized.iloc[1]["成交量"]), 218_943_000)

    def test_akshare_normalizer_feeds_standard_columns(self):
        raw = pd.DataFrame({
            "日期": ["2026-11-13"],
            "开盘": [110.0],
            "收盘": [110.5],
            "最高": [116.28],
            "最低": [108.7],
            "成交量": [606819],
            "成交额": [6_867_347_200.0],
            "涨跌幅": [1.0],
        })

        normalized = AkshareFetcher()._normalize_data(
            _normalize_cn_daily_volume(raw, assume_lots=True), "688981",
        )

        self.assertEqual(int(normalized.iloc[0]["volume"]), 60_681_900)


class DailyVolumeMigrationTest(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._temp_dir.name, "migration.db")
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url=f"sqlite:///{self.db_path}")

    def tearDown(self):
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed(self):
        with self.db.get_session() as session:
            session.add_all([
                StockDaily(
                    code="600519", date=date(2026, 11, 13),
                    close=1435.26, volume=41780, amount=5_954_834_100.0,
                    data_source="EfinanceFetcher",
                ),
                StockDaily(
                    code="000001", date=date(2026, 11, 13),
                    close=12.07, volume=218_943_000, amount=2_624_481_900.0,
                    data_source="AkshareFetcher",
                ),
                StockDaily(
                    code="000001", date=date(2026, 11, 12),
                    close=12.0, volume=2_189_430, amount=2_600_000_000.0,
                    data_source="AkshareFetcher",
                ),
                StockDaily(
                    code="00700", date=date(2026, 11, 13),
                    close=400.0, volume=1_000_000, amount=400_000_000.0,
                    data_source="AkshareFetcher",
                ),
                StockDaily(
                    code="600519", date=date(2026, 11, 12),
                    close=1435.26, volume=41_780, amount=5_954_834_100.0,
                    data_source="TencentFetcher",
                ),
            ])
            session.commit()
            session.query(DatabaseSchemaMigration).filter(
                DatabaseSchemaMigration.version == DAILY_VOLUME_SHARES_MIGRATION_VERSION,
            ).delete()
            session.commit()

    def _volumes(self):
        with self.db.get_session() as session:
            rows = session.query(StockDaily).order_by(StockDaily.id).all()
            return [(row.code, row.date, row.volume) for row in rows]

    def test_migration_multiplies_only_confirmed_lot_rows_once(self):
        self._seed()

        stats = self.db.migrate_daily_volume_to_shares(backup=False)

        self.assertTrue(stats["applied"])
        self.assertEqual(stats["updated"], 2)
        self.assertEqual(stats["skipped"], 1)
        volumes = self._volumes()
        self.assertEqual(volumes[0][2], 4_178_000)
        self.assertEqual(volumes[1][2], 218_943_000)
        self.assertEqual(volumes[2][2], 218_943_000)
        self.assertEqual(volumes[3][2], 1_000_000)
        self.assertEqual(volumes[4][2], 41_780)

        again = self.db.migrate_daily_volume_to_shares(backup=False)

        self.assertTrue(again["already_applied"])
        self.assertEqual([row[2] for row in self._volumes()], [row[2] for row in volumes])

    def test_dry_run_reports_counts_without_writing(self):
        self._seed()
        before = self._volumes()

        stats = self.db.migrate_daily_volume_to_shares(dry_run=True, backup=False)

        self.assertTrue(stats["dry_run"])
        self.assertFalse(stats["applied"])
        self.assertEqual(stats["updated"], 2)
        self.assertEqual(self._volumes(), before)
        with self.db.get_session() as session:
            self.assertIsNone(
                session.get(DatabaseSchemaMigration, DAILY_VOLUME_SHARES_MIGRATION_VERSION)
            )


if __name__ == "__main__":
    unittest.main()


def test_missing_eastmoney_volume_stays_missing():
    # ``(safe_int(v) or 0) * 100`` turned a missing realtime volume into 0,
    # which the pipeline then wrote as the day's volume.
    from data_provider import akshare_fetcher, efinance_fetcher

    for module in (efinance_fetcher, akshare_fetcher):
        assert module._lots_to_shares(None) is None
        assert module._lots_to_shares("") is None
        assert module._lots_to_shares("12") == 1200
        assert module._lots_to_shares(0) == 0
