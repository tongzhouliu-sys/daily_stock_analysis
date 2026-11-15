# -*- coding: utf-8 -*-
"""R23: the rebound Markdown report must match the written artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from src.services.signal_scanner.research.us_equity_rebound.reporting import generate_reports


def _results() -> dict:
    trades = [
        {
            "security_id": "AAA",
            "entry_date": "2021-01-04",
            "exit_date": "2021-01-08",
            "shares": 10,
            "entry_price": 10.0,
            "exit_price": 11.0,
            "pnl": 10.0,
            "return_pct": 10.0,
            "exit_reason": "SMA20_EXIT",
            "holding_days": 4,
        },
        {
            "security_id": "BBB",
            "entry_date": "2021-02-01",
            "exit_date": "2021-02-05",
            "shares": 10,
            "entry_price": 100.0,
            "exit_price": 75.0,
            "pnl": -250.0,
            "return_pct": -25.0,
            "exit_reason": "HARD_STOP",
            "holding_days": 4,
        },
        {
            "security_id": "CCC",
            "entry_date": "2021-03-01",
            "exit_date": "2021-03-05",
            "shares": 10,
            "entry_price": 100.0,
            "exit_price": 95.0,
            "pnl": -50.0,
            "return_pct": -5.0,
            "exit_reason": "TIME_STOP",
            "holding_days": 4,
        },
    ]
    return {
        "trades": trades,
        "daily_records": [
            {"date": "2021-01-04", "equity": 100_000.0, "cash": 100_000.0, "positions_value": 0.0,
             "num_positions": 0, "pending_buys": 0, "pending_sells": 0},
            {"date": "2021-03-05", "equity": 99_710.0, "cash": 99_710.0, "positions_value": 0.0,
             "num_positions": 0, "pending_buys": 0, "pending_sells": 0},
        ],
        "data_info": {
            "universe_name": "测试股票池",
            "universe_size": 100,
            "securities_loaded": 90,
            "max_positions": 10,
            "max_open_positions_observed": 2,
            "etf_count": 7,
            "etf_source": "unit test metadata",
        },
        "performance": {
            "final_equity": 99_710.0,
            "total_return_pct": -0.29,
            "cagr_pct": -0.1,
            "sharpe_ratio": -0.123,
            "max_drawdown": {
                "max_drawdown_pct": -3.0,
                "peak_date": "2021-01-04",
                "valley_date": "2021-03-05",
                "recovery_date": None,
            },
            "total_trades": 3,
            "win_rate": {
                "win_rate_pct": 33.33,
                "winning_trades": 1,
                "losing_trades": 2,
                "avg_win": 10.0,
                "avg_loss": -150.0,
                "profit_factor": 0.033,
            },
            "exit_breakdown": {
                "SMA20_EXIT": {"count": 1, "total_pnl": 10.0, "avg_pnl": 10.0},
                "HARD_STOP": {"count": 1, "total_pnl": -250.0, "avg_pnl": -250.0},
                "TIME_STOP": {"count": 1, "total_pnl": -50.0, "avg_pnl": -50.0},
            },
            "annual_returns": [
                {"year": 2021, "start_equity": 100_000.0, "end_equity": 99_710.0, "return_pct": -0.29},
            ],
        },
    }


def test_report_numbers_match_summary_and_trades(tmp_path: Path) -> None:
    config = {
        "start_date": "2021-01-01",
        "end_date": "2021-12-31",
        "initial_equity": 100_000.0,
        "max_positions": 10,
        "commission_rate": 5e-05,
        "slippage_rate": 0.0005,
        "risk_fraction": 0.01,
        "position_cap": 0.15,
        "rsi_threshold": 10.0,
        "volume_multiple": 2.0,
        "atr_stop_multiple": 2.0,
        "max_holding_sessions": 10,
    }

    generate_reports(_results(), str(tmp_path), config)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    report = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert f"${summary['final_equity']:,.2f}" in report
    assert f"{summary['total_return_pct']:.2f}%" in report
    # Average loss is recomputed from trades.csv: (-250 + -50) / 2.
    assert "| 平均亏损 | $-150.00 |" in report
    assert "单笔最大亏损（按收益率）**: BBB -25.00%" in report
    assert "单笔最大亏损（按金额）**: BBB $-250.00" in report
    assert "共 7 只" in report
    assert "AAA" in report and "BBB" in report and "CCC" in report
