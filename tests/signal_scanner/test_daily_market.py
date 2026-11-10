from datetime import timedelta

import httpx
import pytest

from src.services.signal_scanner.adapters.alphavantage_adapter import AlphaVantageAdapter
from src.services.signal_scanner.adapters.market import ingest
from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
from src.services.signal_scanner.core import DomainError, Settings, digest, now, stamp
from src.services.signal_scanner.services.daily_market import DailyMarket, TiingoPoolBudget, read_json, scan_token, write_json
from src.services.signal_scanner.services.source_data import load_tiingo
from src.services.signal_scanner.storage.db import Database, many
from src.services.signal_scanner.worker.jobs import Worker


def raw_rows(cal, start, end):
    return [
        dict(date=d, open=100, high=102, low=99, close=101, volume=1000, divCash=0, splitFactor=1)
        for d in cal.dates(start, end)
    ]


@pytest.fixture
def daily(tmp_path, cal, monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEYS", raising=False)
    settings = Settings(tmp_path)
    target = cal.last_completed(now())
    rows = raw_rows(cal, cal.shift(target, -319), target)
    adapter = TiingoAdapter(
        cal,
        key="FICTIONAL",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=rows))),
    )
    assert load_tiingo(settings, scan=True, adapter=adapter)["status"] == "SUCCEEDED"

    class Fred:
        def fetch(self, start, end):
            from src.services.signal_scanner.adapters.base import source_report

            return {
                "events": [],
                "source_report": source_report(
                    "FRED", "VIX_HISTORY", True, success=True, freshness="STALE", reason="SOURCE_DATA_STALE"
                ),
            }

    return settings, DailyMarket(settings, tiingo=adapter, fred=Fred())


def test_daily_refresh_is_resumable_idempotent_and_preserves_protected_state(daily):
    settings, collector = daily
    db = Database(settings.db_path)
    protected = ("ledger_entries", "account_snapshots", "account_state", "notifications")
    with db.read() as cx:
        before = {t: digest(many(cx, "SELECT * FROM " + t)) for t in protected}
    assert scan_token(settings, collector.cal.last_completed(now())) is None
    first = collector.step(force=True)
    assert first["status"] == "RUNNING" and first["completed"] == 1
    # A new process reads the same staged inputs instead of redownloading.
    second = DailyMarket(settings, tiingo=collector.tiingo, fred=collector.fred).step(force=True)
    assert second["status"] == "SUCCEEDED" and second["fred"]["reason_code"] == "SOURCE_DATA_STALE"
    assert scan_token(settings, second["target_date"])
    with db.read() as cx:
        reports = len(many(cx, "SELECT id FROM job_runs WHERE artifact_kind='DATA_SOURCE'"))
    assert collector.step(force=True)["cache_hash"] == second["cache_hash"]
    with db.read() as cx:
        assert len(many(cx, "SELECT id FROM job_runs WHERE artifact_kind='DATA_SOURCE'")) == reports
        assert before == {t: digest(many(cx, "SELECT * FROM " + t)) for t in protected}


def test_budget_survives_restart_and_counts_transport_attempts(tmp_path):
    moment = now()
    path = tmp_path / "quota.json"
    write_json(path, {"requests": [stamp(moment)] * 45})
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(DomainError, match="SOURCE_LOCAL_RATE_BUDGET"):
        TiingoPoolBudget(path, client, ["FICTIONAL"], lambda: moment).get("https://example.invalid")
    assert not calls
    TiingoPoolBudget(path, client, ["FICTIONAL"], lambda: moment + timedelta(hours=1, seconds=1)).get(
        "https://example.invalid"
    )
    assert len(calls) == 1 and len(read_json(path)["keys"]["0"]["requests"]) == 1


def test_daily_publish_accepts_replayed_bar_with_a_newer_revision(daily):
    settings, collector = daily
    target = collector.cal.last_completed(now())
    assert collector.step(force=True)["status"] == "RUNNING"
    state = read_json(settings.runtime / "daily-market" / target / "state.json")
    part = collector.files.read(next(iter(state["parts"].values())))
    bundle = read_json(settings.runtime / "imports" / state["cache_file"])
    bar = part["bars"][-1]
    corrected = {**bar, "close": "100.5"}
    with collector.db.transaction() as cx:
        assert ingest(cx, {**bundle, "bars": [bar]}, collector.cal, collector.files)["inserted"] == 1
        assert ingest(cx, {**bundle, "bars": [corrected]}, collector.cal, collector.files)["inserted"] == 1

    assert collector.step(force=True)["status"] == "SUCCEEDED"
    assert scan_token(settings, target)
    with collector.db.read() as cx:
        revisions = many(cx, "SELECT close FROM bar_revisions WHERE market_date=? ORDER BY revision", (target,))
    assert [row["close"] for row in revisions][-2:] == [str(bar["close"]), corrected["close"]]


def test_share_class_uses_provider_symbol_but_preserves_internal_identity(cal):
    end = cal.last_completed(now())
    requests = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: requests.append(r) or httpx.Response(200, json=raw_rows(cal, end, end))
        )
    )
    result = TiingoAdapter(cal, key="FICTIONAL", client=client).fetch("BRK.B", end, end)
    assert requests[0].url.path.endswith("/brk-b/prices")
    assert result["bars"][0]["instrument_id"] == "US:BRK.B"


def test_scheduler_defers_scan_until_collection_commits(daily, monkeypatch):
    settings, collector = daily
    worker = Worker(settings)
    monkeypatch.setenv("SCANNER_DAILY_MARKET", "true")
    monkeypatch.setattr(worker, "run_one", lambda: False)
    monkeypatch.setattr(worker, "deliver", lambda: None)
    worker.tick()
    with worker.db.read() as cx:
        assert not many(cx, "SELECT id FROM job_runs WHERE logical_key LIKE '%:FRESH:%'")
        assert not many(cx, "SELECT id FROM job_runs WHERE job_name='DIGEST'")
    collector.step(force=True)
    collector.step(force=True)
    worker.tick()
    with worker.db.read() as cx:
        fresh = many(cx, "SELECT * FROM job_runs WHERE logical_key LIKE '%:FRESH:%'")
        assert {j["job_name"] for j in fresh} == {"DAILY_SCAN", "VERIFY"}
        assert not many(cx, "SELECT id FROM job_runs WHERE job_name='DIGEST'")
    worker.tick()
    with worker.db.read() as cx:
        assert len(many(cx, "SELECT id FROM job_runs WHERE logical_key LIKE '%:FRESH:%'")) == 2


def test_alphavantage_rotates_valid_keys_skips_limited_and_retains_provenance(tmp_path, cal):
    end = cal.last_completed(now())
    body = {
        "Time Series (Daily)": {
            end: {"1. open": "100", "2. high": "102", "3. low": "99", "4. close": "101", "5. volume": "1000"}
        }
    }
    used = []

    def handle(request):
        key = request.url.params["apikey"]
        used.append(key)
        return httpx.Response(403 if key == "FICTIONAL_1" else 200, json=body)

    path = tmp_path / "av.json"
    adapter = AlphaVantageAdapter(
        cal,
        path,
        keys=["FICTIONAL_1", "FICTIONAL_2", "FICTIONAL_3"],
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    first = adapter.fetch("SPY", end, end)
    adapter.fetch("SPY", end, end)
    assert used == ["FICTIONAL_1", "FICTIONAL_2", "FICTIONAL_3"]
    assert first["actions"] == [] and first["source_report"]["corporate_action_coverage"] == "UNVERIFIED"
    assert first["bars"][0]["provider_fields"]["source_provider"] == "ALPHA_VANTAGE"
    assert "FICTIONAL" not in path.read_text()


def test_daily_fallback_changes_lineage_and_keeps_eligibility_blocked(daily):
    settings, collector = daily

    class Limited:
        def fetch(self, *args):
            raise DomainError("SOURCE_LOCAL_RATE_BUDGET")

    class Fallback:
        def fetch(self, symbol, start, end):
            pack = collector.tiingo.fetch(symbol, start, end)
            pack["provider"] = "ALPHA_VANTAGE"
            pack["actions"] = []
            pack["source_report"]["source"] = "ALPHA_VANTAGE"
            for b in pack["bars"]:
                b["provider_fields"] = {
                    "source_provider": "ALPHA_VANTAGE",
                    "corporate_action_coverage": "UNVERIFIED",
                }
            return pack

    fallback = DailyMarket(settings, tiingo=Limited(), fallback=Fallback(), fred=collector.fred)
    fallback.step(force=True)
    assert fallback.step(force=True)["status"] == "SUCCEEDED"
    cache = read_json(settings.runtime / "imports/tiingo_spy.json")
    assert cache["provider"] == "LOCAL_EOD_MERGED"
    assert cache["daily_collection"]["fallback_corporate_action_coverage"] == "UNVERIFIED"
    with Database(settings.db_path).read() as cx:
        bars = many(cx, "SELECT * FROM bar_revisions WHERE provider='LOCAL_EOD_MERGED' ORDER BY market_date")
        assert bars[-1]["quality_json"]["provider_fields"]["source_provider"] == "ALPHA_VANTAGE"
        assert len(bars) == 320


def test_publication_recovers_after_cache_write_before_status_commit(daily):
    settings, collector = daily
    collector.step(force=True)
    result = collector.step(force=True)
    path = settings.runtime / "daily-market" / result["target_date"] / "state.json"
    state = read_json(path)
    state["status"] = "RUNNING"
    state.pop("cache_hash")
    write_json(path, state)
    recovered = collector.step(force=True)
    assert recovered["status"] == "SUCCEEDED" and recovered["cache_hash"] == result["cache_hash"]


def test_shared_daily_limit_does_not_rotate_to_bypass_provider_limit(tmp_path, cal):
    end = cal.last_completed(now())
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json={"Information": "Daily limit"})
        )
    )
    adapter = AlphaVantageAdapter(
        cal, tmp_path / "quota.json", keys=["FICTIONAL_1", "FICTIONAL_2"], client=client
    )
    with pytest.raises(DomainError, match="SOURCE_RATE_LIMITED"):
        adapter.fetch("SPY", end, end)
    with pytest.raises(DomainError, match="SOURCE_FALLBACK_BUDGET_EXHAUSTED"):
        adapter.fetch("SPY", end, end)
    assert len(calls) == 1


def test_tiingo_pool_preserves_existing_budget_and_rotates_new_slot(tmp_path):
    moment = now()
    path = tmp_path / "quota.json"
    write_json(path, {"requests": [stamp(moment)] * 45})
    used = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: used.append(request.headers["Authorization"]) or httpx.Response(200)
        )
    )
    pool = TiingoPoolBudget(path, client, ["FICTIONAL_1", "FICTIONAL_2"], lambda: moment)
    pool.get("https://example.invalid")
    assert used == ["Token FICTIONAL_2"]
    assert len(read_json(path)["keys"]["0"]["requests"]) == 45
    assert len(read_json(path)["keys"]["1"]["requests"]) == 1
    assert "FICTIONAL" not in path.read_text()
    write_json(path, {"next": 0, "keys": {"0": {"requests": []}, "1": {"requests": []}}})
    pool.get("https://example.invalid")
    pool.get("https://example.invalid")
    assert used[-2:] == ["Token FICTIONAL_1", "Token FICTIONAL_2"]


def test_new_tiingo_slot_resumes_persisted_rate_wait(daily, monkeypatch):
    import json

    settings, collector = daily
    monkeypatch.setenv("TIINGO_API_KEYS", json.dumps(["FICTIONAL_1"]))
    write_json(collector.root / "tiingo-quota.json", {"requests": [stamp()] * 45})

    class Limited:
        def fetch(self, *args):
            raise DomainError("SOURCE_LOCAL_RATE_BUDGET")

    waiting = DailyMarket(settings, tiingo=Limited(), fred=collector.fred)
    first = waiting.step(force=True)
    assert first["completed"] == 0 and first["next_attempt_at"]
    monkeypatch.setenv("TIINGO_API_KEYS", json.dumps(["FICTIONAL_1", "FICTIONAL_2"]))
    resumed = collector.step(force=True)
    assert resumed["completed"] == 1 and resumed["next_attempt_at"] is None
