# -*- coding: utf-8 -*-
"""R11：A 股资金流取数失败不得被当成"市场不支持"。

这些用例走真实的 adapter → manager → analyzer 路径，只替换 akshare 的资金流
函数本身，避免像旧用例那样直接构造一个 ``status`` 结果。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.analyzer import AnalysisResult, stabilize_decision_with_structure


def _cfg(**overrides):
    values = dict(
        fundamental_fetch_timeout_seconds=2.0,
        fundamental_stage_timeout_seconds=2.0,
        fundamental_retry_max=1,
        fundamental_cache_ttl_seconds=0,
        fundamental_cache_max_entries=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager(monkeypatch):
    from data_provider.base import DataFetcherManager

    monkeypatch.setattr("src.config.get_config", lambda: _cfg())
    return DataFetcherManager(fetchers=[])


def _timeout_on_capital_flow(monkeypatch):
    import akshare

    def boom(*_args, **_kwargs):
        raise TimeoutError("upstream timed out")

    monkeypatch.setattr(akshare, "stock_individual_fund_flow", boom, raising=False)
    monkeypatch.setattr(akshare, "stock_sector_fund_flow_rank", boom, raising=False)


def _buy_result(code: str = "600519", name: str = "贵州茅台") -> AnalysisResult:
    current_price = 33.4
    return AnalysisResult(
        code=code,
        name=name,
        sentiment_score=66,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        report_language="zh",
        current_price=current_price,
        change_pct=1.0,
        dashboard={
            "core_conclusion": {"one_sentence": "原始结论"},
            "data_perspective": {
                "price_position": {
                    "current_price": current_price,
                    "support_level": 30.0,
                    "resistance_level": 34.0,
                }
            },
        },
    )


def _stabilize(result: AnalysisResult, context: dict) -> None:
    stabilize_decision_with_structure(
        result,
        SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]),
        context,
    )


def test_a_share_capital_flow_timeout_is_a_failure_not_capability_missing(monkeypatch):
    manager = _manager(monkeypatch)
    _timeout_on_capital_flow(monkeypatch)

    block = manager.get_capital_flow_context("600519")

    assert block["status"] == "failed"
    assert block["coverage"] == {"status": "failed"}
    assert block["errors"], "取数失败必须带错误信息"
    assert block["data"]["stock_flow"] == {}

    result = _buy_result()
    _stabilize(result, {"capital_flow": block})

    assert result.decision_type == "hold"
    assert result.dashboard["decision_stability"]["capital_flow_status"] == "资金流取数失败"
    assert "资金面确认" in result.risk_warning or "资金面确认" in (
        result.dashboard["core_conclusion"].get("one_sentence") or ""
    )
    assert "取数失败" in str(result.dashboard["decision_stability"])


def test_us_instrument_keeps_buy_and_reports_no_capital_flow_feed(monkeypatch):
    manager = _manager(monkeypatch)

    block = manager.get_capital_flow_context("AAPL")

    assert block["status"] == "not_supported"

    result = _buy_result(code="AAPL", name="Apple")
    _stabilize(result, {"capital_flow": block})

    assert result.decision_type == "buy"
    assert result.dashboard["decision_stability"]["capital_flow_status"] == "市场资金流服务暂不支持"


def test_etf_is_capability_missing_and_keeps_buy(monkeypatch):
    manager = _manager(monkeypatch)

    block = manager.get_capital_flow_context("510300")

    assert block["status"] == "not_supported"

    result = _buy_result(code="510300", name="沪深300ETF")
    _stabilize(result, {"capital_flow": block})

    assert result.decision_type == "buy"
    assert result.dashboard["decision_stability"]["capital_flow_status"] == "市场资金流服务暂不支持"


@pytest.mark.parametrize("code", ["sh000001", "000300.SH", "sz399001"])
def test_index_is_capability_missing_and_keeps_buy(monkeypatch, code):
    manager = _manager(monkeypatch)

    block = manager.get_capital_flow_context(code)

    assert block["status"] == "not_supported"


def test_adapter_marks_interface_errors_as_failed(monkeypatch):
    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    _timeout_on_capital_flow(monkeypatch)

    payload = AkshareFundamentalAdapter().get_capital_flow("600519")

    assert payload["status"] == "failed"
    assert payload["errors"]
    assert payload["stock_flow"] == {}


def test_adapter_keeps_not_supported_when_no_interface_exists(monkeypatch):
    import sys

    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    # 只有导入成功但两个接口都不存在的模块，才是"根本没有可调用的接口"。
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace())

    payload = AkshareFundamentalAdapter().get_capital_flow("600519")

    assert payload["status"] == "not_supported"
    assert payload["errors"] == []
