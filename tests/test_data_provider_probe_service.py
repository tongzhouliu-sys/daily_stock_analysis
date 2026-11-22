# -*- coding: utf-8 -*-
"""Tests for the background data-provider runtime probe and its cache."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from data_provider.api_key_pool import ApiKeyPool
from data_provider.runtime_status_cache import (
    clear_runtime_results,
    get_runtime_result,
    record_runtime_result,
    sanitize_error_summary,
)
from src.services.data_capability_service import DataCapabilityService
from src.services.data_provider_probe_service import DataProviderProbeService


class _FetcherManager:
    def __init__(self, fetchers) -> None:
        self._fetchers = list(fetchers)

    def _get_fetchers_snapshot(self):
        return list(self._fetchers)


class _DailyFetcher:
    def __init__(self, name: str, *, error: Exception = None) -> None:
        self.name = name
        self.error = error
        self.calls = []

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        self.calls.append((stock_code, start_date, end_date))
        if self.error is not None:
            raise self.error
        return pd.DataFrame({"date": [end_date], "close": [1.0]})


class _KeyedFetcher:
    def __init__(self, name: str, keys, *, error: Exception = None) -> None:
        self.name = name
        self.error = error
        self.calls = []
        self._key_pool = ApiKeyPool(keys)

    def _request_global_quote(self, symbol, api_key):
        self.calls.append(api_key)
        if self.error is not None:
            raise self.error
        return {"Global Quote": {"05. price": "1.0"}}

    def _request_quote(self, symbol, api_key):
        self.calls.append(api_key)
        if self.error is not None:
            raise self.error
        return {"c": 1.0}


def _config(**overrides):
    values = dict(
        tushare_token=None,
        tickflow_api_key=None,
        tickflow_priority=2,
        futu_opend_host=None,
        longbridge_app_key=None,
        longbridge_app_secret=None,
        longbridge_access_token=None,
        longbridge_oauth_client_id=None,
        finnhub_api_key=None,
        alphavantage_api_key=None,
        enable_realtime_quote=True,
        enable_fundamental_pipeline=True,
        realtime_source_priority="efinance",
        futu_hk_realtime_source_priority="",
        screening_enabled=False,
        agent_event_monitor_enabled=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _provider(overview, name: str):
    return next(item for item in overview["providers"] if item["name"] == name)


def _stamp(minutes_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_probe_success_marks_provider_ok_and_records_check_time() -> None:
    fetcher = _DailyFetcher("YfinanceFetcher")
    manager = _FetcherManager([fetcher])
    service = DataCapabilityService(config=_config(), fetcher_manager=manager)

    results = DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
    ).probe_once(force=True)

    assert results["yfinance"]["status"] == "ok"
    overview = service.get_overview()
    provider = _provider(overview, "yfinance")
    assert provider["status"] == "ok"
    assert provider["last_checked_at"]
    assert provider["warnings"] == []
    assert fetcher.calls


def test_probe_failure_marks_provider_unavailable() -> None:
    fetcher = _DailyFetcher("EfinanceFetcher", error=RuntimeError("HTTP 503 Service Unavailable"))
    manager = _FetcherManager([fetcher])
    service = DataCapabilityService(config=_config(), fetcher_manager=manager)

    DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
    ).probe_once(force=True)

    provider = _provider(service.get_overview(), "efinance")
    assert provider["status"] == "unavailable"
    assert provider["last_checked_at"]
    assert provider["warnings"] == ["provider_marked_unavailable"]
    assert "503" in (provider["last_error"] or "")


def test_rate_limited_probe_stays_ok_with_quota_limited_warning() -> None:
    fetcher = _DailyFetcher(
        "EfinanceFetcher",
        error=RuntimeError("429 Too Many Requests"),
    )
    manager = _FetcherManager([fetcher])
    service = DataCapabilityService(config=_config(), fetcher_manager=manager)

    DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
    ).probe_once(force=True)

    provider = _provider(service.get_overview(), "efinance")
    assert provider["status"] == "ok"
    assert provider["warnings"] == ["quota_limited"]


def test_stale_cache_returns_unknown_with_probe_stale_warning() -> None:
    fetcher = _DailyFetcher("YfinanceFetcher")
    manager = _FetcherManager([fetcher])
    record_runtime_result(
        "yfinance",
        ok=True,
        checked_at=time.time() - 3 * 60 * 60,
        source="probe",
    )

    provider = _provider(
        DataCapabilityService(config=_config(), fetcher_manager=manager).get_overview(),
        "yfinance",
    )

    assert provider["status"] == "unknown"
    assert provider["warnings"] == ["probe_stale"]
    assert provider["last_checked_at"]
    assert fetcher.calls == []


def test_reading_overview_does_not_issue_probe_requests() -> None:
    fetcher = _DailyFetcher("YfinanceFetcher")
    manager = _FetcherManager([fetcher])

    DataCapabilityService(config=_config(), fetcher_manager=manager).get_overview()

    assert fetcher.calls == []


def test_alpha_vantage_probe_uses_one_key_and_records_daily_quota(tmp_path: Path) -> None:
    fetcher = _KeyedFetcher("AlphaVantageFetcher", ["key-a", "key-b"])
    manager = _FetcherManager([fetcher])
    quota_path = tmp_path / "alphavantage-quota.json"

    results = DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
        alpha_vantage_quota_path=quota_path,
    ).probe_once(force=True)

    assert results["alphavantage"]["status"] == "ok"
    assert fetcher.calls == ["key-a"]
    assert fetcher._key_pool.requests_last_day("key-a") == 1
    assert fetcher._key_pool.requests_last_day("key-b") == 0
    quota = json.loads(quota_path.read_text())
    assert len(quota["keys"]["0"]["requests"]) == 1
    assert "key-a" not in quota_path.read_text()


def test_alpha_vantage_probe_keeps_ok_when_daily_quota_is_exhausted(tmp_path: Path) -> None:
    fetcher = _KeyedFetcher("AlphaVantageFetcher", ["key-a", "key-b"])
    manager = _FetcherManager([fetcher])
    quota_path = tmp_path / "alphavantage-quota.json"
    quota_path.write_text(json.dumps({
        "next": 0,
        "keys": {"0": {"requests": [_stamp(minutes_ago=offset) for offset in range(24)]}},
    }))
    service = DataCapabilityService(
        config=_config(alphavantage_api_key="key-a"),
        fetcher_manager=manager,
    )

    results = DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
        alpha_vantage_quota_path=quota_path,
    ).probe_once(force=True)

    assert results["alphavantage"]["status"] == "ok"
    assert results["alphavantage"]["warnings"] == ["quota_limited"]
    assert fetcher.calls == []
    provider = _provider(service.get_overview(), "alphavantage")
    assert provider["status"] == "ok"
    assert provider["warnings"] == ["quota_limited"]


def test_probe_error_summary_does_not_leak_key_or_tokenized_url() -> None:
    secret = "unit-test-secret-key"
    fetcher = _KeyedFetcher(
        "AlphaVantageFetcher",
        [secret],
        error=RuntimeError(
            f"GET https://www.alphavantage.co/query?apikey={secret}&function=GLOBAL_QUOTE failed"
        ),
    )
    manager = _FetcherManager([fetcher])

    DataProviderProbeService(
        fetcher_manager=manager,
        config_provider=_config,
    ).probe_once(force=True)

    cached = get_runtime_result("alphavantage")
    assert cached is not None
    assert secret not in json.dumps(cached)
    assert "https://" not in (cached["error"] or "")


def test_sanitize_error_summary_redacts_urls_and_keys() -> None:
    secret = "another-secret"
    summary = sanitize_error_summary(
        RuntimeError(f"GET https://example.test/query?token={secret} key={secret}"),
        secrets=[secret],
    )

    assert secret not in summary
    assert "https://" not in summary
    assert "***" in summary


def test_runtime_cache_can_be_cleared_between_checks() -> None:
    record_runtime_result("yfinance", ok=True, source="runtime")
    assert get_runtime_result("yfinance") is not None
    clear_runtime_results("yfinance")
    assert get_runtime_result("yfinance") is None
