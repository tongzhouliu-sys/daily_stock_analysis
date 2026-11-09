"""DSA authentication and lifecycle adapter for the imported scanner domain."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from src.services.signal_scanner.core import ROOT, Settings, require

logger = logging.getLogger(__name__)


class HostAuth:
    """Use DSA's current session, retaining scanner write-CSRF protection."""

    def config(self):
        return {"provider": "dsa"}

    def session(self, request):
        from src import auth

        token = request.cookies.get(auth.COOKIE_NAME, "")
        require(not auth.is_auth_enabled() or auth.verify_session(token), "AUTH_REQUIRED", status=401)
        secret = auth._load_session_secret()
        require(secret is not None, "AUTH_UNAVAILABLE", status=503)
        csrf = hmac.new(secret, b"scanner-csrf-v1:" + token.encode(), hashlib.sha256).hexdigest()
        return {"id": "dsa-owner", "csrf_token": csrf, "expires_at": None}

    def public_session(self, request):
        return {"authenticated": True, "managed_by": "dsa", **self.session(request)}

    def validate_write(self, request, session):
        origin = urlsplit(request.headers.get("origin", ""))
        configured = os.environ.get("SCANNER_ORIGIN", "").strip()
        expected = urlsplit(configured or str(request.base_url))
        require(
            origin.scheme in ("http", "https")
            and (origin.scheme, origin.netloc) == (expected.scheme, expected.netloc),
            "ORIGIN_DENIED", status=403,
        )
        require(
            hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf_token"]),
            "CSRF_INVALID", status=403,
        )


class ScannerHost:
    """Own the mounted API and child processes without importing old credentials."""

    def __init__(self):
        self.application = None
        self.settings = None
        self.processes = []
        self.streams = []
        self.lock = threading.RLock()
        self.kinds = []
        self.stopping = threading.Event()
        self.monitor = None

    def _spawn(self, kind, stream):
        command = (
            [sys.executable, "--signal-scanner-worker", kind]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "src.services.signal_scanner.host_worker", kind]
        )
        return subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            env={**os.environ, "SCANNER_RUNTIME": str(self.settings.runtime),
                 "PYTHONPATH": str(ROOT)},
            start_new_session=os.name == "posix",
        )

    def _supervise(self):
        # A failed collector/research process must not leave a healthy Web page
        # with a permanently stalled queue. Rate-limit restarts to this interval.
        while not self.stopping.wait(5):
            with self.lock:
                if self.stopping.is_set():
                    return
                for index, child in enumerate(self.processes):
                    if child.poll() is not None:
                        kind = self.kinds[index]
                        logger.warning("Restarting scanner %s worker after exit %s", kind, child.returncode)
                        try:
                            self.processes[index] = self._spawn(kind, self.streams[index])
                        except OSError:
                            logger.exception("Unable to restart scanner %s worker", kind)

    def initialize(self):
        with self.lock:
            if self.application is not None:
                return self.application
            from src.services.signal_scanner.main import create_app
            from src.services.signal_scanner.services.rule_validation_web.store import Store

            settings = Settings.load()
            for name in ("imports", "backups", "logs"):
                (settings.runtime / name).mkdir(parents=True, exist_ok=True, mode=0o700)
            application = create_app(settings, host_auth=HostAuth(), api_prefix="")
            Store.configured(settings.runtime).initialize()
            self.settings = settings
            self.application = application
            return application

    def start(self):
        if os.environ.get("SCANNER_WORKERS_ENABLED", "true").lower() != "true":
            return
        with self.lock:
            self.initialize()
            if self.processes:
                return
            kinds = ["scan", "research"]
            if os.environ.get("SCANNER_DAILY_MARKET", "false").lower() == "true":
                kinds.append("market")
            self.stopping.clear()
            try:
                for kind in kinds:
                    stream = (self.settings.runtime / "logs" / f"{kind}.log").open("ab")
                    self.streams.append(stream)
                    self.kinds.append(kind)
                    self.processes.append(self._spawn(kind, stream))
                self.monitor = threading.Thread(target=self._supervise, name="scanner-supervisor", daemon=True)
                self.monitor.start()
            except Exception:
                self.stop()
                raise

    def stop(self):
        self.stopping.set()
        with self.lock:
            import signal

            for child in self.processes:
                if child.poll() is None:
                    if os.name == "posix":
                        try:
                            os.killpg(child.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    else:
                        # Research jobs have their own execution child on Windows.
                        # Terminate the owned tree before its parent PID disappears.
                        subprocess.run(
                            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=False,
                        )
            for child in self.processes:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        child.kill()
                    child.wait(timeout=5)
            self.processes.clear()
            self.kinds.clear()
            for stream in self.streams:
                stream.close()
            self.streams.clear()
        if self.monitor and self.monitor is not threading.current_thread():
            self.monitor.join(timeout=6)
            self.monitor = None

    async def __call__(self, scope, receive, send):
        try:
            application = await run_in_threadpool(self.initialize)
        except (ImportError, OSError) as exc:
            logger.error("Scanner initialization failed: %s", type(exc).__name__)
            await JSONResponse({"error": {"code": "SCANNER_UNAVAILABLE",
                                           "message": "扫描器无法初始化，请检查依赖与运行目录"}},
                               status_code=503)(scope, receive, send)
            return
        await application(scope, receive, send)

