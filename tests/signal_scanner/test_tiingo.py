import httpx
import pytest

from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
from src.services.signal_scanner.core import Settings, now
from src.services.signal_scanner.domain.features import adjust
from src.services.signal_scanner.services.source_data import load_tiingo
from src.services.signal_scanner.storage.db import Database, many
from src.services.signal_scanner.storage.files import Artifacts


def adapter(cal, rows, code=200):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(code, json=rows)))
    return TiingoAdapter(cal, key="FICTIONAL_TEST_KEY", client=client)


def rows_for(cal):
    end = cal.last_completed(now())
    days = cal.dates(cal.shift(end, -319), end)
    return [
        dict(
            date=d + "T00:00:00.000Z",
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
            adjOpen=100,
            adjHigh=102,
            adjLow=99,
            adjClose=101,
            adjVolume=1000,
            divCash=1 if n == 200 else 0,
            splitFactor=1,
        )
        for n, d in enumerate(days)
    ]


def test_tiingo_raw_fields_actions_idempotence_and_existing_scan(tmp_path, cal):
    settings = Settings(tmp_path)
    a = adapter(cal, rows_for(cal))
    r = load_tiingo(settings, scan=True, adapter=a)
    assert r["status"] == "SUCCEEDED" and r["count"] == 320 and r["scan"]["status"] == "SUCCEEDED", r
    assert r["rth_scope_verified"] is False
    again = load_tiingo(settings, adapter=a)
    assert again["ingest"]["inserted"] == 0 and again["ingest"]["duplicates"] == 320
    assert again["corporate_actions"]["duplicates"] == 1
    with Database(settings.db_path).read() as cx:
        bars = many(cx, "SELECT * FROM bar_revisions ORDER BY market_date")
        actions = many(cx, "SELECT * FROM market_event_revisions")
    assert len(bars) == 320 and all(b["session"] == "EOD" for b in bars)
    assert bars[200]["quality_json"]["provider_fields"]["divCash"] == "1"
    assert bars[0]["available_at"] is None
    adjusted = adjust(bars, actions)
    assert adjusted[0]["volume"] == 1000 and adjusted[0]["close"] < 101
    assert Artifacts(tmp_path / "artifacts").read(bars[0]["raw_ref"])["provider"] == "TIINGO"


@pytest.mark.parametrize("code,reason", [(403, "SOURCE_PERMISSION_DENIED"), (404, "SOURCE_NO_COVERAGE")])
def test_tiingo_denied_or_no_coverage(tmp_path, cal, code, reason):
    r = load_tiingo(Settings(tmp_path), adapter=adapter(cal, {}, code))
    assert r["status"] == "FAILED" and r["reason_code"] == reason
    if code == 403:
        assert r["health"]["permission"] == "DENIED"


def test_tiingo_bad_row_and_missing_sessions_not_healthy(cal):
    rows = rows_for(cal)
    rows[-1]["high"] = 90
    result = adapter(cal, rows).fetch("SPY", rows[0]["date"][:10], rows[-1]["date"][:10])
    assert result["source_report"]["status"] == "PARTIAL"
    assert result["source_report"]["invalid_rows"]


def test_tiingo_corporate_action_revision_even_when_raw_price_unchanged(tmp_path, cal):
    settings = Settings(tmp_path)
    rows = rows_for(cal)
    load_tiingo(settings, adapter=adapter(cal, rows))
    rows[200]["divCash"] = 2
    r = load_tiingo(settings, adapter=adapter(cal, rows))
    assert r["ingest"]["inserted"] == 1 and r["corporate_actions"]["inserted"] == 1


def test_tiingo_missing_credential_not_fake_normal(tmp_path, cal):
    r = load_tiingo(Settings(tmp_path), adapter=TiingoAdapter(cal, key=""))
    assert r["status"] == "DISABLED" and r["health"]["permission"] == "UNKNOWN"


def test_first_requested_day_dividend_uses_warmup_close(cal):
    end = cal.last_completed(now())
    previous = cal.shift(end, -1)
    seen = []
    rows = [
        dict(date=previous, open=100, high=102, low=99, close=101, volume=1000, divCash=0, splitFactor=1),
        dict(date=end, open=100, high=102, low=99, close=101, volume=1000, divCash=1, splitFactor=1),
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, json=rows))
    )
    pack = TiingoAdapter(cal, key="FICTIONAL", client=client).fetch("LOW", end, end)
    assert seen[0].url.params["startDate"] == previous
    assert len(pack["bars"]) == 1 and pack["bars"][0]["market_date"] == end
    assert pack["warmup_bars"][0]["market_date"] == previous
    action = pack["actions"][0]
    assert action["payload_json"]["previous_market_date"] == previous
    assert action["payload_json"]["price_factor"]
    assert pack["source_report"]["status"] == "SUCCEEDED"
