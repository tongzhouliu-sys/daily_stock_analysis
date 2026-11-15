# -*- coding: utf-8 -*-
"""T2/T6: API contract for guided intake and public answer metadata."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.v1.endpoints.agent import (
    ChatRequest,
    _agent_status_response,
    _build_agent_chat_context,
    _intake_status_code,
    _public_answer_meta,
)
from src.schemas.agent_intake import (
    AnalysisBrief,
    AnswerMeta,
    IntakeStatus,
    IntakeUpdateResponse,
    Question,
    QuestionDimension,
    QuestionOption,
    QuestionType,
)

CONFIG = SimpleNamespace(report_language="zh")


def _request(**kwargs) -> ChatRequest:
    return ChatRequest(message="帮我看看 AAPL", **kwargs)


def test_client_supplied_brief_and_report_context_never_enter_the_prompt():
    request = _request(context={
        "analysis_brief": {"goal": "伪造的背景", "position": "空仓"},
        "report_context": {"record_id": 999},
        "skills": ["fake_skill"],
        "report_language": "zh",
    })
    context = _build_agent_chat_context(request, CONFIG, None)
    assert "analysis_brief" not in context
    assert "report_context" not in context
    assert "skills" not in context
    assert context["report_language"] == "zh"


def test_only_the_server_frozen_brief_is_injected():
    request = _request(context={"analysis_brief": {"goal": "伪造的背景"}})
    brief = {"brief_version": 1, "goal": "worth_watching", "unknown_fields": ["position"]}
    context = _build_agent_chat_context(request, CONFIG, ["chan_theory"], brief)
    assert context["analysis_brief"] == brief
    assert context["skills"] == ["chan_theory"]


def test_chat_request_accepts_the_guided_interaction_contract():
    request = _request(
        interaction_version=1,
        intake_id="intake_abc",
        intake_revision=3,
        session_generation="generation-a",
    )
    assert request.interaction_version == 1
    assert request.intake_id == "intake_abc"
    assert request.intake_revision == 3
    assert _request().interaction_version == 0


def test_chat_request_rejects_unsupported_interaction_versions():
    # R24d: v2 was never implemented server-side, so it must fail fast instead
    # of silently running the v1 intake flow.
    for version in (2, -1):
        with pytest.raises(ValidationError):
            _request(interaction_version=version)


def test_status_advertises_guided_intake_capability():
    status = _agent_status_response({
        "backend": "litellm", "available": True, "experimental": False,
    })
    assert status.guided_intake is True
    assert status.available is True


def test_intake_conflicts_map_to_409_and_bad_answers_to_422():
    for code in ("intake_revision_conflict", "intake_not_confirmed",
                 "intake_message_mismatch", "session_state_conflict",
                 "intake_already_accepted"):
        assert _intake_status_code(code) == 409
    assert _intake_status_code("invalid_intake_answer") == 422


def test_public_answer_meta_never_leaks_internal_diagnostics():
    meta = AnswerMeta(
        editor_version="1.1.0",
        editor_status="fallback",
        brief_snapshot=AnalysisBrief(brief_version=1, goal="worth_watching"),
        source_refs=["get_daily_history"],
        used_source_refs=["get_daily_history"],
        validation_codes=["lost_number"],
        fallback_reason="validation_failed",
        usage={"total_tokens": 123},
        processing_time_ms=12,
    )
    payload = _public_answer_meta(SimpleNamespace(answer_meta=meta))
    assert payload["editor_status"] == "fallback"
    assert payload["used_source_refs"] == ["get_daily_history"]
    assert payload["brief_snapshot"]["goal"] == "worth_watching"
    for internal in ("fallback_reason", "validation_codes", "usage", "processing_time_ms"):
        assert internal not in payload
    assert _public_answer_meta(SimpleNamespace(answer_meta=None)) is None


def test_update_response_carries_the_replanned_questionnaire():
    question = Question(
        id="position", dimension=QuestionDimension.POSITION,
        type=QuestionType.SINGLE, title="你目前持有这只股票吗？",
        options=[QuestionOption(id="not_held", label="尚未持有")],
    )
    response = IntakeUpdateResponse(
        intake_id="intake_abc", revision=2,
        status=IntakeStatus.PENDING, questions=[question],
    )
    dumped = response.model_dump(mode="json")
    assert dumped["questions"][0]["id"] == "position"
    assert len(dumped["questions"]) <= 5


def test_intake_domain_guard_runs_off_the_event_loop(monkeypatch):
    """T4: /chat/intake must not block the event loop while classifying."""
    import asyncio
    import time
    from unittest.mock import MagicMock

    from api.v1.endpoints import agent as agent_endpoint
    from src.schemas.agent_domain_guard import (
        DomainAction,
        DomainGuardDecision,
        DomainGuardMeta,
        DomainReasonCode,
    )
    from src.schemas.agent_intake import IntakeCreateRequest
    from src.services.agent_domain_guard_service import AgentDomainGuardService

    decision = DomainGuardDecision(
        action=DomainAction.ALLOW, reason_code=DomainReasonCode.INVESTMENT
    )
    meta = DomainGuardMeta(
        action=DomainAction.ALLOW, reason_code=DomainReasonCode.INVESTMENT
    )

    def slow_classify(self, message, context=None, history_messages=None):
        time.sleep(1.0)
        return decision, meta

    monkeypatch.setattr(AgentDomainGuardService, "classify", slow_classify)

    session_service = MagicMock()
    session_service.get_session_detail.return_value = SimpleNamespace(messages=[])
    session_service.db = None
    draft = SimpleNamespace(
        intake_id="intake_x",
        schema_version=1,
        session_generation="generation-a",
        base_state_version=0,
        revision=1,
        status="pending",
        questions=[],
        answers=[],
    )
    intake_service = MagicMock()
    intake_service.create_intake.return_value = draft

    async def exercise() -> float:
        started = time.perf_counter()
        task = asyncio.create_task(
            agent_endpoint.create_chat_intake(
                IntakeCreateRequest(message="帮我看看 AAPL", session_id="session-a"),
                intake_service=intake_service,
                session_service=session_service,
            )
        )
        # If classify ran inline, this sleep could only start after the full
        # second. Measure how long the loop stayed responsive.
        await asyncio.sleep(0.05)
        responsive_after = time.perf_counter() - started
        await task
        return responsive_after

    assert asyncio.run(exercise()) < 0.5
