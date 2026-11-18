import os
import time
from unittest.mock import patch

import pytest

from src.services.signal_scanner.core import DomainError, Settings, digest, now, uid
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


# ---------- Backup retention, space guard, orphan cleanup ----------


def _make_fake_backup(folder, name, mtime=None):
    """Create a fake .zip and .sqlite3 pair in folder for retention testing."""
    zp = folder / (name + ".zip")
    dbp = folder / (name + ".sqlite3")
    zp.write_bytes(b"PK\x03\x04fake")
    dbp.write_bytes(b"SQLite format 3\0fake")
    if mtime is not None:
        os.utime(zp, (mtime, mtime))
        os.utime(dbp, (mtime, mtime))
    return zp, dbp


def test_backup_retention_respects_configurable_keep_counts(environment, monkeypatch):
    """Retention should honour SCANNER_BACKUP_KEEP_DAILY / SCANNER_BACKUP_KEEP_WEEKLY."""
    s, w = environment
    folder = s.runtime / "backups"
    folder.mkdir(exist_ok=True, parents=True)

    # Seed 5 old fake backups, each >1 day apart so they fall in different weeks.
    now_ts = time.time()
    for i in range(5):
        _make_fake_backup(folder, f"old_{i}", mtime=now_ts - (i + 1) * 86400 * 2)

    monkeypatch.setenv("SCANNER_BACKUP_KEEP_DAILY", "2")
    monkeypatch.setenv("SCANNER_BACKUP_KEEP_WEEKLY", "1")

    j = run(w, "BACKUP")
    assert j["status"] == "SUCCEEDED"

    remaining_zips = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    # 2 daily + 1 weekly = at most 3
    assert len(remaining_zips) <= 3
    # The latest backup (just created) must be kept.
    assert (folder / j["error_json"].get("archive", "") if False else folder / (j["id"] + ".zip")).exists() or any(
        z.name == j["id"] + ".zip" for z in remaining_zips
    )
    # Standalone .sqlite3 should be removed after zip is created (req #4).
    assert not (folder / (j["id"] + ".sqlite3")).exists()


def test_backup_fails_with_BACKUP_NO_SPACE_when_disk_full(environment, monkeypatch):
    """When disk is full and cleanup cannot free enough, backup should fail with BACKUP_NO_SPACE and leave no half-written files."""
    s, w = environment
    folder = s.runtime / "backups"
    folder.mkdir(exist_ok=True, parents=True)

    monkeypatch.setenv("SCANNER_BACKUP_KEEP_DAILY", "0")
    monkeypatch.setenv("SCANNER_BACKUP_KEEP_WEEKLY", "0")

    # Simulate disk full: shutil.disk_usage returns free=0.
    import shutil as _shutil

    real_disk_usage = _shutil.disk_usage

    def _fake_disk_usage(path):
        usage = real_disk_usage(str(path))
        return os.statvfs_result((0, 0, 0, 0, 0, 0, 0, 0, 0, 0))  # f_frsize * f_bavail = 0

    # Easier: patch shutil.disk_usage on the module where it is looked up.
    import src.services.signal_scanner.worker.jobs as jobs_mod

    original_disk_usage = jobs_mod.shutil.disk_usage

    class _FakeUsage:
        def __init__(self, free):
            self.free = free

    def _fake_du(path):
        return _FakeUsage(0)

    jobs_mod.shutil.disk_usage = _fake_du
    try:
        with pytest.raises(DomainError) as exc_info:
            # Call backup directly so the DomainError propagates (run_one swallows it).
            with w.db.transaction() as cx:
                jid = enqueue(cx, "BACKUP", "BACKUP:diskfull:" + uid(), w.cal.last_completed(now()), {})
            with w.db.read() as cx:
                jj = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
            w.backup(jj)
        assert exc_info.value.code == "BACKUP_NO_SPACE"
    finally:
        jobs_mod.shutil.disk_usage = original_disk_usage

    # No half-written files should remain.
    assert not (folder / (jid + ".sqlite3")).exists()
    assert not (folder / (jid + ".sqlite3-journal")).exists()
    assert not (folder / (jid + ".zip")).exists()


def test_backup_space_guard_triggers_cleanup_before_failing(environment, monkeypatch):
    """When disk is low, backup should first try to clean old backups; if still not enough, fail cleanly."""
    s, w = environment
    folder = s.runtime / "backups"
    folder.mkdir(exist_ok=True, parents=True)

    # Seed old backups that retention can clean.
    now_ts = time.time()
    for i in range(5):
        _make_fake_backup(folder, f"ancient_{i}", mtime=now_ts - (i + 1) * 86400 * 10)

    monkeypatch.setenv("SCANNER_BACKUP_KEEP_DAILY", "0")
    monkeypatch.setenv("SCANNER_BACKUP_KEEP_WEEKLY", "0")

    import src.services.signal_scanner.worker.jobs as jobs_mod

    original_disk_usage = jobs_mod.shutil.disk_usage

    call_count = {"n": 0}

    class _FakeUsage:
        def __init__(self, free):
            self.free = free

    def _fake_du(path):
        call_count["n"] += 1
        # First call: low space -> triggers cleanup.
        # Second call (after cleanup): still not enough -> fail.
        if call_count["n"] <= 1:
            return _FakeUsage(100)
        return _FakeUsage(100)

    jobs_mod.shutil.disk_usage = _fake_du
    try:
        with pytest.raises(DomainError) as exc_info:
            with w.db.transaction() as cx:
                jid = enqueue(cx, "BACKUP", "BACKUP:lowsp:" + uid(), w.cal.last_completed(now()), {})
            with w.db.read() as cx:
                jj = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
            w.backup(jj)
        assert exc_info.value.code == "BACKUP_NO_SPACE"
    finally:
        jobs_mod.shutil.disk_usage = original_disk_usage

    # Old backups should have been cleaned up even though the new backup failed.
    remaining = list(folder.glob("*.zip"))
    assert len(remaining) == 0


def test_orphan_sqlite3_and_journal_are_swept(environment, monkeypatch):
    """Leftover .sqlite3 / .sqlite3-journal without a matching .zip should be removed at the start of backup."""
    s, w = environment
    folder = s.runtime / "backups"
    folder.mkdir(exist_ok=True, parents=True)

    # Create orphan files (no matching .zip).
    orphan_db = folder / "orphan_job.sqlite3"
    orphan_journal = folder / "orphan_job.sqlite3-journal"
    orphan_db.write_bytes(b"fake")
    orphan_journal.write_bytes(b"fake")
    assert orphan_db.exists() and orphan_journal.exists()

    monkeypatch.setenv("SCANNER_BACKUP_KEEP_DAILY", "2")
    monkeypatch.setenv("SCANNER_BACKUP_KEEP_WEEKLY", "1")

    j = run(w, "BACKUP")
    assert j["status"] == "SUCCEEDED"

    # Orphans should be gone.
    assert not orphan_db.exists()
    assert not orphan_journal.exists()
    # The successful backup's own .sqlite3 should also be removed (it's inside the zip).
    assert not (folder / (j["id"] + ".sqlite3")).exists()
    # But the .zip should exist.
    assert (folder / (j["id"] + ".zip")).exists()
