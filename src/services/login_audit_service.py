# -*- coding: utf-8 -*-
"""Login audit service: records authentication events."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from src.auth import get_client_ip
from src.repositories.login_audit_repo import LoginAuditRepository
from src.services.ip_geo_resolver import classify_ip, get_offline_resolver
from src.services.user_agent_parser import parse_user_agent
from src.storage import utc_naive_now

logger = logging.getLogger(__name__)

# Retention constants (single source of truth)
LOGIN_LOG_RETENTION_DAYS_DEFAULT = 90
LOGIN_LOG_MAX_ROWS = 5000


def _get_retention_days() -> int:
    """Get retention days from env, with fallback and validation."""
    try:
        days = int(os.getenv("LOGIN_LOG_RETENTION_DAYS", str(LOGIN_LOG_RETENTION_DAYS_DEFAULT)))
        if 1 <= days <= 3650:
            return days
        logger.warning("LOGIN_LOG_RETENTION_DAYS=%d out of range (1-3650), using default %d", days, LOGIN_LOG_RETENTION_DAYS_DEFAULT)
    except ValueError:
        logger.warning("LOGIN_LOG_RETENTION_DAYS is not a valid integer, using default %d", LOGIN_LOG_RETENTION_DAYS_DEFAULT)
    return LOGIN_LOG_RETENTION_DAYS_DEFAULT


def record_auth_event(
    request,
    event_type: str,
    success: bool,
    *,
    failure_reason: Optional[str] = None,
    username_matched: Optional[bool] = None,
) -> None:
    """Record an authentication event.

    This function captures all exceptions and logs warnings. It never raises.
    """
    try:
        _record_auth_event_inner(request, event_type, success, failure_reason=failure_reason, username_matched=username_matched)
    except Exception as exc:
        logger.warning("Failed to record login audit event: %s", exc, exc_info=True)


def _record_auth_event_inner(
    request,
    event_type: str,
    success: bool,
    *,
    failure_reason: Optional[str] = None,
    username_matched: Optional[bool] = None,
) -> None:
    """Inner implementation that may raise."""
    # Extract IP
    ip_raw = get_client_ip(request)
    ip, ip_scope = classify_ip(ip_raw)

    # Peer IP (TCP endpoint)
    peer_ip = None
    if request.client:
        peer_raw = request.client.host or ""
        peer_normalized, _ = classify_ip(peer_raw)
        if peer_normalized and peer_normalized != ip:
            peer_ip = peer_normalized

    # UA parsing
    ua = request.headers.get("User-Agent", "")
    sec_ch_ua = request.headers.get("Sec-CH-UA")
    sec_ch_ua_mobile = request.headers.get("Sec-CH-UA-Mobile")
    sec_ch_ua_platform = request.headers.get("Sec-CH-UA-Platform")
    client_info = parse_user_agent(ua, sec_ch_ua, sec_ch_ua_mobile, sec_ch_ua_platform)

    # Geo resolution (offline only, synchronous)
    geo_fields: Dict[str, Any] = {}
    if ip_scope == "public":
        resolver = get_offline_resolver()
        if resolver.is_configured():
            geo_result = resolver.resolve(ip)
            geo_fields.update({
                "geo_status": geo_result["geo_status"],
                "geo_source": geo_result["geo_source"],
                "country_code": geo_result["country_code"],
                "country_en": geo_result["country_en"],
                "region_en": geo_result["region_en"],
                "city_en": geo_result["city_en"],
                "country_zh": geo_result["country_zh"],
                "region_zh": geo_result["region_zh"],
                "city_zh": geo_result["city_zh"],
            })
        else:
            geo_fields["geo_status"] = "not_configured"
    else:
        geo_fields["geo_status"] = "skipped"

    # Build row
    fields = {
        "created_at": utc_naive_now(),
        "event_type": event_type,
        "success": success,
        "failure_reason": failure_reason,
        "username_matched": username_matched,
        "ip": ip,
        "peer_ip": peer_ip,
        "ip_scope": ip_scope,
        "user_agent": client_info["user_agent"],
        "client_type": client_info["client_type"],
        "browser": client_info["browser"],
        "browser_version": client_info["browser_version"],
        "os": client_info["os"],
        "device_type": client_info["device_type"],
        **geo_fields,
    }

    # Write to DB
    repo = LoginAuditRepository()
    repo.create(fields)

    # Cleanup
    retention_days = _get_retention_days()
    repo.cleanup(retention_days=retention_days, max_rows=LOGIN_LOG_MAX_ROWS)
