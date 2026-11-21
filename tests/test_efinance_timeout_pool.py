# -*- coding: utf-8 -*-
"""Bounded pool and circuit breaker for efinance timeout wrapper."""

from __future__ import annotations

import threading
import time

from concurrent.futures import ThreadPoolExecutor

import pytest

from data_provider import efinance_fetcher


@pytest.fixture(autouse=True)
def _reset_efinance_pool(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="efinance-test")
    monkeypatch.setattr(efinance_fetcher, "_ef_executor", executor)
    monkeypatch.setattr(
        efinance_fetcher, "_ef_inflight", threading.BoundedSemaphore(2)
    )
    monkeypatch.setattr(efinance_fetcher, "_ef_consecutive_timeouts", 0)
    monkeypatch.setattr(efinance_fetcher, "_ef_circuit_until", 0.0)
    yield
    executor.shutdown(wait=False)


def test_success_passes_through() -> None:
    assert efinance_fetcher._ef_call_with_timeout(lambda: 42, timeout=1) == 42


def test_timeout_raises_builtin_timeout_error() -> None:
    with pytest.raises(TimeoutError):
        efinance_fetcher._ef_call_with_timeout(lambda: time.sleep(0.2), timeout=0.05)


def test_consecutive_timeouts_open_circuit(monkeypatch) -> None:
    for _ in range(efinance_fetcher._EF_TIMEOUT_THRESHOLD):
        with pytest.raises(TimeoutError):
            efinance_fetcher._ef_call_with_timeout(
                lambda: time.sleep(0.2), timeout=0.02
            )

    assert efinance_fetcher._ef_circuit_until > time.time()
    with pytest.raises(TimeoutError, match="circuit open"):
        efinance_fetcher._ef_call_with_timeout(lambda: 1, timeout=1)


def test_success_resets_timeout_counter() -> None:
    efinance_fetcher._ef_consecutive_timeouts = 2
    assert efinance_fetcher._ef_call_with_timeout(lambda: "ok", timeout=1) == "ok"
    assert efinance_fetcher._ef_consecutive_timeouts == 0
