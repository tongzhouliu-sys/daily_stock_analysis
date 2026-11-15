# -*- coding: utf-8 -*-
"""限速回归测试。

覆盖的真实风险路径：
1. 并发 worker 经 ``_call_fetcher_method`` 调用同一数据源时，实际发出的请求
   仍然保持最小间隔（T28 把睡眠挪到请求之后，间隔曾退化为 0）
2. 一个数据源在等待槽位时，不会阻塞其它数据源的调用（各数据源各有调用锁）
3. 基本面并行块共用按数据源划分的并发限制器：在途请求数不超过上限，
   超时被放弃的调用在真正结束前一直占着槽位
4. 多只 A 股并发分析时，资金流不会因为限制器排队而"预算耗尽"
"""

from __future__ import annotations

import threading
import time

import pandas as pd

from data_provider import fundamental_adapter as fa
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetcherManager
from data_provider.efinance_fetcher import EfinanceFetcher

# 新浪/腾讯单股直连接口不加随机抖动，测试用它固定最小间隔
_JITTERLESS_METHOD = "_fetch_stock_data_sina"


# 基本面聚合会调用的全部 akshare 接口。测试必须全部替换，否则会真的联网。
_FUNDAMENTAL_AKSHARE_INTERFACES = (
    "stock_financial_abstract",
    "stock_financial_analysis_indicator",
    "stock_yjyg_em",
    "stock_yjkb_em",
    "stock_fhps_detail_em",
    "stock_history_dividend_detail",
    "stock_dividend_cninfo",
    "stock_institute_hold",
    "stock_gdfx_top_10_em",
    "stock_lhb_stock_statistic_em",
    "stock_lhb_detail_em",
    "stock_lhb_jgmmtj_em",
    "stock_individual_fund_flow",
    "stock_sector_fund_flow_rank",
    "stock_main_fund_flow",
    "stock_individual_fund_flow_rank",
)


def _make_other_akshare_interfaces_unavailable(monkeypatch, keep=()):
    def unavailable(**_kwargs):
        raise RuntimeError("offline test: interface not stubbed")

    for name in _FUNDAMENTAL_AKSHARE_INTERFACES:
        if name not in keep:
            monkeypatch.setattr(f"akshare.{name}", unavailable, raising=False)


def _make_akshare_fetcher(sleep_min: float) -> AkshareFetcher:
    fetcher = AkshareFetcher()
    fetcher.sleep_min = sleep_min
    fetcher.sleep_max = sleep_min
    fetcher._last_request_time = None
    return fetcher


def test_concurrent_managed_calls_keep_min_interval():
    manager = DataFetcherManager(fetchers=[])
    fetcher = _make_akshare_fetcher(1.0)
    sent: list[float] = []
    sent_lock = threading.Lock()

    def fake_request(**_kwargs):
        # 与真实的新浪直连接口一致：发请求前限速（不加抖动），
        # 时间戳在方法内部记录，代表"请求实际发出"的时刻
        fetcher._enforce_rate_limit(jitter=False)
        with sent_lock:
            sent.append(time.monotonic())
        return "ok"

    fetcher.__dict__[_JITTERLESS_METHOD] = fake_request
    threads = [
        threading.Thread(
            target=manager._call_fetcher_method,
            args=(fetcher, _JITTERLESS_METHOD),
            daemon=True,
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    assert len(sent) == 4
    sent.sort()
    gaps = [later - earlier for earlier, later in zip(sent, sent[1:])]
    assert min(gaps) >= 0.9, gaps


def test_slot_wait_does_not_block_other_source():
    manager = DataFetcherManager(fetchers=[])
    waiting = _make_akshare_fetcher(1.0)
    # 让下一个槽位落在 1 秒之后，模拟"上一个请求刚发完"
    waiting._last_request_time = time.time()

    def fake_request(**_kwargs):
        return "slow"

    waiting.__dict__[_JITTERLESS_METHOD] = fake_request
    blocked = threading.Thread(
        target=manager._call_fetcher_method,
        args=(waiting, _JITTERLESS_METHOD),
        daemon=True,
    )
    blocked.start()
    time.sleep(0.05)

    other = EfinanceFetcher()
    other.sleep_min = 0.0
    other.sleep_max = 0.0
    other._last_request_time = None
    other.__dict__["fake_method"] = lambda **_kwargs: "fast"

    started = time.monotonic()
    assert manager._call_fetcher_method(other, "fake_method") == "fast"
    elapsed = time.monotonic() - started

    blocked.join(5.0)
    # 等待槽位发生在调用锁之外，另一个数据源不用陪着等
    assert elapsed < 0.5


def test_second_request_inside_one_call_waits_in_place():
    manager = DataFetcherManager(fetchers=[])
    fetcher = _make_akshare_fetcher(0.3)

    def fake_request(**_kwargs):
        started = time.monotonic()
        # 第一个请求前没有发过请求，不需要等待
        fetcher._enforce_rate_limit(jitter=False)
        first = time.monotonic()
        # 同一次调用内的第二次请求必须在原地预约并等待
        fetcher._enforce_rate_limit(jitter=False)
        return first - started, time.monotonic() - first

    fetcher.__dict__[_JITTERLESS_METHOD] = fake_request
    first_wait, second_wait = manager._call_fetcher_method(fetcher, _JITTERLESS_METHOD)

    assert first_wait < 0.05
    assert second_wait >= 0.25


def test_fundamental_blocks_share_source_limiter(monkeypatch):
    fa._clear_daily_table_cache()
    fa._clear_source_rate_limiters_for_tests()
    limiter = fa._get_source_rate_limiter(fa._FUNDAMENTAL_DEFAULT_SOURCE)
    limiter.min_interval = 0.3

    adapter = fa.AkshareFundamentalAdapter()
    sent: list[float] = []
    sent_lock = threading.Lock()

    def fake_interface(**_kwargs):
        with sent_lock:
            sent.append(time.monotonic())
        return pd.DataFrame({"股票代码": ["600519"], "营业总收入": [1000.0]})

    monkeypatch.setattr("akshare.stock_financial_abstract", fake_interface, raising=False)
    _make_other_akshare_interfaces_unavailable(monkeypatch, keep={"stock_financial_abstract"})

    threads = [
        threading.Thread(
            target=adapter.get_fundamental_bundle,
            args=("600519",),
            daemon=True,
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    fa._clear_daily_table_cache()
    fa._clear_source_rate_limiters_for_tests()

    assert len(sent) == 4
    sent.sort()
    gaps = [later - earlier for earlier, later in zip(sent, sent[1:])]
    assert min(gaps) >= 0.25, gaps


def test_fundamental_source_limiter_caps_concurrency():
    limiter = fa._SourceRateLimiter(max_concurrency=2)
    limiter.min_interval = 0.0

    state = {"in_flight": 0, "peak": 0}
    state_lock = threading.Lock()
    hold = threading.Event()

    def worker() -> None:
        limiter.acquire()
        try:
            with state_lock:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            hold.wait(2.0)
            with state_lock:
                state["in_flight"] -= 1
        finally:
            limiter.release()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    hold.set()
    for thread in threads:
        thread.join(5.0)

    assert limiter.max_concurrency == 2
    assert state["peak"] == 2
    assert state["in_flight"] == 0


def test_abandoned_call_keeps_its_slot_until_it_finishes():
    limiter = fa._SourceRateLimiter(max_concurrency=1)
    finished = threading.Event()

    def slow_interface():
        time.sleep(0.4)
        finished.set()
        return "late"

    assert limiter.acquire(max_wait=1.0)
    try:
        fa._call_with_deadline(slow_interface, {}, 0.05, "slow", on_done=limiter.release)
    except TimeoutError:
        pass
    # The abandoned call is still running upstream: its slot must stay taken.
    assert limiter.acquire(max_wait=0.1) is False
    assert finished.wait(2.0)
    assert limiter.acquire(max_wait=1.0) is True
    limiter.release()


def test_concurrent_a_share_analyses_keep_capital_flow(monkeypatch):
    """Three A-shares analysed at once must all get capital flow within budget.

    With the old 2-second shared interval every stock ran out of its 8-second
    fundamental budget waiting for the limiter, capital flow became "failed"
    and every buy was downgraded to hold, although upstream answered at once.
    The manager's timeout worker pool was also sized for serial blocks, so with
    parallel blocks the last block (often capital flow) was rejected with
    "worker pool exhausted".
    """
    import akshare as ak

    from data_provider.base import DataFetcherManager

    codes = ["600519", "000858", "601318"]

    # Upstream latencies are chosen so every block of all three stocks is in
    # flight at the same time (each block holds a timeout worker slot).
    def table(**_kwargs):
        time.sleep(0.3)
        return pd.DataFrame({
            "股票代码": codes, "代码": codes,
            "营业总收入": [1.0] * 3, "归母净利润": [1.0] * 3, "报告期": ["20260930"] * 3,
        })

    def flow(**_kwargs):
        time.sleep(0.3)
        return pd.DataFrame({
            "日期": pd.date_range("2026-11-01", periods=10).astype(str),
            "主力净流入-净额": [1e6] * 10,
        })

    def sector(**_kwargs):
        time.sleep(0.3)
        return pd.DataFrame({"名称": ["a", "b", "c"], "今日主力净流入-净额": [1.0, 2.0, -1.0]})

    for name in _FUNDAMENTAL_AKSHARE_INTERFACES:
        monkeypatch.setattr(ak, name, table, raising=False)
    monkeypatch.setattr(ak, "stock_individual_fund_flow", flow, raising=False)
    monkeypatch.setattr(ak, "stock_sector_fund_flow_rank", sector, raising=False)
    fa._clear_daily_table_cache()
    fa._clear_source_rate_limiters_for_tests()

    manager = DataFetcherManager(fetchers=[])
    statuses: dict[str, str] = {}
    errors: dict[str, object] = {}

    def slow_sector_rankings(_limit):
        # Offline stand-in for the network call inside the boards block. The
        # block still runs through _run_with_retry, so it holds a timeout
        # worker slot while the other blocks of all three stocks run.
        time.sleep(1.5)
        return [], [], [], "offline test"

    monkeypatch.setattr(manager, "_get_sector_rankings_with_meta", slow_sector_rankings)

    def run(code: str) -> None:
        context = manager.get_fundamental_context(code, realtime_quote=None)
        statuses[code] = context["capital_flow"].get("status")
        errors[code] = context["capital_flow"].get("errors")

    threads = [threading.Thread(target=run, args=(code,), daemon=True) for code in codes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)

    fa._clear_daily_table_cache()
    fa._clear_source_rate_limiters_for_tests()
    assert statuses == {code: "ok" for code in codes}, (statuses, errors)


def test_timeout_worker_pool_scales_with_analysis_workers(monkeypatch):
    from types import SimpleNamespace

    import data_provider.base as base

    monkeypatch.setattr("src.config.get_config", lambda: SimpleNamespace(max_workers=6))
    limit = base._resolve_fundamental_timeout_worker_limit()
    # Every worker may run all parallel fundamental blocks at the same time.
    assert limit >= base._FUNDAMENTAL_PARALLEL_BLOCKS_PER_STOCK * 6
