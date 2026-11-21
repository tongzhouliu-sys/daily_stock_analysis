# -*- coding: utf-8 -*-
"""Login audit log API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class LocationInfo(BaseModel):
    """Geolocation information for an IP address."""

    status: str = Field(..., description="Resolution status: resolved/pending/unresolved/not_configured/skipped")
    source: Optional[str] = Field(None, description="Geo source, e.g. 'mmdb:DBIP-City-Lite' or 'ip-api'")
    countryCode: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code")
    en: dict = Field(default_factory=dict, description="English names: {country, region, city}")
    zh: dict = Field(default_factory=dict, description="Chinese names: {country, region, city}")


class ClientInfo(BaseModel):
    """Client identification information."""

    type: str = Field(..., description="Client type: browser/desktop/script/unknown")
    browser: Optional[str] = Field(None, description="Browser name, e.g. 'Chrome', '微信'")
    browserVersion: Optional[str] = Field(None, description="Browser major version")
    os: Optional[str] = Field(None, description="Operating system family")
    deviceType: str = Field(..., description="Device type: desktop/mobile/tablet/unknown")
    userAgent: Optional[str] = Field(None, description="Raw User-Agent string")


class LoginLogItem(BaseModel):
    """Single login audit log entry."""

    id: int
    createdAt: str = Field(..., description="UTC ISO 8601 timestamp with Z suffix")
    eventType: str = Field(..., description="Event type: login/initial_setup/auth_enable/auth_disable/password_change")
    success: bool
    failureReason: Optional[str] = Field(None, description="Failure reason code")
    usernameMatched: Optional[bool] = Field(None, description="Whether username matched (login failure only)")
    ip: str
    peerIp: Optional[str] = Field(None, description="TCP peer IP if different from ip")
    ipScope: str = Field(..., description="IP scope: public/loopback/private/invalid")
    location: LocationInfo
    client: ClientInfo


class LoginLogResponse(BaseModel):
    """Paginated login audit log response."""

    items: List[LoginLogItem]
    total: int = Field(..., description="Total count matching filter")
    limit: int
    offset: int
    retentionDays: int = Field(..., description="Current retention period in days")
    geo: dict = Field(
        default_factory=dict,
        description="Geo configuration: {offline: {configured, available, databaseType, attribution}, online: {provider}}",
    )
