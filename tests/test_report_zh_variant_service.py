# -*- coding: utf-8 -*-
"""Storage + service tests for cached Chinese report variants (T78)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from src.analyzer import AnalysisResult
from src.config import Config
from src.services.history_service import HistoryService, ReportChineseVariantMissing
from src.services.report_translation_service import ReportTranslationError, ReportTranslationService
from src.storage import AnalysisHistory, DatabaseManager


class ReportChineseVariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_report_zh_variant.db")
        self._original_env = {
            key: os.environ.get(key)
            for key in ("ENV_FILE", "DATABASE_PATH")
        }
        self._env_path = os.path.join(self._temp_dir.name, ".env")
        with open(self._env_path, "w", encoding="utf-8") as env_file:
            env_file.write("STOCK_LIST=600519\n")
        os.environ["ENV_FILE"] = self._env_path
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.service = HistoryService(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temp_dir.cleanup()

    def _save_english_record(self, query_id: str = "query_zh_variant") -> int:
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=78,
            trend_prediction="Bullish",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
            risk_warning="A close below support invalidates the setup.",
            dashboard={
                "core_conclusion": {
                    "one_sentence": "Favor buying on pullbacks.",
                    "position_advice": {"no_position": "Open a starter position."},
                },
                "intelligence": {"risk_alerts": ["Valuation is stretched."]},
            },
        )
        return self.db.save_analysis_history(
            result=result,
            query_id=query_id,
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )

    def test_ensure_and_read_chinese_variant(self):
        record_id = self._save_english_record()
        calls = []

        def translator(batch, timeout):
            calls.append(dict(batch))
            mapping = {
                "Favor buying on pullbacks.": "回调时倾向买入。",
                "Valuation is stretched.": "估值偏高。",
                "Open a starter position.": "先建底仓。",
                "Momentum remains constructive.": "动能仍然积极。",
            }
            return {
                path: mapping.get(text, "中文译文")
                for path, text in batch.items()
            }, "fake", "fake-model"

        translation = ReportTranslationService(translator=translator)
        result = self.service.ensure_chinese_variant(
            str(record_id),
            translation_service=translation,
        )
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["cached"])
        self.assertTrue(calls)

        markdown = self.service.get_markdown_report_zh(str(record_id))
        self.assertIsNotNone(markdown)
        self.assertIn("股票分析报告", markdown)
        self.assertIn("回调时倾向买入。", markdown)
        self.assertIn("估值偏高。", markdown)
        self.assertNotIn("> **一句话决策**: Favor buying on pullbacks.", markdown)
        self.assertNotIn("Valuation is stretched.", markdown)

        # 第二次命中缓存，不再调用翻译器。
        call_count = len(calls)
        cached = self.service.ensure_chinese_variant(
            str(record_id),
            translation_service=ReportTranslationService(translator=lambda *_args: self.fail("should not call")),
        )
        self.assertTrue(cached["cached"])
        self.assertEqual(len(calls), call_count)

    def test_missing_variant_raises_for_html(self):
        record_id = self._save_english_record("query_zh_variant_missing")
        with self.assertRaises(ReportChineseVariantMissing):
            self.service.get_markdown_report_zh(str(record_id))

    def _insert_legacy_record(self, query_id: str, raw_result: dict, *, report_type: str = "full", content: str = "news") -> int:
        import json

        with self.db.session_scope() as session:
            row = AnalysisHistory(
                query_id=query_id,
                code=str(raw_result.get("code") or "MSFT"),
                name=str(raw_result.get("name") or "Microsoft"),
                report_type=report_type,
                raw_result=json.dumps(raw_result, ensure_ascii=False),
                news_content=content,
            )
            session.add(row)
            session.flush()
            return row.id

    def test_legacy_english_record_without_report_language_is_detected(self):
        record_id = self._insert_legacy_record(
            "query_legacy_en_no_lang",
            {
                "code": "MSFT",
                "name": "Microsoft",
                "analysis_summary": "Cloud growth remains strong.",
                "dashboard": {"core_conclusion": {"one_sentence": "Buy on dips."}},
            },
        )
        with self.assertRaises(ReportChineseVariantMissing):
            self.service.get_markdown_report_zh(str(record_id))

        translation = ReportTranslationService(
            translator=lambda batch, _timeout: ({"dashboard.core_conclusion.one_sentence": "回调买入。"}, "fake", "fake")
        )
        ensured = self.service.ensure_chinese_variant(str(record_id), translation_service=translation)
        self.assertEqual(ensured["status"], "ready")
        markdown = self.service.get_markdown_report_zh(str(record_id))
        self.assertIn("回调买入。", markdown)
        self.assertNotIn("Buy on dips.", markdown)

    def test_legacy_chinese_record_without_report_language_needs_no_variant(self):
        record_id = self._insert_legacy_record(
            "query_legacy_zh_no_lang",
            {
                "code": "600519",
                "name": "贵州茅台",
                "analysis_summary": "基本面稳健。",
                "dashboard": {"core_conclusion": {"one_sentence": "回调买入。"}},
            },
        )
        ensured = self.service.ensure_chinese_variant(str(record_id))
        self.assertEqual(ensured["status"], "not_needed")
        markdown = self.service.get_markdown_report_zh(str(record_id))
        self.assertIn("回调买入。", markdown)

    def test_legacy_market_review_without_report_language_is_detected(self):
        english_markdown = "# Market Review\n\n## Index Performance\n\nStocks rallied."
        record_id = self._insert_legacy_record(
            "query_legacy_market_en_no_lang",
            {"raw_response": english_markdown},
            report_type="market_review",
            content=english_markdown,
        )
        with self.assertRaises(ReportChineseVariantMissing):
            self.service.get_markdown_report_zh(str(record_id))

        translation = ReportTranslationService(
            markdown_translator=lambda _text, _timeout: ("# 大盘复盘\n\n## 指数表现\n\n股票上涨。", "fake", "fake")
        )
        ensured = self.service.ensure_chinese_variant(str(record_id), translation_service=translation)
        self.assertEqual(ensured["status"], "ready")
        self.assertIn("大盘复盘", self.service.get_markdown_report_zh(str(record_id)))

    def test_translation_failure_is_persisted_and_reported(self):
        record_id = self._save_english_record("query_zh_variant_failed")

        def boom(_batch, _timeout):
            raise ReportTranslationError("boom", error_code="translation_failed")

        with self.assertRaises(ReportTranslationError):
            self.service.ensure_chinese_variant(
                str(record_id),
                translation_service=ReportTranslationService(translator=boom),
            )
        variant = self.db.get_report_text_variant(record_id)
        self.assertIsNotNone(variant)
        self.assertEqual(variant.status, "failed")
        self.assertEqual(variant.error_code, "translation_failed")
        with self.assertRaises(ReportChineseVariantMissing):
            self.service.get_markdown_report_zh(str(record_id))

    def test_chinese_record_does_not_need_variant(self):
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=80,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="基本面稳健。",
            report_language="zh",
        )
        record_id = self.db.save_analysis_history(
            result=result,
            query_id="query_zh_native",
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )
        ensured = self.service.ensure_chinese_variant(str(record_id))
        self.assertEqual(ensured["status"], "not_needed")
        markdown = self.service.get_markdown_report_zh(str(record_id))
        self.assertIn("贵州茅台", markdown)

    def test_deleting_history_removes_variants(self):
        record_id = self._save_english_record("query_zh_variant_delete")
        translation = ReportTranslationService(
            translator=lambda batch, _timeout: ({"analysis_summary": "中文摘要。"}, "fake", "fake")
        )
        self.service.ensure_chinese_variant(str(record_id), translation_service=translation)
        self.assertIsNotNone(self.db.get_report_text_variant(record_id))
        deleted = self.db.delete_analysis_history_records([record_id])
        self.assertEqual(deleted, 1)
        self.assertIsNone(self.db.get_report_text_variant(record_id))

    def test_market_review_uses_translated_markdown(self):
        english_markdown = "# Market Review\n\n## Index Performance\n\nStocks rallied."
        with self.db.session_scope() as session:
            row = AnalysisHistory(
                query_id="query_market_review_zh",
                code="MARKET",
                name="A股大盘",
                report_type="market_review",
                raw_result=json.dumps({
                    "report_language": "en",
                    "raw_response": english_markdown,
                }, ensure_ascii=False),
                news_content=english_markdown,
                created_at=None,
            )
            session.add(row)
            session.flush()
            record_id = row.id

        def markdown_translator(text, timeout):
            return "# 大盘复盘\n\n## 指数表现\n\n股票上涨。", "fake", "fake-model"

        translation = ReportTranslationService(markdown_translator=markdown_translator)
        ensured = self.service.ensure_chinese_variant(
            str(record_id),
            translation_service=translation,
        )
        self.assertEqual(ensured["status"], "ready")
        markdown = self.service.get_markdown_report_zh(str(record_id))
        self.assertIn("大盘复盘", markdown)
        self.assertNotIn("Market Review", markdown)
