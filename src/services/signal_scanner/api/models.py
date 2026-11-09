from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re

from src.services.signal_scanner.core import dec


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def money_contract(cls, value):
        fields = {
            "entry",
            "stop",
            "structure_low",
            "initial_cash",
            "day_open_equity",
            "month_open_equity",
            "qty",
            "price",
            "avg_price",
            "mark",
            "price_tick",
            "fee",
            "cash_amount",
            "stop_reference",
            "split_ratio",
            "risk_pct",
            "daily_loss_limit",
            "monthly_loss_limit",
            "portfolio_heat_limit",
            "symbol_notional_limit",
            "sector_notional_limit",
            "fixed_entry_fee",
            "fixed_exit_fee",
            "per_share_fee",
            "slippage_bps",
        }

        def check(obj):
            if isinstance(obj, dict):
                for key, item in obj.items():
                    if key in fields and item is not None:
                        if not isinstance(item, str) or not re.fullmatch(r"-?\d+(\.\d{1,10})?", item):
                            raise ValueError(
                                "Decimal input requires a plain string with at most 10 fractional places"
                            )
                        dec(item)
                    else:
                        check(item)
            elif isinstance(obj, list):
                for item in obj:
                    check(item)

        check(value)
        return value


class Login(Model):
    password: str = Field(min_length=1, max_length=200)


class Versioned(Model):
    expected_revision: int = Field(ge=1)


class Note(Model):
    reason: str = Field(min_length=1, max_length=2000)


class Preflight(Model):
    action_market_date: str
    events_seen: bool
    event_hash: str
    risk_seen: bool
    risk_version: str
    mission: str = Field(min_length=1, max_length=500)


class Intent(Model):
    account_id: str
    entry: str
    stop: str
    structure_low: str | None = None

    @field_validator("entry", "stop", "structure_low")
    @classmethod
    def price(cls, value):
        if value is not None:
            dec(value, nonnegative=True)
        return value


class StopOpen(Model):
    card_id: str
    card_revision: int = Field(ge=1)
    action_market_date: str
    plan_intent: Intent


class StopStep(Versioned):
    step: Literal["S", "T", "O", "P"]
    acknowledged: bool | None = None
    emotion: Literal["Calm", "Fear", "Excitement", "Boredom"] | None = None
    evidence_hash: str | None = None
    read_ack: bool | None = None
    reason: str | None = None
    expectation: str | None = None
    invalidation: str | None = None


class Decision(Model):
    card_id: str
    card_revision: int = Field(ge=1)
    action_market_date: str
    action: Literal["WAIT", "IGNORE", "WATCH"]
    reason: str = Field(min_length=1)
    expectation: str = Field(min_length=1)
    invalidation: str = Field(min_length=1)
    emotion: Literal["Calm", "Fear", "Excitement", "Boredom"]


class RiskPreview(Intent):
    card_id: str
    card_revision: int = Field(ge=1)
    action_market_date: str
    max_qty: str | None = None


class Draft(RiskPreview):
    intent: Literal["DRAFT"]


class PlanPatch(Versioned):
    entry: str | None = None
    stop: str | None = None
    structure_low: str | None = None
    max_qty: str | None = None
    note: str | None = None


class Reserve(Versioned):
    card_revision: int
    account_snapshot_id: str
    risk_profile_version: str
    preflight_id: str
    stop_session_id: str
    account_confirmation: str = Field(min_length=1)


class Cancel(Versioned):
    reason: str = Field(min_length=1)


class Export(Versioned):
    format: Literal["markdown", "csv"] = "markdown"
    basis: Literal["EOD_REFERENCE", "HISTORICAL"] = "EOD_REFERENCE"


class Account(Model):
    scope: Literal["PAPER", "MANUAL"]
    origin: Literal["USER_PLAN", "MANUAL"]
    currency: Literal["USD"]
    initial_cash: str
    start_market_date: str
    name: str = Field(default="个人账户", max_length=100)
    initial_positions: list[dict] = Field(default_factory=list, max_length=300)
    day_open_equity: str | None = None
    month_open_equity: str | None = None
    funds_complete: bool = False
    as_of: str | None = None


class Confirm(Model):
    snapshot_id: str
    ledger_head: str
    complete: Literal[True]
    reason: str = Field(min_length=1)


class LedgerRow(Model):
    external_key: str = Field(min_length=1, max_length=200)
    entry_type: Literal["BUY", "SELL", "FEE", "DIVIDEND", "DEPOSIT", "WITHDRAWAL", "SPLIT"]
    instrument_id: str | None = None
    occurred_at: str
    qty: str | None = None
    price: str | None = None
    fee: str
    cash_amount: str | None = None
    currency: Literal["USD"]
    settlement_date: str | None = None
    plan_id: str | None = None
    stop_reference: str | None = None
    split_ratio: str | None = None


class LedgerImport(Model):
    account_id: str
    import_id: str
    dry_run: bool = True
    rows: list[LedgerRow] = Field(min_length=1, max_length=1000)


class Reverse(Note):
    replacement: LedgerRow | None = None


class Configuration(Model):
    expected_version: str
    change_reason: str = Field(min_length=1)
    value: dict


class Retry(Note):
    logical_key: str


class View(Model):
    revision: int
    view_event_id: str
    event_type: Literal["DISPLAYED"]


class Pause(Note):
    paused: bool
    until_action_market_date: str | None = None


class Research(Model):
    experiment_config_version: str
    mode: Literal["REPLAY", "SHADOW"]
    notifications: Literal[False] = False


class PrivacyExport(Model):
    scope: Literal["PERSONAL_DATA"]
    format: Literal["zip"] = "zip"


class PrivacyDelete(Model):
    scope: Literal["EMOTION_AND_NOTES", "ACCOUNT_DATA", "PERSONAL_DATA"]
    export_id: str
    confirmation: Literal["DELETE_REQUESTED"]
    account_id: str | None = None


class BatchHistoryFetch(Model):
    csv_filename: str = Field(min_length=1, max_length=200)
    years: int = Field(default=3, ge=1, le=10)
