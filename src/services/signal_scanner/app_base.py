"""Shared application context and middleware for the signal scanner ASGI app.

Extracted from ``main.create_app`` so the factory stays small and each route
group can be reviewed independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.services.signal_scanner.api import models as m
from src.services.signal_scanner.core import (
    DomainError,
    digest,
    now,
    require,
    stamp,
    uid,
)
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.storage.db import Database, insert, one
from src.services.signal_scanner.storage.files import Artifacts


@dataclass
class AppContext:
    """Runtime dependencies shared by every route group."""

    settings: Any
    db: Database
    files: Artifacts
    auth: Any
    cal: Calendar
    host_auth: Optional[Any]
    api_prefix: str

    def envelope(self, data: Any) -> dict:
        return dict(request_id=uid("request"), as_of=stamp(), data=data, warnings=[])

    def authenticated_session(self, request: Request, cx: Any) -> Any:
        if self.host_auth is not None:
            return self.host_auth.session(request)
        return self.auth.session(cx, request.cookies.get("scanner_session"))

    def mutate(self, request: Request, payload: Any, fn: Any, *, versioned: bool = False) -> Any:
        key = request.headers.get("idempotency-key")
        require(key and len(key) <= 200, "MALFORMED_REQUEST", "缺少Idempotency-Key", 400)
        if versioned:
            match = request.headers.get("if-match")
            require(match, "EXPECTED_REVISION_REQUIRED", status=428)
            require(match.strip('"') == str(payload.expected_revision), "REVISION_CONFLICT")
        value = payload.model_dump() if hasattr(payload, "model_dump") else payload
        request_hash = digest(value)
        with self.db.transaction() as cx:
            self.authenticated_session(request, cx)
            old = one(
                cx,
                "SELECT * FROM idempotency_requests "
                "WHERE actor_id='owner' AND method=? AND route_scope=? AND key=?",
                (request.method, request.url.path, key),
            )
            if old:
                require(old["request_hash"] == request_hash, "IDEMPOTENCY_CONFLICT")
                return old["response_json"]
            result = fn(cx, key)
            insert(
                cx,
                "idempotency_requests",
                dict(
                    id=uid("idem"),
                    actor_id="owner",
                    method=request.method,
                    route_scope=request.url.path,
                    key=key,
                    request_hash=request_hash,
                    state="COMMITTED",
                    http_status=200,
                    response_json=result,
                    expires_at=stamp(now() + timedelta(days=30)),
                ),
            )
        return result


def envelope(data: Any) -> dict:
    """Build the standard response envelope (kept for legacy call sites)."""
    return dict(request_id=uid("request"), as_of=stamp(), data=data, warnings=[])


async def domain_error_response(request: Request, ex: DomainError) -> JSONResponse:
    return JSONResponse(
        dict(
            request_id=uid("request"),
            error=dict(
                code=ex.code,
                message=ex.message,
                details=ex.details,
                retryable=ex.status in (429, 503),
            ),
        ),
        status_code=ex.status,
        headers={"Retry-After": "60"} if ex.status == 429 else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, ex: DomainError) -> JSONResponse:
        return await domain_error_response(request, ex)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, ex: RequestValidationError) -> JSONResponse:
        malformed = any(e["type"] == "json_invalid" for e in ex.errors())
        return JSONResponse(
            dict(
                request_id=uid("request"),
                error=dict(
                    code="MALFORMED_REQUEST" if malformed else "VALIDATION_ERROR",
                    message="请求字段格式不正确",
                    details=dict(
                        fields=[
                            dict(field=".".join(str(x) for x in e["loc"]), type=e["type"])
                            for e in ex.errors()
                        ]
                    ),
                    retryable=False,
                ),
            ),
            status_code=400 if malformed else 422,
        )


def register_security_middleware(app: FastAPI, ctx: AppContext) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if int(request.headers.get("content-length", "0") or "0") > 5 * 1024 * 1024:
            return JSONResponse(
                dict(error=dict(code="PAYLOAD_TOO_LARGE", message="请求上限5MB")),
                status_code=413,
            )
        path = request.scope["path"]
        prefix = request.scope.get("root_path", "")
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        try:
            if path.startswith(f"{ctx.api_prefix}/") and path != f"{ctx.api_prefix}/auth/login":
                with ctx.db.read() as cx:
                    session = ctx.authenticated_session(request, cx)
                request.state.session = session
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    ctx.auth.validate_write(request, session)
            if path == f"{ctx.api_prefix}/auth/login" and request.method == "POST":
                require(
                    request.headers.get("origin") == ctx.settings.origin,
                    "ORIGIN_DENIED",
                    status=403,
                )
            result = await call_next(request)
            result.headers["X-Content-Type-Options"] = "nosniff"
            result.headers["Referrer-Policy"] = "same-origin"
            result.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; frame-ancestors 'none'; connect-src 'self'"
            )
            if path.startswith(f"{ctx.api_prefix}/"):
                result.headers["Cache-Control"] = "no-store"
            return result
        except DomainError as ex:
            return await domain_error_response(request, ex)


def register_health_auth_routes(app: FastAPI, ctx: AppContext) -> None:
    @app.get("/health/live")
    def live():
        return ctx.envelope(dict(status="OK"))

    @app.get("/health/ready")
    def ready():
        with ctx.db.read() as cx:
            require(
                cx.execute("PRAGMA user_version").fetchone()[0] == 1,
                "DEPENDENCY_UNAVAILABLE",
                status=503,
            )
        ctx.auth.config()
        return ctx.envelope(dict(status="READY", database="OK", schema="OK"))

    @app.post(f"{ctx.api_prefix}/auth/login")
    def login(p: m.Login, request: Request):
        require(ctx.host_auth is None, "HOST_AUTH_MANAGED", "请使用 DSA 登录", 409)
        with ctx.db.transaction() as cx:
            token, result = ctx.auth.login(cx, p.password, request.client.host)
        response = JSONResponse(ctx.envelope(result))
        response.set_cookie(
            "scanner_session",
            token,
            httponly=True,
            samesite="strict",
            secure=ctx.settings.secure_cookie,
            max_age=43200,
            path="/",
        )
        return response

    @app.post(f"{ctx.api_prefix}/auth/logout")
    def logout(request: Request):
        require(ctx.host_auth is None, "HOST_AUTH_MANAGED", "请使用 DSA 退出登录", 409)
        with ctx.db.transaction() as cx:
            cx.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE id=?",
                (stamp(), request.state.session["id"]),
            )
        response = JSONResponse(ctx.envelope(dict(authenticated=False)))
        response.delete_cookie("scanner_session")
        return response

    @app.get(f"{ctx.api_prefix}/auth/session")
    def session(request: Request):
        if ctx.host_auth is not None:
            return ctx.envelope(ctx.host_auth.public_session(request))
        return ctx.envelope(
            dict(
                authenticated=True,
                csrf_token=ctx.auth.csrf(request.cookies["scanner_session"]),
                expires_at=request.state.session["expires_at"],
            )
        )
