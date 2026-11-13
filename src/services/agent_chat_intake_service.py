# -*- coding: utf-8 -*-
"""Agent Chat Intake Service - persisted guided questioning flow.

The draft is the only server-side source of truth. A chat request may never
inject its own "profile" text: the brief is rebuilt from the stored,
controlled question bank and consumed once for exactly one research run.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from src.agent.intake_questions import (
    MAX_QUESTIONS, apply_branching, build_analysis_brief,
    invalidate_dependent_answers, select_questions, validate_answers,
)
from src.schemas.agent_intake import AnalysisBrief, Answer, IntakeDraft, IntakeStatus, Question
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_CLIENT_ANSWER_SOURCES = {"user_choice", "unknown"}
_ALL_BRIEF_DIMENSIONS = [
    "object", "goal", "horizon", "position", "risk_focus", "research_focus", "reading_style",
]


class IntakeServiceError(Exception):
    def __init__(self, message: str, code: str = "intake_error"):
        super().__init__(message)
        self.code = code
        self.message = message


class IntakeRevisionConflict(IntakeServiceError):
    def __init__(self, expected: int, actual: Optional[int]):
        super().__init__(f"Revision conflict: expected {expected}, got {actual}",
                         code="intake_revision_conflict")
        self.expected, self.actual = expected, actual


class IntakeNotConfirmed(IntakeServiceError):
    def __init__(self, intake_id: str):
        super().__init__(f"Intake {intake_id} is not confirmed", code="intake_not_confirmed")
        self.intake_id = intake_id


class IntakeMessageMismatch(IntakeServiceError):
    def __init__(self, intake_id: str):
        super().__init__(f"Intake {intake_id} message mismatch", code="intake_message_mismatch")
        self.intake_id = intake_id


class IntakeSessionMismatch(IntakeServiceError):
    def __init__(self, intake_id: str):
        super().__init__(f"Intake {intake_id} does not belong to the current session",
                         code="session_state_conflict")
        self.intake_id = intake_id


class IntakeStaleState(IntakeServiceError):
    def __init__(self, intake_id: str):
        super().__init__(f"Intake {intake_id} was built on a stale session state",
                         code="session_state_conflict")
        self.intake_id = intake_id


class IntakeAlreadyAccepted(IntakeServiceError):
    def __init__(self, intake_id: str):
        super().__init__(f"Intake {intake_id} was already accepted for this request",
                         code="intake_already_accepted")
        self.intake_id = intake_id


def _decode_list(raw: Optional[str], field: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise IntakeServiceError(f"Corrupt {field} payload", code="intake_state_corrupt") from exc
    if not isinstance(value, list):
        raise IntakeServiceError(f"Corrupt {field} payload", code="intake_state_corrupt")
    return value


class AgentChatIntakeService:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @staticmethod
    def _to_draft(row: Optional[Dict[str, Any]]) -> Optional[IntakeDraft]:
        if row is None:
            return None
        questions = [Question.model_validate(q) for q in _decode_list(row["questions_json"], "questions")]
        answers = [Answer.model_validate(a) for a in _decode_list(row["answers_json"], "answers")]
        return IntakeDraft(
            intake_id=row["intake_id"],
            schema_version=int(row["schema_version"] or SCHEMA_VERSION),
            session_id=row["session_id"],
            session_generation=row["session_generation"],
            base_state_version=int(row["base_state_version"] or 0),
            revision=int(row["revision"] or 1),
            original_message=row["original_message"],
            questions=questions, answers=answers,
            status=IntakeStatus(row["status"]),
            created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )

    def _load_draft(self, intake_id: str) -> IntakeDraft:
        draft = self._to_draft(self.db.get_intake_draft(intake_id))
        if draft is None:
            raise IntakeNotConfirmed(intake_id)
        return draft

    @staticmethod
    def _current_state_version(session_id: str, db: DatabaseManager) -> int:
        try:
            return int(db.read_chat_session_snapshot(session_id).get("session_state_version") or 0)
        except Exception:
            logger.warning("Could not read session state version for %s", session_id, exc_info=True)
            return 0

    @staticmethod
    def _sanitize_answers(answers: List[Answer]) -> List[Answer]:
        sanitized: List[Answer] = []
        for answer in answers:
            source = answer.source if answer.source in _CLIENT_ANSWER_SOURCES else "user_choice"
            sanitized.append(Answer(question_id=answer.question_id,
                                    option_ids=list(dict.fromkeys(answer.option_ids)),
                                    source=source))
        return sanitized

    @staticmethod
    def _generic_brief() -> AnalysisBrief:
        return AnalysisBrief(brief_version=1, unknown_fields=list(_ALL_BRIEF_DIMENSIONS))

    def create_intake(
        self, *, message: str, session_id: str, session_generation: str,
        request_id: Optional[str] = None,
        stock_clarification_needed: bool = False, report_follow_up: bool = False,
        intent_hints: Optional[Dict[str, Any]] = None,
    ) -> IntakeDraft:
        message = (message or "").strip()
        if not message:
            raise IntakeServiceError("message is required", code="invalid_intake_answer")
        if len(message) > 4000:
            raise IntakeServiceError("message is too long", code="invalid_intake_answer")
        if not session_id:
            raise IntakeServiceError("session_id is required", code="intake_error")

        # When the client provides a resolved stock code + market, the object
        # dimension is no longer ambiguous — suppress the market question.
        if intent_hints and intent_hints.get("extracted_stock_code") and intent_hints.get("inferred_market"):
            stock_clarification_needed = False

        questions = select_questions(
            message, stock_clarification_needed=stock_clarification_needed,
            report_follow_up=report_follow_up,
            intent_hints=intent_hints,
        )
        if not questions or len(questions) > MAX_QUESTIONS:
            raise IntakeServiceError("question bank produced an invalid questionnaire", code="intake_error")
        generation = session_generation or str(uuid.uuid4())
        base_state_version = self._current_state_version(session_id, self.db)
        row = self.db.create_intake_draft(
            intake_id=f"intake_{uuid.uuid4().hex[:24]}",
            schema_version=SCHEMA_VERSION,
            session_id=session_id, session_generation=generation,
            original_message=message,
            questions_json=json.dumps([q.model_dump(mode="json") for q in questions], ensure_ascii=False),
            base_state_version=base_state_version, request_id=request_id,
        )
        draft = self._to_draft(row)
        assert draft is not None
        logger.info("Created intake %s for session %s with %d questions",
                    draft.intake_id, session_id, len(draft.questions))
        return draft

    def update_intake(
        self, *, intake_id: str, expected_revision: int, answers: List[Answer],
        action: str = "save", session_id: Optional[str] = None,
    ) -> IntakeDraft:
        draft = self._load_draft(intake_id)
        if session_id and draft.session_id != session_id:
            raise IntakeSessionMismatch(intake_id)
        if draft.status in (IntakeStatus.INVALIDATED, IntakeStatus.SKIPPED):
            raise IntakeNotConfirmed(intake_id)
        if draft.revision != expected_revision:
            raise IntakeRevisionConflict(expected_revision, draft.revision)
        sanitized = self._sanitize_answers(answers)
        if len({a.question_id for a in sanitized}) != len(sanitized):
            raise IntakeServiceError("Duplicate answers", code="invalid_intake_answer")
        is_valid, error_msg = validate_answers(draft.questions, sanitized)
        if not is_valid:
            raise IntakeServiceError(error_msg or "Invalid answers", code="invalid_intake_answer")
        previous_map = {a.question_id: a for a in draft.answers}
        submitted_map = {a.question_id: a for a in sanitized}
        changed = {
            qid for qid, submitted in submitted_map.items()
            if qid not in previous_map or previous_map[qid].option_ids != submitted.option_ids
        }
        effective_answers = list(sanitized)
        for qid in changed:
            effective_answers = invalidate_dependent_answers(draft.questions, effective_answers, qid)
        valid_ids = {q.id for q in draft.questions}
        effective_answers = [a for a in effective_answers if a.question_id in valid_ids]
        questions = apply_branching(draft.questions, effective_answers)
        if not questions or len(questions) > MAX_QUESTIONS:
            raise IntakeServiceError("question bank produced an invalid questionnaire", code="intake_error")
        effective_answers = [a for a in effective_answers if a.question_id in {q.id for q in questions}]
        status = {"confirm": IntakeStatus.CONFIRMED.value,
                  "skip": IntakeStatus.SKIPPED.value}.get(action, IntakeStatus.PENDING.value)
        row = self.db.update_intake_draft(
            intake_id=intake_id, expected_revision=expected_revision,
            answers_json=json.dumps([a.model_dump(mode="json") for a in effective_answers], ensure_ascii=False),
            questions_json=json.dumps([q.model_dump(mode="json") for q in questions], ensure_ascii=False),
            status=status,
        )
        if row is None:
            raise IntakeRevisionConflict(expected_revision, None)
        updated = self._to_draft(row)
        assert updated is not None
        return updated

    def get_intake(self, intake_id: str, session_id: Optional[str] = None) -> Optional[IntakeDraft]:
        draft = self._to_draft(self.db.get_intake_draft(intake_id))
        if draft is not None and session_id and draft.session_id != session_id:
            return None
        return draft

    def find_pending_intake(self, session_id: str, session_generation: Optional[str] = None) -> Optional[IntakeDraft]:
        return self._to_draft(self.db.find_pending_intake_draft(session_id, session_generation))

    def preview_brief(self, intake_id: str) -> AnalysisBrief:
        draft = self._load_draft(intake_id)
        return build_analysis_brief(draft.questions, draft.answers)

    def get_confirmed_brief(self, intake_id: str, session_id: Optional[str] = None) -> AnalysisBrief:
        draft = self._load_draft(intake_id)
        if session_id and draft.session_id != session_id:
            raise IntakeSessionMismatch(intake_id)
        if draft.status == IntakeStatus.SKIPPED:
            return self._generic_brief()
        if draft.status != IntakeStatus.CONFIRMED:
            raise IntakeNotConfirmed(intake_id)
        return build_analysis_brief(draft.questions, draft.answers)

    def accept_for_chat(
        self, *, intake_id: str, intake_revision: int, message: str,
        session_id: str, session_generation: Optional[str], request_id: str,
        current_state_version: Optional[int] = None,
    ) -> AnalysisBrief:
        raw = self.db.get_intake_draft(intake_id)
        draft = self._to_draft(raw)
        if draft is None:
            raise IntakeNotConfirmed(intake_id)
        if draft.session_id != session_id:
            raise IntakeSessionMismatch(intake_id)
        if session_generation and draft.session_generation != session_generation:
            raise IntakeSessionMismatch(intake_id)
        current_generation = self.db.read_chat_session_snapshot(session_id).get("session_generation")
        if current_generation and draft.session_generation != current_generation:
            raise IntakeSessionMismatch(intake_id)
        if draft.original_message != (message or ""):
            raise IntakeMessageMismatch(intake_id)
        if raw and raw.get("consumed_request_id") == request_id:
            raise IntakeAlreadyAccepted(intake_id)
        if draft.status not in (IntakeStatus.CONFIRMED, IntakeStatus.SKIPPED):
            raise IntakeNotConfirmed(intake_id)
        if draft.revision != intake_revision:
            raise IntakeRevisionConflict(intake_revision, draft.revision)
        state_version = (self._current_state_version(session_id, self.db)
                         if current_state_version is None else int(current_state_version))
        if state_version != draft.base_state_version:
            raise IntakeStaleState(intake_id)
        brief = (self._generic_brief() if draft.status == IntakeStatus.SKIPPED
                 else build_analysis_brief(draft.questions, draft.answers))
        row = self.db.consume_intake_draft(
            intake_id=intake_id, expected_revision=intake_revision,
            session_generation=draft.session_generation, consumed_request_id=request_id,
        )
        if row is None:
            raise IntakeNotConfirmed(intake_id)
        logger.info("Accepted intake %s for request %s", intake_id, request_id)
        return brief

    @staticmethod
    def build_context_with_brief(base_context: Dict[str, Any], brief: AnalysisBrief) -> Dict[str, Any]:
        context = dict(base_context)
        context.pop("analysis_brief", None)
        context["analysis_brief"] = brief.model_dump(exclude_none=True)
        return context


__all__ = [
    "AgentChatIntakeService", "IntakeServiceError", "IntakeRevisionConflict",
    "IntakeNotConfirmed", "IntakeMessageMismatch", "IntakeSessionMismatch",
    "IntakeStaleState", "IntakeAlreadyAccepted",
]
