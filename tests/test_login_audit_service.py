# -*- coding: utf-8 -*-
"""Unit tests for login audit log storage, repository, and services (L1-L3)."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Keep this test runnable when optional deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.repositories.login_audit_repo import LoginAuditRepository
from src.services.ip_geo_resolver import OfflineGeoResolver, classify_ip
from src.services.login_audit_service import LOGIN_LOG_MAX_ROWS, _get_retention_days
from src.services.user_agent_parser import parse_user_agent
from src.storage import DatabaseManager, LoginAuditLog


class TestClassifyIP(unittest.TestCase):
    """L2a: IP classification tests."""

    def test_public_ipv4(self):
        ip, scope = classify_ip("8.8.8.8")
        self.assertEqual(ip, "8.8.8.8")
        self.assertEqual(scope, "public")

    def test_public_ipv6(self):
        ip, scope = classify_ip("2001:4860:4860::8888")
        self.assertEqual(ip, "2001:4860:4860::8888")
        self.assertEqual(scope, "public")

    def test_loopback_ipv4(self):
        ip, scope = classify_ip("127.0.0.1")
        self.assertEqual(ip, "127.0.0.1")
        self.assertEqual(scope, "loopback")

    def test_loopback_ipv6(self):
        ip, scope = classify_ip("::1")
        self.assertEqual(ip, "::1")
        self.assertEqual(scope, "loopback")

    def test_private_ipv4_10(self):
        ip, scope = classify_ip("10.0.0.1")
        self.assertEqual(ip, "10.0.0.1")
        self.assertEqual(scope, "private")

    def test_private_ipv4_192(self):
        ip, scope = classify_ip("192.168.1.1")
        self.assertEqual(ip, "192.168.1.1")
        self.assertEqual(scope, "private")

    def test_private_ipv4_cgnat(self):
        ip, scope = classify_ip("100.64.0.1")
        self.assertEqual(ip, "100.64.0.1")
        self.assertEqual(scope, "private")

    def test_private_ipv6_link_local(self):
        ip, scope = classify_ip("fe80::1")
        self.assertEqual(ip, "fe80::1")
        self.assertEqual(scope, "private")

    def test_ipv4_mapped_ipv6(self):
        ip, scope = classify_ip("::ffff:8.8.8.8")
        self.assertEqual(ip, "8.8.8.8")
        self.assertEqual(scope, "public")

    def test_invalid_ip(self):
        ip, scope = classify_ip("not-an-ip")
        self.assertEqual(ip, "not-an-ip")
        self.assertEqual(scope, "invalid")

    def test_empty_ip(self):
        ip, scope = classify_ip("")
        self.assertEqual(ip, "")
        self.assertEqual(scope, "invalid")


class TestOfflineGeoResolver(unittest.TestCase):
    """L2a: Offline MMDB resolver tests."""

    def test_resolve_without_config(self):
        """No MMDB configured: returns not_configured."""
        resolver = OfflineGeoResolver()
        with patch.dict(os.environ, {"LOGIN_LOG_GEOIP_DB_PATH": ""}, clear=False):
            result = resolver.resolve("203.0.113.5")
        self.assertEqual(result["geo_status"], "not_configured")
        self.assertIsNone(result["geo_source"])

    def test_resolve_with_missing_file(self):
        """MMDB path set but file missing: returns not_configured."""
        resolver = OfflineGeoResolver()
        with patch.dict(os.environ, {"LOGIN_LOG_GEOIP_DB_PATH": "/nonexistent.mmdb"}, clear=False):
            result = resolver.resolve("203.0.113.5")
        self.assertEqual(result["geo_status"], "not_configured")

    def test_resolve_with_mock_reader(self):
        """Mock MMDB reader returns GeoIP2 structure."""
        resolver = OfflineGeoResolver()
        mock_reader = MagicMock()
        mock_reader.metadata = {"database_type": "DBIP-City-Lite"}
        mock_reader.get.return_value = {
            "country": {
                "iso_code": "SG",
                "names": {"en": "Singapore", "zh-CN": "新加坡"},
            },
            "subdivisions": [
                {
                    "names": {"en": "Central Singapore", "zh-CN": "中区"},
                }
            ],
            "city": {
                "names": {"en": "Singapore", "zh-CN": "新加坡"},
            },
        }

        with patch.dict(os.environ, {"LOGIN_LOG_GEOIP_DB_PATH": "/fake.mmdb"}, clear=False):
            with patch("maxminddb.open_database", return_value=mock_reader):
                resolver._db_path = "/fake.mmdb"
                resolver._db_mtime = 123456.0
                resolver._reader = mock_reader
                resolver._db_type = "DBIP-City-Lite"
                resolver._attribution = "dbip"

                result = resolver.resolve("203.0.113.5")

        self.assertEqual(result["geo_status"], "resolved")
        self.assertEqual(result["geo_source"], "mmdb:DBIP-City-Lite")
        self.assertEqual(result["attribution"], "dbip")
        self.assertEqual(result["country_code"], "SG")
        self.assertEqual(result["country_en"], "Singapore")
        self.assertEqual(result["country_zh"], "新加坡")
        self.assertEqual(result["region_en"], "Central Singapore")
        self.assertEqual(result["city_en"], "Singapore")


class TestParseUserAgent(unittest.TestCase):
    """L3: User-Agent parser tests (parameterized from Appendix B)."""

    def test_chrome_macos(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Chrome")
        self.assertEqual(result["browser_version"], "131")
        self.assertEqual(result["os"], "macOS")
        self.assertEqual(result["device_type"], "desktop")

    def test_edge_windows(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Edge")
        self.assertEqual(result["browser_version"], "131")
        self.assertEqual(result["os"], "Windows")
        self.assertEqual(result["device_type"], "desktop")

    def test_firefox_windows(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Firefox")
        self.assertEqual(result["browser_version"], "133")
        self.assertEqual(result["os"], "Windows")

    def test_safari_macos(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Safari")
        self.assertEqual(result["browser_version"], "18")
        self.assertEqual(result["os"], "macOS")

    def test_safari_ios_mobile(self):
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Safari")
        self.assertEqual(result["os"], "iOS")
        self.assertEqual(result["device_type"], "mobile")

    def test_chrome_ipad_tablet(self):
        ua = "Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Chrome")
        self.assertEqual(result["os"], "iPadOS")
        self.assertEqual(result["device_type"], "tablet")

    def test_chrome_android_mobile(self):
        ua = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Chrome")
        self.assertEqual(result["os"], "Android")
        self.assertEqual(result["device_type"], "mobile")

    def test_chrome_android_tablet(self):
        ua = "Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Chrome")
        self.assertEqual(result["os"], "Android")
        self.assertEqual(result["device_type"], "tablet")

    def test_wechat_ios(self):
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.50(0x1800323c) NetType/WIFI Language/zh_CN"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "微信")
        self.assertEqual(result["browser_version"], "8")
        self.assertEqual(result["os"], "iOS")
        self.assertEqual(result["device_type"], "mobile")

    def test_electron_desktop(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) DailyStockAnalysis/3.21.0 Chrome/131.0.0.0 Electron/31.4.0 Safari/537.36"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "desktop")
        self.assertEqual(result["browser"], "Electron")
        self.assertEqual(result["browser_version"], "31")
        self.assertEqual(result["os"], "macOS")
        self.assertEqual(result["device_type"], "desktop")

    def test_curl_script(self):
        ua = "curl/8.4.0"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "script")
        self.assertEqual(result["browser"], "curl")
        self.assertEqual(result["browser_version"], "8")
        self.assertIsNone(result["os"])
        self.assertEqual(result["device_type"], "unknown")

    def test_python_requests_script(self):
        ua = "python-requests/2.32.3"
        result = parse_user_agent(ua)
        self.assertEqual(result["client_type"], "script")
        self.assertEqual(result["browser"], "python-requests")
        self.assertEqual(result["browser_version"], "2")

    def test_empty_ua(self):
        result = parse_user_agent("")
        self.assertEqual(result["client_type"], "unknown")
        self.assertIsNone(result["browser"])
        self.assertIsNone(result["os"])
        self.assertEqual(result["device_type"], "unknown")

    def test_client_hints_android_mobile(self):
        ua = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        result = parse_user_agent(
            ua,
            sec_ch_ua='"Chromium";v="131"',
            sec_ch_ua_mobile="?1",
            sec_ch_ua_platform='"Android"',
        )
        self.assertEqual(result["client_type"], "browser")
        self.assertEqual(result["browser"], "Chrome")
        self.assertEqual(result["os"], "Android")
        self.assertEqual(result["device_type"], "mobile")

    def test_control_char_stripping(self):
        ua = "Mozilla/5.0\x00\x1f\x7f Test"
        result = parse_user_agent(ua)
        self.assertIsNotNone(result["user_agent"])
        self.assertNotIn("\x00", result["user_agent"])
        self.assertNotIn("\x1f", result["user_agent"])
        self.assertNotIn("\x7f", result["user_agent"])

    def test_ua_truncation(self):
        ua = "A" * 10000
        result = parse_user_agent(ua)
        self.assertEqual(len(result["user_agent"]), 512)


class TestLoginAuditRepository(unittest.TestCase):
    """L1: Repository layer tests with real SQLite."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        os.environ["DATABASE_PATH"] = str(self.db_path)
        DatabaseManager.reset_instance()

    def tearDown(self):
        DatabaseManager.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_create_and_list(self):
        """Create rows and list them with pagination."""
        repo = LoginAuditRepository()
        row1_id = repo.create({
            "created_at": datetime.utcnow(),
            "event_type": "login",
            "success": True,
            "ip": "203.0.113.5",
            "ip_scope": "public",
            "geo_status": "not_configured",
            "client_type": "browser",
            "device_type": "desktop",
        })
        row2_id = repo.create({
            "created_at": datetime.utcnow(),
            "event_type": "login",
            "success": False,
            "failure_reason": "invalid_credentials",
            "ip": "192.168.1.1",
            "ip_scope": "private",
            "geo_status": "skipped",
            "client_type": "script",
            "device_type": "unknown",
        })

        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)
        # Newest first
        self.assertEqual(rows[0].id, row2_id)
        self.assertEqual(rows[1].id, row1_id)

    def test_filter_by_result(self):
        """Filter by success/failure."""
        repo = LoginAuditRepository()
        repo.create({
            "created_at": datetime.utcnow(),
            "event_type": "login",
            "success": True,
            "ip": "1.2.3.4",
            "ip_scope": "public",
            "geo_status": "not_configured",
            "client_type": "browser",
            "device_type": "desktop",
        })
        repo.create({
            "created_at": datetime.utcnow(),
            "event_type": "login",
            "success": False,
            "failure_reason": "invalid_credentials",
            "ip": "5.6.7.8",
            "ip_scope": "public",
            "geo_status": "not_configured",
            "client_type": "browser",
            "device_type": "desktop",
        })

        success_rows, success_total = repo.list_paginated(result="success", limit=10, offset=0)
        self.assertEqual(success_total, 1)
        self.assertTrue(success_rows[0].success)

        failure_rows, failure_total = repo.list_paginated(result="failure", limit=10, offset=0)
        self.assertEqual(failure_total, 1)
        self.assertFalse(failure_rows[0].success)

    def test_cleanup_by_age(self):
        """Delete rows older than retention period."""
        repo = LoginAuditRepository()
        old_time = datetime.utcnow() - timedelta(days=100)
        repo.create({
            "created_at": old_time,
            "event_type": "login",
            "success": True,
            "ip": "1.1.1.1",
            "ip_scope": "public",
            "geo_status": "not_configured",
            "client_type": "browser",
            "device_type": "desktop",
        })
        repo.create({
            "created_at": datetime.utcnow(),
            "event_type": "login",
            "success": True,
            "ip": "2.2.2.2",
            "ip_scope": "public",
            "geo_status": "not_configured",
            "client_type": "browser",
            "device_type": "desktop",
        })

        deleted = repo.cleanup(retention_days=90, max_rows=5000)
        self.assertEqual(deleted, 1)

        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].ip, "2.2.2.2")

    def test_cleanup_by_max_rows(self):
        """Delete oldest rows when exceeding max_rows."""
        repo = LoginAuditRepository()
        for i in range(10):
            repo.create({
                "created_at": datetime.utcnow() - timedelta(seconds=i),
                "event_type": "login",
                "success": True,
                "ip": f"1.1.1.{i}",
                "ip_scope": "public",
                "geo_status": "not_configured",
                "client_type": "browser",
                "device_type": "desktop",
            })

        deleted = repo.cleanup(retention_days=3650, max_rows=5)
        self.assertEqual(deleted, 5)

        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 5)


class TestRetentionConfig(unittest.TestCase):
    """L1: Retention configuration tests."""

    def test_default_retention_days(self):
        """Default retention is 90 days."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOGIN_LOG_RETENTION_DAYS", None)
            days = _get_retention_days()
        self.assertEqual(days, 90)

    def test_custom_retention_days(self):
        """Custom retention from env."""
        with patch.dict(os.environ, {"LOGIN_LOG_RETENTION_DAYS": "180"}, clear=False):
            days = _get_retention_days()
        self.assertEqual(days, 180)

    def test_invalid_retention_days_fallback(self):
        """Invalid value falls back to default."""
        with patch.dict(os.environ, {"LOGIN_LOG_RETENTION_DAYS": "invalid"}, clear=False):
            days = _get_retention_days()
        self.assertEqual(days, 90)

    def test_out_of_range_retention_days(self):
        """Out of range value falls back to default."""
        with patch.dict(os.environ, {"LOGIN_LOG_RETENTION_DAYS": "0"}, clear=False):
            days = _get_retention_days()
        self.assertEqual(days, 90)

        with patch.dict(os.environ, {"LOGIN_LOG_RETENTION_DAYS": "4000"}, clear=False):
            days = _get_retention_days()
        self.assertEqual(days, 90)


class TestMaxRowsConstant(unittest.TestCase):
    """L1: Max rows constant is single source of truth."""

    def test_max_rows_value(self):
        """LOGIN_LOG_MAX_ROWS is 5000."""
        self.assertEqual(LOGIN_LOG_MAX_ROWS, 5000)


if __name__ == "__main__":
    unittest.main()
