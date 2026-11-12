# -*- coding: utf-8 -*-
"""
Agent API endpoints.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from api.deps import get_agent_chat_intake_service, get_agent_chat_session_service
from api.v1.schemas.system_config import AgentBackendStatusResponse
from src.config import get_config
from src.schemas.agent_domain_guard import DomainAction, DomainGuardMeta
from src.services.agent_chat_session_service import AgentChatSessionService
from src.services.agent_domain_guard_service import AgentDomainGuardService
from src.services.agent_model_service import list_agent_model_deployments
from src.services.agent_report_context_service import AgentReportContextError
from src.services.agent_chat_intake_service import (
    AgentChatIntakeService, IntakeNotConfirmed, IntakeServiceError,
)
from src.schemas.agent_intake import (
    IntakeCreateRequest, IntakeCreateResponse, IntakeStatus,
    IntakeUpdateRequest, IntakeUpdateResponse,
)
from src.storage import ChatSessionStateConflict

# Tool name -> Chinese display name mapping
TOOL_DISPLAY_NAMES: Dict[str, str] = {
    "get_realtime_quote":         "获取实时行情",
    "get_daily_history":          "获取历史K线",
    "get_chip_distribution":      "分析筹码分布",
    "get_analysis_context":       "获取分析上下文",
    "get_stock_info":             "获取股票基本面",
    "search_stock_news":          "搜索股票新闻",
    "search_comprehensive_intel": "搜索综合情报",
    "analyze_trend":              "分析技术趋势",
    "calculate_ma":               "计算均线系统",
    "get_volume_analysis":        "分析量能变化",
    "analyze_pattern":            "识别K线形态",
    "get_market_indices":         "获取市场指数",
    "get_sector_rankings":        "分析行业板块",
    "get_skill_backtest_summary": "获取技能回测概览",
    "get_strategy_backtest_summary": "获取策略回测概览",
    "get_stock_backtest_summary": "获取个股回测数据",
}

logger = logging.getLogger(__name__)

router = APIRouter()



class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: Optional[str] = None
    session_generation: Optional[str] = Field(default=None, min_length=1, max_length=36)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    skills: Optional[List[str]] = Field(
        default=None,
        validation_alias=AliasChoices("skills", "strategies"),
    )
    context: Optional[Dict[str, Any]] = None  # Previous analysis context for data reuse
    # T2: Intake flow integration
    interaction_version: int = Field(
        default=0,
        description="Interaction version (1 = intake flow)",
    )
    intake_id: Optional[str] = Field(
        default=None,
        description="Confirmed intake ID",
    )
    intake_revision: Optional[int] = Field(
        default=None,
        description="Confirmed intake revision",
    )
    # T5: Streaming content output
    stream_content: Optional[bool] = Field(
        default=False,
        description="Enable streaming content deltas (requires server-side AGENT_CHAT_STREAM_CONTENT=true)",
    )

    @property
    def effective_skills(self) -> Optional[List[str]]:
        """Return skill ids from the unified request shape."""
        return self.skills


_INTAKE_CONFLICT_CODES = frozenset({
    "intake_revision_conflict", "intake_not_confirmed", "intake_message_mismatch",
    "session_state_conflict", "intake_already_accepted",
})
_EXPLICIT_TICKER_RE = re.compile(r"(?<!\w)(?:[A-Z]{1,5}|\d{6}|HK\d{4,5})(?!\w)")
_CONCEPT_QUESTION_RE = re.compile(r"什么是|什么叫|是什么意思|如何理解|怎么理解|what\s+is|explain", re.I)


def _intake_status_code(code: str) -> int:
    return 409 if code in _INTAKE_CONFLICT_CODES else 422


def _public_answer_meta(result) -> Optional[Dict[str, Any]]:
    """Whitelist metadata shared by done, non-stream and history views."""
    meta = getattr(result, "answer_meta", None)
    if meta is None:
        return None
    payload = meta.model_dump(mode="json", exclude_none=True) if hasattr(meta, "model_dump") else dict(meta)
    keys = ("schema_version", "editor_version", "editor_status",
            "brief_snapshot", "source_refs", "used_source_refs")
    return {key: payload[key] for key in keys if key in payload}


def _needs_object_intake(message: str) -> bool:
    text = (message or "").strip()
    if not text or _CONCEPT_QUESTION_RE.search(text) or _EXPLICIT_TICKER_RE.search(text):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]{2,}", text))


def _reuse_session_brief(session_service, session_id: str) -> Optional[Dict[str, Any]]:
    """Guided-flow follow-ups keep the brief frozen on a previous answer."""
    try:
        from src.schemas.agent_intake import AnalysisBrief
        detail = session_service.get_session_detail(session_id, limit=-1)
        for message in reversed(detail.messages):
            if message.get("role") != "assistant":
                continue
            meta = message.get("answer_meta")
            snapshot = meta.get("brief_snapshot") if isinstance(meta, dict) else None
            if isinstance(snapshot, dict) and snapshot:
                return AnalysisBrief.model_validate(snapshot).model_dump(exclude_none=True)
    except Exception:
        logger.debug("Could not reuse a confirmed brief for %s", session_id, exc_info=True)
    return None


def _build_agent_chat_context(
    request: ChatRequest, config, skills: Optional[List[str]],
    brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Only the server-consumed brief may enter the prompt."""
    context = dict(request.context or {})
    context.pop("skills", None)
    context.pop("strategies", None)
    context.pop("report_context", None)
    context.pop("analysis_brief", None)
    if skills is not None:
        context["skills"] = skills
    report_language = context.get("report_language")
    if report_language is None or (isinstance(report_language, str) and not report_language.strip()):
        context["report_language"] = config.report_language
    if brief is not None:
        context["analysis_brief"] = brief
    return context


class ActiveStockContextResponse(BaseModel):
    stock_code: str
    stock_name: Optional[str]
    canonical_id: str
    asset_type: str


class ActiveReportContextResponse(BaseModel):
    record_id: int
    status: str
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    created_at: Optional[str] = None


class SessionStateResponse(BaseModel):
    selected_skill_ids: Optional[List[str]]


class ChatResponse(BaseModel):
    success: bool
    content: str
    session_id: str
    error: Optional[str] = None
    active_stock_context: Optional[ActiveStockContextResponse]
    active_report_context: Optional[ActiveReportContextResponse] = None
    session_state_version: int
    session_generation: str
    session_state: SessionStateResponse
    clarification: Optional[Dict[str, Any]] = None
    # Domain guard metadata (optional, for non-investment question reminders)
    domain_guard: Optional[Dict[str, Any]] = None
    answer_meta: Optional[Dict[str, Any]] = None


def _public_chat_state(*, active_stock_context, session_state_version, session_generation, selected_skill_ids,
                       active_report_context=None):
    """One whitelist for detail, accepted and non-stream committed state."""
    stock = None if active_stock_context is None else {
        key: active_stock_context[key] for key in ("stock_code", "stock_name", "canonical_id", "asset_type")
    }
    report = None if active_report_context is None else {
        key: active_report_context.get(key) for key in
        ("record_id", "status", "stock_code", "stock_name", "created_at")
    }
    return {
        "active_stock_context": stock,
        "active_report_context": report,
        "session_state_version": session_state_version,
        "session_generation": session_generation,
        "session_state": {"selected_skill_ids": selected_skill_ids},
    }


def _check_domain_guard(request, config, session_service, session_id):
    """Check if the message passes domain guard classification.
    
    Returns:
        None if allowed, otherwise (decision, meta) tuple
    """
    try:
        # Get session context for classification
        session_detail = session_service.get_session_detail(session_id, limit=5)
        context = {
            "stock_code": session_detail.active_stock_context.get("stock_code") if session_detail.active_stock_context else None,
            "stock_name": session_detail.active_stock_context.get("stock_name") if session_detail.active_stock_context else None,
            "active_report_context": session_detail.active_report_context,
        }
        
        # Get recent history
        history_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in session_detail.messages[-6:]  # Last 3 turns
        ]
        
        guard_service = AgentDomainGuardService(config)
        decision, meta = guard_service.classify(
            message=request.message,
            context=context,
            history_messages=history_messages,
        )
        
        if decision.action != DomainAction.ALLOW:
            return decision, meta
        
        return None
    except Exception as e:
        logger.warning("Domain guard check failed: %s", e)
        return None  # Fail open on error


def _prepare_chat_request(request, config, session_id, session_service,
                          intake_service=None, *, request_id=None):
    """Prepare once, accept the frozen brief, then commit exactly one turn."""
    domain_guard_result = _check_domain_guard(request, config, session_service, session_id)

    brief_payload = None
    if request.interaction_version >= 1:
        if request.intake_id:
            if request.intake_revision is None or intake_service is None:
                raise IntakeNotConfirmed(request.intake_id)
            accepted_brief = intake_service.accept_for_chat(
                intake_id=request.intake_id,
                intake_revision=request.intake_revision,
                message=request.message,
                session_id=session_id,
                session_generation=request.session_generation,
                request_id=request_id or request.request_id or "",
            )
            brief_payload = accepted_brief.model_dump(exclude_none=True)
        else:
            brief_payload = _reuse_session_brief(session_service, session_id)

    resolved = session_service.prepare_session_turn(
        config, session_id, request.message, request.effective_skills,
        context=_build_agent_chat_context(request, config, None, brief_payload),
        expected_generation=request.session_generation,
    )
    if resolved.clarification is not None:
        accepted = session_service.commit_user_turn(resolved)
        return None, None, accepted, resolved.clarification, resolved.clarification_payload, domain_guard_result
    executor = _build_executor(config, resolved.skill_selection.effective_skill_ids)
    turn = executor.prepare_turn(
        message=request.message, session_id=session_id, context=resolved.effective_context,
        selected_skill_ids=resolved.skill_selection.selected_skill_ids_update,
        resolved_turn=resolved, session_service=session_service,
    )
    return executor, turn, turn.accepted_snapshot, None, None, domain_guard_result


def _clarification_result(session_service, accepted, content, backend_id):
    from src.agent.executor import AgentResult

    session_service.db.save_conversation_message(
        accepted["session_id"], "assistant", content, accepted_turn=accepted,
    )
    return AgentResult(success=True, content=content, backend=backend_id)


def _accepted_state_payload(accepted):
    return _public_chat_state(
        active_stock_context=accepted["active_stock_context"],
        session_state_version=accepted["session_state_version"],
        session_generation=accepted["session_generation"],
        selected_skill_ids=accepted["selected_skill_ids"],
        active_report_context=accepted.get("active_report_context"),
    )


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str

class SkillsResponse(BaseModel):
    skills: List[SkillInfo]
    default_skill_id: str = ""


class StrategiesResponse(BaseModel):
    strategies: List[SkillInfo]
    default_strategy_id: str = ""


class AgentModelDeployment(BaseModel):
    deployment_id: str
    model: str
    provider: str
    source: str
    api_base: Optional[str] = None
    deployment_name: Optional[str] = None
    is_primary: bool = False
    is_fallback: bool = False


class AgentModelsResponse(BaseModel):
    models: List[AgentModelDeployment]


@router.get("/models", response_model=AgentModelsResponse)
async def get_agent_models():
    """Get configured Agent model deployments for frontend selection."""
    config = get_config()
    from src.agent.agent_backend import AgentBackendConfigError, resolve_agent_backend_id

    try:
        resolve_agent_backend_id(config)
    except AgentBackendConfigError:
        return AgentModelsResponse(models=[])
    return AgentModelsResponse(
        models=[AgentModelDeployment(**item) for item in list_agent_model_deployments(config)]
    )


@router.get("/status", response_model=AgentBackendStatusResponse)
async def get_agent_status():
    """Return the current effective Chat backend status for the Chat page."""
    payload = await asyncio.to_thread(_get_agent_chat_status, get_config())
    return _agent_status_response(payload)


def _agent_status_response(payload: Dict[str, Any]) -> AgentBackendStatusResponse:
    return AgentBackendStatusResponse(
        backend=payload["backend"],
        available=payload["available"],
        experimental=payload["experimental"],
        version=payload.get("version"),
        error_code=payload.get("error_code"),
        message=payload.get("message"),
        guided_intake=True,
    )


def _build_skills_response(config) -> SkillsResponse:
    from src.agent.factory import get_skill_manager
    from src.agent.skills.defaults import get_primary_default_skill_id

    skill_manager = get_skill_manager(config)
    available_skills = sorted(
        [
            skill
            for skill in skill_manager.list_skills()
            if getattr(skill, "user_invocable", True)
        ],
        key=lambda skill: (
            int(getattr(skill, "default_priority", 100)),
            skill.display_name,
            skill.name,
        ),
    )
    skills = [
        SkillInfo(id=skill.name, name=skill.display_name, description=skill.description)
        for skill in available_skills
    ]
    return SkillsResponse(
        skills=skills,
        default_skill_id=get_primary_default_skill_id(available_skills),
    )


@router.get("/skills", response_model=SkillsResponse)
async def get_skills():
    """
    Get available agent strategy skills.
    """
    return _build_skills_response(get_config())


@router.get("/strategies", response_model=StrategiesResponse, include_in_schema=False)
async def get_strategies():
    """Compatibility alias for legacy clients."""
    payload = _build_skills_response(get_config())
    return StrategiesResponse(
        strategies=payload.skills,
        default_strategy_id=payload.default_skill_id,
    )

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    request: ChatRequest,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
    intake_service: "AgentChatIntakeService" = Depends(get_agent_chat_intake_service),
):
    """Chat with the AI Agent without progress events."""
    config = get_config()
    backend_id = _select_agent_chat_backend(config)

    session_id = request.session_id or str(uuid.uuid4())
    request_id = request.request_id or str(uuid.uuid4())

    try:
        executor, turn, accepted, clarification, clarification_payload, domain_guard_result = await asyncio.to_thread(
            _prepare_chat_request, request, config, session_id, session_service, intake_service,
            request_id=request_id,
        )
        
        # T2: Handle domain guard rejection
        if domain_guard_result is not None:
            decision, meta = domain_guard_result
            # Save the reminder as a normal assistant message
            accepted_for_save = session_service.prepare_session_turn(
                config, session_id, request.message, request.effective_skills,
                context=_build_agent_chat_context(request, config, None, intake_service),
                expected_generation=request.session_generation,
            )
            if accepted_for_save.clarification is None:
                accepted_snapshot = session_service.commit_user_turn(accepted_for_save)
                await asyncio.to_thread(
                    _clarification_result, session_service, accepted_snapshot, 
                    decision.message or "Domain guard reminder", backend_id
                )
            
            return ChatResponse(
                success=True,
                content=decision.message or "",
                session_id=session_id,
                error=None,
                clarification=None,
                domain_guard={
                    "action": meta.action.value,
                    "reason_code": meta.reason_code.value,
                    "policy_version": meta.policy_version,
                    "elapsed_ms": meta.elapsed_ms,
                    "source": meta.source,
                },
                **_accepted_state_payload(accepted_snapshot if 'accepted_snapshot' in locals() else accepted),
            )
        
        if clarification is not None:
            result = await asyncio.to_thread(_clarification_result, session_service, accepted, clarification, backend_id)
        else:
            result = await asyncio.to_thread(executor.execute_turn, turn)

        return ChatResponse(
            success=result.success,
            content=result.content,
            session_id=session_id,
            error=result.error,
            clarification=clarification_payload,
            answer_meta=_public_answer_meta(result),
            **_accepted_state_payload(accepted),
        )
    except IntakeServiceError as exc:
        raise HTTPException(status_code=_intake_status_code(exc.code),
                            detail={"error": exc.code, "message": exc.message}) from exc
    except ChatSessionStateConflict as exc:
        raise HTTPException(status_code=409, detail={"error": exc.code, "message": str(exc)}) from exc
    except AgentReportContextError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent chat API failed: {e}")
        logger.exception("Agent chat error details:")
        raise HTTPException(status_code=500, detail=str(e))


class SessionItem(BaseModel):
    session_id: str
    title: str
    message_count: int
    created_at: Optional[str] = None
    last_active: Optional[str] = None

class SessionsResponse(BaseModel):
    sessions: List[SessionItem]

class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]
    session_state: SessionStateResponse
    active_stock_context: Optional[ActiveStockContextResponse]
    active_report_context: Optional[ActiveReportContextResponse] = None
    session_state_version: int
    session_generation: Optional[str]
    is_running: bool = False
    running_progress_steps: List[Dict[str, Any]] = []
    running_turn_id: Optional[str] = None
    running_turn_anchor: Optional[int] = None
    running_started_at: Optional[str] = None
    running_heartbeat_at: Optional[str] = None
    pending_intake: Optional[Dict[str, Any]] = None
    analysis_brief: Optional[Dict[str, Any]] = None


@router.get("/chat/sessions", response_model=SessionsResponse)
async def list_chat_sessions(
    limit: int = 50,
    user_id: Optional[str] = None,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """获取聊天会话列表

    Args:
        limit: Maximum number of sessions to return.
        user_id: Optional platform-prefixed user identifier for session
            isolation.  When provided, only sessions whose session_id
            starts with this prefix are returned.  The value must
            include the platform prefix, e.g. ``telegram_12345``,
            ``feishu_ou_abc``.
    """
    sessions = session_service.list_sessions(limit, user_id)
    return SessionsResponse(sessions=sessions)


@router.get("/chat/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_chat_session_messages(
    session_id: str,
    limit: int = 100,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
    intake_service: AgentChatIntakeService = Depends(get_agent_chat_intake_service),
):
    """获取单个会话的完整消息"""
    detail = session_service.get_session_detail(session_id, limit)
    pending = intake_service.find_pending_intake(session_id, detail.session_generation)
    pending_payload = pending.model_dump(mode="json", exclude_none=True) if pending else None
    brief_payload = None
    if pending is not None and pending.status == IntakeStatus.CONFIRMED:
        brief_payload = intake_service.get_confirmed_brief(
            pending.intake_id).model_dump(mode="json", exclude_none=True)
    else:
        for message in reversed(detail.messages):
            meta = message.get("answer_meta") if isinstance(message, dict) else None
            if isinstance(meta, dict) and isinstance(meta.get("brief_snapshot"), dict):
                brief_payload = meta["brief_snapshot"]
                break
    return SessionMessagesResponse(
        session_id=session_id,
        messages=detail.messages,
        pending_intake=pending_payload,
        analysis_brief=brief_payload,
        is_running=detail.is_running,
        running_progress_steps=detail.running_progress_steps or [],
        running_turn_id=detail.running_turn_id,
        running_turn_anchor=detail.running_turn_anchor,
        running_started_at=detail.running_started_at,
        running_heartbeat_at=detail.running_heartbeat_at,
        **_public_chat_state(
            active_stock_context=detail.active_stock_context,
            session_state_version=detail.session_state_version,
            session_generation=detail.session_generation,
            selected_skill_ids=detail.selected_skill_ids,
            active_report_context=detail.active_report_context,
        ),
    )


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


@router.patch("/chat/sessions/{session_id}")
async def rename_chat_session(
    session_id: str,
    request: RenameSessionRequest,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title must not be blank")
    if not session_service.rename_session(session_id, title):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"title": title}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """删除指定会话"""
    count = session_service.delete_session(session_id)
    return {"deleted": count}


class SendChatRequest(BaseModel):
    """Request body for sending chat content to notification channels."""

    content: str = Field(..., min_length=1, max_length=50000)
    title: Optional[str] = None


@router.post("/chat/send")
async def send_chat_to_notification(request: SendChatRequest):
    """
    Send chat session content to configured notification channels.
    Uses run_in_executor to avoid blocking the event loop.
    """
    from src.notification import NotificationService

    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(
        None,
        lambda: NotificationService().send(request.content),
    )
    if not success:
        return {
            "success": False,
            "error": "no_channels",
            "message": "未配置通知渠道，请先在设置中配置",
        }
    return {"success": True}


def _build_executor(config, skills: Optional[List[str]] = None):
    """Build and return the backend-neutral Chat executor (sync helper)."""
    from src.agent.factory import build_agent_chat_executor

    return build_agent_chat_executor(config, skills=skills)


def _get_agent_chat_status(config) -> Dict[str, Any]:
    from src.services.agent_backend_status_service import AgentBackendStatusService

    return AgentBackendStatusService(config=config).get_status()


def _select_agent_chat_backend(config) -> str:
    """Select the runtime backend without repeating the compatibility probe."""
    from src.services.agent_backend_status_service import evaluate_agent_backend_config

    evaluation = evaluate_agent_backend_config(config)
    if not evaluation["available"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": evaluation["error_code"],
                "message": evaluation["message"],
            },
        )
    return evaluation["backend"]


async def _run_research_in_background(
    agent,
    question: str,
    context: Optional[Dict[str, Any]],
    *,
    timeout: int,
):
    """Run deep research off the event loop with an internal overall timeout."""
    return await asyncio.to_thread(
        agent.research,
        question,
        context,
        timeout_seconds=timeout,
    )


# ============================================================
# Deep research endpoint
# ============================================================

class ResearchRequest(BaseModel):
    question: str
    stock_code: Optional[str] = None

class ResearchResponse(BaseModel):
    success: bool
    content: str
    sources: List[str] = Field(default_factory=list)
    token_usage: int = 0
    error: Optional[str] = None


@router.post("/research", response_model=ResearchResponse)
async def agent_research(request: ResearchRequest):
    """Run a deep-research query via the ResearchAgent.

    Similar to the ``/research`` bot command but exposed as a REST endpoint.
    """
    config = get_config()
    if not config.is_agent_available():
        raise HTTPException(status_code=400, detail="Agent mode is not enabled")

    # T2: Domain guard check before research
    guard_service = AgentDomainGuardService(config)
    decision, meta = guard_service.classify(request.question)
    if decision.action != DomainAction.ALLOW:
        return ResearchResponse(
            success=True,
            content=decision.message or "",
            sources=[],
            token_usage=0,
            error=None,
        )

    question = request.question
    context: Optional[Dict[str, Any]] = None
    if request.stock_code:
        question = f"[Stock: {request.stock_code}] {question}"
        context = {"stock_code": request.stock_code}

    try:
        from src.agent.research import ResearchAgent
        from src.agent.factory import get_tool_registry
        from src.agent.llm_adapter import LLMToolAdapter

        registry = get_tool_registry()
        llm_adapter = LLMToolAdapter(config)
        budget = getattr(config, "agent_deep_research_budget", 30000)

        agent = ResearchAgent(
            tool_registry=registry,
            llm_adapter=llm_adapter,
            token_budget=budget,
        )

        research_timeout = getattr(config, "agent_deep_research_timeout", 180)

        result = await _run_research_in_background(
            agent,
            question,
            context,
            timeout=research_timeout,
        )
        if getattr(result, "timed_out", False):
            logger.warning("Agent research API timed out after %ss", research_timeout)
            return ResearchResponse(
                success=False,
                content="",
                sources=[],
                token_usage=0,
                error=f"Deep research timed out after {research_timeout}s",
            )

        return ResearchResponse(
            success=result.success,
            content=result.report,
            sources=[f"Sub-question {i+1}: {q}" for i, q in enumerate(result.sub_questions)],
            token_usage=result.total_tokens,
            error=result.error if not result.success else None,
        )
    except Exception as e:
        logger.error("Agent research API failed: %s", e)
        logger.exception("Agent research error details:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def agent_chat_stream(
    request: ChatRequest,
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
    intake_service: AgentChatIntakeService = Depends(get_agent_chat_intake_service),
):
    """
    Chat with the AI Agent, streaming progress via SSE.
    Each SSE event is a JSON object with a 'type' field:
      - thinking: AI is deciding next action
      - stage_start: an agent or orchestrator stage has begun
      - stage_done: an agent or orchestrator stage finished
      - tool_start: a tool call has begun
      - tool_done: a tool call finished
      - generating: final answer being generated
      - pipeline_timeout: analysis stopped because the stage/pipeline budget expired
      - pipeline_budget_skipped: analysis stopped before an unstarted stage
        because the remaining budget was too low for useful work
      - done: analysis complete, contains 'content' and 'success'
      - error: error occurred, contains 'message'
    """
    config = get_config()
    backend_id = _select_agent_chat_backend(config)

    session_id = request.session_id or str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    request_id = request.request_id or str(uuid.uuid4())

    def progress_callback(event: dict):
        # Enrich tool events with display names
        if event.get("type") in ("tool_start", "tool_done"):
            tool = event.get("tool", "")
            event["display_name"] = TOOL_DISPLAY_NAMES.get(tool, tool)
        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    def run_sync(executor, turn):
        try:
            # T5: 启用流式内容输出（当请求和服务端配置都开启时）
            stream_content = (
                getattr(request, 'stream_content', False) and 
                getattr(config, 'agent_chat_stream_content', False)
            )
            result = executor.execute_turn(
                turn, 
                progress_callback=progress_callback,
                stream_content=stream_content,
            )
            event = {
                "type": "done",
                "success": result.success,
                "content": result.content,
                "error": result.error,
                "total_steps": result.total_steps,
                "session_id": session_id,
            }
            event.update({
                "backend": getattr(result, "backend", "") or backend_id,
                "error_code": getattr(result, "error_code", None),
                "request_id": request_id,
                # T2: Include persisted message ID and thinking steps for UI recovery
                "assistant_message_id": getattr(result, "assistant_message_id", None),
                "thinking_steps": getattr(result, "thinking_steps", []),
                "answer_meta": _public_answer_meta(result),
            })
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            logger.error("Agent stream error: %s", exc)
            event = {
                "type": "error",
                "message": str(exc),
                "error_code": getattr(exc, "code", "unknown_backend_error"),
                "backend": backend_id,
                "request_id": request_id,
                "session_id": session_id,
            }
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)

    async def event_generator():
        fut = None
        try:
            try:
                executor, turn, accepted, clarification, clarification_payload, domain_guard_result = await asyncio.to_thread(
                    _prepare_chat_request, request, config, session_id, session_service,
                    intake_service, request_id=request_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Agent request preparation failed: %s", exc, exc_info=True)
                recoverable = isinstance(
                    exc, (AgentReportContextError, IntakeServiceError, ChatSessionStateConflict))
                event = {
                    "type": "error",
                    "message": (exc.message if isinstance(exc, IntakeServiceError) else str(exc))
                    if recoverable else "Agent request was not accepted",
                    "error_code": (getattr(exc, "code", None) or "request_not_accepted")
                    if recoverable else "request_not_accepted",
                    "backend": backend_id,
                    "request_id": request_id,
                    "session_id": session_id,
                }
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                return

            accepted_event = {
                "type": "accepted",
                "backend": backend_id,
                "request_id": request_id,
                "session_id": session_id,
                **_accepted_state_payload(accepted),
            }
            yield "data: " + json.dumps(accepted_event, ensure_ascii=False) + "\n\n"

            # Backend execution starts only after the accepted event has been
            # yielded, so Web state and server persistence share one commit point.
            
            # T2: Handle domain guard rejection in SSE stream
            if domain_guard_result is not None:
                decision, meta = domain_guard_result
                # Save the reminder as a normal assistant message
                try:
                    accepted_for_save = session_service.prepare_session_turn(
                        config, session_id, request.message, request.effective_skills,
                        context=_build_agent_chat_context(request, config, None, None),
                        expected_generation=request.session_generation,
                    )
                    if accepted_for_save.clarification is None:
                        accepted_snapshot = session_service.commit_user_turn(accepted_for_save)
                        await asyncio.to_thread(
                            _clarification_result, session_service, accepted_snapshot,
                            decision.message or "Domain guard reminder", backend_id
                        )
                except Exception as save_err:
                    logger.warning("Failed to save domain guard reminder: %s", save_err)
                
                event = {
                    "type": "done",
                    "success": True,
                    "content": decision.message or "",
                    "error": None,
                    "total_steps": 0,
                    "session_id": session_id,
                    "backend": backend_id,
                    "request_id": request_id,
                    "error_code": None,
                    "domain_guard": {
                        "action": meta.action.value,
                        "reason_code": meta.reason_code.value,
                        "policy_version": meta.policy_version,
                        "elapsed_ms": meta.elapsed_ms,
                        "source": meta.source,
                    },
                }
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                return
            
            if clarification is not None:
                result = await asyncio.to_thread(_clarification_result, session_service, accepted, clarification, backend_id)
                event = {"type": "done", "success": result.success, "content": result.content,
                         "error": None, "total_steps": 0, "session_id": session_id,
                         "backend": backend_id, "request_id": request_id, "error_code": None,
                         "clarification": clarification_payload}
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                return
            fut = loop.run_in_executor(None, run_sync, executor, turn)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    event = {"type": "error", "message": "分析超时"}
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event.get("type") in ("done", "error"):
                    break
        finally:
            try:
                if fut is not None:
                    await asyncio.wait_for(fut, timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                # Cleanup taking longer than 5s is treated as an expected timeout; no warning.
                logger.debug("agent executor cleanup timed out after 5s for session %s", session_id)
            except Exception as exc:
                logger.warning("agent executor cleanup error (ignored): %s", exc, exc_info=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# Intake (guided questioning) endpoints
# ============================================================


@router.post("/chat/intake", response_model=IntakeCreateResponse)
async def create_chat_intake(
    request: IntakeCreateRequest,
    intake_service: AgentChatIntakeService = Depends(get_agent_chat_intake_service),
    session_service: AgentChatSessionService = Depends(get_agent_chat_session_service),
):
    """Create a new intake draft. This never executes research."""
    session_id = request.session_id
    config = get_config()
    guard_service = AgentDomainGuardService(config)
    decision, meta = guard_service.classify(request.message)
    if decision.action != DomainAction.ALLOW:
        raise HTTPException(status_code=422, detail={
            "error": "domain_scope_required",
            "message": decision.message or "",
            "domain_guard": {
                "action": meta.action.value, "reason_code": meta.reason_code.value,
                "policy_version": meta.policy_version, "elapsed_ms": meta.elapsed_ms,
                "source": meta.source,
            },
        })
    # "First" is server-side session state, not a browser flag.
    try:
        existing = session_service.get_session_detail(session_id, limit=-1).messages
    except Exception:
        existing = []
    if existing:
        raise HTTPException(status_code=409, detail={
            "error": "intake_not_first_turn",
            "message": "This session already has a conversation; no new intake is needed.",
        })
    try:
        db = getattr(session_service, "db", None)
        generation = request.session_generation
        if db is not None:
            generation = await asyncio.to_thread(
                db.ensure_chat_session_generation, session_id,
                expected_generation=request.session_generation,
            )
        context = request.context or {}
        draft = await asyncio.to_thread(
            intake_service.create_intake,
            message=request.message, session_id=session_id,
            session_generation=generation or "", request_id=request.request_id,
            stock_clarification_needed=_needs_object_intake(request.message),
            report_follow_up=bool(context.get("report_record_id") or context.get("record_id")),
        )
        return IntakeCreateResponse(
            intake_id=draft.intake_id, schema_version=draft.schema_version,
            session_generation=draft.session_generation,
            base_state_version=draft.base_state_version, revision=draft.revision,
            status=draft.status, questions=draft.questions, answers=draft.answers,
        )
    except ChatSessionStateConflict as exc:
        raise HTTPException(status_code=409, detail={"error": exc.code, "message": str(exc)}) from exc
    except IntakeServiceError as exc:
        raise HTTPException(status_code=_intake_status_code(exc.code),
                            detail={"error": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        logger.error("Intake create failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/chat/intake/{intake_id}", response_model=IntakeUpdateResponse)
async def update_chat_intake(
    intake_id: str,
    request: IntakeUpdateRequest,
    intake_service: AgentChatIntakeService = Depends(get_agent_chat_intake_service),
):
    """Save, confirm or skip answers with a server-side revision CAS."""
    try:
        draft = await asyncio.to_thread(
            intake_service.update_intake,
            intake_id=intake_id, expected_revision=request.expected_revision,
            answers=request.answers, action=request.action,
            session_id=request.session_id,
        )
        brief_preview = None
        if draft.status == IntakeStatus.CONFIRMED:
            brief_preview = intake_service.preview_brief(intake_id)
        elif draft.status == IntakeStatus.SKIPPED:
            brief_preview = intake_service.get_confirmed_brief(
                intake_id, session_id=request.session_id)
        return IntakeUpdateResponse(
            intake_id=draft.intake_id, revision=draft.revision,
            status=draft.status, brief_preview=brief_preview,
            questions=draft.questions,
        )
    except IntakeServiceError as exc:
        raise HTTPException(status_code=_intake_status_code(exc.code),
                            detail={"error": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        logger.error("Intake update failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/chat/intake/{intake_id}")
async def get_chat_intake(
    intake_id: str,
    session_id: Optional[str] = None,
    intake_service: AgentChatIntakeService = Depends(get_agent_chat_intake_service),
):
    """Get a draft without exposing another session's state."""
    draft = intake_service.get_intake(intake_id, session_id=session_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"error": "intake_not_found"})
    return draft.model_dump(mode="json")
