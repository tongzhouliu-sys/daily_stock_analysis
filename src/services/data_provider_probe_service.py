# -*- coding: utf-8 -*-
"""Lightweight background runtime probes for on-demand data providers.

Providers such as YFinance / Finnhub / Alpha Vantage / efinance / AkShare /
Tencent / Baostock are called per request and have no persistent availability
flag.  The data-center page used to report them as ``unknown`` because it never
probed them.

This service sends one tiny request per provider on a slow background cadence
(default every 30 minutes), stores the result in the process-wide runtime cache,
and lets the data-center API read that cache without issuing any external
request.  Real business calls also update the same cache, so a recently used
provider is not probed again.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

try:  # pragma: no cover - Windows without fcntl falls back to in-process locking
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from data_provider.runtime_status_cache import (
    get_runtime_result,
    is_quota_limited_error,
    record_runtime_result,
    sanitize_error_summary,
)
from src.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_PROBE_INTERVAL_MINUTES = 30
PROBE_INTERVAL_ENV = "DATA_PROVIDER_PROBE_INTERVAL_MINUTES"

# Alpha Vantage free tier allows 25 requests/day/key.  Keep one request as a
# safety margin so the probe never consumes the last call needed by a real
# analysis, and never use more than one key for probing.
_ALPHA_VANTAGE_DAILY_LIMIT = 24
_ALPHA_QUOTA_WINDOW = timedelta(days=1)
_DAYS_BACK = 14


@dataclass(frozen=True)
class _ProbeSpec:
    provider: str
    fetcher_name: str
    stock_code: str


_PROBE_SPECS: Sequence[_ProbeSpec] = (
    _ProbeSpec("efinance", "EfinanceFetcher", "000001"),
    _ProbeSpec("akshare", "AkshareFetcher", "000001"),
    _ProbeSpec("tencent", "TencentFetcher", "600519"),
    _ProbeSpec("yfinance", "YfinanceFetcher", "AAPL"),
    _ProbeSpec("baostock", "BaostockFetcher", "600519"),
    _ProbeSpec("finnhub", "FinnhubFetcher", "AAPL"),
    _ProbeSpec("alphavantage", "AlphaVantageFetcher", "AAPL"),
)


def probe_interval_minutes() -> int:
    """Return the configured probe interval; ``0`` disables probing."""

    raw_value = os.getenv(PROBE_INTERVAL_ENV)
    if raw_value is None or not str(raw_value).strip():
        return DEFAULT_PROBE_INTERVAL_MINUTES
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        logger.warning(
            "Invalid %s=%r; using fallback %s minutes",
            PROBE_INTERVAL_ENV,
            raw_value,
            DEFAULT_PROBE_INTERVAL_MINUTES,
        )
        return DEFAULT_PROBE_INTERVAL_MINUTES
    return max(0, value)


def probe_is_enabled() -> bool:
    return probe_interval_minutes() > 0


def _default_data_dir() -> Path:
    database_path = os.getenv("DATABASE_PATH")
    if database_path:
        return Path(database_path).resolve().parent
    return Path("./data").resolve()


def default_alpha_vantage_quota_path() -> Path:
    """Path used by the signal scanner for its Alpha Vantage daily budget."""

    runtime = os.getenv("SCANNER_RUNTIME")
    if runtime:
        return Path(runtime).resolve() / "daily-market" / "alphavantage-quota.json"
    return _default_data_dir() / "signal-scanner" / "daily-market" / "alphavantage-quota.json"


def _utc_stamp(moment: Optional[datetime] = None) -> str:
    value = moment or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_stamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


class DataProviderProbeService:
    """Run periodic minimal requests and cache the provider result."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any] = get_config,
        fetcher_manager: Any = None,
        alpha_vantage_quota_path: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config_provider = config_provider
        self._fetcher_manager = fetcher_manager
        self._alpha_vantage_quota_path = (
            Path(alpha_vantage_quota_path)
            if alpha_vantage_quota_path is not None
            else None
        )
        self._clock = clock
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._quota_lock = threading.Lock()

    def start(self) -> None:
        """Start the daemon probe loop when probing is enabled."""

        if not probe_is_enabled():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="data-provider-runtime-probe",
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.probe_once()
            except Exception as exc:  # noqa: BLE001 - background maintenance must not die.
                logger.warning(
                    "[ProviderProbe] probe cycle failed: %s",
                    sanitize_error_summary(exc),
                )
            interval_seconds = max(1, probe_interval_minutes()) * 60
            if self._stop_event.wait(interval_seconds):
                return

    def _get_fetchers(self) -> List[Any]:
        manager = self._fetcher_manager
        if manager is None:
            from data_provider import DataFetcherManager

            manager = DataFetcherManager()
            self._fetcher_manager = manager
        snapshot = getattr(manager, "_get_fetchers_snapshot", None)
        if callable(snapshot):
            return list(snapshot())
        return list(getattr(manager, "_fetchers", []) or [])

    def probe_once(self, *, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Probe enabled providers and return the cached snapshots.

        ``force=True`` ignores the recent-result reuse window (used by tests and
        manual diagnostics).  The API never calls this method.
        """

        interval_minutes = probe_interval_minutes()
        if interval_minutes <= 0 and not force:
            return {}

        fetchers = self._get_fetchers()
        fetcher_map = {
            str(getattr(fetcher, "name", "")): fetcher
            for fetcher in fetchers
            if getattr(fetcher, "name", None)
        }
        results: Dict[str, Dict[str, Any]] = {}
        config = self._config_provider()

        for spec in _PROBE_SPECS:
            fetcher = fetcher_map.get(spec.fetcher_name)
            if fetcher is None:
                continue
            if not self._provider_enabled(spec.provider, fetcher, config):
                continue
            cached = get_runtime_result(spec.provider)
            if (
                not force
                and cached is not None
                and interval_minutes > 0
                and self._clock() - float(cached.get("checked_at") or 0) < interval_minutes * 60
            ):
                results[spec.provider] = cached
                continue
            try:
                result = self._probe_provider(spec, fetcher)
            except Exception as exc:  # noqa: BLE001 - one provider must not stop the cycle.
                secrets = self._key_pool_keys(fetcher)
                quota_limited = is_quota_limited_error(exc)
                result = record_real_provider_result(
                    spec.provider,
                    ok=False,
                    error=exc,
                    secrets=secrets,
                )
                if quota_limited:
                    logger.info(
                        "[ProviderProbe] %s quota limited; keeping provider available",
                        spec.provider,
                    )
                else:
                    logger.warning(
                        "[ProviderProbe] %s failed: %s",
                        spec.provider,
                        sanitize_error_summary(exc, secrets=secrets),
                    )
            if result is not None:
                results[spec.provider] = result
        return results

    @staticmethod
    def _provider_enabled(provider: str, fetcher: Any, config: Any) -> bool:
        if provider == "finnhub":
            pool = getattr(fetcher, "_key_pool", None)
            if pool is not None:
                return bool(getattr(pool, "is_configured", False))
            return bool(getattr(config, "finnhub_api_key", None))
        if provider == "alphavantage":
            pool = getattr(fetcher, "_key_pool", None)
            if pool is not None:
                return bool(getattr(pool, "is_configured", False))
            return bool(getattr(config, "alphavantage_api_key", None))
        return True

    @staticmethod
    def _key_pool_keys(fetcher: Any) -> List[str]:
        pool = getattr(fetcher, "_key_pool", None)
        keys = getattr(pool, "keys", None)
        if isinstance(keys, list):
            return [str(item) for item in keys if item]
        return []

    def _probe_provider(self, spec: _ProbeSpec, fetcher: Any) -> Optional[Dict[str, Any]]:
        provider = spec.provider

        if provider == "alphavantage":
            if not self._reserve_alpha_quota(fetcher):
                logger.info(
                    "[ProviderProbe] %s quota limited; keeping provider available",
                    provider,
                )
                return record_runtime_result(
                    provider,
                    ok=True,
                    quota_limited=True,
                    source="probe",
                )
            key = self._primary_key(fetcher)
            if not key:
                return record_runtime_result(
                    provider,
                    ok=False,
                    error="api key not configured",
                    source="probe",
                )
            self._note_key_request(fetcher, key)
            self._probe_alpha_vantage(fetcher, spec.stock_code, key)
            return record_runtime_result(provider, ok=True, source="probe")

        if provider == "finnhub":
            key = self._primary_key(fetcher)
            if not key:
                return record_runtime_result(
                    provider,
                    ok=False,
                    error="api key not configured",
                    source="probe",
                )
            self._note_key_request(fetcher, key)
            self._probe_finnhub(fetcher, spec.stock_code, key)
            return record_runtime_result(provider, ok=True, source="probe")

        self._probe_daily_raw(fetcher, spec.stock_code)
        return record_runtime_result(provider, ok=True, source="probe")

    @staticmethod
    def _primary_key(fetcher: Any) -> Optional[str]:
        pool = getattr(fetcher, "_key_pool", None)
        primary = getattr(pool, "primary", None)
        if primary:
            return str(primary)
        value = getattr(fetcher, "_api_key", None)
        return str(value) if value else None

    @staticmethod
    def _note_key_request(fetcher: Any, key: str) -> None:
        pool = getattr(fetcher, "_key_pool", None)
        note = getattr(pool, "note_request", None)
        if callable(note):
            note(key)

    @staticmethod
    def _probe_alpha_vantage(fetcher: Any, stock_code: str, key: str) -> None:
        request = getattr(fetcher, "_request_global_quote")
        data = request(stock_code.strip().upper(), key)
        quote = data.get("Global Quote") if isinstance(data, dict) else None
        if not isinstance(quote, dict) or not quote.get("05. price"):
            raise RuntimeError("empty probe result")

    @staticmethod
    def _probe_finnhub(fetcher: Any, stock_code: str, key: str) -> None:
        request = getattr(fetcher, "_request_quote")
        data = request(stock_code.strip().upper(), key)
        if not isinstance(data, dict) or not data.get("c"):
            raise RuntimeError("empty probe result")

    @staticmethod
    def _probe_daily_raw(fetcher: Any, stock_code: str) -> None:
        start_date, end_date = _probe_date_range()
        frame = fetcher._fetch_raw_data(stock_code, start_date, end_date)
        if frame is None or getattr(frame, "empty", False):
            raise RuntimeError("empty probe result")

    # ------------------------------------------------------------------
    # Alpha Vantage quota ledger
    # ------------------------------------------------------------------

    @property
    def alpha_vantage_quota_path(self) -> Path:
        return self._alpha_vantage_quota_path or default_alpha_vantage_quota_path()

    def _reserve_alpha_quota(self, fetcher: Any) -> bool:
        """Atomically check the daily budget and reserve one probe request."""

        with self._quota_lock, self._alpha_file_lock():
            data = self._read_alpha_quota()
            now = datetime.now(timezone.utc)
            blocked_until = _parse_stamp(data.get("blocked_until"))
            if blocked_until is not None and now < blocked_until:
                return False
            file_count = self._recent_alpha_requests(data, now)
            pool = getattr(fetcher, "_key_pool", None)
            pool_count = 0
            requests_last_day = getattr(pool, "requests_last_day", None)
            primary = getattr(pool, "primary", None)
            if callable(requests_last_day) and primary:
                pool_count = int(requests_last_day(primary) or 0)
            if max(file_count, pool_count) >= _ALPHA_VANTAGE_DAILY_LIMIT:
                return False

            entries = data.setdefault("keys", {})
            entry = entries.setdefault("0", {"requests": []})
            requests = entry.get("requests")
            if not isinstance(requests, list):
                requests = []
            entry["requests"] = _recent_stamps(requests, now)
            entry["requests"].append(_utc_stamp(now))
            self._write_alpha_quota(data)
            return True

    @contextmanager
    def _alpha_file_lock(self):
        path = self.alpha_vantage_quota_path
        lock_stream = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if fcntl is not None:
                lock_stream = open(path.with_name(f"{path.name}.lock"), "a", encoding="utf-8")
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if lock_stream is not None:
                try:
                    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_stream.close()

    def _read_alpha_quota(self) -> Dict[str, Any]:
        path = self.alpha_vantage_quota_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        if not isinstance(raw.get("keys"), dict):
            raw = {"next": 0, "keys": {"0": {"requests": raw.get("requests", [])}}}
        if not isinstance(raw["keys"].get("0"), dict):
            raw["keys"]["0"] = {"requests": []}
        return raw

    def _write_alpha_quota(self, data: Dict[str, Any]) -> None:
        path = self.alpha_vantage_quota_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            os.replace(temporary, path)
        except OSError as exc:
            logger.debug("[ProviderProbe] unable to write Alpha Vantage quota: %s", exc)

    @staticmethod
    def _recent_alpha_requests(data: Dict[str, Any], now: datetime) -> int:
        total = 0
        entries = data.get("keys")
        if not isinstance(entries, dict):
            return 0
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            requests = entry.get("requests")
            if isinstance(requests, list):
                total += len(_recent_stamps(requests, now))
        return total


def _recent_stamps(values: Iterable[Any], now: datetime) -> List[str]:
    cutoff = now - _ALPHA_QUOTA_WINDOW
    recent: List[str] = []
    for value in values:
        parsed = _parse_stamp(value)
        if parsed is not None and parsed > cutoff:
            recent.append(value if isinstance(value, str) else _utc_stamp(parsed))
    return recent


def _probe_date_range() -> tuple[str, str]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_DAYS_BACK)
    return start_date.isoformat(), end_date.isoformat()


def record_real_provider_result(
    provider: Any,
    *,
    ok: bool,
    error: Any = None,
    secrets: Iterable[str] = (),
) -> Optional[Dict[str, Any]]:
    """Record a real business call result in the shared runtime cache."""

    quota_limited = (not ok) and is_quota_limited_error(error)
    return record_runtime_result(
        provider,
        ok=ok or quota_limited,
        error=error,
        quota_limited=quota_limited,
        source="runtime",
        secrets=secrets,
    )
