# -*- coding: utf-8 -*-
"""R7: 问股执行生命周期。

覆盖：
- 客户端断开（刷新/关页/断网）不取消这一轮，后端继续跑完
- 空闲超时只统计“多久没有事件”，超时后取消本轮并清理运行态
- 运行态心跳过期（90 秒）不再永久挡住新一轮
- 会话运行中在开始流式响应之前返回真实 HTTP 409
- 停止接口必须带 ``request_id``，只带 ``session_id`` 返回 422
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from api.v1.endpoints import agent as agent_endpoint
from src.agent import cancellation
from src.storage import (
    CHAT_RUNNING_STALE_AFTER_SECONDS,
    DatabaseManager,
    to_utc_isoformat,
    utc_naive_now,
)


class _FakeDb:
    """只实现端点真正用到的会话状态读写。"""

    def __init__(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        self.snapshot = snapshot or {"is_running": False}
        self.running_writes: List[Dict[str, Any]] = []

    def read_chat_session_snapshot(self, session_id: str) -> Dict[str, Any]:
        return dict(self.snapshot)

    def set_session_running(self, session_id: str, value: bool, **kwargs: Any) -> bool:
        self.running_writes.append({"session_id": session_id, "value": value, **kwargs})
        return True


def _accepted_payload() -> Dict[str, Any]:
    return {
        "active_stock_context": None,
        "session_state_version": 1,
        "session_generation": 1,
        "selected_skill_ids": None,
    }


class _FakeExecutor:
    """执行体：可选先发一条进度事件，然后一直等到测试放行。"""

    def __init__(self, release: threading.Event, emit_initial_event: bool = True,
                 wait_seconds: float = 10.0) -> None:
        self.release = release
        self.emit_initial_event = emit_initial_event
        self.wait_seconds = wait_seconds
        self.finished = threading.Event()
        self.cancelled = False

    def execute_turn(self, turn, progress_callback=None, stream_content=False, cancel_event=None):
        if self.emit_initial_event and progress_callback is not None:
            progress_callback({"type": "generating"})
        self.release.wait(self.wait_seconds)
        self.cancelled = bool(cancel_event and cancel_event.is_set())
        self.finished.set()
        return SimpleNamespace(
            success=True,
            content="answer",
            error=None,
            total_steps=1,
            cancelled=self.cancelled,
            backend="litellm",
            error_code=None,
            assistant_message_id=None,
            thinking_steps=[],
            answer_meta=None,
        )


def _read_event(chunk: str) -> Optional[Dict[str, Any]]:
    if not chunk.startswith("data: "):
        return None
    return json.loads(chunk[len("data: "):])


def _turn() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="lifecycle-session",
        run_id="turn-run-id",
        db_manager=None,
        accepted_snapshot=None,
    )


async def _open_stream(session_service, executor, request_id: str) -> Any:
    """在补丁仍然生效时真正进入生成器，避免惰性求值落到补丁之外。"""

    request = agent_endpoint.ChatRequest(
        message="帮我看看 600519", session_id="lifecycle-session", request_id=request_id,
    )
    with patch.object(
        agent_endpoint, "_prepare_chat_request",
        return_value=(executor, _turn(), _accepted_payload(), None, None, None),
    ):
        response = await agent_endpoint.agent_chat_stream(
            request, session_service, intake_service=None,
        )
        stream = response.body_iterator
        first = await stream.__anext__()
    return stream, first


def test_client_disconnect_does_not_cancel_the_run() -> None:
    """Q1: 刷新/关页导致断开时，这一轮继续跑完，只有停止接口才取消。"""

    # 断开后执行体才收尾，用来证明断开没有置位取消事件。
    release = threading.Event()
    executor = _FakeExecutor(release, wait_seconds=1.0)
    db = _FakeDb()
    request_id = "disconnect-run"

    async def _scenario():
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "request_cancel") as cancel_mock:
            stream, first = await _open_stream(SimpleNamespace(db=db), executor, request_id)
            # 再取一条事件，确保执行体已经在后台线程里跑起来了。
            second = await asyncio.wait_for(stream.__anext__(), timeout=5)
            await stream.aclose()
        return first, second, cancel_mock

    text, progress, cancel_mock = asyncio.run(_scenario())

    assert executor.finished.wait(5)
    cancel_mock.assert_not_called()
    assert _read_event(text)["type"] == "accepted"
    assert _read_event(progress)["type"] == "generating"
    # 断开没有取消这一轮：取消事件从未置位，后台执行体自己跑完了。
    assert executor.cancelled is False
    assert not cancellation.is_cancelled(request_id)


def test_stop_after_client_disconnect_still_cancels_run() -> None:
    """Q1: 刷新后仍可点停止。断开不能提前回收 request_id 的取消入口。"""

    release = threading.Event()
    executor = _FakeExecutor(release, wait_seconds=5.0)
    request_id = "disconnect-then-stop"

    async def _scenario():
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"):
            stream, first = await _open_stream(SimpleNamespace(db=_FakeDb()), executor, request_id)
            second = await asyncio.wait_for(stream.__anext__(), timeout=5)
            await stream.aclose()
            # 断开时执行体仍在后台跑，停止入口必须还在。
            assert cancellation.request_cancel(request_id) is True
            release.set()
            await asyncio.to_thread(executor.finished.wait, 5)
            for _ in range(100):
                if cancellation.get_run_event(request_id) is None:
                    break
                await asyncio.sleep(0.01)
        return first, second

    first, second = asyncio.run(_scenario())

    assert _read_event(first)["type"] == "accepted"
    assert _read_event(second)["type"] == "generating"
    assert executor.cancelled is True
    assert cancellation.get_run_event(request_id) is None


def test_idle_timeout_cancels_run_and_clears_running_state() -> None:
    """连续没有业务事件才超时；超时要取消本轮并清运行态。"""

    release = threading.Event()
    executor = _FakeExecutor(release, emit_initial_event=False)
    db = _FakeDb()
    request_id = "idle-run"

    async def _scenario():
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "STREAM_IDLE_TIMEOUT_SECONDS", 0.05), \
                patch.object(agent_endpoint, "HEARTBEAT_INTERVAL_SECONDS", 0.02), \
                patch.object(agent_endpoint, "request_cancel") as cancel_mock:
            stream, first = await _open_stream(SimpleNamespace(db=db), executor, request_id)
            chunks = [first]
            try:
                while True:
                    chunks.append(await asyncio.wait_for(stream.__anext__(), timeout=5))
            except StopAsyncIteration:
                pass
        return chunks, cancel_mock

    try:
        chunks, cancel_mock = asyncio.run(_scenario())
        cancel_mock.assert_called_once_with(request_id)
    finally:
        release.set()
        executor.finished.wait(5)

    events = [event for event in (_read_event(chunk) for chunk in chunks) if event]
    assert events[0]["type"] == "accepted"
    assert events[-1]["type"] == "error"
    assert events[-1]["error_code"] == "stream_idle_timeout"
    assert db.running_writes == [
        {"session_id": "lifecycle-session", "value": False, "turn_id": "turn-run-id"}
    ]


class TestRunningStateStaleness:
    def _snapshot(self, *, heartbeat, started=None) -> Dict[str, Any]:
        return {
            "is_running": True,
            "running_heartbeat_at": heartbeat,
            "running_started_at": started,
            "running_turn_id": "turn-1",
        }

    def test_fresh_heartbeat_keeps_turn_in_progress(self) -> None:
        db = _FakeDb(self._snapshot(heartbeat=to_utc_isoformat(utc_naive_now())))
        assert agent_endpoint._is_session_turn_in_progress(SimpleNamespace(db=db), "s1") is True
        assert db.running_writes == []

    def test_stale_heartbeat_unblocks_and_clears_state(self) -> None:
        stale = utc_naive_now() - timedelta(seconds=CHAT_RUNNING_STALE_AFTER_SECONDS + 30)
        db = _FakeDb(self._snapshot(heartbeat=to_utc_isoformat(stale)))
        assert agent_endpoint._is_session_turn_in_progress(SimpleNamespace(db=db), "s1") is False
        assert db.running_writes == [
            {"session_id": "s1", "value": False, "turn_id": "turn-1"}
        ]

    def test_missing_timestamps_are_treated_as_stale(self) -> None:
        db = _FakeDb(self._snapshot(heartbeat=None, started=None))
        assert agent_endpoint._is_session_turn_in_progress(SimpleNamespace(db=db), "s1") is False

    def test_unreadable_state_fails_open(self) -> None:
        class _Broken:
            def read_chat_session_snapshot(self, _session_id: str):
                raise RuntimeError("db down")

        assert agent_endpoint._is_session_turn_in_progress(SimpleNamespace(db=_Broken()), "s1") is False


class TestStopEndpointContract:
    def test_stop_requires_request_id(self) -> None:
        from fastapi.testclient import TestClient

        from api.app import create_app

        client = TestClient(create_app())
        response = client.post("/api/v1/agent/chat/stop", json={"session_id": "s1"})
        assert response.status_code == 422

    def test_stop_cancels_registered_run(self) -> None:
        from fastapi.testclient import TestClient

        from api.app import create_app

        client = TestClient(create_app())
        event = cancellation.register_run("stop-contract")
        try:
            response = client.post(
                "/api/v1/agent/chat/stop",
                json={"session_id": "s1", "request_id": "stop-contract"},
            )
        finally:
            cancellation.release_run("stop-contract")
        assert response.status_code == 200
        assert response.json()["cancelled"] is True
        assert event.is_set()

    def test_stop_does_not_clear_session_state_for_unknown_run(self) -> None:
        db = _FakeDb()
        request = agent_endpoint.ChatStopRequest(request_id="unknown-run", session_id="s1")
        response = asyncio.run(agent_endpoint.agent_chat_stop(request))
        assert response.cancelled is False
        assert db.running_writes == []


@pytest.fixture()
def db_env(tmp_path: Path):
    DatabaseManager.reset_instance()
    from src.config import Config

    Config.reset_instance()
    DatabaseManager(db_url=f"sqlite:///{tmp_path / 'lifecycle.db'}")
    yield
    DatabaseManager.reset_instance()
    Config.reset_instance()


def test_stream_returns_http_409_before_starting(db_env) -> None:
    """运行中的会话在开始流式响应之前就被拒绝，前端能拿到真实状态码。"""

    db = DatabaseManager()
    db.ensure_chat_session_generation("busy-session")
    db.set_session_running("busy-session", True)

    request = agent_endpoint.ChatRequest(
        message="继续分析", session_id="busy-session", request_id="busy-request",
    )
    session_service = SimpleNamespace(db=db)

    async def _call():
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"):
            return await agent_endpoint.agent_chat_stream(request, session_service, intake_service=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_call())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "session_turn_in_progress"


# =============================================================================
# R7 验收：真实会话服务 + SQLite 的接口级测试
# =============================================================================


@pytest.fixture()
def api_env(tmp_path: Path):
    """真实文件库 + 放行守卫，只替换外部模型执行端点。"""

    from src.config import Config

    DatabaseManager.reset_instance()
    Config.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'lifecycle-api.db'}")
    with patch.object(agent_endpoint, "AgentDomainGuardService") as guard_service:
        from src.schemas.agent_domain_guard import DomainAction

        decision = SimpleNamespace(action=DomainAction.ALLOW, message="")
        guard_service.return_value.classify.return_value = (decision, SimpleNamespace())
        yield db
    DatabaseManager.reset_instance()
    Config.reset_instance()


def _litellm_config():
    return SimpleNamespace(
        agent_backend="auto",
        is_agent_available=lambda: True,
        report_language="zh",
    )


def _real_executor(run_impl):
    """真实 executor（含落库），只把外部后端换成受控实现。"""

    from src.agent.agent_backend import AgentRunResult
    from src.agent.chat_executor import AgentChatExecutor
    from unittest.mock import MagicMock

    backend = MagicMock()
    backend.backend_id = "litellm"
    backend.runtime_owns_loop = False
    backend.run.side_effect = run_impl
    return AgentChatExecutor(
        backend=backend, config=_litellm_config(), context_llm_adapter=None,
    )


def _wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def test_disconnect_lets_run_finish_and_persists_full_answer(api_env) -> None:
    """断开连接后后台继续跑完：落库完整回答、清运行态、不出现“已停止”。"""

    db = api_env

    from src.agent.agent_backend import AgentRunResult
    from src.services.agent_chat_session_service import AgentChatSessionService

    session_id = "disconnect-integration"
    request_id = "disconnect-integration-request"

    def _run(request):
        # 先发一条进度事件：执行体此时已经置位运行态，断开才有意义。
        if getattr(request, "progress_callback", None) is not None:
            request.progress_callback({"type": "generating"})
        time.sleep(0.2)
        return AgentRunResult(
            success=True, final_answer="完整回答", backend="litellm", messages=[],
        )

    executor = _real_executor(_run)
    session_service = AgentChatSessionService(db)

    async def _scenario():
        request = agent_endpoint.ChatRequest(
            message="分析 600519", session_id=session_id, request_id=request_id,
        )
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "_build_executor", return_value=executor):
            response = await agent_endpoint.agent_chat_stream(
                request, session_service, intake_service=None,
            )
            gen = response.body_iterator
            first = await gen.__anext__()
            # 再取一条事件：此时执行体已经在后台线程里跑，运行态已置位。
            progress = await asyncio.wait_for(gen.__anext__(), timeout=5)
            # 客户端断开：只关闭订阅，不取消这一轮。
            await gen.aclose()
        return first, progress

    first, progress = asyncio.run(_scenario())
    assert _read_event(first)["type"] == "accepted"
    assert _read_event(progress)["type"] == "generating"

    _wait_for(lambda: not db.read_chat_session_snapshot(session_id)["is_running"])

    snapshot = db.read_chat_session_snapshot(session_id)
    contents = [message["content"] for message in snapshot["messages"]]
    assert "完整回答" in contents
    assert all("已停止" not in content for content in contents)
    assert executor.backend.run.call_count == 1


def test_running_heartbeat_stays_fresh_while_run_continues_after_disconnect(api_env) -> None:
    """断开后、或只输出正文增量时，执行线程仍要刷新心跳，避免被误判过期而放行并发提问。"""

    db = api_env

    from src.agent.agent_backend import AgentRunResult
    from src.services.agent_chat_session_service import AgentChatSessionService

    session_id = "heartbeat-after-disconnect"
    request_id = "heartbeat-after-disconnect-request"
    release = threading.Event()

    def _run(request):
        if getattr(request, "progress_callback", None) is not None:
            request.progress_callback({"type": "generating"})
        # 之后长时间没有任何会落库的进度事件（等同于只在输出正文增量）。
        release.wait(timeout=5)
        return AgentRunResult(
            success=True, final_answer="完整回答", backend="litellm", messages=[],
        )

    executor = _real_executor(_run)
    session_service = AgentChatSessionService(db)

    async def _scenario():
        request = agent_endpoint.ChatRequest(
            message="分析 600519", session_id=session_id, request_id=request_id,
        )
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "_build_executor", return_value=executor):
            response = await agent_endpoint.agent_chat_stream(
                request, session_service, intake_service=None,
            )
            gen = response.body_iterator
            await gen.__anext__()
            await asyncio.wait_for(gen.__anext__(), timeout=5)
            await gen.aclose()
            # 断言必须在事件循环仍然存活时做：asyncio.run 退出时会等执行线程结束。
            try:
                await asyncio.sleep(0.8)  # 远超过期阈值，但这一轮仍在后台运行
                in_progress = await asyncio.to_thread(
                    agent_endpoint._is_session_turn_in_progress, session_service, session_id,
                )
                still_running = (
                    await asyncio.to_thread(db.read_chat_session_snapshot, session_id)
                )["is_running"]
            finally:
                release.set()
            return in_progress, still_running

    with patch.object(agent_endpoint, "HEARTBEAT_INTERVAL_SECONDS", 0.05), \
            patch.object(agent_endpoint, "CHAT_RUNNING_STALE_AFTER_SECONDS", 0.3):
        in_progress, still_running = asyncio.run(_scenario())

    assert in_progress is True
    assert still_running is True
    _wait_for(lambda: not db.read_chat_session_snapshot(session_id)["is_running"])


def test_stop_endpoint_cancels_turn_and_persists_stopped_marker(api_env) -> None:
    """停止接口：下一个检查点退出，不再调用模型，落“已停止”标记并清运行态。"""

    db = api_env

    from src.agent.agent_backend import AgentRunResult
    from src.services.agent_chat_session_service import AgentChatSessionService

    session_id = "stop-integration"
    request_id = "stop-integration-request"

    def _run(request):
        deadline = time.time() + 10
        while time.time() < deadline:
            cancel_event = getattr(request, "cancel_event", None)
            if cancel_event is not None and cancel_event.is_set():
                return AgentRunResult(
                    success=False, backend="litellm", messages=[],
                    error_code="cancelled", error_message="本轮分析已由用户停止。",
                    cancelled=True,
                )
            time.sleep(0.02)
        return AgentRunResult(success=True, final_answer="未停止", backend="litellm", messages=[])

    executor = _real_executor(_run)
    session_service = AgentChatSessionService(db)

    async def _scenario():
        request = agent_endpoint.ChatRequest(
            message="分析 600519", session_id=session_id, request_id=request_id,
        )
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "_build_executor", return_value=executor):
            response = await agent_endpoint.agent_chat_stream(
                request, session_service, intake_service=None,
            )
            gen = response.body_iterator
            events = [json.loads((await gen.__anext__()).removeprefix("data: "))]
            stop = await agent_endpoint.agent_chat_stop(
                agent_endpoint.ChatStopRequest(session_id=session_id, request_id=request_id)
            )
            async for chunk in gen:
                if chunk.startswith("data: "):
                    events.append(json.loads(chunk.removeprefix("data: ")))
        return events, stop

    events, stop = asyncio.run(_scenario())
    assert stop.cancelled is True
    assert events[0]["type"] == "accepted"
    assert events[-1]["type"] == "done"
    assert events[-1]["cancelled"] is True

    snapshot = db.read_chat_session_snapshot(session_id)
    contents = [message["content"] for message in snapshot["messages"]]
    assert "[已停止] 本轮分析已由用户停止。" in contents
    assert "未停止" not in contents
    assert snapshot["is_running"] is False
    assert executor.backend.run.call_count == 1


def test_progress_events_keep_a_long_answer_alive() -> None:
    """空闲超时按“多久没有事件”计：一直在出事件的回答不会被掐掉。"""

    class _TickingExecutor:
        def __init__(self) -> None:
            self.finished = threading.Event()

        def execute_turn(self, turn, progress_callback=None, stream_content=False, cancel_event=None):
            for _ in range(8):
                time.sleep(0.05)
                if progress_callback is not None:
                    progress_callback({"type": "tool_start", "tool": "get_realtime_quote"})
            self.finished.set()
            return SimpleNamespace(
                success=True, content="长回答", error=None, total_steps=8, cancelled=False,
                backend="litellm", error_code=None, assistant_message_id=None,
                thinking_steps=[], answer_meta=None,
            )

    executor = _TickingExecutor()
    db = _FakeDb()

    async def _scenario():
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(agent_endpoint, "STREAM_IDLE_TIMEOUT_SECONDS", 0.15), \
                patch.object(agent_endpoint, "HEARTBEAT_INTERVAL_SECONDS", 0.02), \
                patch.object(agent_endpoint, "request_cancel") as cancel_mock:
            stream, first = await _open_stream(SimpleNamespace(db=db), executor, "ticking-run")
            chunks = [first]
            while True:
                try:
                    chunks.append(await asyncio.wait_for(stream.__anext__(), timeout=10))
                except StopAsyncIteration:
                    break
        return chunks, cancel_mock

    chunks, cancel_mock = asyncio.run(_scenario())
    cancel_mock.assert_not_called()

    events = [event for event in (_read_event(chunk) for chunk in chunks) if event]
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "长回答"
    assert not any(event.get("error_code") == "stream_idle_timeout" for event in events)
    assert db.running_writes == []


def test_stopping_one_tab_does_not_cancel_the_other() -> None:
    """两个标签页各有一轮时，停止接口只影响它自己那一轮。"""

    first_run = cancellation.register_run("tab-a")
    second_run = cancellation.register_run("tab-b")
    try:
        request = agent_endpoint.ChatStopRequest(request_id="tab-a")
        response = asyncio.run(agent_endpoint.agent_chat_stop(request))
        assert response.cancelled is True
        assert first_run.is_set() is True
        assert second_run.is_set() is False
        assert cancellation.is_cancelled("tab-b") is False
    finally:
        cancellation.release_run("tab-a")
        cancellation.release_run("tab-b")


def test_domain_guard_persistence_failure_still_emits_done() -> None:
    """守卫分支落库失败时记日志，但事件流仍要给出 accepted 和 done。"""

    from src.schemas.agent_domain_guard import (
        DomainAction,
        DomainGuardDecision,
        DomainGuardMeta,
        DomainReasonCode,
    )

    decision = DomainGuardDecision(
        action=DomainAction.REDIRECT,
        reason_code=DomainReasonCode.OFF_TOPIC,
        message="本系统是投资分析系统，请询问股票或投资相关问题。",
    )
    meta = DomainGuardMeta(
        action=DomainAction.REDIRECT,
        reason_code=DomainReasonCode.OFF_TOPIC,
        policy_version="2",
        elapsed_ms=1,
        source="rule",
    )
    db = _FakeDb({
        "is_running": False,
        "session_generation": 3,
        "session_state_version": 7,
        "selected_skill_ids": ["technical"],
        "active_stock_context": None,
    })
    session_service = SimpleNamespace(db=db)

    async def _scenario():
        request = agent_endpoint.ChatRequest(
            message="今天天气怎么样", session_id="guard-session", request_id="guard-run",
        )
        with patch.object(agent_endpoint, "_select_agent_chat_backend", return_value="litellm"), \
                patch.object(
                    agent_endpoint, "_prepare_chat_request",
                    return_value=(None, None, None, None, None, (decision, meta)),
                ), \
                patch.object(
                    agent_endpoint, "_persist_domain_guard_turn",
                    side_effect=RuntimeError("write failed"),
                ):
            response = await agent_endpoint.agent_chat_stream(
                request, session_service, intake_service=None,
            )
            return [
                json.loads(chunk.removeprefix("data: ").strip())
                async for chunk in response.body_iterator
                if chunk.startswith("data: ")
            ]

    events = asyncio.run(_scenario())
    assert [event["type"] for event in events] == ["accepted", "done"]
    assert events[0]["session_generation"] == 3
    assert events[0]["session_state_version"] == 7
    assert events[0]["session_state"] == {"selected_skill_ids": ["technical"]}
    assert events[1]["content"] == "本系统是投资分析系统，请询问股票或投资相关问题。"
    assert events[1]["domain_guard"]["reason_code"] == "off_topic"
