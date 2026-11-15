# -*- coding: utf-8 -*-
"""R3: every configured secret is masked on read and reusable for tests.

The settings read endpoint used to mask only four Hermes/telemetry keys while
46 registry fields were marked ``is_sensitive``; every other API key, mailbox
password and bot token came back verbatim.  These tests walk the real
``get_config`` / ``update`` / channel-test paths.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.config import Config
from src.core.config_manager import ConfigManager
from src.core.config_registry import get_field_definition, get_registered_field_keys
from src.services.system_config_service import SystemConfigService

SECRET_VALUES = {
    "GEMINI_API_KEY": "gemini-secret-value",
    "DEEPSEEK_API_KEY": "deepseek-secret-value",
    "ANTHROPIC_API_KEY": "anthropic-secret-value",
    "AIHUBMIX_KEY": "aihubmix-secret-value",
    "OPENAI_API_KEY": "openai-secret-value",
    "LLM_MY_PROXY_API_KEY": "proxy-secret-value",
    "LLM_MY_PROXY_API_KEYS": "proxy-secret-1,proxy-secret-2",
    "LLM_MY_PROXY_EXTRA_HEADERS": '{"X-Token":"header-secret"}',
    "EMAIL_PASSWORD": "mailbox-secret-value",
    "TELEGRAM_BOT_TOKEN": "telegram-secret-value",
    "DISCORD_BOT_TOKEN": "discord-secret-value",
    "SLACK_BOT_TOKEN": "slack-secret-value",
    "CUSTOM_WEBHOOK_BEARER_TOKEN": "webhook-bearer-secret",
    "PUSHPLUS_TOKEN": "pushplus-secret",
    "SERVERCHAN3_SENDKEY": "serverchan-secret",
    "TUSHARE_TOKEN": "tushare-secret",
}


class SensitiveMaskingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        lines = [
            "STOCK_LIST=600519",
            "ADMIN_AUTH_ENABLED=true",
            "LLM_CHANNELS=deepseek,my_proxy",
            "LLM_DEEPSEEK_PROTOCOL=deepseek",
            "LLM_DEEPSEEK_BASE_URL=https://api.deepseek.com",
            "LLM_DEEPSEEK_MODELS=deepseek-chat",
            "LLM_DEEPSEEK_API_KEY=deepseek-secret-value",
            "LLM_MY_PROXY_PROTOCOL=openai",
            "LLM_MY_PROXY_BASE_URL=https://proxy.example.com/v1",
            "LLM_MY_PROXY_MODELS=gpt-test",
        ]
        for key, value in SECRET_VALUES.items():
            lines.append(f"{key}={value}")
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()
        self.manager = ConfigManager(env_path=self.env_path)
        self.service = SystemConfigService(manager=self.manager)

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    def _items(self) -> Dict[str, Dict[str, Any]]:
        payload = self.service.get_config(include_schema=True)
        return {item["key"]: item for item in payload["items"]}

    def test_no_sensitive_value_is_returned_in_clear(self) -> None:
        payload = self.service.get_config(include_schema=True)
        rendered = json.dumps(payload, ensure_ascii=False)
        items = {item["key"]: item for item in payload["items"]}

        for key, secret in SECRET_VALUES.items():
            with self.subTest(key=key):
                self.assertIn(key, items)
                self.assertNotIn(secret, rendered)
                self.assertEqual(items[key]["value"], payload["mask_token"])
                self.assertTrue(items[key]["is_masked"])
                self.assertTrue(items[key]["raw_value_exists"])

    def test_every_registry_sensitive_key_with_a_value_is_masked(self) -> None:
        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}
        sensitive_keys = [
            key for key in get_registered_field_keys()
            if get_field_definition(key, items.get(key, {}).get("value", "")).get("is_sensitive")
        ]
        self.assertGreaterEqual(len(sensitive_keys), 40)
        for key in sensitive_keys:
            item = items.get(key)
            if item is None or not item["value"]:
                continue
            with self.subTest(key=key):
                self.assertEqual(item["value"], payload["mask_token"])
                self.assertTrue(item["is_masked"])

    def test_submitting_placeholder_keeps_stored_secret(self) -> None:
        before = self.env_path.read_text(encoding="utf-8")
        payload = self.service.get_config(include_schema=True)
        mask_token = payload["mask_token"]

        result = self.service.update(
            config_version=payload["config_version"],
            items=[
                {"key": "GEMINI_API_KEY", "value": mask_token},
                {"key": "LLM_MY_PROXY_API_KEY", "value": mask_token},
                {"key": "STOCK_LIST", "value": "600519,300750"},
            ],
            mask_token=mask_token,
            reload_now=False,
        )

        content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY=gemini-secret-value", content)
        self.assertIn("LLM_MY_PROXY_API_KEY=proxy-secret-value", content)
        self.assertIn("STOCK_LIST=600519,300750", content)
        self.assertEqual(result["skipped_masked_count"], 2)
        self.assertEqual(result["applied_count"], 1)
        self.assertNotEqual(before, content)

    def test_channel_test_reuses_saved_key_when_endpoint_unchanged(self) -> None:
        completion = _completion_mock()
        with patch("litellm.completion", completion):
            result = self.service.test_llm_channel(
                name="my_proxy",
                protocol="openai",
                base_url="https://proxy.example.com/v1",
                api_key="******",
                models=["gpt-test"],
            )

        self.assertTrue(result["success"], result)
        completion.assert_called_once()
        self.assertNotIn("proxy-secret-value", json.dumps(result, ensure_ascii=False))

    def test_channel_test_rejects_reuse_when_endpoint_changed(self) -> None:
        completion = _completion_mock()
        with patch("litellm.completion", completion):
            result = self.service.test_llm_channel(
                name="my_proxy",
                protocol="openai",
                base_url="https://attacker.example.com/v1",
                api_key="******",
                models=["gpt-test"],
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "saved_secret_scope_mismatch")
        self.assertIn("re-enter", (result.get("error") or "").lower())
        completion.assert_not_called()

    def test_model_discovery_rejects_reuse_when_endpoint_changed(self) -> None:
        with patch("src.services.system_config_service.requests.Session") as session_cls:
            result = self.service.discover_llm_channel_models(
                name="my_proxy",
                protocol="openai",
                base_url="https://attacker.example.com/v1",
                api_key="******",
                models=["gpt-test"],
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "saved_secret_scope_mismatch")
        session_cls.assert_not_called()

    def test_export_endpoint_still_returns_raw_dotenv(self) -> None:
        exported = self.service.export_env()
        body = exported.get("content") or json.dumps(exported)
        self.assertIn("GEMINI_API_KEY=gemini-secret-value", body)


def _completion_mock():
    message = SimpleNamespace(content="OK", tool_calls=[])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    from unittest.mock import MagicMock

    return MagicMock(return_value=response)


if __name__ == "__main__":
    unittest.main()
