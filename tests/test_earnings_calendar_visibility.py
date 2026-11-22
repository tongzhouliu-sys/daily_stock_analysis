"""Earnings calendar visibility (API / notification / chat answer_meta) tests."""

from types import SimpleNamespace
import unittest

from src.utils.data_processing import extract_fundamental_detail_fields
from src.notification import NotificationService
from src.report_language import get_report_labels
from src.agent.runner import _last_earnings_calendar_snapshot
from src.schemas.agent_intake import AnswerMeta


def _calendar(next_date="2027-01-28", days=67, events=None, coverage="yfinance_calendar"):
    events = events if events is not None else [{
        "report_period": None,
        "expected_disclosure_date": next_date,
        "actual_disclosure_date": None,
        "disclosure_status": "scheduled",
    }]
    return {
        "next_earnings_date": next_date,
        "days_to_next": days,
        "events": events,
        "coverage": coverage,
        "as_of": "2026-11-22",
    }


class TestExtractFundamentalDetailFields(unittest.TestCase):
    def test_extracts_earnings_calendar(self):
        snapshot = {"enhanced_context": {"fundamental_context": {
            "earnings": {"data": {"calendar": _calendar()}},
        }}}
        result = extract_fundamental_detail_fields(snapshot)
        self.assertEqual(result["earnings_calendar"]["next_earnings_date"], "2027-01-28")

    def test_no_calendar_returns_none(self):
        snapshot = {"enhanced_context": {"fundamental_context": {
            "earnings": {"data": {"financial_report": {"revenue": 1.0}}},
        }}}
        result = extract_fundamental_detail_fields(snapshot)
        self.assertIsNone(result["earnings_calendar"])

    def test_missing_earnings_returns_none(self):
        self.assertIsNone(extract_fundamental_detail_fields(None)["earnings_calendar"])


class TestNotificationEarningsCalendar(unittest.TestCase):
    def test_get_fundamental_blocks_extracts_calendar(self):
        svc = NotificationService.__new__(NotificationService)
        res = SimpleNamespace(fundamental_context={"earnings": {"data": {"calendar": _calendar()}}})
        blocks = svc._get_fundamental_blocks(res)
        self.assertEqual(blocks["earnings_calendar"]["next_earnings_date"], "2027-01-28")

    def test_append_next_date(self):
        svc = NotificationService.__new__(NotificationService)
        lines = []
        svc._append_earnings_calendar(lines, {"earnings_calendar": _calendar()}, get_report_labels("zh"))
        text = "\n".join(lines)
        self.assertIn("财报日历", text)
        self.assertIn("2027-01-28", text)
        self.assertIn("预计", text)

    def test_append_disclosed_only(self):
        svc = NotificationService.__new__(NotificationService)
        cal = _calendar(next_date=None, days=None, events=[{
            "report_period": "2026-09-30",
            "expected_disclosure_date": "2026-10-31",
            "actual_disclosure_date": "2026-10-31",
            "disclosure_status": "disclosed",
        }], coverage="cn_reservation_disclosure")
        lines = []
        svc._append_earnings_calendar(lines, {"earnings_calendar": cal}, get_report_labels("zh"))
        text = "\n".join(lines)
        self.assertIn("最近披露", text)
        self.assertIn("2026-10-31", text)
        self.assertIn("暂无已发布的预约披露日期", text)

    def test_append_nothing_when_empty(self):
        svc = NotificationService.__new__(NotificationService)
        lines = []
        svc._append_earnings_calendar(lines, {"earnings_calendar": {}}, get_report_labels("zh"))
        self.assertEqual(lines, [])

    def test_append_english_no_keyerror(self):
        svc = NotificationService.__new__(NotificationService)
        lines = []
        svc._append_earnings_calendar(lines, {"earnings_calendar": _calendar()}, get_report_labels("en"))
        self.assertIn("Earnings Calendar", "\n".join(lines))

    def test_append_korean_no_keyerror(self):
        svc = NotificationService.__new__(NotificationService)
        lines = []
        svc._append_earnings_calendar(lines, {"earnings_calendar": _calendar()}, get_report_labels("ko"))
        self.assertIn("실적 캘린더", "\n".join(lines))


class TestRunnerSnapshot(unittest.TestCase):
    def test_extracts_snapshot(self):
        log = [
            {"tool": "get_realtime_quote"},
            {"tool": "get_stock_info", "earnings_calendar": _calendar()},
        ]
        self.assertEqual(_last_earnings_calendar_snapshot(log)["next_earnings_date"], "2027-01-28")

    def test_no_snapshot(self):
        self.assertIsNone(_last_earnings_calendar_snapshot([]))
        self.assertIsNone(_last_earnings_calendar_snapshot([{"tool": "get_stock_info"}]))
        self.assertIsNone(_last_earnings_calendar_snapshot(None))


class TestAnswerMeta(unittest.TestCase):
    def test_answer_meta_accepts_calendar(self):
        meta = AnswerMeta(earnings_calendar=_calendar())
        dumped = meta.model_dump(exclude_none=True)
        self.assertEqual(dumped["earnings_calendar"]["next_earnings_date"], "2027-01-28")


class TestPublicAnswerMeta(unittest.TestCase):
    def test_whitelist_includes_earnings_calendar(self):
        from api.v1.endpoints.agent import _public_answer_meta

        result = SimpleNamespace(answer_meta=AnswerMeta(earnings_calendar=_calendar()))
        payload = _public_answer_meta(result)
        self.assertIn("earnings_calendar", payload)
        self.assertEqual(payload["earnings_calendar"]["next_earnings_date"], "2027-01-28")

    def test_no_meta_returns_none(self):
        from api.v1.endpoints.agent import _public_answer_meta

        self.assertIsNone(_public_answer_meta(SimpleNamespace(answer_meta=None)))


if __name__ == "__main__":
    unittest.main()
