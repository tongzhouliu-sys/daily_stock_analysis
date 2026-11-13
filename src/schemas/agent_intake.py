"""Agent Chat Intake - Data structures for guided questioning.

T1: Contract and question bank convergence.

This module defines the data structures for the intake (guided questioning)
flow, including questions, answers, and analysis brief.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


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
