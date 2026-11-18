# -*- coding: utf-8 -*-
"""Integration tests for login audit log API endpoints (L4-L5)."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dotenv import dotenv_values
from fastapi.responses import Response
from starlette.requests import Request

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.v1.endpoints import auth as auth_endpoint
from src.repositories.login_audit_repo import LoginAuditRepository
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class LoginAuditAPITestCase(unittest.TestCase):
    """Integration tests for login audit log recording and querying."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.data_dir / "test.db")
        DatabaseManager.reset_instance()

        self.auth_patcher = patch.object(auth, "_is_auth_enabled_from_env", return_value=True)
        self.data_dir_patcher = patch.object(auth, "_get_data_dir", return_value=self.data_dir)
        self.auth_patcher.start()
        self.data_dir_patcher.start()

    def tearDown(self) -> None:
        self.auth_patcher.stop()
        self.data_dir_patcher.stop()
        DatabaseManager.reset_instance()
        _reset_auth_globals()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _read_auth_enabled_from_env(self) -> bool:
        values = dotenv_values(self.env_path)
        return (values.get("ADMIN_AUTH_ENABLED") or "").strip().lower() in ("true", "1", "yes")

    @staticmethod
    def _build_request(
        method="POST",
        headers=None,
        cookies=None,
        client_host="127.0.0.1",
        body=None,
    ):
        """Build a mock Request object."""
        return SimpleNamespace(
            method=method,
            headers=headers or {},
            url=SimpleNamespace(scheme="http"),
            cookies=cookies or {},
            client=SimpleNamespace(host=client_host),
            scope={"type": "http"},
            _json=body,
        )

    def test_login_success_records_event(self):
        """L4: Successful login records 'login' event."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Login
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0"},
            client_host="8.8.8.8",
        )
        body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
        resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 200)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "login")
        self.assertTrue(rows[0].success)
        self.assertEqual(rows[0].ip, "8.8.8.8")
        self.assertEqual(rows[0].ip_scope, "public")
        self.assertEqual(rows[0].client_type, "browser")
        self.assertEqual(rows[0].browser, "Chrome")

    def test_login_failure_records_event(self):
        """L4: Failed login records 'login' event with failure_reason."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Failed login
        req = self._build_request(
            headers={"User-Agent": "curl/8.4.0"},
            client_host="198.51.100.7",
        )
        body = SimpleNamespace(username="admin", password="WrongPass", password_confirm=None)
        resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 401)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "login")
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].failure_reason, "invalid_credentials")
        self.assertEqual(rows[0].username_matched, True)
        self.assertEqual(rows[0].ip, "198.51.100.7")
        self.assertEqual(rows[0].client_type, "script")
        self.assertEqual(rows[0].browser, "curl")

    def test_initial_setup_success(self):
        """L4: Initial password setup records 'initial_setup' success."""
        # First-time setup (no password set yet)
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/131.0"},
            client_host="192.168.1.10",
        )
        body = SimpleNamespace(username="admin", password="NewPass123", password_confirm="NewPass123")
        resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 200)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "initial_setup")
        self.assertTrue(rows[0].success)
        self.assertEqual(rows[0].ip, "192.168.1.10")
        self.assertEqual(rows[0].ip_scope, "private")

    def test_initial_setup_password_mismatch(self):
        """L4: Initial setup with mismatched passwords records failure."""
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="10.0.0.1",
        )
        body = SimpleNamespace(username="admin", password="Pass1", password_confirm="Pass2")
        resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 400)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "initial_setup")
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].failure_reason, "password_mismatch")

    def test_auth_enable_success(self):
        """L4: Enabling auth records 'auth_enable' success."""
        # Set initial password first
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Enable auth (re-enable with current password)
        session_val = auth.create_session()
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            cookies={"dsa_session": session_val},
            client_host="203.0.113.10",
        )
        body = SimpleNamespace(
            auth_enabled=True,
            password="",
            password_confirm=None,
            current_password="TestPass123",
        )
        resp = asyncio.run(auth_endpoint.auth_update_settings(req, body))

        self.assertEqual(resp.status_code, 200)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertGreaterEqual(total, 1)
        # Find auth_enable event
        auth_enable_rows = [r for r in rows if r.event_type == "auth_enable"]
        self.assertEqual(len(auth_enable_rows), 1)
        self.assertTrue(auth_enable_rows[0].success)

    def test_auth_enable_failure(self):
        """L4: Enabling auth with wrong password records failure."""
        # Set initial password first
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Try to enable auth with wrong password
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="203.0.113.11",
        )
        body = SimpleNamespace(
            auth_enabled=True,
            password="",
            password_confirm=None,
            current_password="WrongPass",
        )
        resp = asyncio.run(auth_endpoint.auth_update_settings(req, body))

        self.assertEqual(resp.status_code, 401)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "auth_enable")
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].failure_reason, "current_password_invalid")

    def test_change_password_success(self):
        """L4: Successful password change records 'password_change' success."""
        # Set initial password
        err = auth.set_initial_password("OldPass123")
        self.assertIsNone(err)

        # Create session
        session_val = auth.create_session()

        # Change password
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            cookies={"dsa_session": session_val},
            client_host="203.0.113.12",
        )
        body = SimpleNamespace(
            current_password="OldPass123",
            new_password="NewPass456",
            new_password_confirm="NewPass456",
        )
        resp = asyncio.run(auth_endpoint.auth_change_password(req, body))

        self.assertIn(resp.status_code, [200, 204])

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertGreaterEqual(total, 1)
        pwd_change_rows = [r for r in rows if r.event_type == "password_change"]
        self.assertEqual(len(pwd_change_rows), 1)
        self.assertTrue(pwd_change_rows[0].success)

    def test_change_password_failure(self):
        """L4: Failed password change records 'password_change' failure."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Create session
        session_val = auth.create_session()

        # Try to change password with wrong current password
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            cookies={"dsa_session": session_val},
            client_host="203.0.113.13",
        )
        body = SimpleNamespace(
            current_password="WrongPass",
            new_password="NewPass456",
            new_password_confirm="NewPass456",
        )
        resp = asyncio.run(auth_endpoint.auth_change_password(req, body))

        self.assertEqual(resp.status_code, 400)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0].event_type, "password_change")
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].failure_reason, "current_password_invalid")

    def test_no_password_in_logs(self):
        """C3: Passwords are never recorded in audit logs."""
        # Set initial password
        err = auth.set_initial_password("SecretPass123")
        self.assertIsNone(err)

        # Failed login
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="8.8.8.14",
        )
        body = SimpleNamespace(username="admin", password="WrongSecretPass", password_confirm=None)
        asyncio.run(auth_endpoint.auth_login(req, body))

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        row = rows[0]

        # Verify no password fields exist
        self.assertEqual(row.failure_reason, "invalid_credentials")  # Only reason code, not password
        self.assertNotIn("SecretPass123", str(row))
        self.assertNotIn("WrongSecretPass", str(row))

    def test_rate_limited_request_not_logged(self):
        """L4: Rate-limited (429) requests do not produce audit log entries."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Simulate rate limiting by filling up the rate limit counter
        ip = "203.0.113.15"
        for _ in range(5):
            auth.record_login_failure(ip)

        # This request should be rate-limited
        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host=ip,
        )
        body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
        resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 429)

        # Check audit log - should be empty (rate-limited requests not logged)
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 0)

    def test_trust_x_forwarded_for(self):
        """C2: With TRUST_X_FORWARDED_FOR=true, log rightmost IP and peer_ip."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        with patch.dict(os.environ, {"TRUST_X_FORWARDED_FOR": "true"}, clear=False):
            req = self._build_request(
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "X-Forwarded-For": "1.1.1.1, 8.8.8.9",
                },
                client_host="10.0.0.1",  # TCP peer
            )
            body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
            resp = asyncio.run(auth_endpoint.auth_login(req, body))

        self.assertEqual(resp.status_code, 200)

        # Check audit log
        repo = LoginAuditRepository()
        rows, total = repo.list_paginated(result=None, limit=10, offset=0)
        self.assertEqual(total, 1)
        row = rows[0]
        self.assertEqual(row.ip, "8.8.8.9")  # Rightmost XFF
        self.assertEqual(row.peer_ip, "10.0.0.1")  # TCP peer
        self.assertEqual(row.ip_scope, "public")

    def test_login_logs_query_endpoint(self):
        """L5: GET /api/v1/auth/login-logs returns paginated results."""
        # Set initial password and create some logs
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Create a few login events
        for i in range(3):
            req = self._build_request(
                headers={"User-Agent": "Mozilla/5.0"},
                client_host=f"203.0.113.{20+i}",
            )
            body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
            asyncio.run(auth_endpoint.auth_login(req, body))

        # Query login logs
        req = self._build_request(method="GET", client_host="127.0.0.1")
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="all",
            limit=10,
            offset=0,
        ))

        self.assertEqual(resp.total, 3)
        self.assertEqual(len(resp.items), 3)
        self.assertEqual(resp.limit, 10)
        self.assertEqual(resp.offset, 0)
        self.assertEqual(resp.retentionDays, 90)

        # Check timestamp format (C7: ISO 8601 with Z suffix)
        for item in resp.items:
            self.assertTrue(item.createdAt.endswith("Z"))

    def test_login_logs_filter_by_result(self):
        """L5: Query endpoint filters by result (success/failure)."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Create success and failure events
        req1 = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="203.0.113.30",
        )
        body1 = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
        asyncio.run(auth_endpoint.auth_login(req1, body1))

        req2 = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="203.0.113.31",
        )
        body2 = SimpleNamespace(username="admin", password="WrongPass", password_confirm=None)
        asyncio.run(auth_endpoint.auth_login(req2, body2))

        # Query success only
        req = self._build_request(method="GET")
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="success",
            limit=10,
            offset=0,
        ))
        self.assertEqual(resp.total, 1)
        self.assertTrue(resp.items[0].success)

        # Query failure only
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="failure",
            limit=10,
            offset=0,
        ))
        self.assertEqual(resp.total, 1)
        self.assertFalse(resp.items[0].success)

    def test_login_logs_pagination(self):
        """L5: Query endpoint supports pagination."""
        # Set initial password
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        # Create 5 login events
        for i in range(5):
            req = self._build_request(
                headers={"User-Agent": "Mozilla/5.0"},
                client_host=f"203.0.113.{40+i}",
            )
            body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
            asyncio.run(auth_endpoint.auth_login(req, body))

        # Query with limit=2, offset=0
        req = self._build_request(method="GET")
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="all",
            limit=2,
            offset=0,
        ))
        self.assertEqual(resp.total, 5)
        self.assertEqual(len(resp.items), 2)
        self.assertEqual(resp.limit, 2)
        self.assertEqual(resp.offset, 0)

        # Query with limit=2, offset=2
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="all",
            limit=2,
            offset=2,
        ))
        self.assertEqual(resp.total, 5)
        self.assertEqual(len(resp.items), 2)
        self.assertEqual(resp.offset, 2)

    def test_login_logs_auth_disabled_returns_403(self):
        """C6: Query endpoint returns 403 when auth is disabled."""
        # Disable auth
        self.env_path.write_text(
            "STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n",
            encoding="utf-8",
        )
        auth._auth_enabled = None  # Reset cached value

        with patch.object(auth, "_is_auth_enabled_from_env", return_value=False):
            req = self._build_request(method="GET")
            resp = asyncio.run(auth_endpoint.auth_login_logs(
                request=req,
                result="all",
                limit=10,
                offset=0,
            ))

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.body, b'{"error":"login_log_unavailable","message":"Login log requires admin authentication to be enabled"}')

    def test_geo_metadata_in_response(self):
        """L5: Response includes geo configuration metadata."""
        # Set initial password and create a log
        err = auth.set_initial_password("TestPass123")
        self.assertIsNone(err)

        req = self._build_request(
            headers={"User-Agent": "Mozilla/5.0"},
            client_host="203.0.113.50",
        )
        body = SimpleNamespace(username="admin", password="TestPass123", password_confirm=None)
        asyncio.run(auth_endpoint.auth_login(req, body))

        # Query login logs
        req = self._build_request(method="GET")
        resp = asyncio.run(auth_endpoint.auth_login_logs(
            request=req,
            result="all",
            limit=10,
            offset=0,
        ))

        # Check geo metadata structure
        self.assertIn("offline", resp.geo)
        self.assertIn("online", resp.geo)
        self.assertIn("configured", resp.geo["offline"])
        self.assertIn("available", resp.geo["offline"])
        self.assertIn("provider", resp.geo["online"])


if __name__ == "__main__":
    unittest.main()
