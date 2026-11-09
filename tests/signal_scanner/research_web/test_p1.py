"""P1 evidence: PIT chart visibility, finite variants, real cancellation and read-only probes."""

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from src.services.signal_scanner.core import DomainError, Settings, canonical, digest, uid
from src.services.signal_scanner.main import create_app
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.research.runner import run
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft, register_dataset
from src.services.signal_scanner.services.rule_validation_web.query import summary
from src.services.signal_scanner.services.rule_validation_web.service import create_run, create_validation
from src.services.signal_scanner.services.rule_validation_web.store import Store
from src.services.signal_scanner.storage.db import insert
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.rule_validation import tick
from fastapi.testclient import TestClient

PREFIX = "/api/rule-validation"


def post(c, path, p, key=None):
    return c.post(PREFIX + path, json=p, headers={"Idempotency-Key": key or uid()})


def queued(store, dsid):
    draft = default_draft(store.get("dataset", dsid))
    draft.update(
        name="P1共享规则与隔离", hypothesis="冻结后有限参数敏感性；依据 /tmp/P1-protocol-note 不改变其他轴"
    )
    with store.write() as cx:
        v = create_validation(store, {"dataset_id": dsid, "draft": draft}, cx)
    assert tick(store)
    v = store.get("validation", v["id"])
    assert v["status"] == "SUCCEEDED", v
    with store.write() as cx:
        r = create_run(
            store, {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}, cx
        )
    return r


@pytest.fixture
def p1(tmp_path, monkeypatch):
    store = Store(tmp_path / "web", [tmp_path / "inputs"])
    store.initialize()
    p = Path(create_fixture(tmp_path / "inputs" / "base", symbols=1, sessions=100))
    m = json.loads(p.read_text())
    m["registration"]["allowed_variations"] = {
        "TR-01": {"rv_min": [1.2, 1.5, 1.8]},
        "TR-03": {"parent_sessions": [8, 10, 12]},
    }
    s = json.loads((p.parent / "snapshot.json").read_text())
    # Later revisions/actions must never be drawn as known at the earlier event clock.
    for b in list(s["bars"]["SYN000"]):
        future = {
            **b,
            "revision": 2,
            "available_at": "2030-01-02T22:00:00.000Z",
            "open": "10000",
            "high": "10001",
            "low": "9999",
            "close": "10000",
        }
        s["bars"]["SYN000"].append(future)
    s["events"].append(
        {
            "event_id": "future-split",
            "revision": 1,
            "instrument_id": "SYN000",
            "event_type": "SPLIT",
            "effective_at": "2030-01-02T14:30:00.000Z",
            "known_at": "2030-01-02T21:10:00.000Z",
            "status": "PUBLISHED",
            "payload_json": {"ratio": "10"},
        }
    )
    content = canonical(s)
    (p.parent / "snapshot.json").write_text(content)
    import hashlib

    m["data_snapshot_ref"]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    p.write_text(canonical(m))
    dsid = register_dataset(store, p, "P1逐时点合成工程输入")
    monkeypatch.setenv("RULE_VALIDATION_WEB_ROOT", str(store.root))
    monkeypatch.setenv("RULE_VALIDATION_INPUT_ROOTS", str(tmp_path / "inputs"))
    app = create_app(Settings(tmp_path / "app"))
    app.state.auth.provision()
    c = TestClient(app)
    login = c.post(
        "/api/auth/login",
        json={"password": (tmp_path / "app" / "首次登录密码.txt").read_text().strip()},
        headers={"Origin": "http://testserver"},
    )
    c.headers.update({"Origin": "http://testserver", "X-CSRF-Token": login.json()["data"]["csrf_token"]})
    r = queued(store, dsid)
    tick(store)
    r = store.get("run", r["id"])
    assert r["status"] == "SUCCEEDED", r
    return c, store, dsid, r, tmp_path


def test_chart_as_known_revision_window_readonly_and_source_integrity(p1):
    c, store, _, r, _ = p1
    event = c.get(PREFIX + f"/runs/{r['id']}/rows?section=events&limit=1").json()["data"]["items"][0]
    path = f"/runs/{r['id']}/events/{event['event_id']}/chart"
    before = {str(p): p.stat().st_mtime_ns for p in store.root.rglob("*") if p.is_file()}
    response = c.get(PREFIX + path + "?as_of=TRIGGER&window=20")
    assert response.status_code == 200, response.text
    chart = response.json()["data"]
    assert chart["availability"] == "AVAILABLE", chart
    assert len(chart["bars"]) <= 20
    assert all(b["revision"] == 1 and float(b["close"]) < 1000 for b in chart["bars"])
    assert max(b["market_date"] for b in chart["bars"]) == event["trigger_market_date"]
    assert not any(a["event_id"] == "future-split" for a in chart["action_refs"])
    assert not any(m["market_date"] > event["trigger_market_date"] for m in chart["markers"])
    assert "/Users/" not in response.text and "snapshot_path" not in response.text
    assert before == {str(p): p.stat().st_mtime_ns for p in store.root.rglob("*") if p.is_file()}
    assert c.get(PREFIX + path + "?window=121").status_code == 422
    assert c.get(PREFIX + path + "?as_of=2030-01-02").status_code == 422
    snapshot = Path(r["chart_source_path"])
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    assert c.get(PREFIX + path).json()["error"]["code"] == "ARTIFACT_CORRUPT"


def test_revised_history_chart_unavailable_instead_of_faked_pit(p1):
    from src.services.signal_scanner.services.rule_validation_web.charts import event_chart

    _, store, dsid, _, tmp = p1
    p = Path(create_fixture(tmp / "inputs" / "revised", symbols=1, sessions=100, mode="REVISED_HISTORY"))
    dsid = register_dataset(store, p, "修订历史")
    r = queued(store, dsid)
    tick(store)
    r = store.get("run", r["id"])
    report = json.loads((Path(r["source_root"]) / r["report_ref"]).read_text())
    d = event_chart(r, report["events"][0]["event_id"])
    assert d["availability"] == "UNAVAILABLE" and d["reason"] == "POINT_IN_TIME_HISTORY_UNVERIFIED"


def test_chart_market_date_survives_evaluation_clock_crossing_utc_midnight(p1):
    from src.services.signal_scanner.services.rule_validation_web.charts import event_chart

    _, store, _, _, tmp = p1
    path = Path(create_fixture(tmp / "inputs" / "late-clock", symbols=1, sessions=100))
    manifest = json.loads(path.read_text())
    manifest["evaluation_clock"]["minutes"] = 600
    path.write_text(canonical(manifest))
    dsid = register_dataset(store, path, "跨UTC午夜的研究时钟")
    r = queued(store, dsid)
    tick(store)
    r = store.get("run", r["id"])
    assert r["status"] == "SUCCEEDED", r
    report = json.loads((Path(r["source_root"]) / r["report_ref"]).read_text())
    event = next(e for e in report["events"] if e.get("confirmed_at"))
    assert event["triggered_at"][:10] != event["trigger_market_date"]
    for observation in ("TRIGGER", "CONFIRMATION", "LAST_EVENT_STATE"):
        chart = event_chart(r, event["event_id"], observation, 20)
        assert chart["availability"] == "AVAILABLE", chart
        if observation == "TRIGGER":
            expected_day = event["trigger_market_date"]
        elif observation == "CONFIRMATION":
            expected_day = next(
                t["market_date"]
                for t in event["state_history"]
                if t.get("evaluated_at") == event["confirmed_at"]
            )
        else:
            expected_day = event["state_history"][-1]["market_date"]
        assert chart["bars"][-1]["market_date"] == expected_day
        assert chart["evaluation_clock"][:10] > expected_day


def test_variants_registered_single_axis_all_results_and_comparison(p1):
    c, store, _, r, _ = p1
    plan = c.get(PREFIX + f"/runs/{r['id']}/variants").json()["data"]
    assert plan["available"]
    original = summary(r)["manifest"]
    original_path = Path(r["source_root"]) / r["report_ref"]
    unchanged = original_path.read_bytes()
    siblings = []
    for val in (1.2, 1.8):
        payload = {
            "expected_report_hash": r["report_hash"],
            "rule_id": "TR-01",
            "parameter": "rv_min",
            "value": val,
        }
        response = post(c, f"/runs/{r['id']}/variants/validations", payload)
        assert response.status_code == 202, response.text
        v = response.json()["data"]
        tick(store)
        v = store.get("validation", v["id"])
        assert v["status"] == "SUCCEEDED", v
        response = post(
            c,
            "/runs",
            {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False},
        )
        assert response.status_code == 202, response.text
        child = response.json()["data"]
        tick(store)
        child = store.get("run", child["id"])
        assert child["status"] == "SUCCEEDED", child
        child_m = summary(child)["manifest"]
        assert child_m["registration"] == original["registration"]
        assert child_m["parameter_overrides"] == {"TR-01": {"rv_min": val}}
        assert child_m["ruleset_version"] != original["ruleset_version"]
        for k in (
            "start_date",
            "end_date",
            "evaluation_clock",
            "label_as_of",
            "cost_assumption",
            "price_basis",
            "benchmark_ref",
            "split_policy",
            "universe_version",
            "code_hash",
            "dependency_hash",
        ):
            assert original[k] == child_m[k]
        siblings.append(child)
    family = c.get(PREFIX + f"/runs/{r['id']}/variants").json()["data"]
    assert {x["id"] for x in family["items"]} == {r["id"], *(x["id"] for x in siblings)}
    assert (
        len(family["validations"]) == 2
        and family["selection_policy"] == "ALL_RESULTS_RETAINED_NO_OPTIMIZATION"
    )
    assert original_path.read_bytes() == unchanged
    assert "/tmp/P1-protocol-note" not in canonical(family)
    paged = c.get(PREFIX + f"/runs/{r['id']}/variants?limit=1&cursor=1&validation_cursor=1").json()["data"]
    assert len(paged["items"]) == len(paged["validations"]) == 1
    assert paged["next_cursor"] == 2 and paged["total"] == 3
    assert c.get(PREFIX + f"/runs/{r['id']}/variants?limit=101").status_code == 422
    p = {"left_run_id": r["id"], "right_run_id": siblings[0]["id"], "axis": "rules"}
    comparison = post(c, "/comparisons", p).json()["data"]
    tick(store)
    assert store.get("comparison", comparison["id"])["status"] == "SUCCEEDED"
    bad = {"expected_report_hash": r["report_hash"], "rule_id": "TR-01", "parameter": "rv_min", "value": 1.3}
    assert (
        post(c, f"/runs/{r['id']}/variants/validations", bad).json()["error"]["code"]
        == "UNREGISTERED_PARAMETER"
    )
    assert post(c, f"/runs/{r['id']}/variants/validations", {**bad, "grid": [1.2, 1.8]}).status_code == 422
    integer = {**bad, "rule_id": "TR-03", "parameter": "parent_sessions", "value": 8.0}
    assert (
        post(c, f"/runs/{r['id']}/variants/validations", integer).json()["error"]["code"]
        == "INVALID_SENSITIVITY"
    )
    assert post(c, f"/runs/{r['id']}/variants/validations", {**integer, "value": 8}).status_code == 202


def test_queue_cancel_idempotent_retry_attempt_signal(p1):
    c, store, dsid, _, _ = p1
    r = queued(store, dsid)
    p = {"expected_attempt": 1, "reason": "排队取消验收"}
    key = uid()
    a = post(c, f"/runs/{r['id']}/cancel", p, key)
    b = post(c, f"/runs/{r['id']}/cancel", p, key)
    assert a.status_code == b.status_code == 202 and a.json()["data"]["status"] == "CANCELLED"
    assert a.json()["data"] == b.json()["data"]
    assert post(c, f"/runs/{r['id']}/retry", {"expected_attempt": 1}).json()["data"]["attempt"] == 2
    tick(store)
    done = store.get("run", r["id"])
    assert done["status"] == "SUCCEEDED", done
    assert done["attempts"][0]["status"] == "CANCELLED"
    assert post(c, f"/runs/{r['id']}/cancel", p).json()["error"]["code"] == "CANCEL_CONFLICT"


def test_real_child_cooperative_cancel_preserves_registration_and_no_report(p1):
    c, store, dsid, _, _ = p1
    r = queued(store, dsid)
    errors = []

    def launch():
        try:
            tick(store, supervised=True)
        except BaseException as e:  # noqa: BLE001 - propagate child-thread failures to the assertion
            errors.append(e)

    th = threading.Thread(target=launch)
    th.start()
    deadline = time.monotonic() + 15
    # Wait for this attempt's engine heartbeat, so the request hits real computation.
    bridge = store.root / "engine" / "web-progress" / (digest(r["logical_key"]) + ".json")
    while not bridge.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    response = post(c, f"/runs/{r['id']}/cancel", {"expected_attempt": 1, "reason": "运行中真实取消验收"})
    assert response.status_code == 202, response.text
    th.join(40)
    assert not th.is_alive() and not errors
    result = store.get("run", r["id"])
    assert result["status"] == "CANCELLED", result
    assert result["report_state"] == "NOT_READY"
    assert (store.root / "engine" / "registrations.json").is_file()
    assert c.get(PREFIX + f"/runs/{r['id']}/report").status_code == 409
    with sqlite3.connect(store.root / "engine" / "scanner.sqlite3") as cx:
        state, code = cx.execute(
            "SELECT status,error_json FROM job_runs WHERE logical_key=?", (r["logical_key"],)
        ).fetchone()
    assert state == "FAILED" and json.loads(code)["code"] == "RESEARCH_CANCELLED"
    assert not list((store.root / "indices").glob(r["id"] + ".tmp"))


def test_cancel_at_publication_boundary_never_exposes_partial_ready(p1, monkeypatch):
    from src.services.signal_scanner.services.rule_validation_web import query

    c, store, dsid, _, _ = p1
    r = queued(store, dsid)
    original = query.index_report

    def publish_then_cancel(*args):
        value = original(*args)
        assert (
            post(c, f"/runs/{r['id']}/cancel", {"expected_attempt": 1, "reason": "完成边界取消"}).status_code
            == 202
        )
        return value

    monkeypatch.setattr(query, "index_report", publish_then_cancel)
    tick(store)
    cancelled = store.get("run", r["id"])
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["report_state"] == "NOT_READY" and "artifacts" not in cancelled
    assert c.get(PREFIX + f"/runs/{r['id']}/artifacts").status_code == 409
    monkeypatch.setattr(query, "index_report", original)
    post(c, f"/runs/{r['id']}/retry", {"expected_attempt": 1})
    tick(store)
    assert store.get("run", r["id"])["status"] == "SUCCEEDED"


def test_timeout_terminates_only_owned_real_child_and_cleans_staging(p1, monkeypatch):
    import subprocess
    import sys

    import src.services.signal_scanner.worker.rule_validation as worker
    from src.services.signal_scanner.services.rule_validation_web.cancellation import signal_cancel

    _, store, dsid, _, _ = p1
    r = queued(store, dsid)
    r.update(
        status="CANCEL_REQUESTED",
        cancellation={"attempt": 1, "reason": "超时边界", "requested_at": "2026-10-26T00:00:00Z"},
    )
    with store.write() as cx:
        store.put("run", r, cx)
    signal_cancel(store, r)
    staging = store.root / "indices" / (r["id"] + ".tmp")
    staging.write_text("incomplete")
    real_popen = subprocess.Popen
    children = []

    def stalled(*args, **kw):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
        children.append(child)
        return child

    monkeypatch.setattr(worker.subprocess, "Popen", stalled)
    worker.supervise(store, "run", r, grace_seconds=0.01)
    done = store.get("run", r["id"])
    assert done["status"] == "CANCELLED" and done["cancellation"]["method"] == "TIMEOUT_TERMINATE"
    assert children[0].poll() is not None and not staging.exists()


def test_cancelled_sealed_reveal_cannot_unsee(tmp_path):
    p = Path(create_fixture(tmp_path / "input", symbols=1, sessions=1261))
    runtime = tmp_path / "engine"
    with pytest.raises(DomainError) as e:
        run(p, runtime, reveal=True, cancellation_requested=lambda: (runtime / "registrations.json").exists())
    assert e.value.code == "RESEARCH_CANCELLED"
    records = json.loads((runtime / "registrations.json").read_text())
    assert next(iter(records.values()))["revealed_at"]
    assert not (runtime / "reports").exists()


def test_probe_four_states_latest_corrupt_no_fallback_and_no_get_writes(p1):
    c, _store, _, _r, tmp = p1
    files = Artifacts(tmp / "app" / "artifacts")
    for n, status in enumerate(("PASS", "FAIL", "SKIPPED", "VERSION_UNAVAILABLE")):
        probe = {
            "status": status,
            "legacy_status": "SKIPPED" if status == "VERSION_UNAVAILABLE" else status,
            "reason": None if status == "PASS" else status,
        }
        if status in ("PASS", "FAIL"):
            probe.update(
                expected_hash="a" * 64,
                actual_hash="a" * 64 if status == "PASS" else "b" * 64,
                network_calls=0,
                account_writes=0,
            )
        ref = files.save(
            {
                "kind": "WEEKLY_REPORT",
                "replay_probe": probe,
                "data_health": [{"code": "REFERENCED_FILES", "evidence_refs": ["scan_" + str(n)]}],
            },
            "reports",
        )
        with c.app.state.db.transaction() as cx:
            insert(
                cx,
                "job_runs",
                {
                    "id": uid("week"),
                    "logical_key": uid(),
                    "revision": 1,
                    "job_name": "WEEKLY_REPORT",
                    "status": "SUCCEEDED",
                    "created_at": f"2026-10-26T00:00:0{n}Z",
                    "output_manifest_ref": ref,
                },
            )
    with c.app.state.db.read() as cx:
        before = digest(list(cx.iterdump()))
    snapshot = {
        str(p): p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file() and not p.name.endswith("-shm")
    }
    data = c.get(PREFIX + "/probes").json()["data"]
    assert {x["status"] for x in data["items"]} == {"PASS", "FAIL", "SKIPPED", "VERSION_UNAVAILABLE"}
    version = data["items"][0]
    assert version["status"] == "VERSION_UNAVAILABLE" and version["network_calls"] is None
    assert all("/Users/" not in canonical(x) for x in data["items"])
    assert snapshot == {
        str(p): p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file() and not p.name.endswith("-shm")
    }
    with c.app.state.db.read() as cx:
        assert digest(list(cx.iterdump())) == before
    assert c.get(PREFIX + "/probes?limit=101").status_code == 422
    with c.app.state.db.read() as cx:
        ref = cx.execute(
            "SELECT output_manifest_ref FROM job_runs WHERE job_name='WEEKLY_REPORT' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
    (tmp / "app" / "artifacts" / ref).write_text("corrupt")
    newer = c.get(PREFIX + "/probes").json()["data"]["items"]
    assert newer[0]["status"] == "FAIL" and newer[0]["integrity"] == "INVALID"
    assert newer[0]["reason"] == "ARTIFACT_CORRUPT" and len(newer) == 4


def test_continuous_parameters_with_integer_defaults_are_not_session_counts():
    from src.services.signal_scanner.services.rule_validation_web.variants import policy

    choices = policy({"allowed_variations": {"VP-01": {"rv_min": [1.6, 2, 2.4]}}})
    assert choices[0]["integer"] is False
    with pytest.raises(DomainError):
        policy({"allowed_variations": {"TR-03": {"parent_sessions": [8.0, 10, 12]}}})


def test_previous_attempt_progress_is_never_reused(tmp_path):
    from src.services.signal_scanner.research.reporting import atomic_text
    from src.services.signal_scanner.services.rule_validation_web.service import run_status

    store = Store(tmp_path / "web")
    store.initialize()
    r = {
        "id": "retry-test",
        "status": "RUNNING",
        "phase": "REPLAY",
        "attempt": 2,
        "logical_key": "retry-key",
        "processed": None,
        "total": None,
        "report_state": "NOT_READY",
    }
    bridge = store.root / "engine" / "web-progress" / (digest(r["logical_key"]) + ".json")
    atomic_text(bridge, canonical({"attempt": 1, "processed": 100, "total": 100, "phase": "COMPLETED"}))
    actual = run_status(store, r)
    assert actual["phase"] == "REPLAY" and actual["processed"] is None
    atomic_text(bridge, canonical({"attempt": 2, "processed": 10, "total": 100, "phase": "REPLAY"}))
    assert run_status(store, r)["processed"] == 10
