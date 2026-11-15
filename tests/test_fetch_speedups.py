# -*- coding: utf-8 -*-
"""T28 取数提速回归测试。

覆盖三条真实风险路径：
1. 限速等待发生在数据源调用锁之外（并发 worker 不会被一个慢限速连带阻塞）
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


def test_managed_call_defers_sleep_outside_lock():
    manager = DataFetcherManager(fetchers=[])
    fetcher = AkshareFetcher()
    fetcher.sleep_min = 0.01
    fetcher.sleep_max = 0.02
    fetcher._last_request_time = None
    observed: dict[str, float] = {}

    def fake_method(**kwargs):
        # 方法内部执行限速：管理器已预约槽位，这里不再睡眠
        started = time.monotonic()
        fetcher._enforce_rate_limit()
        observed["inside"] = time.monotonic() - started
        lock = manager._get_fetcher_call_lock(fetcher)
        observed["lock_free"] = 1.0 if lock.acquire(blocking=False) else 0.0
        if observed["lock_free"]:
            lock.release()
        return "done"

    fetcher.__dict__["fake_method"] = fake_method  # 绑定实例，method 调用无需 self
    slept: list[float] = []

    def fake_sleep(seconds):
        slept.append(seconds)

    started = time.monotonic()
    with patch("data_provider.base.time.sleep", side_effect=fake_sleep):
        result = manager._call_fetcher_method(fetcher, "fake_method")
    elapsed = time.monotonic() - started

    assert result == "done"
    # 方法内部不睡眠（ReentrantLock 同线程可重入，用耗时判断更直观）
    assert observed["inside"] < 0.01
    assert observed["lock_free"] == 1.0
    # T29: 等待发生在进入调用锁之前，方法内部直接消费已预约的槽位
    assert slept and slept[0] >= 0.01
    assert elapsed < 0.2


def test_managed_flag_is_cleared_after_call():
    manager = DataFetcherManager(fetchers=[])
    fetcher = AkshareFetcher()
    fetcher.sleep_min = 0.0
    fetcher.sleep_max = 0.0

    def fake_method(**_kwargs):
        fetcher._enforce_rate_limit()
        return "ok"

    fetcher.__dict__["fake_method"] = fake_method
    manager._call_fetcher_method(fetcher, "fake_method")

    assert getattr(fetcher._rate_limit_tls, "managed", False) is False
    assert getattr(fetcher._rate_limit_tls, "slot_reserved", False) is False


def test_managed_flag_cleared_when_method_raises():
    manager = DataFetcherManager(fetchers=[])
    fetcher = AkshareFetcher()
    fetcher.sleep_min = 0.0
    fetcher.sleep_max = 0.0

    def boom(**_kwargs):
        fetcher._enforce_rate_limit()
        raise RuntimeError("boom")

    fetcher.__dict__["boom"] = boom
    try:
        manager._call_fetcher_method(fetcher, "boom")
    except RuntimeError:
        pass

    assert getattr(fetcher._rate_limit_tls, "managed", False) is False


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
