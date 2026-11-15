# -*- coding: utf-8 -*-
"""R2: config values must not smuggle extra ``.env`` lines.

An authenticated caller could previously submit ``"x ADMIN_AUTH_ENABLED=false"``
(separated by a Unicode line separator) on any editable key.  Validation only
rejected ``\\n`` / ``\\r``, ``ConfigManager`` silently stripped ``\\n`` but not
the other separators, and ``dotenv`` then read the tail as a second assignment.

The tests below go through the real ``SystemConfigService.update`` path and the
real ``ConfigManager`` file IO.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.config import Config
from src.core.config_manager import (
    ConfigManager,
    find_unsafe_env_lines,
    warn_unsafe_env_lines,
)
from src.services.system_config_service import ConfigValidationError, SystemConfigService

# Every character that ``str.splitlines()`` (and dotenv) treats as a line break.
UNSAFE_SEPARATORS = [
    "\n",
    "\r",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
]


class ConfigValueInjectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519,000001",
                    "ADMIN_AUTH_ENABLED=true",
                    "LOG_LEVEL=INFO",
                    'AGENT_EVENT_ALERT_RULES_JSON={"rules":[]}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()
        self.manager = ConfigManager(env_path=self.env_path)
        self.service = SystemConfigService(manager=self.manager)

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    def _update(self, key: str, value: str):
        version = self.manager.get_config_version()
        return self.service.update(
            config_version=version,
            items=[{"key": key, "value": value}],
            mask_token="******",
            reload_now=False,
        )

    def test_every_line_separator_is_rejected_and_env_is_unchanged(self) -> None:
        before = self.env_path.read_bytes()
        for separator in UNSAFE_SEPARATORS:
            with self.subTest(separator=hex(ord(separator))):
                with self.assertRaises(ConfigValidationError) as ctx:
                    self._update("LOG_LEVEL", f"INFO{separator}ADMIN_AUTH_ENABLED=false")
                issue = ctx.exception.issues[0]
                self.assertEqual(issue["code"], "invalid_value")
                self.assertEqual(self.env_path.read_bytes(), before)

    def test_tab_remains_allowed(self) -> None:
        # Tabs cannot start a new dotenv assignment and are used by some
        # template values, so they must stay valid.
        self.assertIsNone(SystemConfigService._env_value_unsafe_reason("INFO\tDEBUG"))
        self.manager.apply_updates(
            updates=[("LOG_LEVEL", "INFO\tDEBUG")],
            sensitive_keys=set(),
            mask_token="******",
        )
        self.assertIn("LOG_LEVEL=INFO\tDEBUG", self.env_path.read_text(encoding="utf-8"))

    def test_write_then_unrelated_save_keeps_admin_flag(self) -> None:
        self._update("LOG_LEVEL", "DEBUG")
        self._update("STOCK_LIST", "600519,300750")
        self.assertIn("ADMIN_AUTH_ENABLED=true", self.env_path.read_text(encoding="utf-8"))
        values = ConfigManager(env_path=self.env_path).read_config_map()
        self.assertEqual(values.get("ADMIN_AUTH_ENABLED"), "true")

    def test_json_field_with_embedded_carriage_return_is_normalized(self) -> None:
        payload = json.dumps(
            {"rules": [{"stock_code": "600519", "alert_type": "price_cross", "price": 1500, "direction": "above"}]},
            indent=2,
        ).replace("\n", "\r\n")
        self.assertIn("\r\n", payload)
        self._update("AGENT_EVENT_ALERT_RULES_JSON", payload)

        content = self.env_path.read_text(encoding="utf-8")
        lines = [line for line in content.split("\n") if line.startswith("AGENT_EVENT_ALERT_RULES_JSON=")]
        self.assertEqual(len(lines), 1)
        stored = lines[0].split("=", 1)[1]
        self.assertNotIn("\r", stored)
        self.assertNotIn("\n", stored)
        self.assertEqual(
            json.loads(stored),
            {"rules": [{"stock_code": "600519", "alert_type": "price_cross", "price": 1500, "direction": "above"}]},
        )

    def test_json_field_with_raw_replacement_separator_is_rejected(self) -> None:
        before = self.env_path.read_bytes()
        with self.assertRaises(ConfigValidationError):
            self._update(
                "AGENT_EVENT_ALERT_RULES_JSON",
                '{"rules":[]}\x0bADMIN_AUTH_ENABLED=false',
            )
        self.assertEqual(self.env_path.read_bytes(), before)


class ConfigManagerLineHandlingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        os.environ["ENV_FILE"] = str(self.env_path)

    def tearDown(self) -> None:
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    def test_reader_keeps_value_containing_unicode_separator_on_one_line(self) -> None:
        # Even if such a file predates the validation, the reader must not
        # reinterpret the tail as a second assignment.
        self.env_path.write_text(
            "NOTES=a\x0bADMIN_AUTH_ENABLED=false\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        manager = ConfigManager(env_path=self.env_path)
        values = manager.read_config_map()
        self.assertTrue(values.get("ADMIN_AUTH_ENABLED") == "true")
        self.assertIn("ADMIN_AUTH_ENABLED", values)
        self.assertNotIn("false", [values.get("ADMIN_AUTH_ENABLED")])

    def test_reader_tolerates_crlf_line_endings(self) -> None:
        self.env_path.write_bytes(b"LOG_LEVEL=DEBUG\r\nADMIN_AUTH_ENABLED=true\r\n")
        manager = ConfigManager(env_path=self.env_path)
        values = manager.read_config_map()
        self.assertEqual(values.get("LOG_LEVEL"), "DEBUG")
        self.assertEqual(values.get("ADMIN_AUTH_ENABLED"), "true")

    def test_apply_updates_refuses_unsafe_value_instead_of_rewriting_it(self) -> None:
        self.env_path.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
        manager = ConfigManager(env_path=self.env_path)
        before = self.env_path.read_bytes()
        with self.assertRaises(ValueError):
            manager.apply_updates(
                updates=[("LOG_LEVEL", "INFO\u2028ADMIN_AUTH_ENABLED=false")],
                sensitive_keys=set(),
                mask_token="******",
            )
        self.assertEqual(self.env_path.read_bytes(), before)

    def test_warn_unsafe_env_lines_reports_line_numbers(self) -> None:
        self.env_path.write_text(
            "LOG_LEVEL=INFO\nNOTES=a\x85b\nADMIN_AUTH_ENABLED=true\n",
            encoding="utf-8",
        )
        text = self.env_path.read_text(encoding="utf-8")
        self.assertEqual(find_unsafe_env_lines(text), [(2, "\x85")])
        with self.assertLogs("src.core.config_manager", level="WARNING") as captured:
            warn_unsafe_env_lines(self.env_path)
        self.assertTrue(any("line 2" in message for message in captured.output))


if __name__ == "__main__":
    unittest.main()
