# -*- coding: utf-8 -*-
"""Real SQLite integration for guided intake persistence and one-shot acceptance."""

from __future__ import annotations

import pytest

from src.agent.intake_questions import (
    MAX_QUESTIONS, build_analysis_brief, describe_analysis_brief, select_questions,
)
from src.schemas.agent_intake import Answer, IntakeStatus
from src.services.agent_chat_intake_service import AgentChatIntakeService, IntakeAlreadyAccepted
from src.storage import DatabaseManager


@pytest.fixture()
def db(tmp_path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'intake.db'}")
    yield manager
    DatabaseManager.reset_instance()


@pytest.fixture()
def service(db):
    return AgentChatIntakeService(db)


def _create(service, message="帮我看看 AAPL", request_id="req-1"):
    return service.create_intake(
        message=message, session_id="session-1",
        session_generation="generation-1", request_id=request_id,
    )


def test_question_bank_and_unknown_projection(service):
    assert 1 <= len(select_questions("帮我看看 AAPL")) <= MAX_QUESTIONS
    assert select_questions("什么是市盈率？")[0].dimension.value == "reading_style"
    questions = select_questions("帮我看看 AAPL")
    brief = build_analysis_brief(questions, [
        Answer(question_id="goal", option_ids=["unsure"], source="unknown"),
    ])
    assert brief.goal is None and brief.position is None
    assert {"goal", "horizon", "risk_focus"} <= set(brief.unknown_fields)
    assert "未提供" in describe_analysis_brief(brief, "zh")
    assert "Not provided" in describe_analysis_brief(brief, "en")


def test_create_persists_draft_without_a_chat_message(service):
    draft = _create(service)
    assert draft.status == IntakeStatus.PENDING
    assert 1 <= len(draft.questions) <= MAX_QUESTIONS
    assert service.find_pending_intake("session-1") is not None


def test_confirmed_draft_is_consumed_exactly_once(service):
    draft = _create(service)
    confirmed = service.update_intake(
        intake_id=draft.intake_id, expected_revision=draft.revision,
        answers=[], action="confirm", session_id="session-1",
    )
    brief = service.accept_for_chat(
        intake_id=draft.intake_id, intake_revision=confirmed.revision,
        message="帮我看看 AAPL", session_id="session-1",
        session_generation=None, request_id="accept-1",
    )
    assert brief.brief_version == 1
    with pytest.raises(IntakeAlreadyAccepted):
        service.accept_for_chat(
            intake_id=draft.intake_id, intake_revision=confirmed.revision,
            message="帮我看看 AAPL", session_id="session-1",
            session_generation=None, request_id="accept-1",
        )


def test_generic_skip_keeps_every_dimension_unknown(service):
    draft = _create(service)
    skipped = service.update_intake(
        intake_id=draft.intake_id, expected_revision=draft.revision,
        answers=[], action="skip", session_id="session-1",
    )
    assert skipped.status == IntakeStatus.SKIPPED
    brief = service.get_confirmed_brief(draft.intake_id, session_id="session-1")
    assert set(brief.unknown_fields) >= {"goal", "horizon", "position", "risk_focus"}
