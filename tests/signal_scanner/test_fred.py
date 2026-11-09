import httpx

from src.services.signal_scanner.adapters.fred_vix_adapter import FredVixAdapter
from src.services.signal_scanner.cli import fixture
from src.services.signal_scanner.core import Settings, now
from src.services.signal_scanner.services.source_data import load_fred
from src.services.signal_scanner.storage.db import one
from src.services.signal_scanner.worker.jobs import Worker
from src.services.signal_scanner.worker.pipeline import latest_workspace


def mock(cal, text, status=200):
    return FredVixAdapter(
        cal, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=text)))
    )


def test_fred_csv_dates_gaps_no_zero_and_idempotence(tmp_path, cal):
    day = cal.last_completed(now())
    previous = cal.shift(day, -1)
    a = mock(cal, f"observation_date,VIXCLS\n{previous},.\n{day},18.25\n")
    settings = Settings(tmp_path)
    first = load_fred(settings, adapter=a)
    second = load_fred(settings, adapter=a)
    assert first["status"] == "SUCCEEDED" and first["latest_value"] == "18.25"
    assert first["missing_sessions"] == [previous]
    assert second["ingest"] == dict(inserted=0, duplicates=1)
    with Worker(settings).db.read() as cx:
        event = one(cx, "SELECT * FROM market_event_revisions WHERE event_type='VIX_VALUE'")
    assert event["source"] == "FRED:VIXCLS" and event["payload_json"]["available_at"] is None
    assert event["payload_json"]["as_of_kind"] == "OBSERVATION_DATE"


def test_fred_current_scan_panic_then_network_failure_unknown(tmp_path, cal):
    settings = Settings(tmp_path)
    fixture(settings)
    day = cal.last_completed(now())
    report = load_fred(settings, scan=True, adapter=mock(cal, f"DATE,VIXCLS\n{day},36.5\n"))
    assert report["scan"]["status"] == "SUCCEEDED", report
    worker = Worker(settings)
    with worker.db.read() as cx:
        j, workspace = latest_workspace(cx, worker.files)
    assert workspace["environment"]["mode"] == "PANIC"

    def fail(request):
        raise httpx.ConnectError("simulated offline")

    broken = FredVixAdapter(cal, httpx.Client(transport=httpx.MockTransport(fail)))
    report = load_fred(settings, scan=True, adapter=broken)
    assert report["health"]["connection"] == "DOWN"
    with worker.db.read() as cx:
        j, workspace = latest_workspace(cx, worker.files)
    assert workspace["environment"]["mode"] == "UNKNOWN"
    assert workspace["environment"]["vix"] is None


def test_fred_denied_no_normal(tmp_path, cal):
    report = load_fred(Settings(tmp_path), adapter=mock(cal, "denied", 403))
    assert report["status"] == "FAILED" and report["health"]["permission"] == "DENIED"


def test_fred_stale_or_negative_not_current(cal):
    last = cal.last_completed(now())
    previous = cal.shift(last, -1)
    result = mock(cal, f"DATE,VIXCLS\n{previous},20\n{last},-1\n").fetch(previous, last)
    assert result["source_report"]["status"] == "PARTIAL"
    assert result["source_report"]["health"]["freshness"] == "STALE"
