"""Resumable, rate-limited daily EOD collection for the existing scanner universe.

Credentials stay in process configuration. Inputs retain provider provenance and
never upgrade EOD, corporate-action or event coverage to verified RTH/PIT data.
"""

from __future__ import annotations

from src.services.signal_scanner.platform_support import locking as fcntl
import json
import os
from datetime import timedelta

import httpx

from src.services.signal_scanner.adapters.base import source_report
from src.services.signal_scanner.adapters.fred_vix_adapter import FredVixAdapter
from src.services.signal_scanner.adapters.market import ingest
from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
from src.services.signal_scanner.core import DomainError, canonical, digest, instant, now, require, stamp
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.services.source_data import record_report, store_events
from src.services.signal_scanner.storage.db import Database, audit, many
from src.services.signal_scanner.storage.files import Artifacts


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def scan_token(settings, target):
    state = read_json(settings.runtime / "daily-market" / target / "state.json", {})
    if state.get("status") in ("SUCCEEDED", "PARTIAL") and state.get("cache_hash"):
        return state["cache_hash"][:16]
    return None


def tiingo_credentials():
    keys = json.loads(os.environ.get("TIINGO_API_KEYS", "[]"))
    if not keys and os.environ.get("TIINGO_API_KEY"):
        keys = [os.environ["TIINGO_API_KEY"]]
    require(
        isinstance(keys, list)
        and 0 < len(keys) <= 10
        and all(isinstance(k, str) and k for k in keys)
        and len(set(keys)) == len(keys),
        "CREDENTIAL_NOT_CONFIGURED",
        status=503,
    )
    return keys


def tiingo_ready_at(path, current, count):
    quota = read_json(path, {})
    slots = quota.get("keys", {"0": {"requests": quota.get("requests", [])}})
    ready = []
    for index in range(count):
        entry = slots.get(str(index), {})
        times = sorted(
            instant(t) for t in entry.get("requests", []) if instant(t) > current - timedelta(hours=1)
        )
        point = times[0] + timedelta(hours=1, seconds=5) if len(times) >= 45 else current
        if entry.get("cooldown_until"):
            point = max(point, instant(entry["cooldown_until"]))
        ready.append(point)
    return min(ready)


class TiingoPoolBudget:
    """Round-robin valid credentials, with a durable hourly budget for each slot."""

    def __init__(self, path, client, keys, at=now):
        self.path, self.client, self.keys, self.at = path, client, keys, at

    def get(self, *args, **kwargs):
        current = self.at()
        quota = read_json(self.path, {"requests": []})
        if "keys" not in quota:
            quota = {"next": 0, "keys": {"0": {"requests": quota.get("requests", [])}}}
        starting_index = quota.get("next", 0)
        denied = False
        for offset in range(len(self.keys)):
            index = (starting_index + offset) % len(self.keys)
            entry = quota["keys"].setdefault(str(index), {"requests": []})
            entry["requests"] = [t for t in entry["requests"] if instant(t) > current - timedelta(hours=1)]
            if len(entry["requests"]) >= 45:
                continue
            if entry.get("cooldown_until") and current < instant(entry["cooldown_until"]):
                continue
            entry["requests"].append(stamp(current))
            quota["next"] = (index + 1) % len(self.keys)
            write_json(self.path, quota)
            headers = {**kwargs.get("headers", {}), "Authorization": "Token " + self.keys[index]}
            response = self.client.get(*args, **{**kwargs, "headers": headers})
            if response.status_code in (401, 403, 429):
                entry["cooldown_until"] = stamp(current + timedelta(hours=1))
                denied = denied or response.status_code in (401, 403)
                write_json(self.path, quota)
                continue
            if response.status_code == 200:
                entry["successful_requests"] = entry.get("successful_requests", 0) + 1
                write_json(self.path, quota)
            return response
        raise DomainError("SOURCE_PERMISSION_DENIED" if denied else "SOURCE_LOCAL_RATE_BUDGET", status=503)


class DailyMarket:
    def __init__(self, settings, *, at=now, tiingo=None, fred=None, fallback=None):
        self.settings, self.at = settings, at
        self.root = settings.runtime / "daily-market"
        self.db, self.cal = Database(settings.db_path), Calendar()
        self.files = Artifacts(settings.runtime / "artifacts")
        self.tiingo, self.fred = tiingo, fred
        self.fallback = fallback
        if fallback is None and os.environ.get("ALPHAVANTAGE_API_KEYS"):
            from src.services.signal_scanner.adapters.alphavantage_adapter import AlphaVantageAdapter

            self.fallback = AlphaVantageAdapter(self.cal, self.root / "alphavantage-quota.json", at=at)

    def _fetch(self, item, state, target):
        try:
            if self.tiingo is not None:
                return self.tiingo.fetch(item["ticker"], state["starts"][item["id"]], target)
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                keys = tiingo_credentials()
                budget = TiingoPoolBudget(self.root / "tiingo-quota.json", client, keys, self.at)
                return TiingoAdapter(self.cal, key=keys[0], client=budget).fetch(
                    item["ticker"], state["starts"][item["id"]], target
                )
        except DomainError as exc:
            if self.fallback and exc.code in (
                "SOURCE_LOCAL_RATE_BUDGET",
                "SOURCE_RATE_LIMITED",
                "SOURCE_UNREACHABLE",
                "SOURCE_UNAVAILABLE",
                "SOURCE_PERMISSION_DENIED",
                "SOURCE_NO_COVERAGE",
                "SOURCE_HISTORY_INCOMPLETE",
                "SOURCE_BUDGET_EXCEEDED",
            ):
                return self.fallback.fetch(item["ticker"], state["starts"][item["id"]], target)
            raise

    def status(self):
        target = self.cal.last_completed(self.at())
        value = read_json(self.root / target / "state.json", {})
        return {
            "target_date": target,
            "status": value.get("status", "NOT_STARTED"),
            "completed": len(value.get("parts", {})),
            "expected": len(value.get("instruments", [])),
            "failed": value.get("failures", {}),
            "next_attempt_at": value.get("next_attempt_at"),
            "completed_at": value.get("completed_at"),
            "cache_hash": value.get("cache_hash"),
            "last_error": value.get("last_error"),
            "fred": value.get("fred"),
        }

    def step(self, *, force=False):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "collector.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "ALREADY_RUNNING"}
            return self._step(force=force)

    def _step(self, *, force=False):
        current = self.at()
        target = self.cal.last_completed(current)
        # Tiingo normally publishes after 17:30 ET; use 18:00 ET, including early closes.
        from src.services.signal_scanner.domain.calendar import NY

        release_at = (
            instant(self.cal.session(target)["close_at"])
            .astimezone(NY)
            .replace(hour=18, minute=0, second=0, microsecond=0)
        )
        if not force and current < release_at:
            return {"status": "WAITING_FOR_EOD", "target_date": target, "ready_after": stamp(release_at)}
        folder = self.root / target
        state_path = folder / "state.json"
        state = read_json(state_path)
        if state and state.get("status") in ("SUCCEEDED", "PARTIAL"):
            return self.status()
        if not state:
            with self.db.read() as cx:
                config = get_config(cx, "provider")["value"]
                pool = get_config(cx, "universe")["value"]["members"]
                instruments = many(cx, "SELECT * FROM instruments WHERE status='ACTIVE'")
            require(config["mode"] == "TIINGO_EOD_CACHE", "DAILY_SOURCE_MODE_UNSUPPORTED", status=503)
            cache_path = self.settings.runtime / "imports" / config["file"]
            cache = read_json(cache_path)
            require(cache is not None, "SOURCE_CACHE_MISSING", status=503)
            cache_ids = {i["id"] for i in cache["instruments"]}
            require(set(pool) <= cache_ids, "SOURCE_UNIVERSE_MISMATCH", status=503)
            instruments = [i for i in instruments if i["id"] in cache_ids]
            require(0 < len(instruments) <= 500, "SOURCE_UNIVERSE_OUT_OF_RANGE", status=503)
            # Context ETFs and formerly stale share classes are refreshed first.
            instruments.sort(
                key=lambda i: (i["asset_type"] != "ETF", "." not in i["current_ticker"], i["id"])
            )
            latest = {}
            for bar in cache["bars"]:
                iid = bar["instrument_id"]
                latest[iid] = max(latest.get(iid, bar["market_date"]), bar["market_date"])
            state = {
                "target_date": target,
                "status": "RUNNING",
                "started_at": stamp(current),
                "cache_file": config["file"],
                "previous_cache_hash": digest(cache),
                "universe_hash": digest(sorted(pool)),
                "instruments": [{"id": i["id"], "ticker": i["current_ticker"]} for i in instruments],
                "starts": {
                    i["id"]: self.cal.shift(latest[i["id"]], -2)
                    if i["id"] in latest
                    else self.cal.shift(target, -319)
                    for i in instruments
                },
                "parts": {},
                "failures": {},
                "attempts": {},
            }
            write_json(state_path, state)
        if state.get("next_attempt_at") and current < instant(state["next_attempt_at"]):
            error = state.get("last_error", {}).get("code", "")
            if error in (
                "SOURCE_LOCAL_RATE_BUDGET",
                "SOURCE_RATE_LIMITED",
                "SOURCE_FALLBACK_BUDGET_EXHAUSTED",
            ):
                ready = tiingo_ready_at(self.root / "tiingo-quota.json", current, len(tiingo_credentials()))
                if ready <= current:
                    state.pop("next_attempt_at", None)
                else:
                    return self.status()
            else:
                return self.status()
        pending = [
            i
            for i in state["instruments"]
            if i["id"] not in state["parts"] and i["id"] not in state["failures"]
        ]
        if not pending:
            return self._publish(folder, state)
        item = pending[0]
        try:
            pack = self._fetch(item, state, target)
            require(pack["source_report"]["status"] == "SUCCEEDED", "SOURCE_HISTORY_INCOMPLETE", status=503)
            # Retain the actual data provider on every appended/revised row.
            for bar in pack["bars"]:
                bar.setdefault("provider_fields", {})["source_provider"] = pack["source_report"]["source"]
            ref = self.files.save(pack, "daily-market-parts")
            state["parts"][item["id"]] = ref
            state.pop("last_error", None)
            state.pop("next_attempt_at", None)
        except DomainError as exc:
            state["last_error"] = {"instrument_id": item["id"], "code": exc.code}
            if exc.code in (
                "SOURCE_LOCAL_RATE_BUDGET",
                "SOURCE_RATE_LIMITED",
                "SOURCE_FALLBACK_BUDGET_EXHAUSTED",
            ):
                try:
                    ready = tiingo_ready_at(
                        self.root / "tiingo-quota.json", current, len(tiingo_credentials())
                    )
                except DomainError:
                    ready = current + timedelta(hours=1)
                retry = max(current + timedelta(minutes=5), ready)
                state["next_attempt_at"] = stamp(retry)
            elif exc.code in ("SOURCE_PERMISSION_DENIED", "CREDENTIAL_NOT_CONFIGURED"):
                state["next_attempt_at"] = stamp(current + timedelta(minutes=30))
            else:
                attempts = state["attempts"].get(item["id"], 0) + 1
                state["attempts"][item["id"]] = attempts
                if attempts >= 3:
                    state["failures"][item["id"]] = exc.code
                else:
                    state["next_attempt_at"] = stamp(current + timedelta(minutes=5))
        write_json(state_path, state)
        return self.status()

    def _publish(self, folder, state):
        cache_path = self.settings.runtime / "imports" / state["cache_file"]
        cache = read_json(cache_path)
        if state.get("publishing_hash") and digest(cache) == state["publishing_hash"]:
            state.update(
                status="PARTIAL" if state["failures"] else "SUCCEEDED",
                cache_hash=state["publishing_hash"],
                completed_at=stamp(self.at()),
            )
            write_json(folder / "state.json", state)
            return self.status()
        require(digest(cache) == state["previous_cache_hash"], "SOURCE_CACHE_CHANGED", status=409)
        with self.db.read() as cx:
            pool = get_config(cx, "universe")["value"]["members"]
        require(digest(sorted(pool)) == state["universe_hash"], "SOURCE_UNIVERSE_CHANGED", status=409)
        parts = [self.files.read(ref) for ref in state["parts"].values()]
        fallback_parts = [p for p in parts if p["source_report"]["source"] == "ALPHA_VANTAGE"]
        primary_parts = [p for p in parts if p["source_report"]["source"] == "TIINGO"]
        previous_provider = cache["provider"]
        if fallback_parts:
            cache["provider"] = "LOCAL_EOD_MERGED"
            cache["coverage"] = "EXPLICIT_MIXED_EOD_PROVIDER_DECLARED"
        bars = {(b["instrument_id"], b["market_date"]): b for b in cache["bars"]}
        for pack in parts:
            for bar in pack["bars"]:
                bars[(bar["instrument_id"], bar["market_date"])] = bar
        cache["bars"] = [b for _, b in sorted(bars.items())]
        cache["daily_collection"] = {
            "target_date": state["target_date"],
            "sources": {"TIINGO": len(primary_parts), "ALPHA_VANTAGE": len(fallback_parts)},
            "observed_at": stamp(self.at()),
            "failures": state["failures"],
            "fallback_corporate_action_coverage": "UNVERIFIED" if fallback_parts else "NOT_USED",
        }
        cache_hash = digest(cache)
        report = source_report(
            "TIINGO",
            "EOD_DAILY",
            True,
            success=True,
            reason="SOURCE_PARTIAL_COVERAGE"
            if state["failures"]
            else "SOURCE_FALLBACK_USED"
            if fallback_parts
            else None,
            freshness="STALE" if state["failures"] else "OK",
            quality="EOD_PROVIDER_DECLARED_RTH_UNVERIFIED",
            count=sum(len(p["bars"]) for p in primary_parts),
            latest_market_date=state["target_date"] if not state["failures"] else None,
            expected_symbols=len(state["instruments"]),
            updated_symbols=len(primary_parts),
            fallback_symbols=len(fallback_parts),
            failures=state["failures"],
            rth_scope_verified=False,
            corporate_actions="PROVIDER_DECLARED",
            part_refs=list(state["parts"].values()),
        )
        if state["failures"] or fallback_parts:
            report["status"] = "PARTIAL"
        try:
            fred = (self.fred or FredVixAdapter(self.cal)).fetch(
                self.cal.shift(state["target_date"], -319), state["target_date"]
            )
        except DomainError as exc:
            fred = {
                "events": [],
                "source_report": source_report("FRED", "VIX_HISTORY", True, reason=exc.code),
            }
        write_json(folder / "previous-cache.json", read_json(cache_path))
        state["publishing_hash"] = cache_hash
        state["fred"] = {
            k: fred["source_report"].get(k) for k in ("status", "latest_market_date", "reason_code")
        }
        write_json(folder / "state.json", state)
        # A lineage change imports historical rows under the new explicit source.
        # Short transactions keep the API and worker responsive during that first import.
        delta_bars = (
            cache["bars"] if previous_provider != cache["provider"] else [b for p in parts for b in p["bars"]]
        )
        counts = {"inserted": 0, "duplicates": 0, "quarantined": 0}
        for offset in range(0, len(delta_bars), 5000):
            with self.db.transaction() as cx:
                batch = ingest(
                    cx, {**cache, "bars": delta_bars[offset : offset + 5000]}, self.cal, self.files
                )
                require(batch["quarantined"] == 0, "SOURCE_INVALID_ROWS", status=503)
                for key in counts:
                    counts[key] += batch[key]
        with self.db.transaction() as cx:
            store_events(cx, [e for p in parts for e in p["actions"]], "TIINGO")
            record_report(cx, self.files, {**report, "ingest": counts})
            if fallback_parts:
                record_report(
                    cx,
                    self.files,
                    source_report(
                        "ALPHA_VANTAGE",
                        "EOD_DAILY",
                        True,
                        success=True,
                        freshness="OK",
                        quality="EOD_ACTIONS_UNVERIFIED",
                        count=sum(len(p["bars"]) for p in fallback_parts),
                        latest_market_date=state["target_date"],
                        updated_symbols=len(fallback_parts),
                        rth_scope_verified=False,
                        corporate_action_coverage="UNVERIFIED",
                    ),
                )
            store_events(cx, fred["events"], "FRED:VIXCLS")
            record_report(cx, self.files, fred["source_report"])
            audit(
                cx,
                "DAILY_MARKET_REFRESH",
                state["target_date"],
                "自动补齐既有标的池日线；保留研究与执行限制",
                expected=len(state["instruments"]),
                updated=len(parts),
                cache_hash=cache_hash,
            )
        # Readiness is published only after both database and cache commit.
        write_json(cache_path, cache)
        state.update(
            status="PARTIAL" if state["failures"] else "SUCCEEDED",
            cache_hash=cache_hash,
            completed_at=stamp(self.at()),
            fred={k: fred["source_report"].get(k) for k in ("status", "latest_market_date", "reason_code")},
        )
        state.pop("next_attempt_at", None)
        write_json(folder / "state.json", state)
        return self.status()
