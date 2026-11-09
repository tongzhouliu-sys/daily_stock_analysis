import json
from copy import deepcopy
from pathlib import Path

import pytest
from src.services.signal_scanner.core import DomainError, Settings, canonical, digest, uid
from src.services.signal_scanner.main import create_app
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft, register_dataset
from src.services.signal_scanner.services.rule_validation_web.store import Store
from src.services.signal_scanner.worker.rule_validation import tick, work
from fastapi.testclient import TestClient

PREFIX = "/api/rule-validation"


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    root = tmp_path_factory.mktemp("research-web")
    store = Store(root / "web", [root / "inputs"])
    store.initialize()
    manifest = create_fixture(root / "inputs" / "small", symbols=1, sessions=45)
    dsid = register_dataset(store, manifest, "合成工程样本")
    return root, store, dsid


@pytest.fixture
def web(system, monkeypatch):
    root, store, dsid = system
    monkeypatch.setenv("RULE_VALIDATION_WEB_ROOT", str(store.root))
    monkeypatch.setenv("RULE_VALIDATION_INPUT_ROOTS", str(root / "inputs"))
    app = create_app(Settings(root / "production"))
    app.state.auth.provision()
    client = TestClient(app)
    res = client.post(
        "/api/auth/login",
        json={"password": (root / "production" / "首次登录密码.txt").read_text().strip()},
        headers={"Origin": "http://testserver"},
    )
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": res.json()["data"]["csrf_token"]}
    )
    return client, store, dsid


def post(c, route, p, key=None):
    return c.post(PREFIX + route, json=p, headers={"Idempotency-Key": key or uid()})


def prepare(c, store, dsid, name="服务闭环"):
    draft = default_draft(store.get("dataset", dsid))
    draft.update(name=name, hypothesis="检验冻结事件与分母")
    r = post(c, "/validations", {"dataset_id": dsid, "draft": draft})
    assert r.status_code == 202, r.text
    v = r.json()["data"]
    assert tick(store)
    v = c.get(PREFIX + "/validations/" + v["id"]).json()["data"]
    assert v["status"] == "SUCCEEDED", v
    assert v["api_schema_version"] == "1" and len(v["manifest_hash"]) == 64
    return v


@pytest.fixture(scope="module")
def completed(system):
    # Build a real engine report once; no mocked HTTP or calculations.
    from src.services.signal_scanner.services.rule_validation_web.service import create_run, create_validation

    _root, store, dsid = system
    draft = default_draft(store.get("dataset", dsid))
    draft.update(name="已完成合成实验", hypothesis="独立工程验证")
    with store.write() as cx:
        v = create_validation(store, {"dataset_id": dsid, "draft": draft}, cx)
    tick(store)
    v = store.get("validation", v["id"])
    assert v["status"] == "SUCCEEDED", v
    with store.write() as cx:
        r = create_run(
            store, {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}, cx
        )
    tick(store)
    r = store.get("run", r["id"])
    assert r["status"] == "SUCCEEDED", r
    return r


def test_uninitialized_reads_have_no_writes(tmp_path, monkeypatch):
    root = tmp_path / "absent"
    monkeypatch.setenv("RULE_VALIDATION_WEB_ROOT", str(root))
    app = create_app(Settings(tmp_path / "prod"))
    app.state.auth.provision()
    c = TestClient(app)
    r = c.get(PREFIX + "/catalog")
    assert r.status_code == 401
    r = c.post(
        "/api/auth/login",
        json={"password": (tmp_path / "prod" / "首次登录密码.txt").read_text().strip()},
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 200
    assert c.get(PREFIX + "/catalog").json()["data"]["runtime_ready"] is False
    assert c.get(PREFIX + "/runs").json()["data"]["items"] == []
    assert not root.exists()


def test_idempotency_conflicts_and_production_isolation(web):
    c, store, dsid = web
    draft = default_draft(store.get("dataset", dsid))
    draft["hypothesis"] = "幂等验证"
    p = {"dataset_id": dsid, "draft": draft}
    with c.app.state.db.read() as cx:
        before_digest = digest(list(cx.iterdump()))
        before = {
            r[0]: cx.execute('SELECT count(*) FROM "' + r[0] + '"').fetchone()[0]
            for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    key = uid()
    a = post(c, "/validations", p, key)
    b = post(c, "/validations", p, key)
    assert a.status_code == b.status_code == 202
    assert a.json()["data"]["id"] == b.json()["data"]["id"]
    p["draft"]["name"] = "冲突"
    assert post(c, "/validations", p, key).json()["error"]["code"] == "IDEMPOTENCY_INPUT_CONFLICT"
    with c.app.state.db.read() as cx:
        after_digest = digest(list(cx.iterdump()))
        after = {
            r[0]: cx.execute('SELECT count(*) FROM "' + r[0] + '"').fetchone()[0]
            for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert before == after and before_digest == after_digest
    tick(store)


def test_draft_mutation_and_stale_versions_rejected(web):
    c, store, dsid = web
    v = prepare(c, store, dsid)
    response = post(c, "/runs", {"validation_id": v["id"], "config_digest": "0" * 64, "reveal_sealed": False})
    assert response.json()["error"]["code"] == "VALIDATION_STALE"
    with store.write() as cx:
        saved = store.get("validation", v["id"], cx)
        saved["code_hash"] = "old"
        store.put("validation", saved, cx)
    assert (
        post(
            c,
            "/runs",
            {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False},
        ).status_code
        == 409
    )


def test_real_report_index_statistics_exports_and_no_get_writes(web, completed):
    c, store, _ = web
    r = completed
    report = json.loads((Path(r["source_root"]) / r["report_ref"]).read_text())
    snapshots = {str(p): p.stat().st_mtime_ns for p in store.root.rglob("*") if p.is_file()}
    detail = c.get(PREFIX + f"/runs/{r['id']}/report")
    assert detail.status_code == 200, detail.text
    assert "source_root" not in detail.text and "/Users/" not in detail.text
    stat = c.get(PREFIX + f"/runs/{r['id']}/statistics").json()["data"]
    expected = [
        x
        for x in report["statistics"]
        if x["dimension"] == "ECONOMIC_TOTAL" and all(x[k] == v for k, v in stat["filters"].items())
    ]
    assert stat["items"] == expected
    events = c.get(PREFIX + f"/runs/{r['id']}/rows?section=events&limit=2").json()["data"]
    assert events["total"] == len(report["events"])
    if events["items"]:
        e = c.get(PREFIX + f"/runs/{r['id']}/events/" + events["items"][0]["event_id"])
        assert e.status_code == 200
    b = c.get(PREFIX + f"/runs/{r['id']}/benchmarks?kind=SPY")
    assert b.status_code == 200, b.text
    assert (
        b.json()["data"]["paired_count"] + b.json()["data"]["missing_count"]
        == b.json()["data"]["signal_completed"]
    )
    for a in c.get(PREFIX + f"/runs/{r['id']}/artifacts").json()["data"]["items"]:
        assert c.get(PREFIX + f"/runs/{r['id']}/artifacts/" + a["name"]).status_code == 200
    assert snapshots == {str(p): p.stat().st_mtime_ns for p in store.root.rglob("*") if p.is_file()}


def test_unknown_filters_and_pagination_fail_closed(web, completed):
    c, _, _ = web
    id = completed["id"]
    for suffix in (
        "statistics?horizon=99",
        "statistics?rule_ids=TR-01",
        "rows?limit=201",
        "statistics?split=SEALED",
        "benchmarks?kind=UNCONDITIONAL&instrument_id=SYN000&rule_id=TR-01",
    ):
        assert c.get(PREFIX + f"/runs/{id}/" + suffix).status_code in (422, 403)


def test_tampered_report_blocks_cached_read_and_download(web, completed):
    c, store, _ = web
    r = deepcopy(completed)
    p = Path(r["source_root"]) / r["report_ref"]
    content = p.read_bytes()
    try:
        p.write_bytes(content + b" ")
        assert c.get(PREFIX + f"/runs/{r['id']}/report").json()["error"]["code"] == "ARTIFACT_CORRUPT"
        assert c.get(PREFIX + f"/runs/{r['id']}/artifacts/report.json").status_code == 409
    finally:
        p.write_bytes(content)
        from src.services.signal_scanner.services.rule_validation_web.query import fingerprints

        r["fingerprints"] = fingerprints(p.parent)
        with store.write() as cx:
            store.put("run", r, cx)
        completed.update(r)


def test_worker_recovery_does_not_execute_twice(web):
    c, store, dsid = web
    v = prepare(c, store, dsid)
    p = {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}
    a = post(c, "/runs", p).json()["data"]
    b = post(c, "/runs", p).json()["data"]
    assert a["id"] == b["id"]
    with store.write() as cx:
        r = store.get("run", a["id"], cx)
        r["status"] = "RUNNING"
        store.put("run", r, cx)
    work(store, once=True)
    r = store.get("run", a["id"])
    assert r["status"] == "FAILED" and r["error"]["code"] == "WORKER_INTERRUPTED"
    res = post(c, f"/runs/{r['id']}/retry", {"expected_attempt": 1})
    assert res.status_code == 202 and res.json()["data"]["attempt"] == 2
    tick(store)


def test_path_symlink_and_changed_dataset_rejected(system, tmp_path):
    _root, store, dsid = system
    from src.services.signal_scanner.services.rule_validation_web.catalog import check_dataset

    with pytest.raises(DomainError):
        register_dataset(store, tmp_path / "manifest.json", "越界")
    ds = store.get("dataset", dsid)
    p = Path(ds["snapshot_path"])
    original = p.read_bytes()
    try:
        p.write_bytes(original + b" ")
        with pytest.raises(DomainError) as exc:
            check_dataset(store, ds)
        assert exc.value.code == "VALIDATION_STALE"
    finally:
        p.write_bytes(original)


def test_csv_formula_escaping_and_null_zero():
    from src.services.signal_scanner.services.rule_validation_web.exports import safe_cell

    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell("  @SUM(A1)").startswith("'")
    assert safe_cell("-cmd|calc").startswith("'")
    assert safe_cell("-0.05") == "-0.05"
    assert safe_cell(None) is None and safe_cell(0) == 0


def test_hand_calculated_joint_statistics_and_paired_intersection(web, completed, tmp_path):
    from src.services.signal_scanner.research.reporting import publish
    from src.services.signal_scanner.services.rule_validation_web.query import benchmark, index_report, statistics

    _c, store, _ = web
    report = json.loads((Path(completed["source_root"]) / completed["report_ref"]).read_text())
    common = {
        "research_mode": "POINT_IN_TIME",
        "horizon": 20,
        "entry_basis": "NEXT_AVAILABLE_OPEN",
        "cost_tier": 10,
        "split": "EXPLORATORY",
        "direction": "LONG",
        "instrument_id": "SYN000",
        "sector": "MANUAL",
        "asset_type": "COMMON",
        "environment": "NORMAL",
        "confirmation_market_date": "2026-10-01",
        "rule_refs": [
            {"rule_id": "TR-01", "rule_version": "1.0.0", "event_id": "a"},
            {"rule_id": "VP-01", "rule_version": "1.0.0", "event_id": "b"},
        ],
    }
    rows = [
        dict(common, economic_event_id="one", status="COMPLETED", gross_return="0.11", net_return="0.1"),
        dict(common, economic_event_id="two", status="COMPLETED", gross_return="0.31", net_return="0.3"),
        dict(
            common,
            economic_event_id="three",
            status="PENDING",
            gross_return=None,
            net_return=None,
            reason_code="NOT_MATURE",
        ),
        dict(
            common,
            economic_event_id="four",
            status="EXCLUDED",
            gross_return=None,
            net_return=None,
            excluded_reason="GAP",
        ),
    ]
    report["outcomes"] = rows
    report["benchmarks"] = [
        {
            "economic_event_id": "one",
            "kind": "SPY",
            "horizon": 20,
            "entry_basis": "NEXT_AVAILABLE_OPEN",
            "cost_tier": 10,
            "status": "PAIRED",
            "signal_net_return": "0.1",
            "benchmark": {"net_return": "0.04"},
            "excess_return": "0.06",
        }
    ]
    ref = publish(tmp_path, report)
    run = {"id": uid("manual")}
    index_report(store, run, tmp_path, ref)
    result = statistics(run, {"rule_id": "TR-01", "sector": "MANUAL"})
    row = result["items"][0]
    assert row["completed"] == 2 and row["pending"] == 1 and row["excluded"] == 1
    assert row["net"] == {
        "mean": "0.2",
        "p05": "0.11",
        "p25": "0.15",
        "p50": "0.2",
        "p75": "0.25",
        "p95": "0.29",
    }
    assert result["denominators"]["economic_events"] == 4  # not doubled by two rule refs
    assert statistics(run, {"sector": "UNKNOWN"})["items"] == []
    b = benchmark(run, {"rule_id": "TR-01", "sector": "MANUAL"}, "SPY")
    assert b["signal_completed"] == 2 and b["paired_count"] == 1 and b["missing_count"] == 1
    assert [r["net"]["mean"] for r in b["items"]] == ["0.1", "0.04", "0.06"]


def test_imported_history_single_axis_comparison_and_strict_compatibility(web, completed, tmp_path):
    from src.services.signal_scanner.research.reporting import publish
    from src.services.signal_scanner.services.rule_validation_web.query import index_report

    c, store, _ = web
    original = json.loads((Path(completed["source_root"]) / completed["report_ref"]).read_text())
    other = deepcopy(original)
    other["identity"]["snapshot_hash"] = "changed-data"
    ref = publish(tmp_path, other)
    id = uid("history")
    r = {
        "id": id,
        "run_id": id,
        "name": "外部单轴对比",
        "status": "SUCCEEDED",
        "phase": "COMPLETED",
        "external": True,
        "created_at": "2026-10-25T00:00:00Z",
        "attempt": 1,
        "heartbeat_at": None,
    }
    index_report(store, r, tmp_path, ref)
    with store.write() as cx:
        store.put("run", r, cx)
    p = {"left_run_id": completed["id"], "right_run_id": id, "axis": "data"}
    response = post(c, "/comparisons", p)
    assert response.status_code == 202, response.text
    id = response.json()["data"]["id"]
    tick(store)
    result = c.get(PREFIX + "/comparisons/" + id).json()["data"]
    assert result["status"] == "SUCCEEDED", result
    assert c.get(PREFIX + f"/comparisons/{id}/changes?kind=added").status_code == 200
    other["identity"]["label_as_of"] = "2030-01-01T00:00:00Z"
    ref = publish(tmp_path, other)
    r["id"] = r["run_id"] = uid("history")
    index_report(store, r, tmp_path, ref)
    with store.write() as cx:
        store.put("run", r, cx)
    response = post(c, "/comparisons", {**p, "right_run_id": r["id"]})
    tick(store)
    result = c.get(PREFIX + "/comparisons/" + response.json()["data"]["id"]).json()["data"]
    assert result["status"] == "FAILED" and result["error"]["details"]["field"] == "label_as_of"


def test_auth_csrf_feature_switch_and_strict_payloads(web, monkeypatch):
    c, _, dsid = web
    assert (
        c.post(PREFIX + "/validations", json={}, headers={"Origin": "http://evil.invalid"}).status_code == 403
    )
    assert post(c, "/validations", {"dataset_id": dsid, "draft": {}}).status_code == 422
    assert (
        post(c, "/validations", {"dataset_id": dsid, "draft": {}, "source_path": "/etc/passwd"}).status_code
        == 422
    )
    monkeypatch.setenv("RULE_VALIDATION_WEB_ENABLED", "false")
    assert c.get(PREFIX + "/catalog").status_code == 404
    assert c.get("/api/auth/session").status_code == 200


def test_sealed_import_protection_and_unknown_schema(web, completed, tmp_path):
    from src.services.signal_scanner.research.reporting import publish
    from src.services.signal_scanner.services.rule_validation_web.query import index_report

    _, store, _ = web
    original = json.loads((Path(completed["source_root"]) / completed["report_ref"]).read_text())
    report = deepcopy(original)
    report["identity"]["split"]["sets"]["SEALED"] = [
        report["identity"]["start_date"],
        report["identity"]["end_date"],
    ]
    ref = publish(tmp_path, report)
    with pytest.raises(DomainError) as ex:
        index_report(store, {"id": uid("bad")}, tmp_path, ref)
    assert ex.value.code == "SEALED_DATA_LEAK"
    report = deepcopy(original)
    report["schema_version"] = 99
    ref = publish(tmp_path, report)
    with pytest.raises(DomainError) as ex:
        index_report(store, {"id": uid("bad")}, tmp_path, ref)
    assert ex.value.code == "UNSUPPORTED_VERSION"


# Three real five-year replays exercise sealed-data failure/retry semantics.
# Keep this bounded without replacing the replay engine with a mock.
@pytest.mark.timeout(600)
def test_reveal_is_derived_idempotent_and_failure_does_not_unsee(web, system, monkeypatch):
    c, store, _ = web
    root, _, _ = system
    p = create_fixture(root / "inputs" / "sealed", symbols=1, sessions=1261)
    dsid = register_dataset(store, p, "五年合成封存样本")
    v = prepare(c, store, dsid, "封存原实验")
    response = post(
        c, "/runs", {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}
    )
    id = response.json()["data"]["id"]
    tick(store)
    original = store.get("run", id)
    assert original["status"] == "SUCCEEDED", original
    source = Path(original["source_root"]) / original["report_ref"]
    before = source.read_bytes()
    assert c.get(PREFIX + f"/runs/{id}/statistics?split=SEALED").status_code == 403
    status = c.get(PREFIX + f"/runs/{id}").json()["data"]
    payload = {
        "expected_registration_revision": status["registration_revision"],
        "confirmation": "REVEAL_SEALED",
        "reason": "执行已登记的封存核验",
    }
    child = post(c, f"/runs/{id}/reveal", payload).json()["data"]
    same = post(c, f"/runs/{id}/reveal", payload).json()["data"]
    assert child["id"] == same["id"] and child["logical_key"] != original["logical_key"]
    from src.services.signal_scanner.research import runner

    publish_fn = runner.publish

    def fail(*args, **kwargs):
        raise DomainError("SIMULATED_DISK_FULL")

    monkeypatch.setattr(runner, "publish", fail)
    tick(store)
    failed = store.get("run", child["id"])
    assert failed["status"] == "FAILED"
    assert c.get(PREFIX + f"/runs/{id}").json()["data"]["current_registration_revealed"] is True
    assert source.read_bytes() == before
    monkeypatch.setattr(runner, "publish", publish_fn)
    post(c, f"/runs/{child['id']}/retry", {"expected_attempt": 1})
    tick(store)
    revealed = store.get("run", child["id"])
    assert revealed["status"] == "SUCCEEDED", revealed
    assert c.get(PREFIX + f"/runs/{child['id']}/statistics?split=SEALED").status_code == 200
    assert source.read_bytes() == before


def test_worker_launch_removes_production_environment(system, monkeypatch):
    import sys

    import src.services.signal_scanner.worker.rule_validation as worker

    _, store, _ = system
    captured = {}
    monkeypatch.setenv("FINNHUB_API_KEY", "should-never-enter-worker")
    monkeypatch.setenv("DATABASE_URL", "production-connection")
    monkeypatch.setattr(sys, "argv", ["rule-validation", "--root", str(store.root), "worker", "--once"])

    def execute(executable, args, env):
        captured.update(executable=executable, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(worker.os, "execve", execute)
    with pytest.raises(SystemExit):
        worker.main()
    assert "FINNHUB_API_KEY" not in captured["env"] and "DATABASE_URL" not in captured["env"]
    assert captured["args"][-1] == "_worker" and captured["executable"] == sys.executable


def test_streaming_verification_matches_loader_with_unordered_pretty_json(web, completed, tmp_path):
    import hashlib

    from src.services.signal_scanner.research.reporting import load_report, publish
    from src.services.signal_scanner.services.rule_validation_web.query import index_report, summary

    _, store, _ = web
    original = load_report(completed["source_root"], completed["report_ref"])
    ref = publish(tmp_path, original)
    p = tmp_path / ref
    content = json.dumps(dict(reversed(list(original.items()))), indent=2, ensure_ascii=False)
    p.write_text(content)
    files = json.loads((p.parent / "files.json").read_text())
    files["report.json"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (p.parent / "files.json").write_text(canonical(files))
    assert load_report(tmp_path, ref)["business_output_hash"] == original["business_output_hash"]
    r = {"id": uid("stream")}
    index_report(store, r, tmp_path, ref)
    assert summary(r)["report_hash"] == original["business_output_hash"]


def test_label_selected_event_join_preserves_all_axes(web, completed):
    c, _, _ = web
    id = completed["id"]
    response = c.get(
        PREFIX
        + f"/runs/{id}/rows?section=events&event_scope=labels&horizon=20&cost_tier=10&entry_basis=NEXT_AVAILABLE_OPEN&split=EXPLORATORY&direction=LONG&rule_id=TR-01"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["filters"]["rule_id"] == "TR-01"
    assert all(r["rule_id"] == "TR-01" or r["rule_id"] == "VP-01" for r in response.json()["data"]["items"])


def test_csv_download_is_rectangular_and_carries_source_hash(web, completed):
    import csv
    import io

    c, _, _ = web
    response = c.get(PREFIX + f"/runs/{completed['id']}/artifacts/outcomes.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows and all(row["source_business_hash"] == completed["report_hash"] for row in rows)
    assert all(None not in row for row in rows)


def test_stream_reader_handles_small_chunks_unicode_and_exponents():
    import io

    from src.services.signal_scanner.services.rule_validation_web.streaming import Reader

    value = {"甲": 1e-40, "beta": [1, True, None, '汉字\\"', {"x": -2.3e19}]}
    content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    class SmallChunks(io.StringIO):
        def read(self, n=-1):
            return super().read(min(n, 2))

    reader = Reader(SmallChunks(content))
    assert {key: reader.value() for key in reader.members()} == value
    assert reader.peek() == ""


def test_concurrent_submission_and_queue_capacity(web):
    from concurrent.futures import ThreadPoolExecutor

    c, store, dsid = web
    draft = default_draft(store.get("dataset", dsid))
    draft["hypothesis"] = "并发受理"
    payload = {"dataset_id": dsid, "draft": draft}
    key = uid()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(c, "/validations", payload, key), range(2)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["data"]["id"] for r in responses}) == 1
    tick(store)
    ids = [uid("full") for _ in range(20)]
    with store.write() as cx:
        for id in ids:
            store.put("validation", {"id": id, "status": "QUEUED"}, cx)
    try:
        result = post(c, "/validations", payload)
        assert result.status_code == 429 and result.json()["error"]["code"] == "QUEUE_FULL"
    finally:
        with store.write() as cx:
            cx.executemany("DELETE FROM objects WHERE id=?", [(id,) for id in ids])


def test_real_process_termination_restart_and_same_input_retry(web):
    import os
    import subprocess
    import sys
    import time

    from src.services.signal_scanner.core import ROOT

    c, store, dsid = web
    v = prepare(c, store, dsid, "真实进程中断验收")
    r = post(
        c, "/runs", {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}
    ).json()["data"]
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ") if k in os.environ}
    env["PYTHONPATH"] = str(ROOT / "backend")
    command = [
        sys.executable,
        "-m",
        "src.services.signal_scanner.worker.rule_validation",
        "--root",
        str(store.root),
        "--input-root",
        str(store.input_roots[0]),
        "worker",
        "--once",
    ]
    process = subprocess.Popen(command, env=env, cwd=ROOT)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            progress = c.get(PREFIX + f"/runs/{r['id']}").json()["data"]
            if progress.get("processed") is not None:
                break
            time.sleep(0.1)
        assert progress["status"] == "RUNNING" and progress.get("engine_job_id")
        process.terminate()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    subprocess.run(command, env=env, cwd=ROOT, check=True, timeout=20)
    saved = store.get("run", r["id"])
    assert saved["status"] == "FAILED" and saved["error"]["code"] == "WORKER_INTERRUPTED"
    restarted = TestClient(create_app(c.app.state.settings))
    restarted.cookies.update(c.cookies)
    restarted.headers.update(c.headers)
    assert restarted.get(PREFIX + f"/runs/{r['id']}").json()["data"]["status"] == "FAILED"
    assert post(c, f"/runs/{r['id']}/retry", {"expected_attempt": 1}).status_code == 202
    subprocess.run(command, env=env, cwd=ROOT, check=True, timeout=60)
    retried = store.get("run", r["id"])
    assert retried["status"] == "SUCCEEDED" and retried["attempt"] == 2


def test_index_failure_keeps_report_unpublished_and_can_resume(web, monkeypatch):
    from src.services.signal_scanner.services.rule_validation_web import query

    c, store, dsid = web
    v = prepare(c, store, dsid, "索引写盘故障")
    r = post(
        c, "/runs", {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}
    ).json()["data"]
    original = query.index_report

    def fail(*args, **kwargs):
        raise DomainError("SIMULATED_INDEX_DISK_FULL")

    monkeypatch.setattr(query, "index_report", fail)
    tick(store)
    failed = store.get("run", r["id"])
    assert failed["status"] == "FAILED" and failed["report_state"] == "CORRUPT"
    assert c.get(PREFIX + f"/runs/{r['id']}/report").status_code == 409
    monkeypatch.setattr(query, "index_report", original)
    assert post(c, f"/runs/{r['id']}/retry", {"expected_attempt": 1}).status_code == 202
    tick(store)
    assert store.get("run", r["id"])["report_state"] == "READY"


def test_run_date_filters_and_invalid_range(web, completed):
    c, _, _ = web
    r = completed
    response = c.get(PREFIX + f"/runs?start_date={r['start_date']}&end_date={r['end_date']}")
    assert response.status_code == 200, response.text
    assert r["id"] in {item["id"] for item in response.json()["data"]["items"]}
    assert r["id"] not in {
        item["id"] for item in c.get(PREFIX + "/runs?end_date=2000-01-01").json()["data"]["items"]
    }
    assert c.get(PREFIX + "/runs?start_date=2026-10-23&end_date=2020-01-01").status_code == 422
    assert c.get(PREFIX + "/runs?start_date=invalid").status_code == 422


def test_parent_reference_uses_original_frozen_and_same_report(web, completed, tmp_path):
    from src.services.signal_scanner.research.reporting import load_report, publish
    from src.services.signal_scanner.research.statistics import lifecycle_counts
    from src.services.signal_scanner.services.rule_validation_web.query import event_detail, index_report

    _, store, _ = web
    report = deepcopy(load_report(completed["source_root"], completed["report_ref"]))
    parent = report["events"][0]
    child = deepcopy(parent)
    child.update(
        event_id="manual-child",
        rule_id="TR-03",
        lifecycle="PENDING",
        confirmed_at=None,
        confirmation_market_date=None,
    )
    child["frozen"]["parent_event_id"] = parent["event_id"]
    report["events"].append(child)
    report["lifecycle_counts"] = lifecycle_counts(report["events"], report["assessments"])
    ref = publish(tmp_path, report)
    r = {"id": uid("parent")}
    index_report(store, r, tmp_path, ref)
    detail = event_detail(r, "manual-child")
    assert detail["parent_refs"][0]["event_id"] == parent["event_id"]
    assert detail["parent_refs"][0]["status"] == "AVAILABLE"
