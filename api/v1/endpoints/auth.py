# -*- coding: utf-8 -*-
"""Authentication endpoints for Web admin login."""

from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from api.deps import get_system_config_service
from api.v1.schemas.login_log import LoginLogResponse
from src.auth import (
    CHANGE_PASSWORD_ERROR_CURRENT_INVALID,
    COOKIE_NAME,
    SESSION_MAX_AGE_HOURS_DEFAULT,
    change_password,
    check_rate_limit,
    clear_rate_limit,
    create_session,
    get_client_ip,
    has_stored_password,
    is_auth_enabled,
    is_password_changeable,
    is_password_set,
    record_login_failure,
    refresh_auth_state,
    rotate_session_secret,
    set_initial_password,
    verify_admin_username,
    verify_password,
    verify_stored_password,
    verify_session,
)
from src.config import Config, setup_env
from src.core.config_manager import ConfigManager
from src.services.login_audit_service import (
    LOGIN_LOG_MAX_ROWS,
    LOGIN_LOG_RETENTION_DAYS_DEFAULT,
    record_auth_event,
)
from src.services.ip_geo_resolver import get_offline_resolver

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    """Admin account credentials. Initial setup also needs password_confirm."""

    model_config = {"populate_by_name": True}

    username: str = Field(default="", description="Required administrator username")
    password: str = Field(default="", description="Admin password")
    password_confirm: str | None = Field(default=None, alias="passwordConfirm", description="Confirm (first-time)")


class ChangePasswordRequest(BaseModel):
    """Change password request body."""

    model_config = {"populate_by_name": True}

    current_password: str = Field(default="", alias="currentPassword")
    new_password: str = Field(default="", alias="newPassword")
    new_password_confirm: str = Field(default="", alias="newPasswordConfirm")


class AuthSettingsRequest(BaseModel):
    """Update auth enablement and initial password settings."""

    model_config = {"populate_by_name": True}

    auth_enabled: bool = Field(alias="authEnabled")
    password: str = Field(default="")
    password_confirm: str | None = Field(default=None, alias="passwordConfirm")
    current_password: str = Field(default="", alias="currentPassword")


def _cookie_params(request: Request) -> dict:
    """Build cookie params including Secure based on request."""
    secure = False
    if os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true":
        proto = request.headers.get("X-Forwarded-Proto", "").lower()
        secure = proto == "https"
    else:
        # Check URL scheme when not behind proxy
        secure = request.url.scheme == "https"

    try:
        max_age_hours = int(os.getenv("ADMIN_SESSION_MAX_AGE_HOURS", str(SESSION_MAX_AGE_HOURS_DEFAULT)))
    except ValueError:
        max_age_hours = SESSION_MAX_AGE_HOURS_DEFAULT
    max_age = max_age_hours * 3600

    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
        "max_age": max_age,
    }


def _apply_auth_enabled(enabled: bool, request: Request | None = None) -> bool:
    """Persist auth toggle to .env and reload runtime config."""
    manager_applied = False
    if request is not None:
        try:
            service = get_system_config_service(request)
            service.apply_simple_updates(
                updates=[("ADMIN_AUTH_ENABLED", "true" if enabled else "false")],
                mask_token="******",
            )
            manager_applied = True
        except Exception as exc:
            logger.warning(
                "Failed to apply auth toggle via shared SystemConfigService, falling back: %s",
                exc,
                exc_info=True,
            )
            manager_applied = False

    if not manager_applied:
        try:
            manager = ConfigManager()
            manager.apply_updates(
                updates=[("ADMIN_AUTH_ENABLED", "true" if enabled else "false")],
                sensitive_keys=set(),
                mask_token="******",
            )
            manager_applied = True
        except Exception as exc:
            logger.error("Failed to apply auth toggle via ConfigManager: %s", exc, exc_info=True)
            manager_applied = False

    if not manager_applied:
        return False

    Config.reset_instance()
    setup_env(override=True)
    refresh_auth_state()
    return True


def _password_set_for_response(auth_enabled: bool) -> bool:
    """Avoid exposing stored-password state when auth is disabled."""
    return is_password_set() if auth_enabled else False


def _set_session_cookie(response: Response, session_value: str, request: Request) -> None:
    """Attach the admin session cookie to a response."""
    params = _cookie_params(request)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_value,
        httponly=params["httponly"],
        samesite=params["samesite"],
        secure=params["secure"],
        path=params["path"],
        max_age=params["max_age"],
    )


def _get_auth_status_dict(request: Request | None = None) -> dict:
    """Helper to build consistent auth status response body."""
    auth_enabled = is_auth_enabled()
    logged_in = False
    if auth_enabled and request:
        cookie_val = request.cookies.get(COOKIE_NAME)
        logged_in = verify_session(cookie_val) if cookie_val else False

    # setupState determination:
    # - enabled: auth is active
    # - password_retained: auth disabled but password exists
    # - no_password: auth disabled and no password exists
    if auth_enabled:
        setup_state = "enabled"
    elif has_stored_password():
        setup_state = "password_retained"
    else:
        setup_state = "no_password"

    return {
        "authEnabled": auth_enabled,
        "loggedIn": logged_in,
        "passwordSet": _password_set_for_response(auth_enabled),
        "passwordChangeable": is_password_changeable() if auth_enabled else False,
        "setupState": setup_state,
    }


@router.get(
    "/status",
    summary="Get auth status",
    description="Returns whether auth is enabled and if the current request is logged in.",
)
async def auth_status(request: Request):
    """Return authEnabled, loggedIn, passwordSet, passwordChangeable, setupState without requiring auth."""
    return _get_auth_status_dict(request)


@router.post(
    "/settings",
    summary="Update auth settings",
    description=(
        "Enable or disable password login. "
        "When enabling without an existing password, password + passwordConfirm are required. "
        "When re-enabling with a stored password, currentPassword is required. "
        "When disabling auth (authEnabled=false) while auth is currently enabled, "
        "currentPassword is required to re-authenticate the admin — a valid session cookie "
        "alone is NOT enough for this high-risk action (CSRF / session-hijacking hardening "
        "for Issue #1970)."
    ),
)
async def auth_update_settings(request: Request, body: AuthSettingsRequest):
    """Manage auth enablement from the settings page."""
    target_enabled = body.auth_enabled
    current_enabled = is_auth_enabled()
    stored_password_exists = has_stored_password()

    password = (body.password or "").strip()
    confirm = (body.password_confirm or "").strip()
    current_password = (body.current_password or "").strip()

    if target_enabled:
        if password or confirm:
            if stored_password_exists:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "password_already_set",
                        "message": "已存在管理员密码，请启用认证后通过修改密码功能更新",
                    },
                )
            if not password:
                return JSONResponse(
                    status_code=400,
                    content={"error": "password_required", "message": "请输入要设置的管理员密码"},
                )
            if password != confirm:
                return JSONResponse(
                    status_code=400,
                    content={"error": "password_mismatch", "message": "两次输入的密码不一致"},
                )
            if has_stored_password():
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "password_already_set",
                        "message": "已存在管理员密码，请启用认证后通过修改密码功能更新",
                    },
                )
            err = set_initial_password(password)
            if err:
                return JSONResponse(
                    status_code=400,
                    content={"error": "invalid_password", "message": err},
                )
        elif not stored_password_exists:
            return JSONResponse(
                status_code=400,
                content={"error": "password_required", "message": "开启密码登录前请先设置密码"},
            )
        else:
            # P1 Vulnerability Fix: Enforce current-password check independent of global cached flag
            # We must verify they actually possess a valid admin session, otherwise an attacker
            # could hit a race condition when auth becomes enabled mid-flight.
            # This triggers whenever trying to enable/keep enabled an existing auth setup.
            cookie_val = request.cookies.get(COOKIE_NAME)
            # if target_enabled is True here, they are requesting to enable or keep auth enabled
            is_valid_session = cookie_val and verify_session(cookie_val)
            
            if not is_valid_session:
                if not current_password:
                    return JSONResponse(
                        status_code=400,
                        content={"error": "current_required", "message": "重新开启认证前请输入当前密码"},
                    )
                ip = get_client_ip(request)
                if not check_rate_limit(ip):
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limited",
                            "message": "Too many failed attempts. Please try again later.",
                        },
                    )
                if not verify_stored_password(current_password):
                    record_login_failure(ip)
                    await run_in_threadpool(
                        record_auth_event,
                        request,
                        "auth_enable",
                        False,
                        failure_reason="current_password_invalid",
                    )
                    return JSONResponse(
                        status_code=401,
                        content={"error": "invalid_password", "message": "当前密码错误"},
                    )
                clear_rate_limit(ip)
    else:
        # target_enabled is False here: caller wants to disable auth.
        # High-risk action: require current admin password re-authentication even when
        # the request carries a cryptographically valid session cookie. A leaked session
        # cookie must never be enough to flip the system into an unauthenticated state
        # (CSRF / session-hijacking hardening for Issue #1970).
        if current_enabled:
            if not current_password:
                return JSONResponse(
                    status_code=400,
                    content={"error": "current_required", "message": "关闭认证前请输入当前密码"},
                )
            ip = get_client_ip(request)
            if not check_rate_limit(ip):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": "Too many failed attempts. Please try again later.",
                    },
                )
            if not verify_stored_password(current_password):
                record_login_failure(ip)
                await run_in_threadpool(
                    record_auth_event,
                    request,
                    "auth_disable",
                    False,
                    failure_reason="current_password_invalid",
                )
                return JSONResponse(
                    status_code=401,
                    content={"error": "invalid_password", "message": "当前密码错误"},
                )
            clear_rate_limit(ip)

    if target_enabled != current_enabled:
        if not _apply_auth_enabled(target_enabled, request=request):
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "message": "Failed to update auth settings"},
            )
        if not rotate_session_secret():
            rollback_ok = _apply_auth_enabled(current_enabled, request=request)
            if not rollback_ok:
                logger.error("Failed to roll back auth state after session secret rotation failure")
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "message": "Failed to rotate session secret"},
            )
    else:
        if not _apply_auth_enabled(target_enabled, request=request):
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "message": "Failed to update auth settings"},
            )

    if target_enabled:
        session_val = create_session()
        if not session_val:
            rollback_ok = _apply_auth_enabled(current_enabled, request=request)
            if not rollback_ok:
                logger.error("Failed to roll back auth state after session creation failure")
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "message": "Failed to create session"},
            )
        # We manually set loggedIn=True because the cookie is being set in this response
        # and won't be visible in request.cookies until the NEXT request.
        content = _get_auth_status_dict(request)
        content["loggedIn"] = True
        resp = JSONResponse(content=content)
        _set_session_cookie(resp, session_val, request)
        await run_in_threadpool(record_auth_event, request, "auth_enable", True)
        return resp

    await run_in_threadpool(record_auth_event, request, "auth_disable", True)
    resp = JSONResponse(content=_get_auth_status_dict(request))
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp



@router.post(
    "/login",
    summary="Sign in as administrator",
    description=(
        "Verify username and password and set a session cookie. "
        "Initial password setup also requires passwordConfirm."
    ),
)
async def auth_login(request: Request, body: LoginRequest):
    """Verify admin credentials before issuing a session cookie."""
    if not is_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"error": "auth_disabled", "message": "Authentication is not configured"},
        )

    password = (body.password or "").strip()
    if not password:
        return JSONResponse(
            status_code=400,
            content={"error": "password_required", "message": "请输入密码"},
        )

    ip = get_client_ip(request)
    if not check_rate_limit(ip):
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": "Too many failed attempts. Please try again later.",
            },
        )

    password_set = is_password_set()
    username_matches = verify_admin_username(body.username)

    if not password_set and username_matches:
        # First-time setup: require passwordConfirm
        confirm = (body.password_confirm or "").strip()
        if password != confirm:
            record_login_failure(ip)
            await run_in_threadpool(
                record_auth_event,
                request,
                "initial_setup",
                False,
                failure_reason="password_mismatch",
            )
            return JSONResponse(
                status_code=400,
                content={"error": "password_mismatch", "message": "Passwords do not match"},
            )
        err = set_initial_password(password)
        if err:
            record_login_failure(ip)
            await run_in_threadpool(
                record_auth_event,
                request,
                "initial_setup",
                False,
                failure_reason="password_rejected",
            )
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_password", "message": err},
            )
    elif not verify_password(password) or not username_matches:
        record_login_failure(ip)
        await run_in_threadpool(
            record_auth_event,
            request,
            "login",
            False,
            failure_reason="invalid_credentials",
            username_matched=username_matches,
        )
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_credentials", "message": "账号或密码错误"},
        )

    clear_rate_limit(ip)
    session_val = create_session()
    if not session_val:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Failed to create session"},
        )

    # Determine event type: initial_setup or login
    event_type = "initial_setup" if not password_set else "login"
    await run_in_threadpool(record_auth_event, request, event_type, True)

    resp = JSONResponse(content={"ok": True})
    _set_session_cookie(resp, session_val, request)
    return resp


@router.post(
    "/change-password",
    summary="Change password",
    description="Change password. Requires valid session.",
)
async def auth_change_password(request: Request, body: ChangePasswordRequest):
    """Change password. Requires login.

    A successful change rotates the session signing secret so every existing
    session (including other devices) is invalidated, then issues a fresh
    session for the caller so this browser stays signed in.
    """
    if not is_password_changeable():
        return JSONResponse(
            status_code=400,
            content={"error": "not_changeable", "message": "Password cannot be changed via web"},
        )

    current = (body.current_password or "").strip()
    new_pwd = (body.new_password or "").strip()
    new_confirm = (body.new_password_confirm or "").strip()

    if not current:
        return JSONResponse(
            status_code=400,
            content={"error": "current_required", "message": "请输入当前密码"},
        )
    if new_pwd != new_confirm:
        return JSONResponse(
            status_code=400,
            content={"error": "password_mismatch", "message": "两次输入的新密码不一致"},
        )

    err = change_password(current, new_pwd)
    if err:
        if err == CHANGE_PASSWORD_ERROR_CURRENT_INVALID:
            await run_in_threadpool(
                record_auth_event,
                request,
                "password_change",
                False,
                failure_reason="current_password_invalid",
            )
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_password", "message": err},
        )

    if is_auth_enabled() and not rotate_session_secret():
        logger.warning("Password changed but session secret rotation failed")
    session_val = create_session() if is_auth_enabled() else None
    await run_in_threadpool(record_auth_event, request, "password_change", True)
    if not session_val:
        return Response(status_code=204)
    resp = JSONResponse(content={"ok": True, "sessions_invalidated": True})
    _set_session_cookie(resp, session_val, request)
    return resp


@router.post(
    "/logout",
    summary="Logout",
    description="Clear session cookie.",
)
async def auth_logout(request: Request):
    """Clear session cookie."""
    if is_auth_enabled() and not rotate_session_secret():
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Failed to invalidate session"},
        )
    resp = Response(status_code=204)
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


def _build_geo_meta() -> dict:
    """Build the ``geo`` metadata block for the login-logs response."""
    resolver = get_offline_resolver()
    configured = resolver.is_configured()
    attribution = resolver.get_attribution() if configured else None

    # Extract database type from the resolver internals
    database_type = None
    if configured:
        database_type = getattr(resolver, "_db_type", None) or None

    online_provider = os.getenv("LOGIN_LOG_GEO_ONLINE_PROVIDER", "").strip() or None

    return {
        "offline": {
            "configured": configured,
            "available": configured,
            "databaseType": database_type,
            "attribution": attribution,
        },
        "online": {
            "provider": online_provider,
        },
    }


def _row_to_item(row, *, resolver=None) -> dict:
    """Convert a LoginAuditLog ORM row to a response dict (camelCase)."""
    from src.storage import to_utc_isoformat

    created_at_str = to_utc_isoformat(row.created_at) or ""

    # Build location block
    geo_status = row.geo_status or "not_configured"
    location = {
        "status": geo_status,
        "source": row.geo_source,
        "countryCode": row.country_code,
        "en": {
            "country": row.country_en,
            "region": row.region_en,
            "city": row.city_en,
        },
        "zh": {
            "country": row.country_zh,
            "region": row.region_zh,
            "city": row.city_zh,
        },
    }

    # On-the-fly resolution for not_configured rows when offline DB is now available
    if geo_status == "not_configured" and row.ip_scope == "public" and resolver and resolver.is_configured():
        geo_result = resolver.resolve(row.ip)
        if geo_result["geo_status"] == "resolved":
            location["status"] = "resolved"
            location["source"] = geo_result["geo_source"]
            location["countryCode"] = geo_result["country_code"]
            location["en"] = {
                "country": geo_result["country_en"],
                "region": geo_result["region_en"],
                "city": geo_result["city_en"],
            }
            location["zh"] = {
                "country": geo_result["country_zh"],
                "region": geo_result["region_zh"],
                "city": geo_result["city_zh"],
            }

    client = {
        "type": row.client_type or "unknown",
        "browser": row.browser,
        "browserVersion": row.browser_version,
        "os": row.os,
        "deviceType": row.device_type or "unknown",
        "userAgent": row.user_agent,
    }

    return {
        "id": row.id,
        "createdAt": created_at_str,
        "eventType": row.event_type,
        "success": row.success,
        "failureReason": row.failure_reason,
        "usernameMatched": row.username_matched,
        "ip": row.ip,
        "peerIp": row.peer_ip,
        "ipScope": row.ip_scope,
        "location": location,
        "client": client,
    }


@router.get(
    "/login-logs",
    summary="List login audit logs",
    description="Return paginated login and authentication audit logs.",
    response_model=LoginLogResponse,
)
async def auth_login_logs(
    request: Request,
    result: Literal["all", "success", "failure"] = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Return paginated login audit log entries.

    Auth must be enabled; returns 403 when auth is off.
    """
    if not is_auth_enabled():
        return JSONResponse(
            status_code=403,
            content={"error": "login_log_unavailable", "message": "Login log requires admin authentication to be enabled"},
        )

    try:
        from src.repositories.login_audit_repo import LoginAuditRepository

        filter_value = None if result == "all" else result
        rows, total = await run_in_threadpool(
            LoginAuditRepository().list_paginated,
            result=filter_value,
            limit=limit,
            offset=offset,
        )

        resolver = get_offline_resolver()
        items = [_row_to_item(row, resolver=resolver) for row in rows]

        retention_days = LOGIN_LOG_RETENTION_DAYS_DEFAULT
        try:
            env_val = int(os.getenv("LOGIN_LOG_RETENTION_DAYS", str(retention_days)))
            if 1 <= env_val <= 3650:
                retention_days = env_val
        except ValueError:
            pass

        geo_meta = _build_geo_meta()

        return LoginLogResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            retentionDays=retention_days,
            geo=geo_meta,
        )
    except Exception as exc:
        logger.error("Failed to read login audit logs: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Failed to read login logs"},
        )
