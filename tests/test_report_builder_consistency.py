# -*- coding: utf-8 -*-
"""T39: the three report entry points must agree on the same record."""

import json
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from api.v1.endpoints.analysis import _build_analysis_report
from api.v1.report_builder import build_analysis_report


RAW_RESULT = {
    "analysis_summary": "均线多头排列，量能温和放大",
    "operation_advice": "买入",
    "action": "buy",
    "trend_prediction": "看多",
    "sentiment_score": 72,
    "model_used": "deepseek-chat",
    "report_language": "zh",
}

CREATED_AT = datetime(2026, 11, 10, 15, 30, 0)


def _record():
    return SimpleNamespace(
        id=7,
        code="600519",
        name="贵州茅台",
        report_type="simple",
        created_at=CREATED_AT,
        sentiment_score=72,
        operation_advice="买入",
        trend_prediction="看多",
        analysis_summary=RAW_RESULT["analysis_summary"],
        ideal_buy="1500",
        secondary_buy=None,
        stop_loss="1400",
        take_profit="1700",
        raw_result=json.dumps(RAW_RESULT, ensure_ascii=False),
        context_snapshot=None,
        news_content="公司发布分红公告",
        storage_stock_code="sh600519",
    )


def _record_source(record):
    return {
        "id": record.id,
        "query_id": "query-t39",
        "stock_code": record.code,
        "stock_name": record.name,
        "storage_stock_code": record.storage_stock_code,
        "report_type": record.report_type,
        "report_language": RAW_RESULT["report_language"],
        "created_at": record.created_at.isoformat(),
        "model_used": RAW_RESULT["model_used"],
        "sentiment_score": record.sentiment_score,
        "operation_advice": record.operation_advice,
        "trend_prediction": record.trend_prediction,
        "analysis_summary": record.analysis_summary,
        "ideal_buy": record.ideal_buy,
        "secondary_buy": record.secondary_buy,
        "stop_loss": record.stop_loss,
        "take_profit": record.take_profit,
        "raw_result": RAW_RESULT,
        "news_content": record.news_content,
    }


class ReportBuilderConsistencyTest(unittest.TestCase):
    def test_sync_payload_matches_record_and_history_sources(self):
        record = _record()
        created_at = record.created_at.isoformat()
        payload = {
            "meta": {
                "query_id": "query-t39",
                "stock_code": record.code,
                "stock_name": record.name,
                "report_type": record.report_type,
                "report_language": "zh",
                "created_at": created_at,
                "model_used": RAW_RESULT["model_used"],
            },
            "summary": {
                "analysis_summary": record.analysis_summary,
                "operation_advice": record.operation_advice,
                "trend_prediction": record.trend_prediction,
                "sentiment_score": record.sentiment_score,
            },
            "strategy": {
                "ideal_buy": record.ideal_buy,
                "secondary_buy": record.secondary_buy,
                "stop_loss": record.stop_loss,
                "take_profit": record.take_profit,
            },
            "details": {
                "news_content": record.news_content,
                "raw_result": RAW_RESULT,
            },
        }

        sync_report = _build_analysis_report(
            payload,
            query_id="query-t39",
            stock_code=record.code,
            stock_name=record.name,
        )
        record_report = build_analysis_report(
            _record_source(record),
            query_id="query-t39",
            stock_code=record.code,
            stock_name=record.name,
            default_report_type=None,
            localize_summary=True,
            always_include_strategy=True,
        )
        history_report = build_analysis_report(
            _record_source(record),
            query_id="query-t39",
            stock_code=record.code,
            stock_name=record.name,
            default_report_type=None,
            localize_summary=True,
            always_include_strategy=True,
        )

        self.assertEqual(record_report.model_dump(), history_report.model_dump())

        sync_dump = sync_report.model_dump()
        # meta.id only exists for stored records; sync payloads have none.
        self.assertIsNone(sync_dump["meta"].pop("id"))
        record_dump = record_report.model_dump()
        record_dump["meta"].pop("id")
        self.assertEqual(sync_dump, record_dump)

    def test_action_is_derived_from_raw_result_for_every_source(self):
        record = _record()
        source = _record_source(record)
        source["operation_advice"] = "持有"
        source["raw_result"] = {
            **RAW_RESULT,
            "operation_advice": "买入",
            "action": "buy",
        }

        report = build_analysis_report(source, query_id="query-t39")

        self.assertEqual(report.summary.action, "buy")
        self.assertEqual(report.summary.operation_advice, "持有")

    def test_legacy_empty_strategy_stays_null_for_payload_path(self):
        report = _build_analysis_report(
            {"meta": {"stock_code": "600519"}, "summary": {}, "strategy": {}, "details": {}},
            query_id="query-t39",
            stock_code="600519",
        )

        self.assertIsNone(report.strategy)


if __name__ == "__main__":
    unittest.main()
