# -*- coding: utf-8 -*-
"""Agent Chat session state service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.agent.factory import normalize_requested_skill_ids
from src.agent.stock_scope import (
    SWITCH_CLEANUP_KEYS, StockIdentity, StockScope, StockScopeResolution, resolve_stock_scope,
)
from src.agent.chat_object_decision import decide_chat_object, validate_chat_identity as _code_identity
from src.data.stock_index_loader import get_chat_stock_lookup
from src.services.agent_report_context_service import (
    AgentReportContextError,
    LoadedReportContext,
    load_agent_report_context,
    parse_report_record_id,
    try_load_agent_report_context,
    unavailable_report_context,
)
from src.services.stock_list_parser import IndexRegistry, _index_entry_from_row
from src.storage import ChatSessionStateConflict, DatabaseManager


def _stock_lookup() -> tuple[IndexRegistry, dict]:
    lookup = get_chat_stock_lookup()
    entries = [_index_entry_from_row(row) for row in lookup["index_rows"]]
    return IndexRegistry(entry for entry in entries if entry is not None), lookup


def _stock_record(identity: StockIdentity, lookup: dict, registry: IndexRegistry) -> Dict[str, Any]:
    name = identity.stock_name
    for code, label, kind in lookup["codes"].get(identity.stock_code.upper(), ()):
        candidate = StockIdentity.from_code(code, registry)
        if kind == candidate.asset_type and candidate == identity:
            name = label
            break
    return dict(identity.as_payload(), stock_name=name)


def _hint_identity(context: Dict[str, Any], registry: IndexRegistry, lookup: dict) -> Optional[StockIdentity]:
    try:
        identity = _code_identity(context.get("stock_code"), registry, lookup)
    except (TypeError, ValueError):
        return None
    if (context.get("canonical_id", identity.canonical_id) != identity.canonical_id
            or context.get("asset_type", identity.asset_type) != identity.asset_type):
        return None
    return identity


def _identity(stock: Dict[str, Any]) -> StockIdentity:
    return StockIdentity(stock["stock_code"], stock["canonical_id"], stock["asset_type"], stock.get("stock_name"))


def _same_report_identity(left: StockIdentity, right: StockIdentity) -> bool:
    """Report and session identities compare by canonical object, not spelling."""
    return (left.canonical_id, left.asset_type) == (right.canonical_id, right.asset_type)


def _report_pointer_from_snapshot(snapshot: Dict[str, Any]) -> Optional[int]:
    """Return the persisted pointer only when its accepted-turn anchor still holds."""
    record_id = snapshot.get("active_report_record_id")
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id <= 0:
        return None
    if snapshot["session_state_version"] <= 0 or snapshot["last_accepted_user_anchor"] is None:
        return None
    if snapshot["latest_user_anchor"] != snapshot["last_accepted_user_anchor"]:
        return None
    return record_id


def _load_report(
    record_id: int,
    context: Dict[str, Any],
    registry: IndexRegistry,
    lookup: dict,
) -> LoadedReportContext:
    return load_agent_report_context(
        record_id,
        registry=registry,
        lookup=lookup,
        hint_code=context.get("stock_code"),
    )


def _context_report_reference(context: Dict[str, Any]) -> tuple[bool, Optional[int]]:
    """Return ``(explicit, record_id)`` for the tri-state request field."""
    if "report_record_id" not in context:
        return False, None
    value = context.get("report_record_id")
    if value is None:
        return True, None
    return True, parse_report_record_id(value).record_id


def _public_stock(stock: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if stock is None:
        return None
    return {key: stock.get(key) for key in ("stock_code", "stock_name", "canonical_id", "asset_type")}


def _market_key(stock: Dict[str, Any]) -> str:
    canonical = str(stock.get("canonical_id") or "").lower()
    if canonical.startswith("hk"):
        return "HK"
    if canonical.startswith(("sh", "sz", "bj", "csi")):
        return "CN"
    if stock.get("asset_type") == "index":
        return "CN"
    return "US"


def _same_stock(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (left["canonical_id"], left["asset_type"]) == (right["canonical_id"], right["asset_type"])


def _pick_recommended(
    candidates: List[Dict[str, Any]], current: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Prefer an unambiguous listing only when every candidate names the same object."""
    if not candidates:
        return None
    names = {str(item.get("stock_name") or "").strip().casefold() for item in candidates}
    if len(names) != 1:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if current is not None:
        same_market = [item for item in candidates if _market_key(item) == _market_key(current)]
        if len(same_market) == 1:
            return same_market[0]
        same_asset = [item for item in same_market if item["asset_type"] == current["asset_type"]]
        if len(same_asset) == 1:
            return same_asset[0]
    cn_stocks = [
        item for item in candidates
        if item["asset_type"] == "stock" and _market_key(item) == "CN"
    ]
    if len(cn_stocks) == 1:
        return cn_stocks[0]
    stocks = [item for item in candidates if item["asset_type"] == "stock"]
    if len(stocks) == 1:
        return stocks[0]
    return None


def _clarification_payload(
    decision, baseline: Optional[Dict[str, Any]], registry: IndexRegistry, lookup: dict,
    *, preferred: Optional[StockIdentity] = None,
) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for identity in decision.candidates:
        record = _public_stock(_stock_record(identity, lookup, registry))
        key = (record["canonical_id"], record["asset_type"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(record)
    current = _public_stock(baseline)
    recommended = (_public_stock(_stock_record(preferred, lookup, registry))
                   if preferred is not None else _pick_recommended(candidates, current))
    if recommended is not None:
        action = "keep" if current is not None and _same_stock(recommended, current) else "switch"
    elif candidates:
        action = "ask"
    elif current is not None:
        action = "keep"
    else:
        action = "ask"
    if preferred is not None:
        reason = "hint_conflict"
    elif not candidates:
        reason = "missing_object"
    elif len(candidates) == 1:
        reason = "unique_candidate"
    else:
        names = {str(item.get("stock_name") or "").strip().casefold() for item in candidates}
        reason = "ambiguous_name" if len(names) == 1 else "multiple_objects"
    return {
        "reason": reason,
        "action": action,
        "current": current,
        "candidates": candidates,
        "recommended": recommended,
    }


def _active_from_snapshot(snapshot: Dict[str, Any], registry: IndexRegistry, lookup: dict) -> Optional[Dict[str, Any]]:
    if not snapshot["session_generation"]:
        return None
    if snapshot["session_state_version"] > 0:
        stock = snapshot["active_stock_context"]
        if (snapshot["last_accepted_user_anchor"] is None
                or snapshot["latest_user_anchor"] != snapshot["last_accepted_user_anchor"]):
            return None
        if stock is not None and not any(
            message["id"] == stock["source_message_id"] and message["role"] == "user"
            and message["created_at"] == stock["updated_at"] for message in snapshot["messages"]
        ):
            return None
        return dict(stock) if stock else None

    active = None
    for message in snapshot["messages"]:
        if message["role"] != "user":
            continue
        current = _identity(active) if active else None
        decision = decide_chat_object(message["content"], current, registry, lookup)
        if decision.kind == "confirm" and decision.target.kind == "code":
            active = dict(_stock_record(decision.target.identity, lookup, registry),
                          source_message_id=message["id"], updated_at=message["created_at"])
        elif decision.changes_target and (
            decision.target is None or decision.target.identity != current
        ):
            # Historical names cannot confirm a target-position code fact.
            # Negative/question/compare/maintain decisions never invalidate it.
            active = None
    return active


@dataclass(frozen=True)
class ChatSkillSelection:
    """Effective Skill ids and the optional state update for one chat turn."""

    effective_skill_ids: Optional[List[str]]
    selected_skill_ids_update: Optional[List[str]]


@dataclass(frozen=True)
class ChatSessionDetail:
    """Visible messages and the persisted Skill selection for one session."""

    messages: List[Dict[str, Any]]
    selected_skill_ids: Optional[List[str]]
    active_stock_context: Optional[Dict[str, Any]] = None
    active_report_context: Optional[Dict[str, Any]] = None
    session_state_version: int = 0
    session_generation: Optional[str] = None
    is_running: bool = False
    running_progress_steps: Optional[List[Dict[str, Any]]] = None
    running_turn_id: Optional[str] = None
    running_turn_anchor: Optional[int] = None
    running_started_at: Optional[str] = None
    running_heartbeat_at: Optional[str] = None


@dataclass(frozen=True)
class ResolvedChatTurn:
    message: str
    snapshot: Dict[str, Any]
    active_stock_context: Optional[Dict[str, Any]]
    stock_scope: StockScope
    effective_context: Dict[str, Any]
    skill_selection: ChatSkillSelection
    confirm_stock: bool = False
    clarification: Optional[str] = None
    clarification_payload: Optional[Dict[str, Any]] = None
    compatibility_disposition: str = "invalidate"
    report_context: Optional[LoadedReportContext] = None
    active_report_context: Optional[Dict[str, Any]] = None
    active_report_record_id: Optional[int] = None
    stock_source_new: bool = False


class AgentChatSessionService:
    """Coordinate Agent Chat session state without exposing storage to HTTP handlers."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def resolve_skill_selection(
        self,
        config,
        session_id: str,
        requested_skill_ids: Optional[List[str]],
        *, snapshot: Optional[Dict[str, Any]] = None,
    ) -> ChatSkillSelection:
        def inherited():
            return (snapshot["selected_skill_ids"] if snapshot is not None
                    else self.db.get_conversation_session_selected_skill_ids(session_id))
        if requested_skill_ids is None:
            return ChatSkillSelection(
                effective_skill_ids=(
                    inherited()
                ),
                selected_skill_ids_update=None,
            )
        if not requested_skill_ids:
            return ChatSkillSelection(
                effective_skill_ids=[],
                selected_skill_ids_update=[],
            )

        normalized = normalize_requested_skill_ids(config, requested_skill_ids)
        if not normalized:
            return ChatSkillSelection(
                effective_skill_ids=(
                    inherited()
                ),
                selected_skill_ids_update=None,
            )
        return ChatSkillSelection(
            effective_skill_ids=normalized,
            selected_skill_ids_update=normalized,
        )

    def list_sessions(
        self,
        limit: int,
        user_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        return self.db.get_chat_sessions(
            limit=limit,
            session_prefix=user_id,
            extra_session_ids=[user_id] if user_id else None,
        )

    def get_session_detail(
        self,
        session_id: str,
        limit: int,
    ) -> ChatSessionDetail:
        snapshot = self.db.read_chat_session_snapshot(session_id)
        registry, lookup = _stock_lookup()
        active_stock = _active_from_snapshot(snapshot, registry, lookup)
        report_context = self._session_report_context(snapshot, active_stock, registry, lookup)

        return ChatSessionDetail(
            messages=[dict(message, id=str(message["id"])) for message in
                      (snapshot["messages"][:limit] if limit >= 0 else snapshot["messages"])],
            selected_skill_ids=snapshot["selected_skill_ids"],
            active_stock_context=active_stock,
            active_report_context=report_context,
            session_state_version=snapshot["session_state_version"],
            session_generation=snapshot["session_generation"],
            is_running=snapshot.get("is_running", False),
            running_progress_steps=snapshot.get("running_progress_steps", []),
            running_turn_id=snapshot.get("running_turn_id"),
            running_turn_anchor=snapshot.get("running_turn_anchor"),
            running_started_at=snapshot.get("running_started_at"),
            running_heartbeat_at=snapshot.get("running_heartbeat_at"),
        )

    def _session_report_context(
        self,
        snapshot: Dict[str, Any],
        active_stock: Optional[Dict[str, Any]],
        registry: IndexRegistry,
        lookup: dict,
    ) -> Optional[Dict[str, Any]]:
        """Re-validate the persisted pointer without ever reading its content as state."""
        record_id = _report_pointer_from_snapshot(snapshot)
        if record_id is None:
            return None
        loaded = try_load_agent_report_context(record_id, registry=registry, lookup=lookup)
        if loaded is None:
            return unavailable_report_context(record_id)
        if active_stock is not None and not _same_report_identity(
            loaded.identity, _identity(active_stock),
        ):
            return unavailable_report_context(record_id)
        return loaded.public

    def rename_session(self, session_id: str, title: str) -> bool:
        return self.db.rename_conversation_session(session_id, title)

    def prepare_session_turn(
        self, config: Any, session_id: str, message: str,
        requested_skill_ids: Optional[List[str]] = None,
        *, context: Optional[Dict[str, Any]] = None, expected_generation: Optional[str] = None,
    ) -> ResolvedChatTurn:
        generation = self.db.ensure_chat_session_generation(session_id, expected_generation=expected_generation)
        snapshot = self.db.read_chat_session_snapshot(session_id)
        if snapshot["session_generation"] != generation:
            raise ChatSessionStateConflict()
        return self._resolve_session_turn(config, snapshot, message, requested_skill_ids, context=context)

    def _resolve_session_turn(
        self, config: Any, snapshot: Dict[str, Any], message: str,
        requested_skill_ids: Optional[List[str]], *, context: Optional[Dict[str, Any]],
        allow_report_context: bool = True,
    ) -> ResolvedChatTurn:
        skills = self.resolve_skill_selection(config, snapshot["session_id"], requested_skill_ids, snapshot=snapshot)
        registry, lookup = _stock_lookup()
        raw_context = dict(context or {})
        if not allow_report_context:
            raw_context.pop("report_context", None)
            raw_context.pop("report_record_id", None)
        active = _active_from_snapshot(snapshot, registry, lookup)
        baseline = dict(active) if active else None
        decision = decide_chat_object(message, _identity(active) if active else None, registry, lookup)
        compare = decision.kind == "compare"
        confirm = False
        clarification = None
        preferred: Optional[StockIdentity] = None
        if decision.kind == "clarify":
            clarification = "请明确提供本次讨论的股票或指数代码，或确认是否切换。"
        elif decision.kind == "confirm":
            selected = decision.target.identity
            if decision.target.kind != "code" and raw_context.get("stock_code"):
                hint = _hint_identity(raw_context, registry, lookup)
                if hint != selected:
                    clarification = "名称与报告对象不一致，请明确提供完整股票或指数代码。"
                    preferred = selected
            if clarification is None:
                active = _stock_record(selected, lookup, registry)
                confirm = True

        loaded_report: Optional[LoadedReportContext] = None
        stock_source_new = False
        if allow_report_context:
            explicit_report, explicit_record_id = _context_report_reference(raw_context)
            report_record_id = explicit_record_id if explicit_report else _report_pointer_from_snapshot(snapshot)
            if (not explicit_report and report_record_id is not None
                    and clarification is None and baseline is not None and active is not None
                    and not _same_report_identity(_identity(baseline), _identity(active))):
                # An explicit switch drops the previous report before any read.
                report_record_id = None
            if report_record_id is not None and (clarification is None or explicit_report):
                loaded_report = _load_report(report_record_id, raw_context, registry, lookup)
                if clarification is not None:
                    # Validate the client's explicit record, but a clarification
                    # turn must not establish or carry report material.
                    loaded_report = None
                elif active is not None and not _same_report_identity(
                    loaded_report.identity, _identity(active),
                ):
                    if explicit_report:
                        raise AgentReportContextError(
                            "报告与本次讨论对象不一致，请从原报告重新发起追问。",
                        )
                    loaded_report = None
                elif active is None and decision.kind in ("maintain", "compare"):
                    if (decision.kind == "compare"
                            and loaded_report.identity not in decision.participants):
                        loaded_report = None
                    else:
                        active = _stock_record(loaded_report.identity, lookup, registry)
                        stock_source_new = True

        clarification_payload = None
        if clarification is not None:
            active = baseline
            confirm = False
            loaded_report = None
            stock_source_new = False
            clarification_payload = _clarification_payload(
                decision, baseline, registry, lookup, preferred=preferred,
            )
            from src.report_language import normalize_report_language

            if normalize_report_language(raw_context.get("report_language") or getattr(config, "report_language", "zh")) == "en":
                clarification = "The discussion target is not confirmed. Please provide the complete stock or index code."
        allowed = []
        if active is not None:
            allowed.append(_identity(active))
        if compare and clarification is None:
            allowed.extend(item for item in decision.participants if item not in allowed)
        scope = StockScope(expected_stock_code=active["stock_code"] if active else "",
                           allowed_stock_codes={item.stock_code for item in allowed},
                           mode="compare" if compare else "switch" if confirm else "maintain",
                           strict=True, identities=tuple(allowed))
        effective_context = dict(raw_context)
        effective_context.pop("allowed_stock_codes", None)
        effective_context.pop("skills", None)
        effective_context.pop("strategies", None)
        effective_context.pop("report_record_id", None)
        hint_matches = False
        if active and effective_context.get("stock_code"):
            hint_matches = _hint_identity(effective_context, registry, lookup) == _identity(active)
        if not hint_matches or loaded_report is not None:
            for key in SWITCH_CLEANUP_KEYS:
                effective_context.pop(key, None)
        for key in ["stock_code", "stock_name", "canonical_id", "asset_type"]:
            effective_context.pop(key, None)
        if active:
            effective_context.update({key: active[key] for key in ["stock_code", "stock_name", "canonical_id", "asset_type"]})
        if loaded_report is not None:
            # One validated projection replaces every legacy client hint so a
            # report follow-up cannot mix sources for the same facts.
            effective_context["report_context"] = loaded_report.payload
        if skills.effective_skill_ids is not None:
            effective_context["skills"] = list(skills.effective_skill_ids)
        preserves = (clarification is None and baseline is not None and active is not None
                     and _identity(active) == _identity(baseline))
        return ResolvedChatTurn(
            message, snapshot, active, scope, effective_context, skills, confirm, clarification,
            clarification_payload, "preserve" if preserves else "invalidate",
            report_context=loaded_report,
            active_report_context=loaded_report.public if loaded_report is not None else None,
            active_report_record_id=loaded_report.record_id if loaded_report is not None else None,
            stock_source_new=stock_source_new,
        )

    def prepare_compatibility_turn(
        self, config: Any, snapshot: Dict[str, Any], message: str,
        *, context: Optional[Dict[str, Any]] = None,
    ) -> tuple[StockScopeResolution, str]:
        """Resolve a current Bot turn from its captured snapshot, without stock-write authority.

        Fresh/old version-zero sessions keep their existing single-turn context
        behavior. Positive-version sessions consume the authoritative decision
        core; only a safe maintain/compare may advance the trusted user anchor.
        """
        if snapshot["session_state_version"] == 0:
            return resolve_stock_scope(message, context), "invalidate"
        turn = self._resolve_session_turn(
            config, snapshot, message, None, context=context, allow_report_context=False,
        )
        if turn.clarification is not None:
            # Bot can still answer, but an unconfirmed target cannot grant tools
            # access to the previous object or carry its cached analysis facts.
            effective = dict(turn.effective_context)
            for key in SWITCH_CLEANUP_KEYS | {"stock_code", "canonical_id", "asset_type"}:
                effective.pop(key, None)
            return StockScopeResolution(effective, StockScope(strict=True)), "invalidate"
        return StockScopeResolution(turn.effective_context, turn.stock_scope), turn.compatibility_disposition

    def commit_user_turn(self, turn: ResolvedChatTurn) -> Dict[str, Any]:
        accepted = self.db.commit_chat_user_turn(
            turn.snapshot, turn.message, selected_skill_ids=turn.skill_selection.selected_skill_ids_update,
            active_stock_context=turn.active_stock_context, confirm_stock=turn.confirm_stock,
            active_stock_source_new=turn.stock_source_new,
            report_reference_update=turn.active_report_record_id,
        )
        accepted["active_report_context"] = turn.active_report_context
        return accepted

    def delete_session(self, session_id: str) -> int:
        return self.db.delete_conversation_session(session_id)
