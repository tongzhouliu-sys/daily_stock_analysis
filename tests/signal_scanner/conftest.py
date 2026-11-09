import pytest
from fastapi.testclient import TestClient

from src.services.signal_scanner.core import Settings, digest, now, stamp, uid
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.main import create_app
from src.services.signal_scanner.storage.db import insert


@pytest.fixture(scope="session")
def cal():
    return Calendar()


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path))
    app.state.auth.provision()
    c = TestClient(app)
    password = (tmp_path / "首次登录密码.txt").read_text().strip()
    result = c.post(
        "/api/auth/login", json={"password": password}, headers={"Origin": "http://testserver"}
    )
    assert result.status_code == 200
    c.headers.update({"Origin": "http://testserver", "X-CSRF-Token": result.json()["data"]["csrf_token"]})
    c.app = app
    return c


def write(client, path, value, method="POST", key=None):
    headers = {"Idempotency-Key": key or uid()}
    if "expected_revision" in value:
        headers["If-Match"] = str(value["expected_revision"])
    return client.request(method, path, json=value, headers=headers)


def seed_card(db, cal, symbol="TEST"):
    at = stamp()
    day = cal.last_completed(now())
    end = cal.session(cal.shift(day, 3))["close_at"]
    cid = "card-" + symbol
    eid = "evt-" + symbol
    jid = "input-" + symbol
    with db.transaction() as cx:
        insert(
            cx,
            "instruments",
            dict(
                id=symbol,
                current_ticker=symbol,
                display_name="虚构验证证券",
                primary_venue="XNYS",
                asset_type="COMMON",
                currency="USD",
                sector_code="TECH",
                profile_json=dict(market_cap="10000000000"),
                reference_roles_json=["TRADE"],
            ),
        )
        insert(
            cx,
            "job_runs",
            dict(id=jid, logical_key=jid, revision=1, job_name="DAILY_SCAN", status="SUCCEEDED"),
        )
        insert(
            cx,
            "rule_events",
            dict(
                id=uid(),
                event_id=eid,
                revision=1,
                instrument_id=symbol,
                rule_id="TR-01",
                rule_version="1.0.0",
                family="BREAKOUT",
                kind="DIRECTION",
                direction="LONG",
                trigger_market_date=day,
                confirmation_market_date=day,
                triggered_at=at,
                confirmed_at=at,
                lifecycle="CONFIRMED",
                valid_until=end,
                frozen_json=dict(entry_anchor=100, atr=2, level=98),
                evidence_json=dict(close=100, rv=2),
                stage="APPROVED",
                input_run_id=jid,
                content_hash=digest(eid),
            ),
        )
        insert(
            cx,
            "signal_cards",
            dict(
                id=uid(),
                card_id=cid,
                revision=1,
                view_kind="PRODUCTION",
                instrument_id=symbol,
                family="BREAKOUT",
                direction="LONG",
                trigger_market_date=day,
                rule_refs_json=[dict(event_id=eid, revision=1)],
                primary_rule_ref=eid,
                quality_score=85,
                quality_grade="HIGH",
                effective_grade="HIGH",
                evidence_groups_json=["PRICE", "VOLUME", "PARTICIPATION"],
                lifecycle="CONFIRMED",
                availability="PLAN_ALLOWED",
                reason_codes_json=[],
                score_trace_json=[],
                context_json=dict(
                    entry_anchor="100",
                    atr_ref="2",
                    price_basis="RAW",
                    adjustment_epoch="epoch",
                    features=dict(source_market_date=day),
                    source_market_date=day,
                    event_status="CLEAR",
                    status_verified_at=at,
                    environment_mode="NORMAL",
                    price_context=dict(basis="EOD_REFERENCE", observed_at=at),
                ),
                evidence_hash=digest(symbol),
                valid_until=end,
                data_as_of=at,
                input_run_id=jid,
                content_hash=digest(cid),
            ),
        )
    return cid
