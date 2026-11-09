"""Migration uses real databases and files, preserving source and protected data."""

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.middlewares.legacy_scanner import LegacyScannerRedirectMiddleware
from scripts.signal_scanner.migrate_runtime import migrate


def test_runtime_copy_preserves_data_and_removes_old_sessions(tmp_path):
    source, destination = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    with sqlite3.connect(source / "scanner.sqlite3") as cx:
        cx.executescript("""
        CREATE TABLE job_runs(id TEXT, status TEXT);
        INSERT INTO job_runs VALUES('historical', 'SUCCEEDED');
        CREATE TABLE auth_sessions(token TEXT);
        INSERT INTO auth_sessions VALUES('old-private-cookie');
        """)
    (source / "auth.json").write_text("old-password-hash")
    (source / "imports").mkdir()
    (source / "imports" / "bars.json").write_text('{"close": "100.00"}')
    (source / "imports" / "worker.lock").touch()
    result = migrate(source, destination)
    assert result["table_counts"]["job_runs"] == 1
    assert result["table_counts"]["auth_sessions"] == 0
    assert result["verified_files"] == 1
    assert (destination / "imports" / "bars.json").read_bytes() == (source / "imports" / "bars.json").read_bytes()
    assert not (destination / "auth.json").exists()
    assert not (destination / "imports" / "worker.lock").exists()
    with sqlite3.connect(source / "scanner.sqlite3") as cx:
        assert cx.execute("SELECT token FROM auth_sessions").fetchone()[0] == "old-private-cookie"
    with pytest.raises(ValueError, match="must not exist"):
        migrate(source, destination)


def test_active_source_is_not_published(tmp_path):
    source, destination = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    with sqlite3.connect(source / "scanner.sqlite3") as cx:
        cx.executescript("CREATE TABLE job_runs(status TEXT); INSERT INTO job_runs VALUES('RUNNING');")
    with pytest.raises(ValueError, match="running jobs"):
        migrate(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".scanner-migration-*"))


def test_legacy_entry_redirects_to_one_login_without_replaying_writes(monkeypatch):
    monkeypatch.setenv("SCANNER_LEGACY_HOST", "old.example.test")
    monkeypatch.setenv("SCANNER_ORIGIN", "https://research.example.test")
    app = FastAPI()
    app.add_middleware(LegacyScannerRedirectMiddleware)
    client = TestClient(app, base_url="https://old.example.test")
    result = client.get("/signals/card-1?revision=2", follow_redirects=False)
    assert result.status_code == 302
    assert result.headers["location"] == "https://research.example.test/scanner/signals/card-1?revision=2"
    assert client.post("/api/auth/login", json={"password": "old-password"}).status_code == 409
    assert client.get("/login", follow_redirects=False).headers["location"] == "https://research.example.test/scanner"
    assert client.get("https://research.example.test/scanner", follow_redirects=False).status_code == 404
