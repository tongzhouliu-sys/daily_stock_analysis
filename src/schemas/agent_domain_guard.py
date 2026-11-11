# -*- coding: utf-8 -*-
"""Agent Domain Guard — Data structures for investment relevance classification.

T1: Shared domain decision contract.

This module defines the data structures for the domain guard flow,
including the decision enum, reason codes, and the decision result.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DomainAction(str, Enum):
    """Action to take after domain classification."""

    ALLOW = "allow"              # Investment-related, proceed to normal flow
    REDIRECT = "redirect"        # Clearly off-topic, remind and end turn
    CLARIFY = "clarify"          # Ambiguous investment relevance, ask for context
    DIRECT_REPLY = "direct_reply"  # Short social response, no research


class DomainReasonCode(str, Enum):
    """Reason code for the domain decision."""

    INVESTMENT = "investment"          # Clearly investment-related
    MIXED = "mixed"                    # Mixed request with separable investment part
    SYSTEM_HELP = "system_help"        # System usage help question
    SOCIAL = "social"                  # Short social greeting
    OFF_TOPIC = "off_topic"            # Clearly not investment-related
    AMBIGUOUS = "ambiguous"            # Cannot determine investment relevance
    CHECK_UNAVAILABLE = "check_unavailable"  # Check failed (timeout/error)


# Valid (action, reason_code) pairs — server validates against this
_VALID_ACTION_REASON = {
    (DomainAction.ALLOW, DomainReasonCode.INVESTMENT),
    (DomainAction.ALLOW, DomainReasonCode.MIXED),
    (DomainAction.ALLOW, DomainReasonCode.SYSTEM_HELP),
    (DomainAction.ALLOW, DomainReasonCode.SOCIAL),
    (DomainAction.REDIRECT, DomainReasonCode.OFF_TOPIC),
    (DomainAction.CLARIFY, DomainReasonCode.AMBIGUOUS),
    (DomainAction.CLARIFY, DomainReasonCode.CHECK_UNAVAILABLE),
    (DomainAction.DIRECT_REPLY, DomainReasonCode.SOCIAL),
}

POLICY_VERSION = "1.0.0"


class DomainGuardDecision(BaseModel):
    """Result of a domain guard classification."""

    action: DomainAction = Field(..., description="Action to take")
    reason_code: DomainReasonCode = Field(..., description="Reason code")
    message: Optional[str] = Field(
        default=None,
        description="User-visible reminder message (empty for allow/investment)",
    )
    allowed_request: Optional[str] = Field(
        default=None,
        description="For mixed requests: the investment sub-request only",
    )
    policy_version: str = Field(
        default=POLICY_VERSION,
        description="Policy version for diagnostics",
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: DomainAction) -> DomainAction:
        return v

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, v: DomainReasonCode) -> DomainReasonCode:
        return v

    def model_post_init(self, __context: object) -> None:
        """Validate action/reason_code combination."""
        pair = (self.action, self.reason_code)
        if pair not in _VALID_ACTION_REASON:
            raise ValueError(
                f"Invalid (action, reason_code) pair: "
                f"({self.action.value}, {self.reason_code.value}). "
                f"Valid pairs: {sorted((a.value, r.value) for a, r in _VALID_ACTION_REASON)}"
            )


class DomainGuardMeta(BaseModel):
    """Optional metadata attached to chat responses."""

    schema_version: int = Field(default=1, description="Schema version")
    action: DomainAction = Field(..., description="Action taken")
    reason_code: DomainReasonCode = Field(..., description="Reason code")
    policy_version: str = Field(default=POLICY_VERSION, description="Policy version")
    elapsed_ms: Optional[int] = Field(
        default=None,
        description="Classification elapsed time in milliseconds",
    )
    source: str = Field(
        default="rule",
        description="Classification source: rule | model | fallback",
    )


__all__ = [
    "DomainAction",
    "DomainReasonCode",
    "DomainGuardDecision",
    "DomainGuardMeta",
    "POLICY_VERSION",
]
