from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal


from src.services.signal_scanner.core import digest, now, stamp
from src.services.signal_scanner.storage.db import insert, many, one, update
from src.services.signal_scanner.services.accounts import derive
from tests.signal_scanner.conftest import seed_card, write


def new_account(client, cal):
    r = write(
        client,
        "/api/accounts",
        dict(
            scope="PAPER",
            origin="USER_PLAN",
            currency="USD",
            initial_cash="100000",
            start_market_date=cal.action_date(now()),
        ),
    )
    assert r.status_code == 200, r.text
    a = r.json()["data"]
    s = a["snapshot"]
    r = write(
        client,
        "/api/accounts/" + a["id"] + "/confirm",
        dict(
            snapshot_id=s["id"],
            ledger_head=s["ledger_head_ref"],
            complete=True,
            reason="仅测试夹具中的已核对纸面现金",
        ),
    )
    assert r.status_code == 200, r.text
    return a["id"]


def prepare(client, cal, aid, cid, maxqty=None):
    c = client.get("/api/signals/" + cid).json()["data"]
    d = client.get("/api/dashboard").json()["data"]
    day = cal.action_date(now())
    r = write(
        client,
        "/api/preflight",
        dict(
            action_market_date=day,
            events_seen=True,
            event_hash=d["events_hash"],
            risk_seen=True,
            risk_version=d["risk_version"],
            mission="测试按协议执行",
        ),
        method="PUT",
    )
    assert r.status_code == 200, r.text
    pre = r.json()["data"]
    base = dict(account_id=aid, entry="100", stop="94", card_id=cid, card_revision=1, action_market_date=day)
    r = write(
        client, "/api/plans", {**base, "intent": "DRAFT", **({"max_qty": str(maxqty)} if maxqty else {})}
    )
    assert r.status_code == 200, r.text
    draft = r.json()["data"]
    r = write(
        client,
        "/api/stop-sessions",
        dict(
            card_id=cid,
            card_revision=1,
            action_market_date=day,
            plan_intent=dict(account_id=aid, entry="100", stop="94"),
        ),
    )
    assert r.status_code == 200, r.text
    stop = r.json()["data"]
    for step, extra in [
        ("S", dict(acknowledged=True)),
        ("T", dict(emotion="Calm")),
        ("O", dict(read_ack=True, evidence_hash=c["evidence_hash"])),
        ("P", dict(reason="测试理由", expectation="测试预期", invalidation="测试证伪")),
    ]:
        r = write(
            client,
            "/api/stop-sessions/" + stop["id"],
            dict(expected_revision=stop["revision"], step=step, **extra),
            method="PATCH",
        )
        assert r.status_code == 200, r.text
        stop = {**stop, **r.json()["data"]}
    s = client.get("/api/accounts/" + aid + "/risk").json()["data"]
    body = dict(
        expected_revision=draft["revision"],
        card_revision=1,
        account_snapshot_id=s["snapshot_id"],
        risk_profile_version=d["risk_version"],
        preflight_id=pre["id"],
        stop_session_id=stop["id"],
        account_confirmation="测试快照已完整确认",
    )
    return draft, stop, body


def cool(client, stop):
    with client.app.state.db.transaction() as cx:
        update(
            cx, "decision_sessions", "id", stop["id"], dict(cooling_until=stamp(now() - timedelta(seconds=1)))
        )


def test_CT18_day_lock_persists(client, cal):
    aid = new_account(client, cal)
    payload = dict(
        account_id=aid,
        import_id="loss",
        dry_run=False,
        rows=[
            dict(
                external_key="fee",
                entry_type="FEE",
                occurred_at=stamp(),
                fee="0",
                cash_amount="3000",
                currency="USD",
            )
        ],
    )
    r = write(client, "/api/ledger/import", payload)
    assert r.status_code == 200, r.text
    assert any(
        x["type"] == "DAY_LOSS" for x in client.get("/api/accounts/" + aid + "/risk").json()["data"]["locks"]
    )
    payload["import_id"] = "refund"
    payload["rows"] = [
        dict(
            external_key="gain",
            entry_type="DIVIDEND",
            instrument_id=None,
            occurred_at=stamp(),
            fee="0",
            cash_amount="1000",
            currency="USD",
        )
    ]
    # Use a deposit: a flow must not clear an already triggered loss lock.
    payload["rows"][0]["entry_type"] = "DEPOSIT"
    assert write(client, "/api/ledger/import", payload).status_code == 200
    assert any(
        x["type"] == "DAY_LOSS" for x in client.get("/api/accounts/" + aid + "/risk").json()["data"]["locks"]
    )


def test_CT22_stop_clock_and_get_readonly(client, cal):
    db = client.app.state.db
    cid = seed_card(db, cal)
    aid = new_account(client, cal)
    draft, stop, body = prepare(client, cal, aid, cid)
    r = write(client, "/api/plans/" + draft["id"] + "/reserve", body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "COOLING"
    with db.read() as cx:
        before = digest(list(cx.iterdump()))
    assert client.get("/api/signals/" + cid).status_code == 200
    assert client.get("/api/market-context").status_code == 200
    with db.read() as cx:
        assert digest(list(cx.iterdump())) == before
    r = write(
        client,
        "/api/stop-sessions",
        dict(
            card_id=cid,
            card_revision=1,
            action_market_date=cal.action_date(now()),
            plan_intent=dict(account_id=aid, entry="100", stop="94"),
            opened_at="2000-01-01T00:00:00Z",
        ),
    )
    assert r.status_code == 422


def test_CT23_idempotent_reservation(client, cal):
    cid = seed_card(client.app.state.db, cal)
    aid = new_account(client, cal)
    draft, stop, body = prepare(client, cal, aid, cid)
    cool(client, stop)
    a = write(client, "/api/plans/" + draft["id"] + "/reserve", body, key="one")
    assert a.status_code == 200, a.text
    b = write(client, "/api/plans/" + draft["id"] + "/reserve", body, key="one")
    assert a.json()["data"] == b.json()["data"]
    with client.app.state.db.read() as cx:
        assert one(cx, "SELECT COUNT(*) AS n FROM decisions WHERE plan_id=?", (draft["id"],))["n"] == 1
    assert (
        write(
            client,
            "/api/plans/" + draft["id"] + "/reserve",
            {**body, "account_confirmation": "changed"},
            key="one",
        ).status_code
        == 409
    )


def test_CT24_concurrent_budget(client, cal):
    # Two independent requests each want ~703.50, while portfolio remaining risk is 1000.
    risk = client.get("/api/configs/risk").json()["data"]
    r = write(
        client,
        "/api/configs/risk",
        dict(
            expected_version=risk["version"],
            change_reason="测试降低组合热度",
            value=dict(portfolio_heat_limit="0.01"),
        ),
        method="PUT",
    )
    assert r.status_code == 200, r.text
    aid = new_account(client, cal)
    prepared = []
    for sym in ("TEST1", "TEST2"):
        cid = seed_card(client.app.state.db, cal, sym)
        p, s, b = prepare(client, cal, aid, cid, 115)
        cool(client, s)
        prepared.append((p, b))

    def submit(item):
        return write(client, "/api/plans/" + item[0]["id"] + "/reserve", item[1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, prepared))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    with client.app.state.db.read() as cx:
        ps = many(cx, "SELECT * FROM plans WHERE account_id=? AND status='RESERVED'", (aid,))
    assert sum(Decimal(p["risk_reserved"]) for p in ps) <= 1000
    assert sorted(p["qty_reserved"] for p in ps) == [48, 115]


def test_CT25_partial_fill_release(client, cal):
    cid = seed_card(client.app.state.db, cal)
    aid = new_account(client, cal)
    draft, s, body = prepare(client, cal, aid, cid, maxqty=100)
    cool(client, s)
    r = write(client, "/api/plans/" + draft["id"] + "/reserve", body)
    assert r.status_code == 200, r.text
    r = write(
        client,
        "/api/ledger/import",
        dict(
            account_id=aid,
            import_id="fills",
            dry_run=False,
            rows=[
                dict(
                    external_key="fill1",
                    entry_type="BUY",
                    instrument_id="TEST",
                    occurred_at=stamp(),
                    qty="40",
                    price="100",
                    fee="1",
                    currency="USD",
                    plan_id=draft["id"],
                    stop_reference="94",
                )
            ],
        ),
    )
    assert r.status_code == 200, r.text
    state = client.get("/api/accounts/" + aid + "/risk").json()["data"]
    p = state["plans"][0]
    assert p["qty_remaining"] == 60 and p["qty_reported_filled"] == 40
    r = write(
        client,
        "/api/plans/" + draft["id"] + "/cancel",
        dict(expected_revision=p["revision"], reason="取消未成交余量"),
    )
    assert r.status_code == 200, r.text
    state = client.get("/api/accounts/" + aid + "/risk").json()["data"]
    assert state["plans"][0]["qty_remaining"] == 0 and state["snapshot"]["positions_json"][0]["qty"] == "40"


def test_CT26_loss_streak_not_partial(client, cal):
    # Episodes count only flat-to-flat; the calendar supplies two complete future sessions.
    seed_card(client.app.state.db, cal)
    aid = new_account(client, cal)
    for i in range(3):
        for typ, price in [("BUY", "100"), ("SELL", "90")]:
            r = write(
                client,
                "/api/ledger/import",
                dict(
                    account_id=aid,
                    import_id=f"{i}-{typ}",
                    dry_run=False,
                    rows=[
                        dict(
                            external_key=f"{i}-{typ}",
                            entry_type=typ,
                            instrument_id="TEST",
                            occurred_at=stamp(),
                            qty="1",
                            price=price,
                            fee="1",
                            currency="USD",
                            stop_reference="80",
                        )
                    ],
                ),
            )
            assert r.status_code == 200, r.text
    state = client.get("/api/accounts/" + aid + "/risk").json()["data"]
    lock = next(x for x in state["locks"] if x["type"] == "LOSS_STREAK")
    assert cal.distance(lock["risk_market_date"], lock["cooldown_end_session"]) == 2


def test_CT32_unknown_delivery_not_retried(client):
    from src.services.signal_scanner.worker.jobs import Worker

    with client.app.state.db.transaction() as cx:
        insert(
            cx,
            "notifications",
            dict(
                id="unknown",
                artifact_id="missing",
                artifact_revision=1,
                recipient_id="owner",
                channel="WEBHOOK",
                message_type="DIGEST",
                status="UNKNOWN",
                delivery_id="d",
                expires_at=stamp(now() + timedelta(hours=1)),
            ),
        )
    Worker(client.app.state.settings).deliver()
    with client.app.state.db.read() as cx:
        assert one(cx, "SELECT status,attempt_count FROM notifications WHERE id=?", ("unknown",)) == dict(
            status="UNKNOWN", attempt_count=0
        )


def test_CT33_no_permission_and_no_shadow_account(client):
    r = write(
        client,
        "/api/accounts",
        dict(
            scope="PAPER",
            origin="SHADOW_EXPERIMENT",
            currency="USD",
            initial_cash="100000",
            start_market_date="2026-10-22",
        ),
    )
    assert r.status_code == 422
    r = write(
        client,
        "/api/configs/system",
        dict(
            expected_version="system-v1", change_reason="测试禁止交易", value=dict(order_execution="ENABLED")
        ),
        method="PUT",
    )
    assert r.status_code == 422


def test_CT37_overnight_unknown_vs_empty(client, cal):
    from src.services.signal_scanner.worker.pipeline import overnight

    context = cal.context(now())
    db = client.app.state.db
    with db.read() as cx:
        assert overnight(cx, [], context, stamp())[0]["positions_status"] == "UNKNOWN"
    new_account(client, cal)
    with db.read() as cx:
        r = overnight(cx, [], context, stamp())[0]
    assert r["positions_status"] == "CONFIRMED_EMPTY" and r["broker_order_verified"] is False


def test_security_and_registered_routes_including_screen(client):
    # FastAPI may lazily include APIRouter objects; use its resolved route contract.
    inventory = {(method.upper(), path) for path, methods in client.app.openapi()["paths"].items()
                 if path.startswith(("/api/", "/health/"))
                 for method in methods if method in ("get", "post", "put", "delete", "patch")}
    legacy = {(method, path) for method, path in inventory if not path.startswith("/api/rule-validation")}
    research = inventory - legacy
    assert len(legacy) == 43 and len(research) == 23
    assert {
        ("GET", "/api/rule-validation/runs/{id}/events/{event_id}/chart"),
        ("GET", "/api/rule-validation/runs/{id}/variants"),
        ("POST", "/api/rule-validation/runs/{id}/variants/validations"),
        ("POST", "/api/rule-validation/runs/{id}/cancel"),
        ("GET", "/api/rule-validation/probes"),
    } <= research
    assert {("POST", "/api/batch-history/start"), ("GET", "/api/batch-history/status"),
            ("GET", "/api/batch-history/report")} <= legacy
    assert {("GET", "/api/rule-validation/catalog"), ("POST", "/api/rule-validation/runs"),
            ("GET", "/api/rule-validation/runs/{id}/statistics")} <= research
    assert client.get("/health/live").status_code == 200
    malformed = client.post("/api/plans", content="{", headers={"Content-Type": "application/json"})
    assert malformed.status_code == 400 and malformed.json()["error"]["code"] == "MALFORMED_REQUEST"
    r = client.post(
        "/api/user-pause",
        json=dict(paused=True, reason="x"),
        headers={"Origin": "http://evil.invalid", "Idempotency-Key": "bad"},
    )
    assert r.status_code == 403
    assert client.get("/api/market-context?display_timezone=Unknown/Place").status_code == 422
    assert client.get("/api/market-context?at=2099-01-01T00:00:00Z").status_code == 422


def test_lower_loss_policy_creates_persistent_lock(client, cal):
    risk = client.get("/api/configs/risk").json()["data"]
    r = write(
        client,
        "/api/configs/risk",
        dict(
            expected_version=risk["version"],
            change_reason="测试更严格日损上限",
            value=dict(daily_loss_limit="0.01"),
        ),
        method="PUT",
    )
    assert r.status_code == 200, r.text
    aid = new_account(client, cal)
    r = write(
        client,
        "/api/ledger/import",
        dict(
            account_id=aid,
            import_id="loss-fee",
            dry_run=False,
            rows=[
                dict(
                    external_key="loss-fee",
                    entry_type="FEE",
                    occurred_at=stamp(),
                    fee="0",
                    cash_amount="2000",
                    currency="USD",
                )
            ],
        ),
    )
    assert r.status_code == 200, r.text
    state = client.get("/api/accounts/" + aid + "/risk").json()["data"]
    assert any(x["type"] == "DAY_LOSS" for x in state["locks"])


def test_plan_export_rechecks_policy_version(client, cal):
    cid = seed_card(client.app.state.db, cal)
    aid = new_account(client, cal)
    draft, stop, body = prepare(client, cal, aid, cid)
    cool(client, stop)
    reserved = write(client, "/api/plans/" + draft["id"] + "/reserve", body)
    assert reserved.status_code == 200, reserved.text
    plan = reserved.json()["data"]
    risk = client.get("/api/configs/risk").json()["data"]
    r = write(
        client,
        "/api/configs/risk",
        dict(
            expected_version=risk["version"], change_reason="测试导出前预算变化", value=dict(risk_pct="0.005")
        ),
        method="PUT",
    )
    assert r.status_code == 200
    r = write(
        client,
        "/api/plans/" + draft["id"] + "/export",
        dict(expected_revision=plan["revision"], format="markdown", basis="EOD_REFERENCE"),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_CONFLICT"


def test_bad_ledger_batch_rolls_back(client, cal):
    aid = new_account(client, cal)
    r = write(
        client,
        "/api/ledger/import",
        dict(
            account_id=aid,
            import_id="bad",
            dry_run=False,
            rows=[
                dict(
                    external_key="ok",
                    entry_type="DEPOSIT",
                    occurred_at=stamp(),
                    fee="0",
                    cash_amount="10",
                    currency="USD",
                ),
                dict(
                    external_key="bad",
                    entry_type="BUY",
                    occurred_at=stamp(),
                    fee="1",
                    qty="10",
                    price="100",
                    currency="USD",
                    instrument_id="nonexistent",
                ),
            ],
        ),
    )
    assert r.status_code == 422
    with client.app.state.db.read() as cx:
        assert one(cx, "SELECT COUNT(*) AS n FROM ledger_entries WHERE account_id=?", (aid,))["n"] == 0


def test_reversal_preserves_original(client, cal):
    aid = new_account(client, cal)
    r = write(
        client,
        "/api/ledger/import",
        dict(
            account_id=aid,
            import_id="d",
            dry_run=False,
            rows=[
                dict(
                    external_key="d",
                    entry_type="DEPOSIT",
                    occurred_at=stamp(),
                    fee="0",
                    cash_amount="10",
                    currency="USD",
                )
            ],
        ),
    )
    assert r.status_code == 200
    eid = client.get("/api/reviews?section=LEDGER&account_id=" + aid).json()["data"]["items"][0]["id"]
    r = write(client, "/api/ledger/" + eid + "/reverse", dict(reason="错误金额"))
    assert r.status_code == 200, r.text
    assert write(client, "/api/ledger/" + eid + "/reverse", dict(reason="再次")).status_code == 409
    with client.app.state.db.read() as cx:
        assert one(cx, "SELECT COUNT(*) AS n FROM ledger_entries WHERE account_id=?", (aid,))["n"] == 2
        assert derive(cx, one(cx, "SELECT * FROM account_state WHERE account_id=?", (aid,)), now())[
            "cash"
        ] == Decimal(100000)
