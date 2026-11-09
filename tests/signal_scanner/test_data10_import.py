"""Delivery boundary regressions: reject wrong-day rows and ambiguous duplicates."""

import csv
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/signal_scanner/load_data10.py"
spec = importlib.util.spec_from_file_location("load_data10", SCRIPT)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def record(**changes):
    return {
        "ticker": "AAPL",
        "volume": "100",
        "open": "10",
        "close": "11",
        "high": "12",
        "low": "9",
        "window_start": "1464667200000000000",
        "transactions": "3",
        **changes,
    }


@pytest.mark.parametrize(
    "row,expected",
    [
        (record(window_start="1464753600000000000"), "WINDOW_DATE_MISMATCH"),
        (record(high="10"), "INVALID_OHLC"),
        (record(close="NaN"), "NON_FINITE"),
        (record(volume="-1"), "INVALID_PRICE_OR_VOLUME"),
        (record(window_start="bad"), "INVALID_FIELDS"),
    ],
)
def test_invalid_daily_rows(row, expected):
    assert expected in loader.problems(row, "2016-05-31")


def test_valid_raw_row_preserves_unadjusted_strings():
    assert loader.problems(record(), "2016-05-31") == []
    assert loader.problems(record(volume="0", transactions="0"), "2016-05-31") == []


def test_audit_quarantines_conflicts_and_indexes_every_valid_ticker(tmp_path):
    source, output = tmp_path / "delivery", tmp_path / "result"
    source.mkdir()
    with (source / "2016-05-31.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=loader.FIELDS)
        writer.writeheader()
        writer.writerows(
            [
                record(),
                record(close="10.5"),
                record(ticker="SPY"),
                record(ticker="BAD", window_start="1464753600000000000"),
            ]
        )
    loader.audit(source, output)
    audit = json.loads((output / "audit.json").read_text())
    assert (audit["rows"], audit["accepted_rows"], audit["quarantined_rows"], audit["duplicate_rows"]) == (
        4,
        2,
        2,
        1,
    )
    assert audit["errors"] == {"DUPLICATE_CONFLICT": 1, "WINDOW_DATE_MISMATCH": 1}
    assert not audit["rth_verified"] and audit["available_at_kind"] == "UNKNOWN"
    with sqlite3.connect(output / "market.sqlite3") as cx:
        assert cx.execute("select ticker,close from bars order by ticker").fetchall() == [
            ("AAPL", "11"),
            ("SPY", "11"),
        ]
    with pytest.raises(ValueError, match="already exists"):
        loader.audit(source, output)
