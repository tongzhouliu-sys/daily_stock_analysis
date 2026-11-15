# -*- coding: utf-8 -*-
"""T28 取数提速回归测试。

覆盖三条真实风险路径：
1. 限速在真正发请求时生效：慢请求之后的请求仍保持间隔，免抖动接口不被加抖动，缓存命中不占用槽位
2. 全市场表进程内按天缓存命中 / 跨日失效 / 不缓存逐股接口
3. 基本面单接口限时，慢接口不会把已取到的块一起作废
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from data_provider import fundamental_adapter as fa
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetcherManager


def test_reserve_rate_limit_delay_is_pure_accounting():
    fetcher = AkshareFetcher()
    fetcher.sleep_min = 0.5
    fetcher.sleep_max = 1.0
    fetcher._last_request_time = None

    started = time.monotonic()
    first = fetcher._reserve_rate_limit_delay(jitter=False)
    second = fetcher._reserve_rate_limit_delay(jitter=False)
    elapsed = time.monotonic() - started

    assert first == 0.0
    # 第二次请求登记时最小间隔还没走完，返回需要等待的秒数
    assert 0.0 < second <= 0.5
    # 只做记账，不在调用方线程里睡眠
    assert elapsed < 0.2


def _manager_and_fetcher(sleep_min: float, sleep_max: float):
    manager = DataFetcherManager(fetchers=[])
    fetcher = AkshareFetcher()
    fetcher.sleep_min = sleep_min
    fetcher.sleep_max = sleep_max
    fetcher._last_request_time = None
    return manager, fetcher


def test_requests_stay_spaced_when_an_earlier_request_is_slow():
    # A slot reserved before waiting for the call lock used to expire while a
    # slow request held the lock; the queued requests then went out back to back.
    manager, fetcher = _manager_and_fetcher(0.3, 0.3)
    sent_at: list[float] = []
    sent_lock = threading.Lock()

    def fetch(slow: bool):
        fetcher._enforce_rate_limit(jitter=False)
        with sent_lock:
            sent_at.append(time.monotonic())
        time.sleep(1.0 if slow else 0.01)
        return "ok"

    fetcher.__dict__["fetch"] = fetch
    threads = [
        threading.Thread(target=manager._call_fetcher_method, args=(fetcher, "fetch", index == 0))
        for index in range(4)
    ]
    threads[0].start()
    time.sleep(0.05)  # the slow request goes first
    for thread in threads[1:]:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    sent_at.sort()
    gaps = [later - earlier for earlier, later in zip(sent_at, sent_at[1:])]
    assert len(sent_at) == 4
    assert min(gaps) >= 0.27, gaps


def test_jitterless_endpoint_gets_no_jitter_through_the_manager():
    manager, fetcher = _manager_and_fetcher(0.0, 0.5)

    def tencent_quote():
        fetcher._enforce_rate_limit(jitter=False)
        return "quote"

    fetcher.__dict__["get_realtime_quote"] = tencent_quote
    with patch.object(fetcher, "random_sleep_seconds", return_value=0.4):
        started = time.monotonic()
        assert manager._call_fetcher_method(fetcher, "get_realtime_quote") == "quote"
        elapsed = time.monotonic() - started

    assert elapsed < 0.2


def test_calls_that_never_hit_the_network_do_not_wait_for_a_slot():
    manager, fetcher = _manager_and_fetcher(0.3, 0.3)

    def cached_lookup():
        return "cached"  # no _enforce_rate_limit: nothing is sent

    fetcher.__dict__["cached_lookup"] = cached_lookup
    started = time.monotonic()
    for _ in range(3):
        assert manager._call_fetcher_method(fetcher, "cached_lookup") == "cached"
    assert time.monotonic() - started < 0.1


def test_daily_cache_reuses_market_wide_table(monkeypatch):
    fa._clear_daily_table_cache()
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []
    frame = pd.DataFrame({"板块": ["半导体"], "今日主力净流入-净额": [1.0]})

    def fake_rank(**kwargs):
        calls.append(1)
        return frame.copy()

    monkeypatch.setattr("akshare.stock_sector_fund_flow_rank", fake_rank, raising=False)

    first = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])
    second = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])

    assert len(calls) == 1
    assert first[1] == "stock_sector_fund_flow_rank"
    assert second[1] == "stock_sector_fund_flow_rank:daily_cache"
    assert second[0] is not frame  # 返回副本，调用方改写不会污染缓存
    fa._clear_daily_table_cache()


def test_daily_cache_skips_per_stock_interfaces(monkeypatch):
    fa._clear_daily_table_cache()
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []

    def fake_flow(**kwargs):
        calls.append(1)
        return pd.DataFrame({"日期": ["2026-11-13"], "主力净流入-净额": [1.0]})

    monkeypatch.setattr("akshare.stock_individual_fund_flow", fake_flow, raising=False)

    adapter._call_df_candidates([("stock_individual_fund_flow", {"stock": "600519", "market": "sh"})])
    adapter._call_df_candidates([("stock_individual_fund_flow", {"stock": "600519", "market": "sh"})])

    assert len(calls) == 2
    fa._clear_daily_table_cache()


def test_daily_cache_expires_on_new_day(monkeypatch):
    fa._clear_daily_table_cache()
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []

    def fake_rank(**kwargs):
        calls.append(1)
        return pd.DataFrame({"板块": ["半导体"], "今日主力净流入-净额": [1.0]})

    monkeypatch.setattr("akshare.stock_sector_fund_flow_rank", fake_rank, raising=False)
    adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])

    key = fa._daily_cache_key("stock_sector_fund_flow_rank", {})
    with fa._daily_table_cache_lock:
        _day, _stored_at, payload = fa._daily_table_cache[key]
        fa._daily_table_cache[key] = ("1999-01-01", _stored_at, payload)

    result = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])
    assert len(calls) == 2
    assert result[1] == "stock_sector_fund_flow_rank"
    fa._clear_daily_table_cache()


def test_slow_interface_keeps_previously_fetched_blocks(monkeypatch):
    fa._clear_daily_table_cache()
    adapter = fa.AkshareFundamentalAdapter()

    fin_df = pd.DataFrame(
        {
            "股票代码": ["600519"],
            "报告期": ["2026-09-30"],
            "营业总收入": [1000.0],
            "归母净利润": [300.0],
            "营业收入同比": [12.0],
            "净利润同比": [9.5],
        }
    )

    def slow_forecast(**kwargs):
        time.sleep(0.4)
        return pd.DataFrame({"股票代码": ["600519"], "业绩变动": ["预增"]})

    monkeypatch.setattr("akshare.stock_financial_abstract", lambda **kwargs: fin_df.copy(), raising=False)
    monkeypatch.setattr("akshare.stock_yjyg_em", slow_forecast, raising=False)
    monkeypatch.setattr(
        "akshare.stock_financial_analysis_indicator", lambda **kwargs: pd.DataFrame(), raising=False
    )
    monkeypatch.setattr("akshare.stock_yjkb_em", lambda **kwargs: pd.DataFrame(), raising=False)
    monkeypatch.setattr("akshare.stock_fhps_detail_em", lambda **kwargs: pd.DataFrame(), raising=False)
    monkeypatch.setattr("akshare.stock_institute_hold", lambda **kwargs: pd.DataFrame(), raising=False)
    monkeypatch.setattr("akshare.stock_gdfx_top_10_em", lambda **kwargs: pd.DataFrame(), raising=False)

    started = time.monotonic()
    bundle = adapter.get_fundamental_bundle("600519", budget_seconds=0.2)
    elapsed = time.monotonic() - started

    # 慢接口不拖住整轮：总耗时受预算约束
    assert elapsed < 1.0
    # 已取到的成长块保留
    assert bundle["growth"]["revenue_yoy"] == 12.0
    assert any("stock_yjyg_em" in error for error in bundle["errors"])
    fa._clear_daily_table_cache()


def test_bundle_budget_is_optional_for_direct_callers():
    adapter = fa.AkshareFundamentalAdapter()
    with patch.object(adapter, "_call_df_candidates", return_value=(None, None, [])):
        bundle = adapter.get_fundamental_bundle("600519")
    assert bundle["status"] == "not_supported"


def test_manager_runs_independent_fundamental_blocks_concurrently():
    manager = DataFetcherManager(fetchers=[])
    block_names = ("capital_flow", "dragon_tiger", "boards")
    started_flags = {name: threading.Event() for name in block_names}

    def blocking_block(name, payload):
        started_flags[name].set()
        # 只有其它块都已并发启动时才返回；串行实现会一直等到超时
        for event in started_flags.values():
            if not event.wait(0.5):
                return {"status": "failed", "source_chain": [], "errors": [f"{name} serial"]}
        return payload

    block = {"status": "partial", "source_chain": [], "errors": []}
    with patch.object(manager, "get_realtime_quote", return_value=None), patch.object(
        manager,
        "_fundamental_adapter",
    ) as adapter, patch.object(
        manager, "get_capital_flow_context", side_effect=lambda *a, **k: blocking_block("capital_flow", block)
    ), patch.object(
        manager, "get_dragon_tiger_context", side_effect=lambda *a, **k: blocking_block("dragon_tiger", block)
    ), patch.object(
        manager, "get_board_context", side_effect=lambda *a, **k: blocking_block("boards", block)
    ):
        adapter.get_fundamental_bundle.return_value = {
            "status": "not_supported",
            "source_chain": [],
            "errors": [],
        }
        ctx = manager.get_fundamental_context("600519", budget_seconds=1.0)

    assert ctx["capital_flow"]["status"] == "partial"
    assert ctx["dragon_tiger"]["status"] == "partial"
    assert ctx["boards"]["status"] == "partial"


def _fixed_cache_clock(monkeypatch, initial: datetime):
    clock = {"now": initial}
    monkeypatch.setattr(fa, "_resolve_cache_now", lambda now=None: clock["now"])
    return clock


def test_intraday_market_table_refetches_after_five_minutes(monkeypatch):
    fa._clear_daily_table_cache()
    clock = _fixed_cache_clock(monkeypatch, datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc))  # 10:00 北京
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []

    def fake_rank(**kwargs):
        calls.append(1)
        return pd.DataFrame({"板块": ["半导体"], "今日主力净流入-净额": [float(len(calls))]})

    monkeypatch.setattr("akshare.stock_sector_fund_flow_rank", fake_rank, raising=False)

    adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])
    clock["now"] = datetime(2026, 11, 16, 2, 6, tzinfo=timezone.utc)
    refreshed = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])

    assert len(calls) == 2
    assert refreshed[1] == "stock_sector_fund_flow_rank"
    fa._clear_daily_table_cache()


def test_intraday_market_table_hits_cache_within_five_minutes(monkeypatch):
    fa._clear_daily_table_cache()
    clock = _fixed_cache_clock(monkeypatch, datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc))
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []

    def fake_rank(**kwargs):
        calls.append(1)
        return pd.DataFrame({"板块": ["半导体"], "今日主力净流入-净额": [1.0]})

    monkeypatch.setattr("akshare.stock_sector_fund_flow_rank", fake_rank, raising=False)

    adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])
    clock["now"] = datetime(2026, 11, 16, 2, 4, tzinfo=timezone.utc)
    cached = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])

    assert len(calls) == 1
    assert cached[1] == "stock_sector_fund_flow_rank:daily_cache"
    fa._clear_daily_table_cache()


def test_after_close_market_table_reuses_trading_day_cache(monkeypatch):
    fa._clear_daily_table_cache()
    clock = _fixed_cache_clock(monkeypatch, datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc))  # 16:00 北京
    adapter = fa.AkshareFundamentalAdapter()
    calls: list[int] = []

    def fake_rank(**kwargs):
        calls.append(1)
        return pd.DataFrame({"板块": ["半导体"], "今日主力净流入-净额": [1.0]})

    monkeypatch.setattr("akshare.stock_sector_fund_flow_rank", fake_rank, raising=False)

    adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])
    clock["now"] = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    cached = adapter._call_df_candidates([("stock_sector_fund_flow_rank", {})])

    assert len(calls) == 1
    assert cached[1] == "stock_sector_fund_flow_rank:daily_cache"
    fa._clear_daily_table_cache()


def test_cache_day_uses_market_timezone_not_server_timezone(monkeypatch):
    # 服务器在 UTC，北京时间 08:00（开盘前）仍应落在同一个 A 股交易日。
    fixed = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
    assert fa._trading_day_cache_key(fixed) == "2026-11-16"

    # 周末回退到最近一个交易日。
    saturday = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    assert fa._trading_day_cache_key(saturday) == "2026-11-13"
