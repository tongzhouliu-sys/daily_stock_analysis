import pytest

from src.services.signal_scanner.core import Settings, digest, now, uid
from src.services.signal_scanner.cli import fixture, restore
from src.services.signal_scanner.storage.db import Database, one, many
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.jobs import Worker, enqueue
from src.services.signal_scanner.worker.pipeline import calculate, business_view, latest_workspace


@pytest.fixture
def environment(tmp_path):
    s = Settings(tmp_path / "fictional")
    fixture(s)
    return s, Worker(s)


def run(worker, name, progress=None):
    with worker.db.transaction() as cx:
        jid = enqueue(cx, name, name + ":" + uid(), worker.cal.last_completed(now()), progress or {})
    assert worker.run_one()
    with worker.db.read() as cx:
        return one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))


def test_scan_publishes_consistent_fixture(environment):
    s, w = environment
    with w.db.read() as cx:
        j, space = latest_workspace(cx, w.files)
    assert space["coverage"] == dict(expected=2, ok=2, failed=0) and len(space["cards"]) > 0
    assert space["provider_health"]["connection"] == "UP"
    assert space["provider_health"]["permission"] == "UNKNOWN"
    assert space["provider_health"]["freshness"] == "OK" and space["provider_health"]["quality"] == "OK"
    manifest = w.files.read(j["input_manifest_ref"])
    assert digest(business_view(calculate(manifest, w.cal))) == space["business_output_hash"]


def test_CT40_weekly_replay_and_missing_inputs(environment):
    s, w = environment
    j = run(w, "WEEKLY_REPORT")
    assert j["status"] == "PARTIAL"
    report = w.files.read(j["output_manifest_ref"])
    assert report["replay_probe"]["status"] == "PASS", report["replay_probe"]
    assert any(c["status"] == "NOT_CHECKED" for c in report["data_health"])
    with w.db.read() as cx:
        scan = one(
            cx,
            "SELECT * FROM job_runs WHERE job_name='DAILY_SCAN' AND status='SUCCEEDED' ORDER BY published_seq DESC LIMIT 1",
        )
    (w.files.root / scan["input_manifest_ref"]).unlink()
    j = run(w, "WEEKLY_REPORT")
    report = w.files.read(j["output_manifest_ref"])
    assert report["replay_probe"]["status"] == "FAIL"


def test_backup_restore_keeps_files_and_does_not_resend(environment, tmp_path):
    s, w = environment
    j = run(w, "BACKUP")
    assert j["status"] == "SUCCEEDED"
    report = w.files.read(j["output_manifest_ref"])
    archive = s.runtime / "backups" / report["archive"]
    target = tmp_path / "restored"
    restore(archive, target)
    assert (target / "imports" / "market.json").read_bytes() == (
        s.runtime / "imports" / "market.json"
    ).read_bytes()
    assert (target / "imports" / "events.json").read_bytes() == (
        s.runtime / "imports" / "events.json"
    ).read_bytes()
    db = Database(target / "scanner.sqlite3")
    with db.read() as cx:
        assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        job, space = latest_workspace(cx, Artifacts(target / "artifacts"))
        assert space["coverage"]["ok"] == 2
        assert not many(cx, "SELECT id FROM notifications WHERE status IN ('PENDING','SENDING')")


def test_research_uses_same_offline_engine(environment, monkeypatch):
    s, w = environment
    import httpx

    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: pytest.fail("Offline replay attempted network"))
    j = run(w, "REPLAY", dict(mode="REPLAY"))
    assert j["status"] == "SUCCEEDED", j["error_json"]
    report = w.files.read(j["output_manifest_ref"])
    assert report["approval"] == "RESEARCH"
    assert report["paper_strategy"]["status"] in ("COMPUTED", "NO_FILLS", "INSUFFICIENT_INPUTS")


def test_frozen_files_cannot_be_substituted(environment):
    s, w = environment
    ref = w.files.save(dict(frozen=True))
    (w.files.root / ref).write_text("{}")
    from src.services.signal_scanner.core import DomainError

    with pytest.raises(DomainError, match="ARTIFACT_CORRUPT"):
        w.files.read(ref)


def test_digest_does_not_relabel_research_as_production(environment):
    s, w = environment
    j = run(w, "DIGEST")
    assert j["status"] == "SUCCEEDED"
    report = w.files.read(j["output_manifest_ref"])
    assert report["production_candidates"] == [] and report["research_count"] > 0


def test_requested_point_in_time_does_not_relabel_revised_history(environment):
    from src.services.signal_scanner.cli import activate
    from src.services.signal_scanner.services.config import get_config

    s, w = environment
    with w.db.transaction() as cx:
        value = get_config(cx, "experiment")["value"]
        activate(cx, "experiment", {**value, "data_mode": "POINT_IN_TIME"})
    job = run(w, "REPLAY")
    assert job["status"] == "SUCCEEDED", job["error_json"]
    report = w.files.read(job["output_manifest_ref"])
    assert report["requested_data_mode"] == "POINT_IN_TIME" and report["data_mode"] == "REVISED_HISTORY"
    assert report["degradation_reason"] == "POINT_IN_TIME_HISTORY_UNVERIFIED"
