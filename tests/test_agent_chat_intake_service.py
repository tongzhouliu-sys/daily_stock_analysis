# -*- coding: utf-8 -*-
"""T1/T2: controlled question bank, draft ownership and one-shot acceptance."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from src.agent.intake_questions import MAX_QUESTIONS, select_questions
from src.schemas.agent_intake import Answer, IntakeStatus
from src.services.agent_chat_intake_service import (
    AgentChatIntakeService,
    IntakeAlreadyAccepted,
    IntakeMessageMismatch,
    IntakeNotConfirmed,
    IntakeRevisionConflict,
    IntakeServiceError,
    IntakeSessionMismatch,
    IntakeStaleState,
)

SESSION_ID = "session-a"
GENERATION = "generation-a"


class FakeDB:
    """In-memory projection of the intake_drafts table + session snapshot."""

    def __init__(self, state_version: int = 0, generation: str = GENERATION):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.state_version = state_version
        self.generation = generation

    def read_chat_session_snapshot(self, session_id: str) -> Dict[str, Any]:
        return {"session_generation": self.generation, "session_state_version": self.state_version}

    def create_intake_draft(self, **kwargs) -> Dict[str, Any]:
        request_id = kwargs.get("request_id")
        if request_id:
            for row in self.rows.values():
                if row["request_id"] == request_id:
                    return dict(row)
        row = {
            "intake_id": kwargs["intake_id"],
            "schema_version": kwargs["schema_version"],
            "session_id": kwargs["session_id"],
            "session_generation": kwargs["session_generation"],
            "base_state_version": kwargs["base_state_version"],
            "revision": 1,
            "original_message": kwargs["original_message"],
            "questions_json": kwargs["questions_json"],
            "answers_json": "[]",
            "status": "pending",
            "request_id": request_id,
            "consumed_request_id": None,
            "created_at": None,
            "updated_at": None,
        }
        self.rows[row["intake_id"]] = row
        return dict(row)

    def get_intake_draft(self, intake_id: str) -> Optional[Dict[str, Any]]:
        row = self.rows.get(intake_id)
        return dict(row) if row else None

    def find_pending_intake_draft(self, session_id, session_generation=None):
        for row in reversed(list(self.rows.values())):
            if (row["session_id"] == session_id
                    and row["status"] in ("pending", "confirmed")
                    and row["consumed_request_id"] is None
                    and (not session_generation
                         or row["session_generation"] == session_generation)):
                return dict(row)
        return None

    def update_intake_draft(self, **kwargs):
        row = self.rows.get(kwargs["intake_id"])
        if row is None or row["revision"] != kwargs["expected_revision"]:
            return None
        row.update({
            "revision": kwargs["expected_revision"] + 1,
            "answers_json": kwargs["answers_json"],
            "questions_json": kwargs["questions_json"],
            "status": kwargs["status"],
        })
        return dict(row)

    def consume_intake_draft(self, **kwargs):
        row = self.rows.get(kwargs["intake_id"])
        if row is None or row["revision"] != kwargs["expected_revision"]:
            return None
        row["consumed_request_id"] = kwargs["consumed_request_id"]
        return dict(row)


def _service(**kwargs) -> AgentChatIntakeService:
    return AgentChatIntakeService(db_manager=FakeDB(**kwargs))


def _create(service, message="帮我看看 AAPL", **kwargs) -> str:
    params = dict(
        message=message, session_id=SESSION_ID,
        session_generation=GENERATION, request_id="request-1",
    )
    params.update(kwargs)
    return service.create_intake(**params).intake_id


def _answer(question_id: str, *option_ids: str, source: str = "user_choice") -> Answer:
    return Answer(question_id=question_id, option_ids=list(option_ids), source=source)


# ── Question bank ────────────────────────────────────────────────────────────

def test_concept_question_only_asks_about_reading_style():
    questions = select_questions("什么是市盈率？")
    assert 1 <= len(questions) <= MAX_QUESTIONS
    assert [q.dimension.value for q in questions] == ["reading_style"]


def test_complete_question_shows_one_confirmation():
    questions = select_questions("没有持仓，研究 AAPL 未来半年，优先控制风险")
    assert len(questions) == 1 and questions[0].id == "confirm"


def test_object_ambiguity_is_merged_into_the_same_questionnaire():
    questions = select_questions("帮我看看中芯国际", stock_clarification_needed=True)
    assert len(questions) <= MAX_QUESTIONS
    assert questions[0].id == "object"
    assert len({q.id for q in questions}) == len(questions)


def test_report_follow_up_never_expands_into_a_full_questionnaire():
    assert 1 <= len(select_questions("这份报告提到的持仓还能继续吗", report_follow_up=True)) <= 2


# ── Validation / ownership ──────────────────────────────────────────────────

def test_invalid_enum_and_forged_source_are_rejected_or_sanitized():
    service = _service()
    intake_id = _create(service)
    draft = service.get_intake(intake_id)
    question = draft.questions[0]

    with pytest.raises(IntakeServiceError) as bad_option:
        service.update_intake(
            intake_id=intake_id, expected_revision=draft.revision,
            answers=[_answer(question.id, "not_an_option")], action="save",
        )
    assert bad_option.value.code == "invalid_intake_answer"

    service.update_intake(
        intake_id=intake_id, expected_revision=draft.revision,
        answers=[_answer(question.id, question.options[0].id, source="explicit_message")],
        action="save",
    )
    assert service.get_intake(intake_id).answers[0].source != "explicit_message"


def test_branch_change_removes_answers_in_the_invalidated_branch():
    service = _service()
    intake_id = _create(service)
    draft = service.update_intake(
        intake_id=intake_id, expected_revision=1,
        answers=[_answer("goal", "evaluate_holding")], action="save",
    )
    assert "position" in {q.id for q in draft.questions}

    with_position = service.update_intake(
        intake_id=intake_id, expected_revision=draft.revision,
        answers=[_answer("goal", "evaluate_holding"), _answer("position", "not_held")],
        action="save",
    )
    assert any(a.question_id == "position" for a in with_position.answers)

    replanned = service.update_intake(
        intake_id=intake_id, expected_revision=with_position.revision,
        answers=[_answer("goal", "worth_watching"), _answer("position", "not_held")],
        action="save",
    )
    assert "position" not in {q.id for q in replanned.questions}
    assert all(a.question_id != "position" for a in replanned.answers)


def test_revision_cas_conflict():
    service = _service()
    intake_id = _create(service)
    with pytest.raises(IntakeRevisionConflict):
        service.update_intake(
            intake_id=intake_id, expected_revision=99, answers=[], action="save",
        )


def test_preview_never_consumes_and_keeps_unknown_dimensions():
    service = _service()
    intake_id = _create(service)
    draft = service.update_intake(
        intake_id=intake_id, expected_revision=1,
        answers=[_answer("goal", "worth_watching")], action="save",
    )
    preview = service.preview_brief(intake_id)
    assert preview.goal == "worth_watching"
    assert "horizon" in preview.unknown_fields
    assert preview.position is None
    restored = service.find_pending_intake(SESSION_ID, GENERATION)
    assert restored.status == IntakeStatus.PENDING
    assert restored.revision == draft.revision


def test_skipped_draft_freezes_an_all_unknown_brief():
    service = _service()
    intake_id = _create(service)
    skipped = service.update_intake(
        intake_id=intake_id, expected_revision=1, answers=[], action="skip",
    )
    assert skipped.status == IntakeStatus.SKIPPED
    brief = service.get_confirmed_brief(intake_id)
    assert brief.object_scope is None and brief.position is None
    assert set(brief.unknown_fields) >= {"object", "goal", "horizon", "position", "risk_focus"}


# ── One-shot acceptance ──────────────────────────────────────────────────────

def _confirmed(service) -> str:
    intake_id = _create(service)
    service.update_intake(
        intake_id=intake_id, expected_revision=1,
        answers=[_answer("goal", "worth_watching")], action="confirm",
    )
    return intake_id


def _base(intake_id: str) -> dict:
    return dict(
        intake_id=intake_id, intake_revision=2, message="帮我看看 AAPL",
        session_id=SESSION_ID, session_generation=GENERATION, request_id="run-1",
    )


def test_accept_requires_confirmation_message_session_and_revision():
    service = _service()
    base = _base(_confirmed(service))
    with pytest.raises(IntakeMessageMismatch):
        service.accept_for_chat(**{**base, "message": "改成别的问题"})
    with pytest.raises(IntakeSessionMismatch):
        service.accept_for_chat(**{**base, "session_id": "another-session"})
    with pytest.raises(IntakeRevisionConflict):
        service.accept_for_chat(**{**base, "intake_revision": 1})


def test_accept_replay_is_rejected():
    service = _service()
    intake_id = _confirmed(service)
    base = _base(intake_id)
    brief = service.accept_for_chat(**base)
    assert brief.goal == "worth_watching"
    with pytest.raises(IntakeAlreadyAccepted):
        service.accept_for_chat(**base)
    assert service.db.get_intake_draft(intake_id)["consumed_request_id"] == "run-1"


def test_stale_session_state_rejects_the_draft():
    service = _service()
    intake_id = _confirmed(service)
    service.db.state_version = 7
    with pytest.raises(IntakeStaleState):
        service.accept_for_chat(**{**_base(intake_id), "request_id": "run-2"})


def test_draft_from_a_previous_generation_is_never_replayed():
    service = _service(generation="generation-b")
    with pytest.raises(IntakeSessionMismatch):
        service.accept_for_chat(**{**_base(_confirmed(service)), "request_id": "run-3"})


def test_unconfirmed_draft_cannot_start_research():
    service = _service()
    with pytest.raises(IntakeNotConfirmed):
        service.accept_for_chat(
            intake_id=_create(service), intake_revision=1, message="帮我看看 AAPL",
            session_id=SESSION_ID, session_generation=GENERATION, request_id="run-4",
        )


def test_session_scoped_lookup_never_exposes_another_sessions_draft():
    service = _service()
    intake_id = _create(service)
    assert service.get_intake(intake_id, session_id=SESSION_ID) is not None
    assert service.get_intake(intake_id, session_id="other-session") is None
    assert service.find_pending_intake(SESSION_ID, "another-generation") is None
