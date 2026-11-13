"""Agent Chat Intake - Data structures for guided questioning.

T1: Contract and question bank convergence.
v2: Smart intake with object resolution and auto-inference.

This module defines the data structures for the intake (guided questioning)
flow, including questions, answers, and analysis brief.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# v2 Types: Object Resolution and Smart Intake
# ─────────────────────────────────────────────────────────────────────────────


class ResolutionStatus(str, Enum):
    """Object resolution status."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    NOT_REQUIRED = "not_required"


class ResolutionScope(str, Enum):
    """Object resolution scope."""

    STOCK = "stock"
    INDEX = "index"
    ETF = "etf"
    MARKET = "market"
    PORTFOLIO = "portfolio"
    WATCHLIST = "watchlist"
    REPORT = "report"
    CONCEPT = "concept"
    UNKNOWN = "unknown"


class BriefSource(str, Enum):
    """Source of a brief field value."""

    EXPLICIT_MESSAGE = "explicit_message"
    USER_CHOICE = "user_choice"
    SESSION_INHERITED = "session_inherited"
    SYSTEM_INFERRED = "system_inferred"
    USER_DEFAULT = "user_default"
    UNKNOWN = "unknown"


class QuestionKind(str, Enum):
    """v2 question kind for smart intake."""

    OBJECT_CHOICE = "object_choice"
    OBJECT_SEARCH = "object_search"
    SCOPE_CLARIFY = "scope_clarify"
    INFO_CONFIRM = "info_confirm"


class ObjectIdentity(BaseModel):
    """v2: Stock identity with market label."""

    stock_code: str = Field(..., description="Stock code (e.g. '600519', 'HK00700', 'AAPL')")
    stock_name: Optional[str] = Field(default=None, description="Stock name")
    canonical_id: str = Field(..., description="Canonical ID (e.g. '600519.SH', 'HK00700')")
    asset_type: str = Field(default="stock", description="Asset type: stock | index | etf")
    market: str = Field(..., description="Market code: CN | HK | US | JP | KR | TW | UNKNOWN")
    market_label: str = Field(..., description="User-readable market label")


class ResolutionEvidence(BaseModel):
    """Evidence for object resolution."""

    kind: str = Field(..., description="Evidence kind: bare_cn_code | suffix_code | name_match | etc.")
    text: str = Field(..., description="Matched text from input")
    start: Optional[int] = Field(default=None, description="Start position in input")
    end: Optional[int] = Field(default=None, description="End position in input")


class ObjectResolution(BaseModel):
    """v2: Result of object resolution."""

    status: ResolutionStatus = Field(..., description="Resolution status")
    scope: ResolutionScope = Field(default=ResolutionScope.UNKNOWN, description="Resolution scope")
    identity: Optional[ObjectIdentity] = Field(default=None, description="Resolved identity")
    candidates: List[ObjectIdentity] = Field(
        default_factory=list,
        description="Candidate identities for ambiguous cases",
    )
    recommended_option_id: Optional[str] = Field(
        default=None,
        description="Recommended candidate ID",
    )
    recommendation_reason: Optional[str] = Field(
        default=None,
        description="Reason for recommendation",
    )
    evidence: Optional[ResolutionEvidence] = Field(default=None, description="Resolution evidence")
    reason: Optional[str] = Field(
        default=None,
        description="Resolution reason: unique_code | unique_name | session_inherited | etc.",
    )


class IntakePlan(BaseModel):
    """v2: Intake plan from question selection."""

    auto_start: bool = Field(default=False, description="Whether to auto-start research")
    blocking_reason: Optional[str] = Field(
        default=None,
        description="Reason for blocking: ambiguous | missing | unsupported | etc.",
    )
    question_count: int = Field(default=0, description="Number of questions to ask")


class QuestionType(str, Enum):
    """Question input type."""

    SINGLE = "single"
    MULTIPLE = "multiple"


class QuestionDimension(str, Enum):
    """Question semantic dimension."""

    OBJECT = "object"
    GOAL = "goal"
    HORIZON = "horizon"
    POSITION = "position"
    RISK_FOCUS = "risk_focus"
    RESEARCH_FOCUS = "research_focus"
    READING_STYLE = "reading_style"


class QuestionOption(BaseModel):
    """A single option in a question."""

    id: str = Field(..., description="Option identifier")
    label: str = Field(..., description="Display label")
    is_unknown: bool = Field(
        default=False,
        description="Whether this represents 'unknown/not provided'",
    )
    # v2: Optional identity for object choice questions
    identity: Optional[ObjectIdentity] = Field(
        default=None,
        description="v2: Concrete stock identity for object choice options",
    )


class Question(BaseModel):
    """A guided question."""

    id: str = Field(..., description="Question identifier")
    dimension: QuestionDimension = Field(..., description="Semantic dimension")
    type: QuestionType = Field(..., description="Input type")
    title: str = Field(..., description="Question title")
    help: str = Field(default="", description="Help text")
    options: List[QuestionOption] = Field(..., description="Available options")
    required: bool = Field(default=False, description="Whether required")
    depends_on: Optional[str] = Field(
        default=None,
        description="ID of question this depends on",
    )
    max_selections: Optional[int] = Field(
        default=None,
        description="For MULTIPLE type, max selectable options",
    )
    # v2: Question kind for smart intake
    kind: Optional[QuestionKind] = Field(
        default=None,
        description="v2: Question kind for smart intake",
    )
    # v2: Recommended option ID
    recommended_option_id: Optional[str] = Field(
        default=None,
        description="v2: Recommended option ID",
    )
    # v2: Allow free text input
    allow_free_text: bool = Field(
        default=False,
        description="v2: Whether to allow free text input",
    )
    # v2: Free text kind
    free_text_kind: Optional[str] = Field(
        default=None,
        description="v2: Free text kind: stock_search",
    )

    @field_validator("max_selections")
    @classmethod
    def validate_max_selections(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("max_selections must be >= 1")
        return v


class Answer(BaseModel):
    """User's answer to a question."""

    question_id: str = Field(..., description="ID of the question")
    option_ids: List[str] = Field(..., description="Selected option IDs")
    source: str = Field(
        default="user_choice",
        description="Source: explicit_message | user_choice | unknown",
    )

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        allowed = {"explicit_message", "user_choice", "unknown"}
        if v not in allowed:
            raise ValueError(f"source must be one of {allowed}")
        return v


class AnalysisBrief(BaseModel):
    """Confirmed analysis context derived from intake answers."""

    brief_version: int = Field(default=1, description="Schema version")
    object_scope: Optional[str] = Field(
        default=None,
        description="Resolved stock code or object identifier",
    )
    goal: Optional[str] = Field(default=None, description="Analysis goal")
    horizon: Optional[str] = Field(default=None, description="Time horizon")
    position: Optional[str] = Field(default=None, description="Position status")
    risk_focus: Optional[str] = Field(default=None, description="Risk preference")
    focus_areas: List[str] = Field(
        default_factory=list,
        description="Research focus areas",
    )
    reading_style: Optional[str] = Field(
        default=None,
        description="Reading style preference",
    )
    unknown_fields: List[str] = Field(
        default_factory=list,
        description="Dimensions where user selected 'unknown'",
    )
    # v2: Concrete object identity
    object: Optional[ObjectIdentity] = Field(
        default=None,
        description="v2: Concrete resolved object identity",
    )
    # v2: Sources for each field
    sources: Dict[str, BriefSource] = Field(
        default_factory=dict,
        description="v2: Source of each field value",
    )
    # v2: Evidence for inferred fields
    evidence: Dict[str, str] = Field(
        default_factory=dict,
        description="v2: Evidence text for inferred fields",
    )
    # v2: Assumptions made by system
    assumptions: List[str] = Field(
        default_factory=list,
        description="v2: Assumptions made by system inference",
    )
    # v2: Auto-confirmed flag
    auto_confirmed: bool = Field(
        default=False,
        description="v2: Whether this brief was auto-confirmed",
    )


class IntakeStatus(str, Enum):
    """Intake draft lifecycle status."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"


class IntakeDraft(BaseModel):
    """Server-side intake draft state."""

    intake_id: str = Field(..., description="Unique intake identifier")
    schema_version: int = Field(default=1, description="Schema version")
    session_id: str = Field(..., description="Associated session ID")
    session_generation: str = Field(..., description="Session generation token")
    base_state_version: int = Field(default=0, description="Base state version")
    revision: int = Field(default=1, description="Current revision number")
    original_message: str = Field(..., description="Original user message")
    questions: List[Question] = Field(..., description="Questions to ask")
    answers: List[Answer] = Field(default_factory=list, description="User answers")
    status: IntakeStatus = Field(default=IntakeStatus.PENDING, description="Status")
    created_at: Optional[str] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="Last update timestamp")


class AnswerMeta(BaseModel):
    """Metadata about answer editing applied to a message."""

    schema_version: int = Field(default=1, description="Schema version")
    editor_version: str = Field(default="1.0", description="Editor version")
    editor_status: str = Field(
        default="applied",
        description="Status: applied | normalized | fallback",
    )
    brief_snapshot: Optional[AnalysisBrief] = Field(
        default=None,
        description="Brief snapshot at edit time",
    )
    source_refs: List[str] = Field(default_factory=list, description="Source references used")
    used_source_refs: List[str] = Field(default_factory=list)
    validation_codes: List[str] = Field(default_factory=list)
    processing_time_ms: Optional[int] = Field(default=None)
    fallback_reason: Optional[str] = Field(default=None)
    usage: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator("editor_status")
    @classmethod
    def validate_editor_status(cls, v: str) -> str:
        allowed = {"applied", "normalized", "fallback"}
        if v not in allowed:
            raise ValueError(f"editor_status must be one of {allowed}")
        return v


# Request/Response models for API


class IntentHints(BaseModel):
    """Client-side intent inference hints sent alongside the intake request.

    The server merges these with its own regex-based classification. Every
    field is optional — the server falls back to its own inference when a
    hint is absent.
    """

    extracted_stock_code: Optional[str] = Field(
        default=None,
        description="Stock code extracted by the client (e.g. '600519', 'HK00700', 'AAPL')",
    )
    inferred_market: Optional[str] = Field(
        default=None,
        description="Market inferred by the client: a_share | hk | us | index | other",
    )
    goal: Optional[str] = Field(
        default=None,
        description="Goal hint: worth_watching | evaluate_holding | understand_move | learn_method",
    )
    horizon: Optional[str] = Field(
        default=None,
        description="Horizon hint: days | weeks | months | long",
    )
    position: Optional[str] = Field(
        default=None,
        description="Position hint: not_held | held_protect_profit | held_control_drawdown",
    )


class IntakeCreateRequest(BaseModel):
    """Request to create a new intake draft."""

    message: str = Field(..., description="Original user message")
    session_id: str = Field(..., description="Session ID")
    session_generation: Optional[str] = Field(
        default=None,
        description="Session generation token",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Idempotency key",
    )
    skills: Optional[List[str]] = Field(
        default=None,
        description="Requested skill IDs",
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional context",
    )
    intent_hints: Optional[IntentHints] = Field(
        default=None,
        description="Client-side extraction hints for market, goal, horizon, etc.",
    )
    # v2: Interaction version for smart intake
    interaction_version: int = Field(
        default=1,
        description="Interaction version: 1 = v1 guided questions, 2 = v2 smart intake",
    )


class IntakeCreateResponse(BaseModel):
    """Response after creating an intake draft."""

    intake_id: str = Field(..., description="Created intake ID")
    schema_version: int = Field(..., description="Schema version")
    session_generation: str = Field(..., description="Session generation")
    base_state_version: int = Field(..., description="Base state version")
    revision: int = Field(..., description="Initial revision")
    status: IntakeStatus = Field(..., description="Initial status")
    questions: List[Question] = Field(..., description="Questions to ask")
    answers: List[Answer] = Field(default_factory=list, description="Initial answers")
    # v2: Object resolution result
    resolution: Optional[ObjectResolution] = Field(
        default=None,
        description="v2: Object resolution result",
    )
    # v2: Brief preview
    brief_preview: Optional[AnalysisBrief] = Field(
        default=None,
        description="v2: Brief preview",
    )
    # v2: Intake plan
    plan: Optional[IntakePlan] = Field(
        default=None,
        description="v2: Intake plan with auto_start flag",
    )


class IntakeUpdateRequest(BaseModel):
    """Request to update intake answers."""

    expected_revision: int = Field(..., description="Expected current revision")
    answers: List[Answer] = Field(..., description="Updated answers")
    action: str = Field(default="save", description="Action: save | confirm | skip")
    session_id: Optional[str] = Field(default=None)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"save", "confirm", "skip"}
        if v not in allowed:
            raise ValueError(f"action must be one of {allowed}")
        return v


class IntakeUpdateResponse(BaseModel):
    """Response after updating intake."""

    intake_id: str = Field(..., description="Intake ID")
    revision: int = Field(..., description="New revision number")
    status: IntakeStatus = Field(..., description="Updated status")
    brief_preview: Optional[AnalysisBrief] = Field(
        default=None, description="Brief preview (only on confirm)")
    questions: List[Question] = Field(
        default_factory=list, description="Replanned questionnaire after this update")


class ChatRequestWithIntake(BaseModel):
    """Extended chat request with intake reference."""

    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    session_generation: Optional[str] = Field(
        default=None,
        description="Session generation",
    )
    request_id: Optional[str] = Field(default=None, description="Request ID")
    skills: Optional[List[str]] = Field(default=None, description="Skill IDs")
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Context data",
    )
    # New fields for intake integration
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


__all__ = [
    # v2 types
    "ResolutionStatus",
    "ResolutionScope",
    "BriefSource",
    "QuestionKind",
    "ObjectIdentity",
    "ResolutionEvidence",
    "ObjectResolution",
    "IntakePlan",
    # v1 types
    "QuestionType",
    "QuestionDimension",
    "QuestionOption",
    "Question",
    "Answer",
    "AnalysisBrief",
    "IntakeStatus",
    "IntakeDraft",
    "AnswerMeta",
    "IntentHints",
    "IntakeCreateRequest",
    "IntakeCreateResponse",
    "IntakeUpdateRequest",
    "IntakeUpdateResponse",
    "ChatRequestWithIntake",
]
