"""Strict public draft contract; paths, actors, versions and rules stay server-owned."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Clock(Strict):
    kind: Literal["SESSION_CLOSE_PLUS_MINUTES"]
    minutes: int = Field(ge=0, le=1440)


class Cost(Strict):
    currency: Literal["USD"]
    slippage_bps: list[int]
    fee_unit: Literal["BPS_OF_ENTRY", "FIXED_USD"]
    entry_fee: str = Field(max_length=50)
    exit_fee: str = Field(max_length=50)
    reference_notional: str | None = Field(default=None, max_length=50)
    version: str = "two-sided-slippage-fees-v1"


class Split(Strict):
    kind: Literal["EXPLORATORY", "CHRONOLOGICAL_60_20_20"]
    embargo_sessions: int | None = None


class Draft(Strict):
    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=2000)
    registration_id: str = Field(min_length=1, max_length=128)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    warmup_bars: int = Field(ge=260, le=10000)
    evaluation_clock: Clock
    label_as_of: str = Field(max_length=40)
    research_mode: Literal["POINT_IN_TIME", "REVISED_HISTORY"]
    price_return_kind: Literal["PRICE_RETURN", "TOTAL_RETURN"]
    entry_bases: list[Literal["NEXT_AVAILABLE_OPEN", "CONFIRM_CLOSE"]] = Field(min_length=1, max_length=2)
    cost_assumption: Cost
    split_policy: Split
    min_completed_samples: int = Field(ge=1, le=10000000)


class ValidationRequest(Strict):
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    draft: Draft
    draft_id: str | None = Field(default=None, max_length=128)
    config_revision: int | None = Field(default=None, ge=1)
    config_digest: str | None = Field(default=None, max_length=64)


class RunRequest(Strict):
    validation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    config_digest: str = Field(min_length=64, max_length=64)
    reveal_sealed: Literal[False]


class RetryRequest(Strict):
    expected_attempt: int = Field(ge=1)


class RevealRequest(Strict):
    expected_registration_revision: str = Field(min_length=64, max_length=64)
    confirmation: Literal["REVEAL_SEALED"]
    reason: str = Field(min_length=1, max_length=2000)


class ComparisonRequest(Strict):
    left_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    right_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    axis: Literal["rules", "universe", "data", "price_basis"]


class CancelRequest(Strict):
    expected_attempt: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class VariantRequest(Strict):
    expected_report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_id: str = Field(max_length=10)
    parameter: str = Field(max_length=80)
    value: int | float
