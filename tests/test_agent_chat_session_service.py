# -*- coding: utf-8 -*-
"""Agent Chat session service tests."""

from types import SimpleNamespace
from unittest.mock import patch

from src.services.agent_chat_session_service import AgentChatSessionService
from src.storage import DatabaseManager


def test_skill_selection_distinguishes_inherit_clear_and_explicit() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)
    config = SimpleNamespace()

    new_selection = service.resolve_skill_selection(config, "new-session", None)
    assert new_selection.effective_skill_ids is None
    assert new_selection.selected_skill_ids_update is None

    db.save_conversation_user_turn(
        "saved-session",
        "question",
        ["technical", "risk"],
    )
    inherited = service.resolve_skill_selection(config, "saved-session", None)
    assert inherited.effective_skill_ids == ["technical", "risk"]
    assert inherited.selected_skill_ids_update is None

    cleared = service.resolve_skill_selection(config, "saved-session", [])
    assert cleared.effective_skill_ids == []
    assert cleared.selected_skill_ids_update == []

    with patch(
        "src.services.agent_chat_session_service.normalize_requested_skill_ids",
        return_value=["technical"],
    ) as normalize:
        explicit = service.resolve_skill_selection(
            config,
            "saved-session",
            [" technical ", "technical", "unknown"],
        )

    assert explicit.effective_skill_ids == ["technical"]
    assert explicit.selected_skill_ids_update == ["technical"]
    normalize.assert_called_once_with(
        config,
        [" technical ", "technical", "unknown"],
    )


def test_all_invalid_nonempty_selection_inherits_without_clearing_state() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)
    config = SimpleNamespace()
    db.save_conversation_user_turn(
        "saved-session",
        "first question",
        ["technical"],
    )

    with patch(
        "src.services.agent_chat_session_service.normalize_requested_skill_ids",
        return_value=[],
    ):
        inherited = service.resolve_skill_selection(
            config,
            "saved-session",
            ["old_technical"],
        )

    assert inherited.effective_skill_ids == ["technical"]
    assert inherited.selected_skill_ids_update is None

    db.save_conversation_user_turn(
        "saved-session",
        "follow-up",
        inherited.selected_skill_ids_update,
    )
    assert db.get_conversation_session_selected_skill_ids("saved-session") == [
        "technical"
    ]


def test_all_invalid_nonempty_selection_uses_implicit_default_without_state() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)

    with patch(
        "src.services.agent_chat_session_service.normalize_requested_skill_ids",
        return_value=[],
    ):
        inherited = service.resolve_skill_selection(
            SimpleNamespace(),
            "new-session",
            ["unknown"],
        )

    assert inherited.effective_skill_ids is None
    assert inherited.selected_skill_ids_update is None
    assert db.get_conversation_session_selected_skill_ids("new-session") is None


def test_session_detail_preserves_missing_persisted_state() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)
    db.save_conversation_message("legacy-session", "user", "legacy question")

    detail = service.get_session_detail(
        "legacy-session",
        limit=100,
    )

    assert [message["content"] for message in detail.messages] == ["legacy question"]
    assert detail.selected_skill_ids is None
    assert db.get_conversation_session_selected_skill_ids("legacy-session") is None


def test_session_detail_exposes_running_turn_ownership_and_heartbeat() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)
    session_id = "running-detail-session"
    db.ensure_chat_session_generation(session_id)
    accepted = db.commit_chat_user_turn(db.read_chat_session_snapshot(session_id), "question")
    assert db.set_session_running(
        session_id,
        True,
        [{"type": "thinking", "message": "working"}],
        turn_id="turn-1",
        turn_anchor=accepted["user_message_id"],
    ) is True

    detail = service.get_session_detail(session_id, limit=100)

    assert detail.is_running is True
    assert detail.running_turn_id == "turn-1"
    assert detail.running_turn_anchor == accepted["user_message_id"]
    assert detail.running_started_at is not None
    assert detail.running_heartbeat_at is not None
    assert detail.running_progress_steps[-1]["message"] == "working"
