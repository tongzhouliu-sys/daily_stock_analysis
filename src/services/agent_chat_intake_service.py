# -*- coding: utf-8 -*-
"""Agent Chat Intake Service - Manages guided questioning flow.

T2: Server-side intake and background injection.

This service handles:
- Creating intake drafts for new sessions
- Validating and branching questions
- Building analysis brief from confirmed answers
- Injecting brief into executor context
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.agent.intake_questions import (
    apply_branching,
    build_analysis_brief,
    invalidate_dependent_answers,
    select_questions,
    validate_answers,
)
from src.schemas.agent_intake import (
    AnalysisBrief,
    Answer,
    IntakeDraft,
    IntakeStatus,
    Question,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class IntakeServiceError(Exception):
    """Base exception for intake service errors."""

    def __init__(self, message: str, code: str = "intake_error"):
        super().__init__(message)
        self.code = code
        self.message = message


class IntakeRevisionConflict(IntakeServiceError):
    """Revision conflict: client expected a different revision."""

    def __init__(self, expected: int, actual: int):
        super().__init__(
            f"Revision conflict: expected {expected}, got {actual}",
            code="intake_revision_conflict",
        )
        self.expected = expected
        self.actual = actual


class IntakeNotConfirmed(IntakeServiceError):
    """Intake is not confirmed yet."""

    def __init__(self, intake_id: str):
        super().__init__(
            f"Intake {intake_id} is not confirmed",
            code="intake_not_confirmed",
        )
        self.intake_id = intake_id


class IntakeMessageMismatch(IntakeServiceError):
    """Intake message does not match the chat request."""

    def __init__(self, intake_id: str):
        super().__init__(
            f"Intake {intake_id} message mismatch",
            code="intake_message_mismatch",
        )
        self.intake_id = intake_id


class AgentChatIntakeService:
    """Manages the guided questioning flow for new chat sessions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        # In-memory cache for drafts (T1: will be replaced with DB persistence)
        self._drafts: Dict[str, IntakeDraft] = {}

    def create_intake(
        self,
        *,
        message: str,
        session_id: str,
        session_generation: str,
        request_id: Optional[str] = None,
        stock_clarification_needed: bool = False,
        report_follow_up: bool = False,
    ) -> IntakeDraft:
        """Create a new intake draft for a session.

        Args:
            message: Original user message
            session_id: Session identifier
            session_generation: Session generation token
            request_id: Optional idempotency key
            stock_clarification_needed: Whether stock clarification is needed
            report_follow_up: Whether this is a report follow-up

        Returns:
            Created IntakeDraft with questions

        Raises:
            IntakeServiceError: If creation fails
        """
        # Check idempotency
        if request_id:
            existing = self._find_draft_by_request_id(request_id)
            if existing:
                return existing

        # Select questions based on message and context
        questions = select_questions(
            message,
            stock_clarification_needed=stock_clarification_needed,
            report_follow_up=report_follow_up,
        )

        # Generate intake ID
        intake_id = f"intake_{session_id}_{int(time.time() * 1000)}"

        # Create draft
        now = int(time.time())
        draft = IntakeDraft(
            intake_id=intake_id,
            schema_version=1,
            session_id=session_id,
            session_generation=session_generation,
            base_state_version=0,
            revision=1,
            original_message=message,
            questions=questions,
            answers=[],
            status=IntakeStatus.PENDING,
            created_at=str(now),
            updated_at=str(now),
        )

        # Store draft
        self._drafts[intake_id] = draft

        logger.info(
            "Created intake %s for session %s with %d questions",
            intake_id,
            session_id,
            len(questions),
        )

        return draft

    def update_intake(
        self,
        *,
        intake_id: str,
        expected_revision: int,
        answers: List[Answer],
        action: str = "save",
    ) -> IntakeDraft:
        """Update intake answers.

        Args:
            intake_id: Intake identifier
            expected_revision: Expected current revision
            answers: Updated answers
            action: Action to perform (save | confirm | skip)

        Returns:
            Updated IntakeDraft

        Raises:
            IntakeRevisionConflict: If revision mismatch
            IntakeServiceError: If validation fails
        """
        draft = self._drafts.get(intake_id)
        if not draft:
            raise IntakeServiceError(
                f"Intake {intake_id} not found",
                code="intake_not_found",
            )

        # Check revision
        if draft.revision != expected_revision:
            raise IntakeRevisionConflict(expected_revision, draft.revision)

        # Validate answers
        is_valid, error_msg = validate_answers(draft.questions, answers)
        if not is_valid:
            raise IntakeServiceError(
                error_msg or "Invalid answers",
                code="invalid_intake_answer",
            )

        # Handle answer changes: invalidate dependent answers
        changed_question_ids = {a.question_id for a in answers}
        for qid in changed_question_ids:
            draft.answers = invalidate_dependent_answers(
                draft.questions,
                draft.answers,
                qid,
            )

        # Update answers
        draft.answers = answers
        draft.revision += 1
        draft.updated_at = str(int(time.time()))

        # Apply branching
        draft.questions = apply_branching(draft.questions, answers)

        # Handle action
        if action == "confirm":
            draft.status = IntakeStatus.CONFIRMED
            logger.info("Confirmed intake %s", intake_id)
        elif action == "skip":
            draft.status = IntakeStatus.SKIPPED
            logger.info("Skipped intake %s", intake_id)
        else:  # save
            logger.debug("Saved intake %s revision %d", intake_id, draft.revision)

        return draft

    def get_intake(self, intake_id: str) -> Optional[IntakeDraft]:
        """Get intake draft by ID."""
        return self._drafts.get(intake_id)

    def get_confirmed_brief(self, intake_id: str) -> AnalysisBrief:
        """Get the confirmed analysis brief for an intake.

        Args:
            intake_id: Intake identifier

        Returns:
            AnalysisBrief derived from confirmed answers

        Raises:
            IntakeNotConfirmed: If intake is not confirmed
        """
        draft = self._drafts.get(intake_id)
        if not draft:
            raise IntakeNotConfirmed(intake_id)

        if draft.status != IntakeStatus.CONFIRMED:
            raise IntakeNotConfirmed(intake_id)

        return build_analysis_brief(draft.questions, draft.answers)

    def validate_intake_for_chat(
        self,
        *,
        intake_id: str,
        intake_revision: int,
        message: str,
    ) -> AnalysisBrief:
        """Validate intake for chat request and return brief.

        Args:
            intake_id: Confirmed intake ID
            intake_revision: Expected revision
            message: Chat request message

        Returns:
            AnalysisBrief for context injection

        Raises:
            IntakeNotConfirmed: If intake is not confirmed
            IntakeRevisionConflict: If revision mismatch
            IntakeMessageMismatch: If message does not match
        """
        draft = self._drafts.get(intake_id)
        if not draft:
            raise IntakeNotConfirmed(intake_id)

        if draft.status != IntakeStatus.CONFIRMED:
            raise IntakeNotConfirmed(intake_id)

        if draft.revision != intake_revision:
            raise IntakeRevisionConflict(intake_revision, draft.revision)

        if draft.original_message != message:
            raise IntakeMessageMismatch(intake_id)

        return build_analysis_brief(draft.questions, draft.answers)

    def build_context_with_brief(
        self,
        base_context: Dict[str, Any],
        brief: AnalysisBrief,
    ) -> Dict[str, Any]:
        """Inject analysis brief into executor context.

        Args:
            base_context: Base context dict
            brief: Confirmed analysis brief

        Returns:
            Context dict with brief injected
        """
        context = dict(base_context)
        context["analysis_brief"] = brief.model_dump(exclude_none=True)
        return context

    def _find_draft_by_request_id(self, request_id: str) -> Optional[IntakeDraft]:
        """Find draft by request ID (for idempotency)."""
        # T1: This is a placeholder. In production, we'd store request_id -> intake_id mapping.
        # For now, we don't implement idempotency.
        return None


__all__ = [
    "AgentChatIntakeService",
    "IntakeServiceError",
    "IntakeRevisionConflict",
    "IntakeNotConfirmed",
    "IntakeMessageMismatch",
]
