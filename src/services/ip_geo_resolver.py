# -*- coding: utf-8 -*-
"""IP geolocation resolver (offline MMDB + optional online ip-api)."""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def classify_ip(ip_str: str) -> Tuple[str, str]:
    """Classify IP into scope: public/loopback/private/invalid.

    Returns (normalized_ip, ip_scope).
    IPv4-mapped IPv6 addresses are converted to IPv4.
    """
    if not ip_str:
        return ("", "invalid")

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return (ip_str[:64], "invalid")

    # Convert IPv4-mapped IPv6 to IPv4
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    normalized = str(ip)

    if ip.is_loopback:
        return (normalized, "loopback")
    elif ip.is_global:
        return (normalized, "public")
    else:
        return (normalized, "private")


class OfflineGeoResolver:
    """Offline MMDB resolver with lazy loading and caching."""

    def __init__(self):
        self._reader = None
        self._db_path = None
        self._db_mtime = None
        self._db_type = None
        self._attribution = None
        self._warned_missing = False

    def _load_db(self) -> None:
        """Load or reload MMDB if path/mtime changed."""
        db_path = os.getenv("LOGIN_LOG_GEOIP_DB_PATH", "").strip()
        if not db_path:
            if not self._warned_missing:
                logger.warning("LOGIN_LOG_GEOIP_DB_PATH not configured; public IPs will show '未解析'")
                self._warned_missing = True
            return

        path = Path(db_path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            if not self._warned_missing:
                logger.warning("GeoIP DB not found at %s; public IPs will show '未解析'", db_path)
                self._warned_missing = True
            return

        # Reload if path or mtime changed
        if self._db_path != db_path or self._db_mtime != mtime:
            try:
                import maxminddb  # type: ignore
                self._reader = maxminddb.open_database(db_path)
                self._db_path = db_path
                self._db_mtime = mtime
                self._warned_missing = False

                # Detect DB type from metadata
                metadata = self._reader.metadata
                db_type = metadata.get("database_type", "") if hasattr(metadata, "get") else ""
                self._db_type = db_type
                if "dbip" in db_type.lower():
                    self._attribution = "dbip"
                elif "maxmind" in db_type.lower() or "geolite" in db_type.lower():
                    self._attribution = "maxmind"
                else:
                    self._attribution = None

                logger.info("Loaded GeoIP DB: %s (type=%s)", db_path, db_type)
            except Exception as exc:
                logger.warning("Failed to load GeoIP DB %s: %s", db_path, exc)
                self._reader = None
                self._warned_missing = True

    def resolve(self, ip: str) -> Dict[str, Any]:
        """Resolve IP location using offline MMDB.

        Returns dict with keys: geo_status, geo_source, country_code, country_en, region_en, city_en, country_zh, region_zh, city_zh, attribution
        """
        result = {
            "geo_status": "not_configured",
            "geo_source": None,
            "country_code": None,
            "country_en": None,
            "region_en": None,
            "city_en": None,
            "country_zh": None,
            "region_zh": None,
            "city_zh": None,
            "attribution": None,
        }

        self._load_db()
        if not self._reader:
            return result

        try:
            record = self._reader.get(ip)
            if not record:
                result["geo_status"] = "unresolved"
                return result

            # Extract fields (GeoIP2 City structure)
            country = record.get("country", {})
            subdivisions = record.get("subdivisions", [])
            city = record.get("city", {})

            result["geo_status"] = "resolved"
            result["geo_source"] = f"mmdb:{self._db_type}" if self._db_type else "mmdb"
            result["attribution"] = self._attribution

            result["country_code"] = country.get("iso_code")
            names = country.get("names", {})
            result["country_en"] = names.get("en")
            result["country_zh"] = names.get("zh-CN")

            if subdivisions:
                region = subdivisions[0]
                region_names = region.get("names", {})
                result["region_en"] = region_names.get("en")
                result["region_zh"] = region_names.get("zh-CN")

            city_names = city.get("names", {})
            result["city_en"] = city_names.get("en")
            result["city_zh"] = city_names.get("zh-CN")

            return result
        except Exception as exc:
            logger.warning("GeoIP lookup failed for %s: %s", ip, exc)
            result["geo_status"] = "unresolved"
            return result

    def get_attribution(self) -> Optional[str]:
        """Return attribution type: dbip/maxmind/None."""
        self._load_db()
        return self._attribution

    def is_configured(self) -> bool:
        """Return whether offline DB is configured and available."""
        self._load_db()
        return self._reader is not None


# Global instance
_offline_resolver = OfflineGeoResolver()


def get_offline_resolver() -> OfflineGeoResolver:
    """Get global offline resolver instance."""
    return _offline_resolver
