# -*- coding: utf-8 -*-
"""T29 限速"先等后发"回归测试。

覆盖三条真实风险路径：
1. 并发 worker 经 ``_call_fetcher_method`` 调用同一数据源时，实际发出的请求
   仍然保持最小间隔（T28 把睡眠挪到请求之后，间隔曾退化为 0）
2. 一个数据源在等待槽位时，不会阻塞其它数据源的调用（等待发生在调用锁之外）
3. 基本面并行块共用按数据源划分的模块级限速器：请求间隔与 fetcher 一致，
   在途请求数不超过 2
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
        # 时间戳在方法内部记录，代表"请求实际发出"的时刻
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


def test_second_request_inside_managed_call_waits_in_place():
    manager = DataFetcherManager(fetchers=[])
    fetcher = _make_akshare_fetcher(0.3)

    def fake_request(**_kwargs):
        started = time.monotonic()
        # 第一个请求消费管理器已预约的槽位，不等待
        fetcher._enforce_rate_limit()
        first = time.monotonic()
        # 同一次调用内的第二次请求必须在原地预约并等待
        fetcher._enforce_rate_limit()
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
