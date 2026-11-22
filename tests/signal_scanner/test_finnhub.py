from datetime import timedelta

import httpx
import pytest

from src.services.signal_scanner.adapters import base as adapter_base
from src.services.signal_scanner.adapters.finnhub_earnings_adapter import FinnhubEarningsAdapter
from src.services.signal_scanner.core import DomainError, Settings, now
from src.services.signal_scanner.services.source_data import load_finnhub
from src.services.signal_scanner.storage.db import Database, many, one
from src.services.signal_scanner.worker.pipeline import gate_events


def adapter(rows, code=200):
    def handler(request):
        assert "token" not in request.url.params
        assert request.headers["X-Finnhub-Token"] == "FICTIONAL_KEY"
        assert "symbol" not in request.url.params
        return httpx.Response(code, json={"earningsCalendar": rows})

    return FinnhubEarningsAdapter(
        key="FICTIONAL_KEY", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def calendar_row():
    day = (now() + timedelta(days=3)).date().isoformat()
    return [dict(symbol="AAPL", date=day, hour="bmo", year=2026, quarter=4)]


def keyed_client(seen, codes):
    """按 Key 返回预设状态码的客户端，并把实际使用的 Key 记录到 ``seen``。"""
    def respond(request):
        token = request.headers["X-Finnhub-Token"]
        seen.append(token)
        code = codes.get(token, 200)
        if code == 200:
            return httpx.Response(200, json={"earningsCalendar": calendar_row()})
        return httpx.Response(code, json={"error": "invalid api key"})

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_finnhub_hours_actual_time_unknown_idempotent(tmp_path):
    day = (now() + timedelta(days=3)).date().isoformat()
    rows = [
        dict(
            symbol=s,
            date=day,
            hour=h,
            quarter=4,
            year=2026,
            epsActual=1.2,
            epsEstimate=1.1,
            revenueActual=1000,
            revenueEstimate=900,
        )
        for s, h in [("AAPL", "bmo"), ("MSFT", "amc"), ("NVDA", "dmh")]
    ]
    settings = Settings(tmp_path)
    first = load_finnhub(settings, adapter=adapter(rows))
    second = load_finnhub(settings, adapter=adapter(rows))
    assert first["ingest"]["inserted"] == 3 and second["ingest"] == dict(inserted=0, duplicates=3)
    with Database(settings.db_path).read() as cx:
        events = many(cx, "SELECT * FROM market_event_revisions WHERE event_type='EARNINGS'")
    assert {e["payload_json"]["hour"] for e in events} == {"BMO", "AMC", "UNKNOWN"}
    assert all(e["published_at"] is None and e["scheduled_at"] is None for e in events)
    assert first["complete_coverage_verified"] is False


def test_finnhub_empty_is_no_coverage_not_clear(tmp_path):
    report = load_finnhub(Settings(tmp_path), adapter=adapter([]))
    assert report["status"] == "PARTIAL" and report["reason_code"] == "NO_COVERAGE"
    assert set(report["coverage_by_symbol"].values()) == {"NO_COVERAGE"}


def test_finnhub_reschedule_new_revision_not_new_fact(tmp_path):
    day = now().date()
    settings = Settings(tmp_path)
    row = dict(symbol="AAPL", date=(day + timedelta(days=3)).isoformat(), hour="amc", year=2026, quarter=4)
    load_finnhub(settings, adapter=adapter([row]))
    row["date"] = (day + timedelta(days=5)).isoformat()
    report = load_finnhub(settings, adapter=adapter([row]))
    assert report["ingest"]["inserted"] == 1
    with Database(settings.db_path).read() as cx:
        value = one(
            cx, "SELECT COUNT(DISTINCT event_id) AS n,MAX(revision) AS revision FROM market_event_revisions"
        )
    assert value == dict(n=1, revision=2)


def test_overdue_earnings_window_blocks_even_with_partial_coverage(tmp_path, cal):
    day = cal.last_completed(now())
    report = load_finnhub(
        Settings(tmp_path), adapter=adapter([dict(symbol="AAPL", date=day, hour="bmo", year=2026, quarter=4)])
    )
    assert report["ingest"]["inserted"] == 1
    with Database(tmp_path / "scanner.sqlite3").read() as cx:
        events = many(cx, "SELECT * FROM market_event_revisions")
    assert events[0]["status"] == "OVERDUE_UNVERIFIED"
    gate, _ = gate_events("US:AAPL", events, dict(coverage="UNKNOWN"), {}, cal, now())
    assert gate == "BLOCKED"


def test_finnhub_missing_key_no_network(tmp_path):
    report = load_finnhub(Settings(tmp_path), adapter=FinnhubEarningsAdapter(key=""))
    assert report["status"] == "DISABLED" and report["reason_code"] == "CREDENTIAL_NOT_CONFIGURED"


def test_finnhub_denied_is_not_success(tmp_path):
    report = load_finnhub(Settings(tmp_path), adapter=adapter([], 403))
    assert report["status"] == "FAILED" and report["health"]["permission"] == "DENIED"


def test_finnhub_env_accepts_comma_separated_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("FINNHUB_API_KEY", "key_a,key_b")
    seen = []
    subject = FinnhubEarningsAdapter(client=keyed_client(seen, {}))

    assert subject.keys == ["key_a", "key_b"] and subject.key == "key_a"
    report = load_finnhub(Settings(tmp_path), adapter=subject)
    assert report["ingest"]["inserted"] == 1
    assert seen == ["key_a"]


def test_finnhub_single_key_never_switches(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter_base.time, "sleep", lambda seconds: None)
    seen = []
    subject = FinnhubEarningsAdapter(key="only_key", client=keyed_client(seen, {"only_key": 401}))

    assert subject.keys == ["only_key"]
    report = load_finnhub(Settings(tmp_path), adapter=subject)
    assert report["status"] == "FAILED" and report["reason_code"] == "SOURCE_PERMISSION_DENIED"
    assert seen == ["only_key"]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_finnhub_switches_to_next_key_on_key_level_failure(code, monkeypatch, tmp_path, caplog):
    # 429 先由传输层重试，这里跳过等待以保持测试快速
    monkeypatch.setattr(adapter_base.time, "sleep", lambda seconds: None)
    seen = []
    subject = FinnhubEarningsAdapter(
        key="key_a,key_b", client=keyed_client(seen, {"key_a": code})
    )

    report = load_finnhub(Settings(tmp_path), adapter=subject)

    assert report["ingest"]["inserted"] == 1
    assert seen[-1] == "key_b" and set(seen) == {"key_a", "key_b"}
    assert "key_a" not in caplog.text and "key_b" not in caplog.text


def test_finnhub_all_keys_failing_reports_permission_denied(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(adapter_base.time, "sleep", lambda seconds: None)
    seen = []
    subject = FinnhubEarningsAdapter(
        key="key_a,key_b", client=keyed_client(seen, {"key_a": 401, "key_b": 401})
    )

    report = load_finnhub(Settings(tmp_path), adapter=subject)

    assert report["status"] == "FAILED" and report["reason_code"] == "SOURCE_PERMISSION_DENIED"
    assert report["health"]["permission"] == "DENIED"
    assert seen == ["key_a", "key_b"]
    assert "key_a" not in caplog.text and "key_b" not in caplog.text


def test_finnhub_all_keys_rate_limited_raises_domain_error(monkeypatch):
    monkeypatch.setattr(adapter_base.time, "sleep", lambda seconds: None)
    seen = []
    subject = FinnhubEarningsAdapter(
        key="key_a,key_b", client=keyed_client(seen, {"key_a": 429, "key_b": 429})
    )

    with pytest.raises(DomainError) as info:
        subject.fetch(["AAPL"], "2026-01-01", "2026-01-31")

    assert info.value.code == "SOURCE_RATE_LIMITED"
    assert set(seen) == {"key_a", "key_b"}
    assert "key_a" not in str(info.value) and "key_b" not in str(info.value)


def test_finnhub_network_error_does_not_switch_key(monkeypatch):
    monkeypatch.setattr(adapter_base.time, "sleep", lambda seconds: None)
    seen = []

    def handler(request):
        seen.append(request.headers["X-Finnhub-Token"])
        raise httpx.ConnectError("network down")

    subject = FinnhubEarningsAdapter(
        key="key_a,key_b", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(DomainError) as info:
        subject.fetch(["AAPL"], "2026-01-01", "2026-01-31")

    assert info.value.code == "SOURCE_UNREACHABLE"
    assert set(seen) == {"key_a"}

