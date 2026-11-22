"""Earnings calendar (next disclosure date) contract tests."""

from datetime import datetime, timedelta
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

from data_provider.fundamental_adapter import (
    AkshareFundamentalAdapter,
    _build_earnings_calendar,
    _cninfo_period_label,
    _earnings_calendar_periods,
)


def _future(days=30):
    return (datetime.now() + timedelta(days=days)).date()


def _past(days=60):
    return (datetime.now() - timedelta(days=days)).date()


def _reservation_row(code="600519", first=None, actual=None):
    return {
        "股票代码": code,
        "股票简称": "测试",
        "首次预约时间": first,
        "一次变更日期": None,
        "二次变更日期": None,
        "三次变更日期": None,
        "实际披露时间": actual,
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        ("20260331", "2026一季"),
        ("20260630", "2026半年报"),
        ("20260930", "2026三季"),
        ("20261231", "2026年报"),
        ("2026-12-31", None),
        ("bad", None),
        ("", None),
    ],
)
def test_cninfo_period_label(value, expected):
    assert _cninfo_period_label(value) == expected


def test_earnings_calendar_periods_are_quarter_ends():
    assert _earnings_calendar_periods(datetime(2026, 11, 22)) == ["20260930", "20261231"]
    assert _earnings_calendar_periods(datetime(2026, 4, 5)) == ["20260331", "20260630"]


def test_build_calendar_picks_next_future_and_recent_disclosed():
    future = _future(20)
    past = _past(60)
    rows = [
        ("20260930", pd.Series(_reservation_row(first=past, actual=past))),
        ("20261231", pd.Series(_reservation_row(first=future))),
    ]

    calendar = _build_earnings_calendar(rows, now=datetime.now())

    assert calendar["next_earnings_date"] == future.isoformat()
    assert calendar["days_to_next"] == 20
    assert calendar["coverage"] == "cn_reservation_disclosure"
    assert calendar["events"][0]["disclosure_status"] == "scheduled"
    assert any(event["disclosure_status"] == "disclosed" for event in calendar["events"])


def test_build_calendar_is_empty_without_usable_dates():
    assert _build_earnings_calendar([]) == {}
    assert _build_earnings_calendar([("20260930", pd.Series(_reservation_row()))]) == {}


def test_build_calendar_uses_latest_change_over_first_appointment():
    future = _future(12)
    later = _future(40)
    row = _reservation_row(first=future)
    row["三次变更日期"] = later

    calendar = _build_earnings_calendar([("20261231", pd.Series(row))], now=datetime.now())

    assert calendar["next_earnings_date"] == later.isoformat()
    assert calendar["days_to_next"] == 40


def test_bundle_includes_calendar_from_reservation_endpoint(monkeypatch):
    future = _future(15)
    calls = []

    def yysj(symbol, date):
        calls.append((symbol, date))
        return pd.DataFrame([_reservation_row(first=future)])

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_yysj_em=yysj))

    result = AkshareFundamentalAdapter().get_fundamental_bundle("600519")

    calendar = result["earnings"]["calendar"]
    assert calendar["next_earnings_date"] == future.isoformat()
    assert calendar["days_to_next"] == 15
    assert any(source.startswith("earnings_calendar:") for source in result["source_chain"])
    assert result["status"] == "partial"
    assert calls and calls[0][0] == "沪深A股"


def test_bundle_calendar_failure_is_fail_open(monkeypatch):
    def yysj(symbol, date):
        raise ConnectionError("unavailable")

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_yysj_em=yysj))

    result = AkshareFundamentalAdapter().get_fundamental_bundle("600519")

    assert "calendar" not in result["earnings"]
    assert result["status"] == "not_supported"
    assert any("stock_yysj_em" in error for error in result["errors"])


def test_earnings_block_has_values_accepts_calendar_only():
    from data_provider.base import DataFetcherManager

    payload = {"calendar": {"next_earnings_date": "2026-12-31", "events": []}}
    assert DataFetcherManager._earnings_block_has_values(payload) is True
    assert DataFetcherManager._earnings_block_has_values({"calendar": {}}) is False


def test_yfinance_calendar_from_ticker_calendar():
    from data_provider.yfinance_fundamental_adapter import _extract_yf_earnings_calendar

    future = _future(10)
    ticker = SimpleNamespace(calendar={"Earnings Date": [future]})

    calendar = _extract_yf_earnings_calendar(ticker, now=datetime.now())

    assert calendar["next_earnings_date"] == future.isoformat()
    assert calendar["days_to_next"] == 10
    assert calendar["coverage"] == "yfinance_calendar"


def test_yfinance_calendar_empty_when_only_past_dates():
    from data_provider.yfinance_fundamental_adapter import _extract_yf_earnings_calendar

    ticker = SimpleNamespace(calendar={"Earnings Date": [_past(10)]})

    assert _extract_yf_earnings_calendar(ticker, now=datetime.now()) == {}


def test_analyzer_renders_next_earnings_row():
    from src.analyzer import _build_earnings_and_chip_event_rows

    context = {
        "earnings": {
            "data": {
                "calendar": {
                    "next_earnings_date": "2026-12-31",
                    "days_to_next": 40,
                }
            }
        }
    }

    rows = _build_earnings_and_chip_event_rows(context)

    labels = [row[0] for row in rows]
    assert "Next Earnings Date" in labels
    value = next(row[2] for row in rows if row[0] == "Next Earnings Date")
    assert value == "2026-12-31 (est. +40d)"
