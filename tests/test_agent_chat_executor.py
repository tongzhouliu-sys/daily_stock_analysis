# -*- coding: utf-8 -*-
"""Backend-neutral Chat preparation and persistence boundary tests."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from src.storage import ConversationSessionState, DatabaseManager, utc_naive_now


@pytest.fixture(autouse=True)
def isolated_chat_database(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'chat.db'}")
    yield db
    DatabaseManager.reset_instance()

from src.agent.agent_backend import AgentRunResult
from src.agent.chat_executor import AgentChatExecutor, PreparedAgentChatTurn
from src.agent.executor import PreparedAgentChat


class _Backend:
    def __init__(self, *, runtime_owns_loop: bool) -> None:
        self.runtime_owns_loop = runtime_owns_loop
        self.backend_id = "litellm"
        self.request = None

    def run(self, request):
        self.request = request
        return AgentRunResult(
            success=True,
            final_answer="answer",
            backend="litellm",
            model="model",
            messages=[{"role": "assistant", "content": "answer"}],
            total_steps=1,
        )


def _failure_backend(
    *,
    runtime_owns_loop: bool,
    error_code: str,
    error_message: str,
) -> _Backend:
    backend = _Backend(runtime_owns_loop=runtime_owns_loop)
    backend.run = lambda _request: AgentRunResult(
        success=False,
        backend="litellm",
        error_code=error_code,
        error_message=error_message,
        total_steps=1,
    )
    return backend


def _executor(backend: _Backend) -> AgentChatExecutor:
    return AgentChatExecutor(
        backend=backend,
        config=SimpleNamespace(),
        context_llm_adapter=object(),
        max_steps=7,
        timeout_seconds=45,
    )


def test_dsa_owned_backend_keeps_provider_trace_roundtrip() -> None:
    backend = _Backend(runtime_owns_loop=False)
    prepared = PreparedAgentChat(
        system_prompt="system",
        history_messages=[{"role": "assistant", "content": "provider trace"}],
        stock_scope=None,
    )
    with patch("src.agent.chat_executor.prepare_agent_chat", return_value=prepared) as prepare, \
         patch("src.agent.chat_executor.conversation_manager.get_or_create"), \
         patch("src.agent.chat_executor.conversation_manager.commit_user_turn", return_value={"user_message_id": 11}), \
         patch("src.agent.chat_executor.conversation_manager.add_message", return_value=12), \
         patch("src.agent.chat_executor.persist_provider_trace_turns") as persist_trace:
        result = _executor(backend).chat("question", "session")

    assert result.backend == "litellm"
    assert prepare.call_args.kwargs["include_provider_trace"] is True
    assert prepare.call_args.kwargs["strict_initial_stock_scope"] is False
    persist_trace.assert_called_once()
    assert persist_trace.call_args.kwargs["baseline_len"] == 3
    assert persist_trace.call_args.kwargs["user_message_id"] == 11
    assert persist_trace.call_args.kwargs["assistant_message_id"] == 12


def test_timed_out_litellm_turn_keeps_existing_analysis_failure_note() -> None:
    backend = _failure_backend(
        runtime_owns_loop=False,
        error_code="timeout",
        error_message="Agent execution timed out after 45 seconds",
    )
    prepared = PreparedAgentChat(
        system_prompt="system",
        history_messages=[],
        stock_scope=None,
    )
    with patch("src.agent.chat_executor.prepare_agent_chat", return_value=prepared), \
         patch("src.agent.chat_executor.conversation_manager.get_or_create"), \
         patch("src.agent.chat_executor.conversation_manager.commit_user_turn", return_value={"user_message_id": 1}), \
         patch("src.agent.chat_executor.conversation_manager.add_message", return_value=2) as add_message:
        result = _executor(backend).chat("question", "session")

    assert result.error_code == "timeout"
    assert add_message.call_args_list[-1].args == (
        "session",
        "assistant",
        "[分析失败] Agent execution timed out after 45 seconds",
    )


def test_failed_litellm_turn_keeps_existing_analysis_failure_note() -> None:
    backend = _failure_backend(
        runtime_owns_loop=False,
        error_code="unknown_backend_error",
        error_message="provider failed",
    )
    prepared = PreparedAgentChat(
        system_prompt="system",
        history_messages=[],
        stock_scope=None,
    )
    with patch("src.agent.chat_executor.prepare_agent_chat", return_value=prepared), \
         patch("src.agent.chat_executor.conversation_manager.get_or_create"), \
         patch("src.agent.chat_executor.conversation_manager.commit_user_turn", return_value={"user_message_id": 1}), \
         patch("src.agent.chat_executor.conversation_manager.add_message", return_value=2) as add_message:
        result = _executor(backend).chat("question", "session")

    assert result.error_code == "unknown_backend_error"
    assert add_message.call_args_list[-1].args == (
        "session",
        "assistant",
        "[分析失败] provider failed",
    )


def test_context_preparation_failure_does_not_persist_or_start_backend() -> None:
    backend = _Backend(runtime_owns_loop=False)
    with patch(
        "src.agent.chat_executor.prepare_agent_chat",
        side_effect=RuntimeError("context preparation failed"),
    ), patch("src.agent.chat_executor.conversation_manager.get_or_create"), patch(
        "src.agent.chat_executor.conversation_manager.commit_user_turn"
    ) as add_user_message:
        try:
            _executor(backend).prepare_turn(message="question", session_id="session")
        except RuntimeError as exc:
            assert str(exc) == "context preparation failed"
        else:
            raise AssertionError("context preparation failure must propagate")

    add_user_message.assert_not_called()
    assert backend.request is None


def test_user_message_persistence_failure_does_not_start_backend() -> None:
    backend = _Backend(runtime_owns_loop=False)
    prepared = PreparedAgentChat(
        system_prompt="system",
        history_messages=[],
        stock_scope=None,
    )
    with patch("src.agent.chat_executor.prepare_agent_chat", return_value=prepared), patch(
        "src.agent.chat_executor.conversation_manager.get_or_create"
    ), patch(
        "src.agent.chat_executor.conversation_manager.commit_user_turn",
        side_effect=RuntimeError("database write failed"),
    ):
        try:
            _executor(backend).prepare_turn(message="question", session_id="session")
        except RuntimeError as exc:
            assert str(exc) == "database write failed"
        else:
            raise AssertionError("database failure must propagate")

    assert backend.request is None


def test_running_state_is_visible_during_execution_and_cleared_after_result(
    isolated_chat_database,
) -> None:
    """Refresh recovery relies on the real persisted running snapshot."""
    db = isolated_chat_database
    session_id = "running-session"
    db.ensure_chat_session_generation(session_id)
    accepted = db.commit_chat_user_turn(
        db.read_chat_session_snapshot(session_id), "question",
    )
    observed: dict = {}

    class ProgressBackend(_Backend):
        def run(self, request):
            observed["during"] = db.read_chat_session_snapshot(session_id)
            request.progress_callback({"type": "tool_start", "tool": "get_stock_info"})
            observed["progress"] = db.read_chat_session_snapshot(session_id)[
                "running_progress_steps"
            ]
            return AgentRunResult(
                success=True,
                final_answer="answer",
                backend="litellm",
                model="model",
                messages=[{"role": "assistant", "content": "answer"}],
                total_steps=1,
            )

    turn = PreparedAgentChatTurn(
        message="question",
        session_id=session_id,
        prepared=PreparedAgentChat(
            system_prompt="system", history_messages=[], stock_scope=None,
        ),
        baseline_len=0,
        run_id="run",
        user_message_id=accepted["user_message_id"],
        accepted_snapshot=accepted,
        db_manager=db,
    )

    def enrich(event):
        event["display_name"] = "获取股票基本面"

    result = _executor(ProgressBackend(runtime_owns_loop=False)).execute_turn(
        turn, progress_callback=enrich,
    )

    assert result.success is True
    assert observed["during"]["is_running"] is True
    assert observed["progress"][-1]["display_name"] == "获取股票基本面"
    after = db.read_chat_session_snapshot(session_id)
    assert after["is_running"] is False
    assert after["running_progress_steps"] == []
    assert [message["content"] for message in after["messages"]] == ["question", "answer"]


def test_old_turn_cannot_clear_or_update_newer_running_turn(
    isolated_chat_database,
) -> None:
    db = isolated_chat_database
    session_id = "turn-ownership-session"
    db.ensure_chat_session_generation(session_id)
    first = db.commit_chat_user_turn(db.read_chat_session_snapshot(session_id), "first")
    assert db.set_session_running(
        session_id,
        True,
        [],
        turn_id="turn-1",
        turn_anchor=first["user_message_id"],
    ) is True

    second = db.commit_chat_user_turn(db.read_chat_session_snapshot(session_id), "second")
    assert db.set_session_running(
        session_id,
        True,
        [],
        turn_id="turn-2",
        turn_anchor=second["user_message_id"],
    ) is True

    assert db.update_session_running_progress(
        session_id,
        [{"type": "thinking", "message": "late"}],
        turn_id="turn-1",
    ) is False
    assert db.set_session_running(session_id, False, turn_id="turn-1") is False

    snapshot = db.read_chat_session_snapshot(session_id)
    assert snapshot["is_running"] is True
    assert snapshot["running_turn_id"] == "turn-2"
    assert snapshot["running_turn_anchor"] == second["user_message_id"]
    assert snapshot["running_heartbeat_at"] is not None

    assert db.set_session_running(session_id, False, turn_id="turn-2") is True
    snapshot = db.read_chat_session_snapshot(session_id)
    assert snapshot["is_running"] is False
    assert snapshot["running_turn_id"] is None
    assert snapshot["running_heartbeat_at"] is None


def test_clear_stale_chat_running_states_only_clears_expired_turns(
    isolated_chat_database,
    monkeypatch,
) -> None:
    from src import storage

    db = isolated_chat_database
    process_started_at = utc_naive_now()
    monkeypatch.setattr(storage, "_PROCESS_STARTED_AT", process_started_at)

    def start_session(session_id: str) -> None:
        db.ensure_chat_session_generation(session_id)
        accepted = db.commit_chat_user_turn(db.read_chat_session_snapshot(session_id), "question")
        assert db.set_session_running(
            session_id,
            True,
            [],
            turn_id=f"turn-{session_id}",
            turn_anchor=accepted["user_message_id"],
        ) is True

    start_session("stale-session")
    start_session("fresh-heartbeat-session")
    start_session("newer-than-process-session")

    with db.get_session() as session:
        stale = session.get(ConversationSessionState, "stale-session")
        stale.running_started_at = process_started_at - timedelta(minutes=10)
        stale.running_heartbeat_at = process_started_at - timedelta(minutes=2)

        fresh = session.get(ConversationSessionState, "fresh-heartbeat-session")
        fresh.running_started_at = process_started_at - timedelta(hours=1)
        fresh.running_heartbeat_at = utc_naive_now()

        newer = session.get(ConversationSessionState, "newer-than-process-session")
        newer.running_started_at = process_started_at + timedelta(seconds=1)
        newer.running_heartbeat_at = process_started_at - timedelta(hours=1)
        session.commit()

    assert db.clear_stale_chat_running_states() == 1

    assert db.read_chat_session_snapshot("stale-session")["is_running"] is False
    assert db.read_chat_session_snapshot("stale-session")["running_turn_id"] is None
    assert db.read_chat_session_snapshot("fresh-heartbeat-session")["is_running"] is True
    assert db.read_chat_session_snapshot("newer-than-process-session")["is_running"] is True


def test_chat_executor_module_can_be_imported() -> None:
    """Verify chat_executor.py has no SyntaxError and can be imported."""
    # This test ensures the asyncio.run() fix for the answer editor
    # doesn't introduce syntax errors in the synchronous execute_turn method.
    from src.agent.chat_executor import AgentChatExecutor, PreparedAgentChatTurn
    assert AgentChatExecutor is not None
    assert PreparedAgentChatTurn is not None


def test_execute_turn_can_call_async_answer_editor() -> None:
    """Verify execute_turn can call async answer editor via asyncio.run()."""
    from unittest.mock import MagicMock
    from src.agent.chat_executor import AgentChatExecutor, PreparedAgentChatTurn
    from src.agent.executor import PreparedAgentChat
    from src.schemas.agent_intake import AnalysisBrief
    
    backend = _Backend(runtime_owns_loop=False)
    prepared = PreparedAgentChat(
        system_prompt="system",
        history_messages=[],
        stock_scope=None,
        analysis_brief=AnalysisBrief(
            interaction_version=1,
            intake_id="test-intake",
            stock_code="600519",
            stock_name="贵州茅台",
            analysis_target="趋势分析",
            time_frame="中期",
            position_status="未持仓",
            risk_focus=["波动率"],
        ),
    )
    
    turn = PreparedAgentChatTurn(
        message="分析茅台",
        session_id="test-session",
        prepared=prepared,
        baseline_len=0,
        run_id="test-run",
        user_message_id=1,
        accepted_snapshot={},
        db_manager=MagicMock(),
    )
    
    # Mock the answer editor to return formatted content
    mock_editor_instance = MagicMock()
    mock_editor_instance.format_response = MagicMock(
        return_value=("formatted answer", {"version": "1.0.0"})
    )
    
    with patch("src.agent.chat_executor.prepare_agent_chat", return_value=prepared), \
         patch("src.agent.chat_executor.conversation_manager.get_or_create"), \
         patch("src.agent.chat_executor.conversation_manager.commit_user_turn", return_value={"user_message_id": 1}), \
         patch("src.agent.chat_executor.conversation_manager.add_message", return_value=2), \
         patch("src.agent.chat_executor.StockAnswerEditorService", return_value=mock_editor_instance):
        result = _executor(backend).execute_turn(turn)
    
    # Verify the answer editor was called and the result was updated
    assert result.success is True
    assert result.content == "formatted answer"
    assert mock_editor_instance.format_response.called
