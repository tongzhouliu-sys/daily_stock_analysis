# -*- coding: utf-8 -*-
"""R10: concurrent in-flight dedup for network history fetches.

Covers the two defects in ``_NetworkCache``:

* multiple waiters used to pop each other's registry entry and get empty data;
* completed fetches were never removed, so a stale (even failed) result was
  served to the next caller, and a ``BaseException`` in the owner stranded
  waiters for the full timeout.
"""
from __future__ import annotations

import threading
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def _frame(rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame({"date": ["2026-04-18"] * rows, "close": range(rows)})


@pytest.fixture(autouse=True)
def _clean_cache():
    from src.services.history_loader import clear_history_cache_for_tests

    clear_history_cache_for_tests()
    yield
    clear_history_cache_for_tests()


def test_owner_and_three_waiters_all_receive_same_frame():
    from src.services.history_loader import _network_cache

    key = "600519|2026-04-18|120"
    gate = threading.Event()
    results: list = []
    lock = threading.Lock()

    def waiter():
        record, is_owner = _network_cache.start_inflight(key)
        assert is_owner is False
        df, source, error = _network_cache.wait_inflight(record)
        with lock:
            results.append((df, source, error))

    record, is_owner = _network_cache.start_inflight(key)
    assert is_owner is True
    threads = [threading.Thread(target=waiter) for _ in range(3)]
    for t in threads:
        t.start()
    # Give the waiters time to register before the owner publishes.
    time.sleep(0.05)
    gate.wait(timeout=1)
    expected = _frame()
    _network_cache.finish_inflight(key, record, expected, "eastmoney", None)
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 3
    for df, source, error in results:
        assert error is None
        assert source == "eastmoney"
        assert df is not None and len(df) == len(expected)


def test_failed_owner_publishes_error_and_next_caller_refetches():
    from src.services.history_loader import _network_cache

    key = "600519|2026-04-18|120"
    record, is_owner = _network_cache.start_inflight(key)
    assert is_owner
    error = RuntimeError("boom")
    _network_cache.finish_inflight(key, record, None, None, error)

    # Registry is empty again: the next caller becomes the owner.
    record2, is_owner2 = _network_cache.start_inflight(key)
    assert is_owner2 is True
    assert record2 is not record
    _network_cache.finish_inflight(key, record2, _frame(), "eastmoney", None)
    assert _network_cache.get_inflight(key) is None


def test_base_exception_releases_waiters_quickly():
    from src.services.history_loader import _network_cache

    key = "600519|2026-04-18|120"
    record, is_owner = _network_cache.start_inflight(key)
    assert is_owner
    released: list = []

    def waiter():
        df, source, error = _network_cache.wait_inflight(record, timeout=120.0)
        released.append((df, source, error))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    _network_cache.finish_inflight(key, record, None, None, KeyboardInterrupt())
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "waiter was stranded past the 1 second budget"
    assert len(released) == 1
    assert isinstance(released[0][2], BaseException)


def test_registry_is_empty_after_all_fetches_complete():
    from src.services.history_loader import _network_cache

    for idx in range(3):
        key = f"60051{idx}|2026-04-18|120"
        record, _ = _network_cache.start_inflight(key)
        _network_cache.finish_inflight(key, record, _frame(), "eastmoney", None)
    assert _network_cache._inflight == {}


@patch("src.services.history_loader._get_fetcher_manager")
@patch("src.storage.get_db")
def test_load_history_df_shares_one_fetch_between_four_threads(mock_get_db, mock_get_fm):
    from src.services.history_loader import load_history_df

    mock_db = MagicMock()
    mock_db.get_data_range.return_value = []
    mock_get_db.return_value = mock_db

    calls = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def fake_get_daily_data(code, days=None, **kwargs):
        calls["n"] += 1
        started.set()
        release.wait(timeout=5)
        return _frame(), "eastmoney"

    manager = MagicMock()
    manager.get_daily_data.side_effect = fake_get_daily_data
    mock_get_fm.return_value = manager

    out: list = []
    lock = threading.Lock()

    def run():
        df, source = load_history_df("600519", days=60, target_date=date(2026, 4, 18))
        with lock:
            out.append((df, source))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    started.wait(timeout=5)
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(timeout=10)

    assert calls["n"] == 1, "expected a single network fetch shared by all callers"
    assert len(out) == 4
    for df, source in out:
        assert df is not None and len(df) == 5
        assert source == "eastmoney"
