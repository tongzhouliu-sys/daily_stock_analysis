import json

import pytest

from src.services.signal_scanner.core import DomainError, Settings, now, uid
from src.services.signal_scanner.cli import fixture
from src.services.signal_scanner.storage.db import insert, one
from src.services.signal_scanner.worker.jobs import Worker, enqueue
from src.services.signal_scanner.worker.pipeline import latest_workspace, calculate


def execute(worker, name):
    with worker.db.transaction() as cx:
        jid = enqueue(cx, name, name + uid(), worker.cal.last_completed(now()))
    assert worker.run_one()
    with worker.db.read() as cx:
        return one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))


def test_invalid_latest_revision_blocks_only_affected_symbol(tmp_path):
    settings = Settings(tmp_path / "f")
    fixture(settings)
    w = Worker(settings)
    path = settings.runtime / "imports" / "market.json"
    pack = json.loads(path.read_text())
    row = next(b for b in reversed(pack["bars"]) if b["instrument_id"] == "DEMO")
    row["close"] = "-1"
    path.write_text(json.dumps(pack))
    job = execute(w, "DAILY_SCAN")
    assert job["status"] == "PARTIAL"
    with w.db.read() as cx:
        j, space = latest_workspace(cx, w.files)
    assert space["coverage"] == dict(expected=2, ok=1, failed=1)
    assert space["failures"]["DEMO"] == "LATEST_REVISION_INVALID"
    assert any(r.get("carried_forward") for r in space["cards"])


def test_source_correction_retracts_preserving_original_evidence(tmp_path):
    settings = Settings(tmp_path / "f")
    fixture(settings)
    w = Worker(settings)
    with w.db.read() as cx:
        old = one(
            cx,
            "SELECT * FROM signal_cards WHERE instrument_id='DEMO' AND family='BREAKOUT' ORDER BY revision DESC LIMIT 1",
        )
    path = settings.runtime / "imports" / "market.json"
    pack = json.loads(path.read_text())
    rows = [b for b in pack["bars"] if b["instrument_id"] == "DEMO"]
    rows[-1]["close"] = rows[-2]["close"]
    rows[-1]["low"] = str(float(rows[-2]["close"]) - 1)
    path.write_text(json.dumps(pack))
    assert execute(w, "DAILY_SCAN")["status"] == "SUCCEEDED"
    with w.db.read() as cx:
        latest = one(
            cx, "SELECT * FROM signal_cards WHERE card_id=? ORDER BY revision DESC LIMIT 1", (old["card_id"],)
        )
        original = one(cx, "SELECT * FROM signal_cards WHERE id=?", (old["id"],))
    assert latest["lifecycle"] == "RETRACTED" and latest["availability"] == "BLOCKED"
    assert original["quality_score"] == old["quality_score"] and original["lifecycle"] == "CONFIRMED"


def test_failures_not_requeued_forever(client, cal):
    from src.services.signal_scanner.storage.db import update

    with client.app.state.db.transaction() as cx:
        jid = enqueue(cx, "DAILY_SCAN", "same-slot", cal.last_completed(now()))
        update(cx, "job_runs", "id", jid, dict(status="FAILED"))
        assert enqueue(cx, "DAILY_SCAN", "same-slot", cal.last_completed(now())) == jid
        assert one(cx, "SELECT COUNT(*) AS n FROM job_runs WHERE logical_key=?", ("same-slot",))["n"] == 1


def test_credentials_rejected_before_raw_archive(client, cal):
    from src.services.signal_scanner.adapters.market import ingest

    with client.app.state.db.transaction() as cx:
        with pytest.raises(DomainError):
            ingest(
                cx,
                dict(
                    provider="x", feed="x", coverage="x", instruments=[], bars=[], api_key="TEST_NOT_A_SECRET"
                ),
                cal,
                client.app.state.files,
            )
    assert not list(client.app.state.files.root.rglob("*.json"))


def test_shadow_cannot_be_modified_by_user(client, cal):
    from tests.signal_scanner.conftest import write, seed_card

    seed_card(client.app.state.db, cal)
    with client.app.state.db.transaction() as cx:
        insert(
            cx,
            "account_state",
            dict(
                account_id="shadow",
                scope="PAPER",
                origin="SHADOW_EXPERIMENT",
                currency="USD",
                profile_json={},
            ),
        )
    r = write(
        client,
        "/api/plans",
        dict(
            intent="DRAFT",
            account_id="shadow",
            entry="100",
            stop="94",
            card_id="card-TEST",
            card_revision=1,
            action_market_date=cal.action_date(now()),
        ),
    )
    assert r.status_code == 422


def test_feature_builder_once_per_instrument(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "f")
    fixture(settings)
    w = Worker(settings)
    with w.db.read() as cx:
        j, space = latest_workspace(cx, w.files)
    manifest = w.files.read(j["input_manifest_ref"])
    import src.services.signal_scanner.worker.pipeline as p

    original = p.build_features
    calls = {}

    def count(*args, **kwargs):
        iid = args[2]
        calls[iid] = calls.get(iid, 0) + 1
        return original(*args, **kwargs)

    monkeypatch.setattr(p, "build_features", count)
    calculate(manifest, w.cal)
    assert len(calls) == 6 and set(calls.values()) == {1}


def test_review_cursor_freezes_cutoff_and_rejects_changed_filters(client, cal):
    from tests.signal_scanner.conftest import seed_card, write

    cid = seed_card(client.app.state.db, cal)
    body = dict(
        card_id=cid,
        card_revision=1,
        action_market_date=cal.action_date(now()),
        action="WAIT",
        reason="分页测试",
        expectation="继续观察",
        invalidation="结构破坏",
        emotion="Calm",
    )
    for _ in range(3):
        assert write(client, "/api/decisions", body).status_code == 200
    first = client.get("/api/reviews?section=DECISIONS&limit=1").json()["data"]
    assert first["total"] == 3 and first["next_cursor"]
    assert write(client, "/api/decisions", body).status_code == 200
    second = client.get(
        "/api/reviews", params=dict(section="DECISIONS", limit=1, cursor=first["next_cursor"])
    )
    assert second.status_code == 200, second.text
    value = second.json()["data"]
    assert value["total"] == 3 and value["items"][0]["id"] != first["items"][0]["id"]
    assert (
        client.get(
            "/api/reviews",
            params=dict(section="DECISIONS", account_id="changed", cursor=first["next_cursor"]),
        ).status_code
        == 409
    )
    assert client.get("/api/reviews?section=DECISIONS&cursor=e30").status_code == 400


def test_privacy_cleanup_removes_notes_cached_responses_and_export(client, cal):
    from tests.signal_scanner.conftest import seed_card, write

    cid = seed_card(client.app.state.db, cal)
    r = write(
        client,
        "/api/decisions",
        dict(
            card_id=cid,
            card_revision=1,
            action_market_date=cal.action_date(now()),
            action="WAIT",
            reason="需要删除的私人笔记",
            expectation="私人预期",
            invalidation="私人条件",
            emotion="Calm",
        ),
    )
    assert r.status_code == 200, r.text
    worker = Worker(client.app.state.settings)
    export = execute(worker, "PRIVACY_EXPORT")
    assert export["status"] == "SUCCEEDED"
    exported_path = worker.files.root / export["output_manifest_ref"]
    assert exported_path.exists()
    with worker.db.transaction() as cx:
        jid = enqueue(
            cx, "PRIVACY_DELETE", uid(), progress=dict(scope="EMOTION_AND_NOTES", export_id=export["id"])
        )
    assert worker.run_one()
    with worker.db.read() as cx:
        job = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
        assert job["status"] == "SUCCEEDED", job["error_json"]
        assert one(cx, "SELECT reason,emotion FROM decisions")["reason"] == "[已删除]"
        assert one(cx, "SELECT COUNT(*) AS n FROM idempotency_requests")["n"] == 0
        assert (
            one(cx, "SELECT output_manifest_ref FROM job_runs WHERE id=?", (export["id"],))[
                "output_manifest_ref"
            ]
            is None
        )
    assert not exported_path.exists()


def test_account_valuation_does_not_skip_invalid_latest_revision(client, cal):
    from src.services.signal_scanner.adapters.market import ingest
    from src.services.signal_scanner.services.accounts import account, derive
    from tests.signal_scanner.conftest import seed_card, write

    seed_card(client.app.state.db, cal)
    r = write(
        client,
        "/api/accounts",
        dict(
            scope="MANUAL",
            origin="MANUAL",
            currency="USD",
            initial_cash="1000",
            start_market_date=cal.action_date(now()),
            initial_positions=[
                dict(instrument_id="TEST", qty="10", avg_price="100", mark="100", stop_reference="94")
            ],
            day_open_equity="2000",
            month_open_equity="2000",
            funds_complete=True,
        ),
    )
    assert r.status_code == 200, r.text
    aid = r.json()["data"]["id"]
    row = dict(
        instrument_id="TEST",
        market_date=cal.last_completed(now()),
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1000",
        is_final=True,
    )
    bundle = dict(provider="TEST", feed="FICTIONAL", coverage="EXPLICIT_TEST", instruments=[], bars=[row])
    with client.app.state.db.transaction() as cx:
        ingest(cx, bundle, cal, client.app.state.files)
        assert derive(cx, account(cx, aid), now())["equity"] == 2010
        row["close"] = "-1"
        ingest(cx, bundle, cal, client.app.state.files)
        state = derive(cx, account(cx, aid), now())
    assert state["equity"] is None
    assert state["positions"][0]["qty"] == "10" and state["positions"][0]["mark"] is None
    assert state["positions"][0]["reason"] == "LATEST_REVISION_INVALID"


def test_capability_layers_block_adapter_calls(client, monkeypatch):
    from src.services.signal_scanner.adapters.market import AlpacaRTH
    from src.services.signal_scanner.cli import activate

    monkeypatch.setattr(
        AlpacaRTH, "fetch", lambda *a, **k: pytest.fail("unentitled or disabled capability called provider")
    )
    worker = Worker(client.app.state.settings)
    for enabled, entitlement in ((False, "GRANTED"), (True, "DENIED")):
        with worker.db.transaction() as cx:
            activate(
                cx,
                "provider",
                dict(
                    mode="ALPACA_RTH",
                    file="market.json",
                    feed="sip",
                    quote_enabled=False,
                    capabilities=[
                        dict(
                            capability_id="RTH_DAILY",
                            support="SUPPORTED",
                            entitlement=entitlement,
                            enabled=enabled,
                            verified_at=None,
                            evidence_ref=None,
                        )
                    ],
                ),
            )
        job = execute(worker, "DAILY_SCAN")
        assert job["status"] == "FAILED" and job["error_json"]["code"] == "SOURCE_PERMISSION_UNVERIFIED"
