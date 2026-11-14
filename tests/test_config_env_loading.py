# -*- coding: utf-8 -*-
"""Config fields must be populated from the environment when documented."""

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config

CONFIG_SOURCE_PATH = Path(__file__).resolve().parent.parent / "src" / "config.py"


class AgentChatConfigEnvLoadingTestCase(unittest.TestCase):
    """Agent chat latency / streaming switches read their env keys."""

    def tearDown(self) -> None:
        Config.reset_instance()

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    @patch.object(Config, "_parse_stock_email_groups", return_value=[])
    def test_agent_chat_switches_load_from_env(self, *_mocks) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_CHAT_TOOL_REUSE_SECONDS": "120",
                "AGENT_LLM_CALL_TIMEOUT_S": "90",
                "AGENT_OPT_IN_THINKING": "false",
                "AGENT_CHAT_STREAM_CONTENT": "true",
            },
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertEqual(config.agent_chat_tool_reuse_seconds, 120)
        self.assertEqual(config.agent_llm_call_timeout_s, 90.0)
        self.assertFalse(config.agent_opt_in_thinking)
        self.assertTrue(config.agent_chat_stream_content)

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    @patch.object(Config, "_parse_stock_email_groups", return_value=[])
    def test_agent_chat_switches_keep_documented_defaults(self, *_mocks) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.agent_chat_tool_reuse_seconds, 600)
        self.assertEqual(config.agent_llm_call_timeout_s, 0.0)
        self.assertTrue(config.agent_opt_in_thinking)
        self.assertFalse(config.agent_chat_stream_content)


class ConfigEnvLoadingStructureTestCase(unittest.TestCase):
    """Every public Config field is loaded from the environment, or exempted."""

    # Fields declared on Config but intentionally not read from the environment.
    ALLOWED_UNLOADED_FIELDS = {
        "akshare_sleep_min": (
            "Legacy flow-control placeholder: no env key is documented and no "
            "code reads this field; the live rate limit is enforced elsewhere."
        ),
        "akshare_sleep_max": (
            "Legacy flow-control placeholder: no env key is documented and no "
            "code reads this field; the live rate limit is enforced elsewhere."
        ),
        "tushare_rate_limit_per_minute": (
            "Legacy flow-control placeholder: no env key is documented and no "
            "code reads this field."
        ),
        "max_retries": (
            "Legacy retry placeholder: no env key is documented and no code "
            "reads this field."
        ),
        "retry_base_delay": (
            "Legacy retry placeholder: no env key is documented and no code "
            "reads this field."
        ),
        "retry_max_delay": (
            "Legacy retry placeholder: no env key is documented and no code "
            "reads this field."
        ),
    }

    def test_every_public_config_field_is_loaded_from_env(self) -> None:
        tree = ast.parse(CONFIG_SOURCE_PATH.read_text(encoding="utf-8"))
        config_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Config"
        )

        declared_fields = {
            node.target.id
            for node in config_class.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        public_fields = {name for name in declared_fields if not name.startswith("_")}

        load_from_env = next(
            node
            for node in config_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_load_from_env"
        )
        loaded_fields = {
            keyword.arg
            for node in ast.walk(load_from_env)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "cls"
            for keyword in node.keywords
            if keyword.arg
        }

        self.assertEqual(
            loaded_fields - declared_fields,
            set(),
            "Config._load_from_env passes a field that Config does not declare",
        )

        unloaded_fields = public_fields - loaded_fields
        self.assertEqual(
            unloaded_fields,
            set(self.ALLOWED_UNLOADED_FIELDS),
            "Add the missing assignment in Config._load_from_env, or extend "
            "ALLOWED_UNLOADED_FIELDS with a reason when the field really must "
            "not be environment-driven.",
        )
        for field_name, reason in self.ALLOWED_UNLOADED_FIELDS.items():
            self.assertTrue(reason.strip(), f"Missing exemption reason for {field_name}")


if __name__ == "__main__":
    unittest.main()
