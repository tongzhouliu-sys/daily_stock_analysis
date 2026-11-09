"""Minimal data-only adapter contract and bounded, sanitized HTTP transport."""

import time
from typing import Protocol

import httpx

from src.services.signal_scanner.core import DomainError, stamp


class MarketDataAdapter(Protocol):
    def fetch(self, symbol: str, start: str, end: str) -> dict: ...


def get_response(client, url, *, headers=None, params=None):
    started = time.monotonic()
    for attempt in range(3):
        try:
            response = client.get(url, headers=headers, params=params)
        except httpx.RequestError:
            if attempt == 2:
                raise DomainError("SOURCE_UNREACHABLE", status=503) from None
        else:
            if response.status_code in (401, 403):
                raise DomainError("SOURCE_PERMISSION_DENIED", status=503)
            if response.status_code == 404:
                raise DomainError("SOURCE_NO_COVERAGE", status=503)
            if response.status_code == 200:
                return response
            if response.status_code != 429 and response.status_code < 500:
                raise DomainError("SOURCE_REQUEST_REJECTED", status=503)
            if attempt == 2:
                raise DomainError(
                    "SOURCE_RATE_LIMITED" if response.status_code == 429 else "SOURCE_UNAVAILABLE", status=503
                )
        if time.monotonic() - started >= 45:
            raise DomainError("SOURCE_BUDGET_EXCEEDED", status=503)
        delay = min(5, 2**attempt)
        if "response" in locals():
            retry = response.headers.get("Retry-After", "")
            if retry.isdigit():
                delay = min(15, int(retry))
        time.sleep(delay)
    raise DomainError("SOURCE_UNAVAILABLE", status=503)


def source_report(
    source,
    capability,
    enabled,
    *,
    success=False,
    reason=None,
    freshness="UNKNOWN",
    quality="UNKNOWN",
    **facts,
):
    denied = reason == "SOURCE_PERMISSION_DENIED"
    return dict(
        source=source,
        observed_at=stamp(),
        status="SUCCEEDED" if success else "FAILED" if enabled else "DISABLED",
        capabilities=[
            dict(
                capability_id=capability,
                support="SUPPORTED",
                entitlement="DENIED" if denied else "GRANTED" if success else "UNKNOWN",
                enabled=enabled,
                verified_at=stamp() if success else None,
                evidence_ref="本次来源请求与存储报告" if success else None,
            )
        ],
        health=dict(
            connection="UP" if success or denied else "UNKNOWN" if not enabled else "DOWN",
            permission="DENIED" if denied else "GRANTED" if success else "UNKNOWN",
            freshness=freshness,
            quality=quality,
        ),
        quality_status=quality,
        reason_code=reason,
        **facts,
    )


def unavailable(source, capability, reason="CREDENTIAL_NOT_CONFIGURED"):
    return source_report(source, capability, False, reason=reason)
