import hashlib
import hmac
import json
import os
import secrets
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from src.services.signal_scanner.core import require, now, stamp, uid, instant
from src.services.signal_scanner.storage.db import insert, one


class Auth:
    def __init__(self, settings):
        self.settings = settings
        self.attempts = defaultdict(deque)

    def provision(self):
        folder = self.settings.runtime
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        path = folder / "auth.json"
        if path.exists():
            return False
        password = secrets.token_urlsafe(20)
        for target, content in (
            (
                path,
                json.dumps(dict(password_hash=PasswordHasher().hash(password), secret=secrets.token_hex(32))),
            ),
            (folder / "首次登录密码.txt", password + "\n"),
        ):
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(content)
        return True

    def config(self):
        path = self.settings.runtime / "auth.json"
        require(path.exists(), "DEPENDENCY_UNAVAILABLE", "先运行初始化命令", 503)
        require(path.stat().st_mode & 0o077 == 0, "DEPENDENCY_UNAVAILABLE", "认证配置文件权限须为600", 503)
        return json.loads(path.read_text())

    def csrf(self, token):
        return hmac.new(bytes.fromhex(self.config()["secret"]), token.encode(), "sha256").hexdigest()

    def login(self, cx, password, host):
        q = self.attempts[host]
        cut = now() - timedelta(minutes=1)
        while q and q[0] < cut:
            q.popleft()
        require(len(q) < 5, "RATE_LIMITED", "请稍后重试", 429)
        q.append(now())
        try:
            valid = PasswordHasher().verify(self.config()["password_hash"], password)
        except VerificationError:
            valid = False
        require(valid, "AUTH_REQUIRED", "密码不正确", 401)
        token = secrets.token_urlsafe(32)
        csrf = self.csrf(token)
        expiry = stamp(now() + timedelta(hours=12))
        insert(
            cx,
            "auth_sessions",
            dict(
                id=uid("session"),
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
                expires_at=expiry,
                revoked_at=None,
            ),
        )
        return token, dict(authenticated=True, csrf_token=csrf, expires_at=expiry)

    def session(self, cx, token):
        require(token, "AUTH_REQUIRED", status=401)
        s = one(
            cx,
            "SELECT * FROM auth_sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        require(s and not s["revoked_at"] and instant(s["expires_at"]) > now(), "SESSION_EXPIRED", status=401)
        return s

    def validate_write(self, request, session):
        require(request.headers.get("origin") == self.settings.origin, "ORIGIN_DENIED", status=403)
        token = request.headers.get("x-csrf-token", "")
        require(
            hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), session["csrf_hash"]),
            "CSRF_INVALID",
            status=403,
        )
