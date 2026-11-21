# -*- coding: utf-8 -*-
"""First-time setup token gate for /auth/login and /auth/settings."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.v1.endpoints import auth as auth_endpoint
from src.config import Config


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}
    auth.reset_setup_token()


class SetupTokenTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "ADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.data_dir / "test.db")
        os.environ["ADMIN_SETUP_TOKEN"] = "test-setup-token"
        Config.reset_instance()

        self.auth_patcher = patch.object(auth, "_is_auth_enabled_from_env", return_value=True)
        self.data_dir_patcher = patch.object(auth, "_get_data_dir", return_value=self.data_dir)
        self.client_patcher = patch.object(
            auth_endpoint, "_request_client_host", return_value="8.8.8.8"
        )
        self.auth_patcher.start()
        self.data_dir_patcher.start()
        self.client_patcher.start()

    def tearDown(self) -> None:
        self.client_patcher.stop()
        self.data_dir_patcher.stop()
        self.auth_patcher.stop()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("ADMIN_SETUP_TOKEN", None)
        Config.reset_instance()
        self.temp_dir.cleanup()

    @staticmethod
    def _request(host: str = "8.8.8.8"):
        return SimpleNamespace(
            headers={},
            url=SimpleNamespace(scheme="http"),
            cookies={},
            client=SimpleNamespace(host=host),
            app=SimpleNamespace(state=SimpleNamespace()),
        )

    def _credential_path(self) -> Path:
        return self.data_dir / ".admin_password_hash"

    # -- /auth/login -----------------------------------------------------

    def test_login_first_setup_requires_token_for_public_client(self) -> None:
        body = auth_endpoint.LoginRequest(
            username="admin",
            password="long-enough-password",
            password_confirm="long-enough-password",
        )
        response = asyncio.run(auth_endpoint.auth_login(self._request(), body))

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"setup_token_required", response.body)
        self.assertFalse(self._credential_path().exists())

    def test_login_first_setup_rejects_wrong_token(self) -> None:
        body = auth_endpoint.LoginRequest(
            username="admin",
            password="long-enough-password",
            password_confirm="long-enough-password",
            setup_token="wrong-token",
        )
        response = asyncio.run(auth_endpoint.auth_login(self._request(), body))

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"setup_token_invalid", response.body)
        self.assertFalse(self._credential_path().exists())

    def test_login_first_setup_accepts_correct_token(self) -> None:
        body = auth_endpoint.LoginRequest(
            username="admin",
            password="long-enough-password",
            password_confirm="long-enough-password",
            setup_token="test-setup-token",
        )
        response = asyncio.run(auth_endpoint.auth_login(self._request(), body))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._credential_path().exists())

    def test_login_first_setup_allows_loopback_without_token(self) -> None:
        self.client_patcher.stop()
        try:
            with patch.object(auth_endpoint, "_request_client_host", return_value="127.0.0.1"):
                body = auth_endpoint.LoginRequest(
                    username="admin",
                    password="long-enough-password",
                    password_confirm="long-enough-password",
                )
                response = asyncio.run(auth_endpoint.auth_login(self._request("127.0.0.1"), body))
        finally:
            self.client_patcher.start()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._credential_path().exists())

    # -- /auth/settings --------------------------------------------------

    def test_settings_enable_requires_token_for_public_client(self) -> None:
        self.auth_patcher.stop()
        try:
            with patch.object(auth, "_is_auth_enabled_from_env", return_value=False):
                auth._auth_enabled = False
                body = auth_endpoint.AuthSettingsRequest(
                    auth_enabled=True,
                    password="long-enough-password",
                    password_confirm="long-enough-password",
                )
                response = asyncio.run(auth_endpoint.auth_update_settings(self._request(), body))
        finally:
            self.auth_patcher.start()

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"setup_token_required", response.body)
        self.assertFalse(self._credential_path().exists())

    def test_settings_enable_accepts_token_and_enables_auth(self) -> None:
        from dotenv import dotenv_values

        def _auth_enabled_from_file() -> bool:
            value = dotenv_values(self.env_path).get("ADMIN_AUTH_ENABLED") or ""
            return value.strip().lower() in ("true", "1", "yes")

        self.auth_patcher.stop()
        try:
            with patch.object(auth, "_is_auth_enabled_from_env", side_effect=_auth_enabled_from_file):
                auth._auth_enabled = False
                body = auth_endpoint.AuthSettingsRequest(
                    auth_enabled=True,
                    password="long-enough-password",
                    password_confirm="long-enough-password",
                    setup_token="test-setup-token",
                )
                response = asyncio.run(auth_endpoint.auth_update_settings(self._request(), body))
        finally:
            self.auth_patcher.start()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._credential_path().exists())
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))

    def test_settings_existing_password_still_requires_current_password(self) -> None:
        self.assertTrue(auth.set_initial_password("old-enough-password") is None)
        body = auth_endpoint.AuthSettingsRequest(
            auth_enabled=True,
            setup_token="test-setup-token",
            current_password="wrong-password",
        )
        response = asyncio.run(auth_endpoint.auth_update_settings(self._request(), body))

        self.assertEqual(response.status_code, 401)

    # -- /auth/status ----------------------------------------------------

    def test_status_reports_setup_token_requirement(self) -> None:
        data = asyncio.run(auth_endpoint.auth_status(self._request()))
        self.assertTrue(data["setupTokenRequired"])

    def test_status_loopback_does_not_require_token(self) -> None:
        self.client_patcher.stop()
        try:
            with patch.object(auth_endpoint, "_request_client_host", return_value="127.0.0.1"):
                data = asyncio.run(auth_endpoint.auth_status(self._request("127.0.0.1")))
        finally:
            self.client_patcher.start()

        self.assertFalse(data["setupTokenRequired"])

    # -- startup policy --------------------------------------------------

    def test_public_unauthenticated_bind_can_be_rejected(self) -> None:
        os.environ["DSA_REQUIRE_AUTH_FOR_PUBLIC"] = "true"
        self.auth_patcher.stop()
        try:
            with patch.object(auth, "_is_auth_enabled_from_env", return_value=False):
                auth._auth_enabled = False
                with self.assertRaises(auth.InsecurePublicBindError):
                    auth.log_auth_startup_state("0.0.0.0", 8000)
        finally:
            self.auth_patcher.start()
            os.environ.pop("DSA_REQUIRE_AUTH_FOR_PUBLIC", None)

    def test_loopback_never_rejected(self) -> None:
        os.environ["DSA_REQUIRE_AUTH_FOR_PUBLIC"] = "true"
        self.auth_patcher.stop()
        try:
            with patch.object(auth, "_is_auth_enabled_from_env", return_value=False):
                auth._auth_enabled = False
                auth.log_auth_startup_state("127.0.0.1", 8000)
        finally:
            self.auth_patcher.start()
            os.environ.pop("DSA_REQUIRE_AUTH_FOR_PUBLIC", None)


if __name__ == "__main__":
    unittest.main()
