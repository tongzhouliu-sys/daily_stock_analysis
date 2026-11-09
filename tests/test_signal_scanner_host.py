"""Exercise the real mounted scanner with DSA auth and its real SQLite writes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import json
import time

from src import auth
from src.services.signal_scanner.core import Settings
from src.services.signal_scanner.host import HostAuth, ScannerHost
from src.services.signal_scanner.main import create_app


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda value: value == "valid-dsa-session")
    monkeypatch.delenv("SCANNER_ORIGIN", raising=False)
    child = create_app(Settings(tmp_path), host_auth=HostAuth(), api_prefix="")
    parent = FastAPI()
    parent.mount("/api/v1/scanner", child)
    client = TestClient(parent)
    return client, child, tmp_path


def authenticate(client):
    client.cookies.set(auth.COOKIE_NAME, "valid-dsa-session")
    response = client.get("/api/v1/scanner/auth/session")
    assert response.status_code == 200
    assert response.json()["data"]["managed_by"] == "dsa"
    return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["data"]["csrf_token"],
            "Idempotency-Key": "host-preferences-1"}


def test_host_session_and_dynamic_routes(mounted):
    client, _, runtime = mounted
    assert client.get("/api/v1/scanner/dashboard").status_code == 401
    authenticate(client)
    assert client.get("/api/v1/scanner/dashboard").status_code == 200
    assert client.get("/api/v1/scanner/configs/preferences").json()["data"]["key"] == "preferences"
    assert client.get("/api/v1/scanner/signals/missing").status_code == 404
    assert not (runtime / "auth.json").exists()
    client.cookies.clear()
    assert client.get("/api/v1/scanner/configs/preferences").status_code == 401


def test_csrf_origin_idempotency_and_no_duplicate_configuration_write(mounted):
    client, child, _ = mounted
    headers = authenticate(client)
    original = client.get("/api/v1/scanner/configs/preferences").json()["data"]
    payload = {"expected_version": original["version"], "change_reason": "Host integration test",
               "value": {**original["value"], "display_timezone": "UTC"}}
    path = "/api/v1/scanner/configs/preferences"
    assert client.put(path, json=payload).status_code == 403
    assert client.put(path, json=payload, headers={**headers, "Origin": "https://untrusted.invalid"}).status_code == 403
    assert client.put(path, json=payload, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    first = client.put(path, json=payload, headers=headers)
    assert first.status_code == 200, first.text
    second = client.put(path, json=payload, headers=headers)
    assert second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    with child.state.db.read() as cx:
        assert cx.execute("SELECT count(*) FROM config_versions WHERE config_key='preferences'").fetchone()[0] == 2
    changed = {**payload, "change_reason": "Changed request"}
    assert client.put(path, json=changed, headers=headers).status_code == 409


def test_host_research_initialization_and_disabled_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_RUNTIME", str(tmp_path))
    monkeypatch.setenv("SCANNER_WORKERS_ENABLED", "false")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    host = ScannerHost()
    assert host.application is None
    host.start()
    assert not host.processes and host.application is None
    app = FastAPI()
    app.mount("/api/v1/scanner", host)
    client = TestClient(app)
    response = client.get("/api/v1/scanner/rule-validation/catalog")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["runtime_ready"]
    assert not host.processes
    assert not (tmp_path / "auth.json").exists()


def test_managed_workers_start_once_and_stop_with_host(tmp_path, monkeypatch):
    """Run real isolated processes; verify both entry points and shutdown."""
    monkeypatch.setenv("SCANNER_RUNTIME", str(tmp_path))
    monkeypatch.setenv("SCANNER_WORKERS_ENABLED", "true")
    monkeypatch.setenv("SCANNER_DAILY_MARKET", "false")
    monkeypatch.delenv("SCANNER_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("RULE_VALIDATION_WEB_ROOT", raising=False)
    monkeypatch.delenv("RULE_VALIDATION_INPUT_ROOTS", raising=False)
    host = ScannerHost()
    children = []
    try:
        host.start()
        children = list(host.processes)
        host.start()
        assert host.processes == children and len(children) == 2
        from src.services.signal_scanner.services.rule_validation_web.store import Store

        research = Store.configured(tmp_path)
        heartbeat = tmp_path / "worker-heartbeat.json"
        worker_pid = research.root / "worker.pid"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert all(p.poll() is None for p in children), [
                p.read_text() for p in (tmp_path / "logs").glob("*.log")
            ]
            if heartbeat.exists() and worker_pid.exists():
                break
            time.sleep(0.1)
        assert json.loads(heartbeat.read_text())["pid"] == children[0].pid
        assert int(worker_pid.read_text()) == children[1].pid
        # Exercise recovery with a real crashed process, not a mocked Popen.
        crashed = children[0]
        crashed.terminate()
        crashed.wait(timeout=10)
        deadline = time.monotonic() + 15
        while host.processes[0] is crashed and time.monotonic() < deadline:
            time.sleep(0.1)
        assert host.processes[0] is not crashed
        children.append(host.processes[0])
        assert host.processes[0].poll() is None
    finally:
        host.stop()
    assert not host.processes and not host.streams
    assert all(p.poll() is not None for p in children)


def test_packaged_entry_dispatches_before_loading_private_configuration(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import runpy
    import sys

    private_env = tmp_path / ".env"
    private_env.write_text("SCANNER_PRIVATE_SENTINEL=must-not-enter-research\n")
    monkeypatch.setenv("ENV_FILE", str(private_env))
    monkeypatch.delenv("SCANNER_PRIVATE_SENTINEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--signal-scanner-entry",
                                    "src.services.signal_scanner.research.cli", "--help"])

    def entry(module, run_name):
        assert module == "src.services.signal_scanner.research.cli"
        assert run_name == "__main__"
        assert "SCANNER_PRIVATE_SENTINEL" not in os.environ
        assert sys.argv[1:] == ["--help"]

    monkeypatch.setattr(runpy, "run_module", entry)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "main.py"), run_name="__main__")
    assert result.value.code == 0


def test_real_dsa_login_cookie_and_logout_protect_scanner(tmp_path, monkeypatch):
    from api.middlewares.auth import add_auth_middleware
    from api.v1.endpoints.auth import router as auth_router

    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_AUTH_ENABLED=true\nADMIN_USERNAME=scanner-test-admin\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("ADMIN_USERNAME", "scanner-test-admin")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "host.db"))
    monkeypatch.delenv("SCANNER_ORIGIN", raising=False)
    for name in ("_auth_enabled", "_session_secret", "_password_hash_salt", "_password_hash_stored"):
        monkeypatch.setattr(auth, name, None)
    monkeypatch.setattr(auth, "_rate_limit", {})

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")
    add_auth_middleware(app)
    app.mount("/api/v1/scanner", create_app(Settings(tmp_path / "scanner"),
                                          host_auth=HostAuth(), api_prefix=""))
    client = TestClient(app)
    assert client.get("/api/v1/scanner/dashboard").status_code == 401
    response = client.post("/api/v1/auth/login", json={
        "username": "scanner-test-admin", "password": "Test-only-credential-123!",
        "passwordConfirm": "Test-only-credential-123!",
    })
    assert response.status_code == 200, response.text
    token = client.cookies.get(auth.COOKIE_NAME)
    assert token and auth.verify_session(token)
    session = client.get("/api/v1/scanner/auth/session").json()["data"]
    assert session["managed_by"] == "dsa" and session["csrf_token"]
    assert client.get("/api/v1/scanner/dashboard").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/scanner/dashboard").status_code == 401
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get("/api/v1/scanner/dashboard").status_code == 401
    assert not (tmp_path / "scanner" / "auth.json").exists()


def test_csrf_survives_host_recreation_but_not_session_rotation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda value: value == "session")
    request = SimpleNamespace(cookies={auth.COOKIE_NAME: "session"})
    first = HostAuth().session(request)["csrf_token"]
    assert HostAuth().session(request)["csrf_token"] == first
    assert auth.rotate_session_secret()
    assert HostAuth().session(request)["csrf_token"] != first
