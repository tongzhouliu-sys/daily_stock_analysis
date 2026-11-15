# -*- coding: utf-8 -*-
"""T26: 停止按钮真正取消后端。

覆盖：
- 运行级取消事件注册/置位/回收
- ``run_agent_loop`` 在每次模型调用前、每次工具批次后检查取消；
  被停止后不再发生模型调用
- 停止时落“已停止”终态标记而不是完整回答
- 会话运行中再次提问返回 409 / ``session_turn_in_progress``
- 停止接口必须带 ``request_id``，只带 ``session_id`` 返回 422
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from src.agent import cancellation
from src.agent.cancellation import (
    get_run_event,
    is_cancelled,
    register_run,
    release_run,
    request_cancel,
)
from src.config import Config
from src.storage import ChatTurnInProgress, DatabaseManager
from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestCancellationRegistry(unittest.TestCase):
    def tearDown(self) -> None:
        cancellation.release_run("run-1")

    def test_register_and_cancel(self) -> None:
        event = register_run("run-1")
        self.assertIs(get_run_event("run-1"), event)
        self.assertFalse(is_cancelled("run-1"))
        self.assertTrue(request_cancel("run-1"))
        self.assertTrue(event.is_set())
        self.assertTrue(is_cancelled("run-1"))
        release_run("run-1")
        self.assertIsNone(get_run_event("run-1"))
        self.assertFalse(request_cancel("run-1"))
        self.assertFalse(is_cancelled("run-1"))


    def test_alias_shares_the_event_and_is_released_with_the_run(self) -> None:
        event = register_run("run-1")
        self.assertTrue(cancellation.alias_run("run-1", "turn-1"))
        self.assertIs(get_run_event("turn-1"), event)
        self.assertTrue(request_cancel("turn-1"))
        self.assertTrue(event.is_set())
        release_run("run-1")
        self.assertIsNone(get_run_event("turn-1"))

    def test_alias_for_unknown_run_is_ignored(self) -> None:
        self.assertFalse(cancellation.alias_run("missing-run", "turn-2"))
        self.assertIsNone(get_run_event("turn-2"))

class TestRunLoopCancellation(unittest.TestCase):
    """停止后循环在下一个检查点退出，不再发生模型调用。"""

    def _build_registry(self):
        from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="noop_tool",
            description="noop",
            parameters=[],
            handler=lambda: "ok",
        ))
        return registry

    def _fake_response(self, index: int):
        from src.agent.llm_adapter import LLMResponse

        return LLMResponse(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id=f"call_{index}",
                    name="noop_tool",
                    arguments={},
                    thought_signature=None,
                    provider_specific_fields=None,
                )
            ],
            provider="openai",
            model="gpt-4o-mini",
        )

    def test_stops_after_tool_batch_and_skips_next_model_call(self) -> None:
        from src.agent.runner import run_agent_loop

        registry = self._build_registry()
        adapter = MagicMock()
        cancel_event = threading.Event()
        counter = {"llm": 0}

        def _call(_messages, _tools, **kwargs):
            counter["llm"] += 1
            return self._fake_response(counter["llm"])

        adapter.call_with_tools.side_effect = _call

        def _progress(event):
            if event.get("type") == "tool_done":
                cancel_event.set()

        result = run_agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=5,
            progress_callback=_progress,
            cancel_event=cancel_event,
        )

        self.assertTrue(result.cancelled)
        self.assertFalse(result.success)
        self.assertEqual(counter["llm"], 1)
        self.assertEqual(counter["llm"], adapter.call_with_tools.call_count)

    def test_cancel_before_first_call_makes_no_model_call(self) -> None:
        from src.agent.runner import run_agent_loop

        registry = self._build_registry()
        adapter = MagicMock()
        cancel_event = threading.Event()
        cancel_event.set()

        result = run_agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=3,
            cancel_event=cancel_event,
        )

        self.assertTrue(result.cancelled)
        adapter.call_with_tools.assert_not_called()



class TestCancelledTurnPersistence(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path: Path):
        DatabaseManager.reset_instance()
        Config.reset_instance()
        DatabaseManager(db_url=f"sqlite:///{tmp_path / 'cancel.db'}")
        yield
        DatabaseManager.reset_instance()
        Config.reset_instance()

    def test_cancelled_result_persists_stopped_marker(self) -> None:
        from src.agent.chat_executor import AgentChatExecutor
        from src.agent.executor import AgentResult

        executor = AgentChatExecutor.__new__(AgentChatExecutor)
        executor.max_steps = 5
        executor.timeout_seconds = None
        executor.config = SimpleNamespace()
        executor.context_llm_adapter = None

        turn = SimpleNamespace(
            session_id="cancel-session",
            message="hi",
            run_id="run-cancel",
            baseline_len=0,
            user_message_id=None,
            accepted_snapshot=None,
            db_manager=None,
            prepared=SimpleNamespace(
                system_prompt="sys",
                history_messages=[],
                stock_scope=None,
                analysis_brief=None,
                report_language="zh",
            ),
        )

        backend_result = SimpleNamespace(
            success=False,
            final_answer="",
            tool_calls_log=[],
            total_steps=1,
            model="",
            backend="litellm",
            usage=None,
            diagnostics={},
            error_message="本轮分析已由用户停止。",
            error_code="cancelled",
            messages=[],
            cancelled=True,
        )
        executor.backend = MagicMock()
        executor.backend.run.return_value = backend_result

        recorded: List[str] = []
        with patch("src.agent.chat_executor.conversation_manager") as manager:
            manager.add_message.side_effect = lambda *a, **k: recorded.append(a[2])
            manager.set_running.return_value = True
            result = executor.execute_turn(turn, cancel_event=threading.Event())

        self.assertTrue(result.cancelled)
        self.assertFalse(result.success)
        self.assertEqual(recorded, ["[已停止] 本轮分析已由用户停止。"])


class TestRunningTurnConflict(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path: Path):
        DatabaseManager.reset_instance()
        Config.reset_instance()
        DatabaseManager(db_url=f"sqlite:///{tmp_path / 'conflict.db'}")
        yield
        DatabaseManager.reset_instance()
        Config.reset_instance()

    def test_running_session_rejects_new_turn(self) -> None:
        from api.v1.endpoints.agent import _is_session_turn_in_progress

        db = DatabaseManager()
        session_service = SimpleNamespace(db=db)
        db.ensure_chat_session_generation("running-session")
        db.set_session_running("running-session", True)

        self.assertTrue(_is_session_turn_in_progress(session_service, "running-session"))
        db.set_session_running("running-session", False)
        self.assertFalse(_is_session_turn_in_progress(session_service, "running-session"))

    def test_conflict_exception_contract(self) -> None:
        exc = ChatTurnInProgress()
        self.assertEqual(exc.code, "session_turn_in_progress")
        self.assertEqual(exc.status_code, 409)
        self.assertIn("上一轮仍在停止中", str(exc))


class TestStopEndpoint(unittest.TestCase):
    def test_stop_requests_cancel(self) -> None:
        client = TestClient(create_app())
        event = register_run("stop-request")
        try:
            response = client.post(
                "/api/v1/agent/chat/stop",
                json={"session_id": "s1", "request_id": "stop-request"},
            )
        finally:
            release_run("stop-request")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancelled"])
        self.assertTrue(event.is_set())


if __name__ == "__main__":
    unittest.main()
