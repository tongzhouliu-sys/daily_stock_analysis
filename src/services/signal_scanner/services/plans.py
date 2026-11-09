from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.services.signal_scanner.core import dec, digest, instant, now, require, stamp, text_decimal, uid
from src.services.signal_scanner.domain.risk import calculate_risk
from src.services.signal_scanner.services.accounts import account, snapshot
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.storage.db import audit, insert, many, one, update


def card(cx, cid, revision=None):
    raw = one(
        cx,
        "SELECT * FROM signal_cards WHERE card_id=?"
        + (" AND revision=?" if revision else "")
        + " ORDER BY revision DESC LIMIT 1",
        (cid, revision) if revision else (cid,),
    )
    require(raw, "RESOURCE_NOT_FOUND", status=404)
    instrument = one(cx, "SELECT * FROM instruments WHERE id=?", (raw["instrument_id"],))
    primary = next(r for r in raw["rule_refs_json"] if r["event_id"] == raw["primary_rule_ref"])
    main = one(
        cx,
        "SELECT * FROM rule_events WHERE event_id=? AND revision=?",
        (primary["event_id"], primary["revision"]),
    )
    value = {k: v for k, v in raw.items() if not k.endswith("_json")}
    for key in ("rule_refs", "evidence_groups", "reason_codes", "score_trace", "context"):
        value[key] = raw[key + "_json"]
    value.update(
        id=raw["card_id"],
        instrument=dict(
            id=instrument["id"],
            ticker=instrument["current_ticker"],
            name=instrument["display_name"],
            type=instrument["asset_type"],
            currency=instrument["currency"],
        ),
        primary_rule=main["rule_id"],
        rule_stage=main["stage"],
        kind=main["kind"],
        trigger_values=main["evidence_json"],
        frozen_levels=main["frozen_json"],
        confirmed_at=main["confirmed_at"],
        confirmation_market_date=main["confirmation_market_date"],
        bear_cases=["量价形态并非机构行为证明", "跳空与流动性可能使实际损失超预算"],
        base_rate=dict(status="INSUFFICIENT", sample_size=0, reason="RULE_NOT_VALIDATED"),
    )
    value.update(
        {
            k: raw["context_json"].get(k)
            for k in (
                "entry_anchor",
                "atr_ref",
                "adjustment_epoch",
                "price_basis",
                "distance_to_entry_pct",
                "distance_to_entry_atr",
                "price_context",
            )
        }
    )
    anchor = value["entry_anchor"]
    atr = value["atr_ref"]
    from decimal import ROUND_FLOOR

    tick = Decimal(instrument["price_tick"])
    value["entry_range"] = (
        [
            str(anchor),
            str(
                ((Decimal(str(anchor)) + Decimal(str(atr)) / 2) / tick).to_integral_value(
                    rounding=ROUND_FLOOR
                )
                * tick
            ),
        ]
        if anchor is not None and atr is not None
        else None
    )
    if anchor is not None and atr is not None:
        from decimal import ROUND_FLOOR

        value["suggested_entry"] = str(
            (Decimal(str(anchor)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
        )
        value["suggested_stop"] = str(
            ((Decimal(str(anchor)) - 2 * Decimal(str(atr))) / tick).to_integral_value(rounding=ROUND_FLOOR)
            * tick
        )
    if value["valid_until"] <= stamp() and value["lifecycle"] in ("PENDING", "CONFIRMED"):
        value["availability"] = "BLOCKED"
        value["reason_codes"] += ["SIGNAL_EXPIRED"]
    return value


def preflight(cx, day):
    r = one(
        cx,
        "SELECT * FROM decision_sessions WHERE kind='PREFLIGHT' AND actor_id='owner' AND action_market_date=?",
        (day,),
    )
    if not r:
        return dict(
            status="INCOMPLETE", events_seen=False, risk_seen=False, mission="", action_market_date=day
        )
    return dict(id=r["id"], revision=r["row_version"], status=r["status"], **r["ack_json"])


def event_hash(cx):
    return digest(
        many(
            cx, "SELECT event_id,revision,content_hash FROM market_event_revisions ORDER BY event_id,revision"
        )
    )


def set_preflight(cx, p):
    require(
        p.event_hash == event_hash(cx) and p.risk_version == get_config(cx, "risk")["version"],
        "REVISION_CONFLICT",
    )
    old = one(
        cx,
        "SELECT * FROM decision_sessions WHERE kind='PREFLIGHT' AND actor_id='owner' AND action_market_date=?",
        (p.action_market_date,),
    )
    ack = p.model_dump()
    status = "COMPLETE" if p.events_seen and p.risk_seen else "INCOMPLETE"
    if old:
        update(
            cx,
            "decision_sessions",
            "id",
            old["id"],
            dict(ack_json=ack, status=status, row_version=old["row_version"] + 1),
        )
    else:
        insert(
            cx,
            "decision_sessions",
            dict(
                id=uid("preflight"),
                kind="PREFLIGHT",
                actor_id="owner",
                action_market_date=p.action_market_date,
                opened_at=stamp(),
                current_step="COMPLETE",
                ack_json=ack,
                status=status,
            ),
        )
    return preflight(cx, p.action_market_date)


def intent_hash(p, card_id, action_date, structure_basis=None):
    return digest(
        dict(
            account_id=p["account_id"],
            entry=text_decimal(dec(p["entry"])),
            stop=text_decimal(dec(p["stop"])),
            structure_low=p.get("structure_low"),
            card_id=card_id,
            action_market_date=action_date,
            structure_basis=structure_basis,
        )
    )


def open_stop(cx, p):
    c = card(cx, p.card_id)
    require(c["revision"] == p.card_revision, "REVISION_CONFLICT")
    require(
        account(cx, p.plan_intent.account_id)["origin"] != "SHADOW_EXPERIMENT", "OUT_OF_RANGE", status=422
    )
    key = intent_hash(p.plan_intent.model_dump(), p.card_id, p.action_market_date, digest(c["frozen_levels"]))
    old = one(
        cx,
        "SELECT * FROM decision_sessions WHERE actor_id='owner' AND card_id=? AND plan_intent_hash=? AND kind='STOP'",
        (p.card_id, key),
    )
    if not old:
        sid = uid("stop")
        insert(
            cx,
            "decision_sessions",
            dict(
                id=sid,
                kind="STOP",
                actor_id="owner",
                action_market_date=p.action_market_date,
                card_id=p.card_id,
                plan_intent_hash=key,
                opened_at=stamp(),
                cooling_until=stamp(now() + timedelta(minutes=30)),
                current_step="S",
                evidence_hash=c["evidence_hash"],
                ack_json=dict(intent=p.plan_intent.model_dump(), card_revision=p.card_revision),
                status="OPENED",
            ),
        )
        old = one(cx, "SELECT * FROM decision_sessions WHERE id=?", (sid,))
    if old["evidence_hash"] != c["evidence_hash"]:
        ack = old["ack_json"]
        ack.update(read_ack=False, card_revision=c["revision"])
        ack.pop("O", None)
        ack.pop("P", None)
        update(
            cx,
            "decision_sessions",
            "id",
            old["id"],
            dict(
                evidence_hash=c["evidence_hash"],
                ack_json=ack,
                current_step="O",
                status="STALE",
                row_version=old["row_version"] + 1,
            ),
        )
        old = {**old, "current_step": "O", "status": "STALE", "row_version": old["row_version"] + 1}
    if old["status"] == "STALE" and "intent" not in old["ack_json"]:
        ack = dict(intent=p.plan_intent.model_dump(), card_revision=p.card_revision)
        opened = stamp()
        cooling = stamp(now() + timedelta(minutes=30))
        update(
            cx,
            "decision_sessions",
            "id",
            old["id"],
            dict(
                ack_json=ack,
                current_step="S",
                status="OPENED",
                opened_at=opened,
                cooling_until=cooling,
                row_version=old["row_version"] + 1,
            ),
        )
        old = {
            **old,
            "current_step": "S",
            "status": "OPENED",
            "opened_at": opened,
            "cooling_until": cooling,
            "row_version": old["row_version"] + 1,
        }
    return dict(
        id=old["id"],
        revision=old["row_version"],
        step=old["current_step"],
        opened_at=old["opened_at"],
        cooling_until=old["cooling_until"],
        status=old["status"],
    )


def stop_step(cx, sid, p):
    s = one(cx, "SELECT * FROM decision_sessions WHERE id=? AND kind='STOP'", (sid,))
    require(s, "RESOURCE_NOT_FOUND", status=404)
    require(s["row_version"] == p.expected_revision, "REVISION_CONFLICT")
    c = card(cx, s["card_id"])
    require(c["evidence_hash"] == s["evidence_hash"], "REVISION_CONFLICT", "反方证据变化，需要重新打开确认")
    require(p.step == s["current_step"], "STOP_REQUIRED", "步骤次序不匹配")
    ack = s["ack_json"]
    status = "OPENED"
    if p.step == "S":
        require(p.acknowledged is True, "STOP_REQUIRED")
        nxt = "T"
    elif p.step == "T":
        require(p.emotion is not None, "STOP_REQUIRED")
        ack["emotion"] = p.emotion
        nxt = "O"
    elif p.step == "O":
        require(p.read_ack is True and p.evidence_hash == s["evidence_hash"], "REVISION_CONFLICT")
        ack["read_ack"] = True
        nxt = "P"
    else:
        require(
            all(isinstance(x, str) and x.strip() for x in (p.reason, p.expectation, p.invalidation)),
            "STOP_REQUIRED",
        )
        ack.update(reason=p.reason, expectation=p.expectation, invalidation=p.invalidation)
        nxt = "READY"
        status = "READY"
    ack[p.step] = True
    update(
        cx,
        "decision_sessions",
        "id",
        sid,
        dict(current_step=nxt, ack_json=ack, status=status, row_version=s["row_version"] + 1),
    )
    return dict(
        id=sid, revision=s["row_version"] + 1, next_step=nxt, status=status, cooling_until=s["cooling_until"]
    )


def decision(cx, p, key=None):
    c = card(cx, p.card_id, p.card_revision)
    insert(
        cx,
        "decisions",
        dict(
            id=(did := uid("decision")),
            actor_id="owner",
            card_id=p.card_id,
            card_revision=p.card_revision,
            action_market_date=p.action_market_date,
            action=p.action,
            reason=p.reason,
            expectation=p.expectation,
            invalidation=p.invalidation,
            emotion=p.emotion,
            evidence_hash=c["evidence_hash"],
            server_time=stamp(),
            idempotency_key=key,
        ),
    )
    return dict(id=did, action=p.action, recorded=True)


def risk_preview(cx, p, cal, files, exclude_plan=None):
    c = card(cx, p.card_id)
    require(c["revision"] == p.card_revision, "REVISION_CONFLICT")
    a, s = snapshot(cx, p.account_id)
    policy = get_config(cx, "risk")
    reasons = []
    require(a["origin"] != "SHADOW_EXPERIMENT", "OUT_OF_RANGE", "不能用用户计划修改研究账户", 422)
    from src.services.signal_scanner.worker.pipeline import latest_workspace, load_events, source_failure

    latest, workspace = latest_workspace(cx, files)
    if workspace and c["instrument_id"] in workspace.get("failures", {}):
        reasons.append("DATA_INCOMPLETE")
    if source_failure(cx):
        reasons.append("LATEST_SOURCE_CHECK_FAILED")
    if s["completeness"] != "COMPLETE" or a["confirmed_ledger_head"] != s["ledger_head_ref"]:
        reasons.append("ACCOUNT_INCOMPLETE")
    if s["risk_market_date"] != cal.risk_date(now()) or s["risk_month"] != cal.risk_date(now())[:7]:
        reasons.append("ACCOUNT_SNAPSHOT_STALE")
    if a["locks_json"]:
        reasons.append("RISK_LOCKED")
    if c["availability"] != "PLAN_ALLOWED":
        reasons += c["reason_codes"] or ["SIGNAL_INVALID"]
    if c["valid_until"] <= stamp():
        reasons.append("SIGNAL_EXPIRED")
    if c["rule_stage"] != "APPROVED" or c["view_kind"] != "PRODUCTION" or c["direction"] != "LONG":
        reasons.append("RULE_NOT_APPROVED")
    if c["context"].get("event_status") != "CLEAR":
        reasons.append("EVENT_BLOCKED")
    if not c["context"].get("status_verified_at") or now() - instant(
        c["context"].get("status_verified_at", stamp())
    ) > timedelta(minutes=60):
        reasons.append("STATUS_UNVERIFIED")
    context = cal.context(now())
    if context["session_status"] == "RTH":
        reasons.append("REALTIME_QUOTE_NOT_CONFIGURED")
    if c["context"].get(
        "source_market_date", c["context"].get("features", {}).get("source_market_date")
    ) != cal.last_completed(now()):
        reasons.append("DATA_STALE")
    require(
        p.action_market_date == cal.action_date(now()), "OUT_OF_RANGE", "目标会话必须为当前可准备会话", 422
    )
    for e in load_events(cx, stamp()):
        if (
            e["event_type"] in ("FOMC", "CPI", "EMPLOYMENT")
            and e.get("scheduled_at")
            and abs((now() - instant(e["scheduled_at"])).total_seconds()) <= 1800
        ):
            reasons.append("EVENT_BLOCKED")
    inst = one(cx, "SELECT * FROM instruments WHERE id=?", (c["instrument_id"],))
    sector = inst["sector_code"] or inst["profile_json"].get("concentration_bucket")
    if sector is None:
        reasons.append("SECTOR_UNKNOWN")
    pending = many(
        cx,
        "SELECT * FROM plans WHERE account_id=? AND status IN ('RESERVED','PARTIALLY_FILLED') AND id!=?",
        (p.account_id, exclude_plan or ""),
    )
    heat = Decimal(0)
    symbol = Decimal(0)
    industry = Decimal(0)
    for pos in s["positions_json"]:
        if pos.get("mark") is None or pos.get("mark_as_of") is None:
            reasons.append("POSITION_PRICE_MISSING")
            continue
        price, qty = dec(pos["mark"]), dec(pos["qty"])
        stop = pos.get("stop_reference")
        if qty <= 0 or not stop or price <= dec(stop) or pos["mark_as_of"][:10] < cal.last_completed(now()):
            reasons.append("STOP_BREACHED_OR_UNMEASURABLE")
        else:
            heat += (
                (price - dec(stop)) * qty
                + price * qty * dec(policy["value"]["slippage_bps"]) / 10000
                + dec(policy["value"]["fixed_exit_fee"])
                + qty * dec(policy["value"]["per_share_fee"])
            )
        if pos["instrument_id"] == inst["id"]:
            symbol += price * qty
        if (pos.get("sector_code") or pos.get("concentration_bucket")) == sector:
            industry += price * qty
    hp = cp = Decimal(0)
    for plan in pending:
        hp += dec(plan["risk_reserved"])
        cp += dec(plan["cash_reserved"])
        if plan["instrument_id"] == inst["id"]:
            symbol += dec(plan["symbol_notional_reserved"])
        pi = one(cx, "SELECT * FROM instruments WHERE id=?", (plan["instrument_id"],))
        if (pi["sector_code"] or pi["profile_json"].get("concentration_bucket")) == sector:
            industry += dec(plan["sector_notional_reserved"])
    if a["scope"] == "PAPER" and any(
        x["instrument_id"] == inst["id"] and dec(x["qty"]) > 0 for x in s["positions_json"]
    ):
        reasons.append("POSITION_ALREADY_OPEN")
    entry, stop = dec(p.entry), dec(p.stop)
    limits = c.get("entry_range")
    if limits and not dec(limits[0]) <= entry <= dec(limits[1]):
        reasons.append("ENTRY_OUTSIDE_FROZEN_RANGE")
    atr = c.get("atr_ref")
    if p.structure_low is not None and atr is not None:
        stop = min(stop, entry - 2 * Decimal(str(atr)), dec(p.structure_low) - Decimal(str(atr)))
    inp = dict(
        equity=s["equity"],
        settled_cash=s["cash_settled"],
        day_open_equity=s["day_open_equity"],
        month_open_equity=s["month_open_equity"],
        pnl_day=s["pnl_day"],
        pnl_month=s["pnl_month"],
        entry=p.entry,
        stop=text_decimal(stop),
        tick=inst["price_tick"],
        heat_open=text_decimal(heat),
        heat_pending=text_decimal(hp),
        cash_pending=text_decimal(cp),
        symbol_used=text_decimal(symbol),
        sector_used=text_decimal(industry),
        reasons=reasons,
        environment=c["context"].get("environment_mode", "UNKNOWN"),
        recovery_remaining=a["recovery_remaining"],
        max_qty=int(p.max_qty) if p.max_qty and p.max_qty.isdigit() else None,
    )
    if p.max_qty is not None:
        require(p.max_qty.isdigit(), "OUT_OF_RANGE", status=422)
    result = calculate_risk(inp, policy["value"])
    result.update(
        account_id=p.account_id,
        account_snapshot_id=s["id"],
        card_revision=c["revision"],
        policy_version=policy["version"],
        action_market_date=p.action_market_date,
        quote_context=c["price_context"],
        input_price_basis="RAW",
        expires_at=c["valid_until"],
    )
    return result


def draft(cx, p):
    require(account(cx, p.account_id)["origin"] != "SHADOW_EXPERIMENT", "OUT_OF_RANGE", status=422)
    c = card(cx, p.card_id)
    require(c["revision"] == p.card_revision, "REVISION_CONFLICT")
    require(0 < dec(p.stop) < dec(p.entry), "INVALID_PRICE_RELATION", status=422)
    require(p.max_qty is None or p.max_qty.isdigit(), "OUT_OF_RANGE", status=422)
    i = one(cx, "SELECT * FROM instruments WHERE id=?", (c["instrument_id"],))
    aid = account(cx, p.account_id)
    anchor = c.get("entry_anchor")
    pid = uid("plan")
    insert(
        cx,
        "plans",
        dict(
            id=pid,
            account_id=p.account_id,
            origin=aid["origin"],
            card_id=p.card_id,
            card_revision=p.card_revision,
            ruleset_hash=get_config(cx, "rules")["version"],
            instrument_id=c["instrument_id"],
            action_market_date=p.action_market_date,
            intended_entry_date=p.action_market_date,
            valid_until=c["valid_until"],
            currency="USD",
            entry_anchor=str(anchor) if anchor is not None else None,
            entry_min=str(anchor) if anchor else None,
            entry_max=c["entry_range"][1] if c.get("entry_range") else None,
            entry_ref=p.entry,
            stop_ref=p.stop,
            structure_low=p.structure_low,
            price_basis="RAW",
            adjustment_epoch=c.get("adjustment_epoch"),
            price_tick=i["price_tick"],
            status="DRAFT",
            max_qty=int(p.max_qty) if p.max_qty is not None else None,
        ),
    )
    return dict(id=pid, revision=1, status="DRAFT", risk_reserved="0")


def get_plan(cx, pid):
    p = one(cx, "SELECT * FROM plans WHERE id=?", (pid,))
    require(p, "RESOURCE_NOT_FOUND", status=404)
    return p


def reserve(cx, pid, p, cal, files, key):
    from src.services.signal_scanner.api.models import RiskPreview

    plan = get_plan(cx, pid)
    require(plan["row_version"] == p.expected_revision, "REVISION_CONFLICT")
    require(plan["status"] in ("DRAFT", "RESERVED"), "SIGNAL_INVALID")
    c = card(cx, plan["card_id"])
    require(c["revision"] == p.card_revision == plan["card_revision"], "REVISION_CONFLICT")
    a, s = snapshot(cx, plan["account_id"])
    require(s["id"] == p.account_snapshot_id, "REVISION_CONFLICT")
    require(get_config(cx, "risk")["version"] == p.risk_profile_version, "REVISION_CONFLICT")
    pre = preflight(cx, plan["action_market_date"])
    require(pre.get("id") == p.preflight_id and pre["status"] == "COMPLETE", "PREFLIGHT_REQUIRED")
    require(
        pre["event_hash"] == event_hash(cx) and pre["risk_version"] == p.risk_profile_version,
        "PREFLIGHT_REQUIRED",
    )
    st = one(cx, "SELECT * FROM decision_sessions WHERE id=? AND kind='STOP'", (p.stop_session_id,))
    require(st, "STOP_REQUIRED")
    intent = dict(
        account_id=plan["account_id"],
        entry=plan["entry_ref"],
        stop=plan["stop_ref"],
        structure_low=plan["structure_low"],
    )
    require(
        st["plan_intent_hash"]
        == intent_hash(intent, plan["card_id"], plan["action_market_date"], digest(c["frozen_levels"]))
        and st["current_step"] == "READY"
        and st["evidence_hash"] == c["evidence_hash"],
        "STOP_REQUIRED",
    )
    require(st["cooling_until"] <= stamp(), "COOLING", available_after=st["cooling_until"])
    daily = a["daily_plan_keys_json"]
    used = daily.get(plan["action_market_date"], [])
    isnew = plan["first_reserved_date"] is None
    require(not isnew or len(used) < 5, "RISK_LOCKED", "当日新方向计划已达5个")
    r = risk_preview(
        cx,
        RiskPreview(
            **intent,
            card_id=plan["card_id"],
            card_revision=plan["card_revision"],
            action_market_date=plan["action_market_date"],
            max_qty=str(plan["max_qty"]) if plan["max_qty"] is not None else None,
        ),
        cal,
        files,
        exclude_plan=pid,
    )
    require(
        r["status"] == "CALCULATED",
        "BUDGET_EXCEEDED"
        if r["status"] == "CALCULATED_NO_CAPACITY"
        else (r.get("all_reasons") or ["RISK_LOCKED"])[0],
        details=r,
    )
    q = int(r["qty"])
    notional = text_decimal(dec(r["entry"]) * q)
    try:
        update(
            cx,
            "plans",
            "id",
            pid,
            dict(
                status="RESERVED",
                qty_reserved=q,
                qty_remaining=q,
                risk_reserved=r["planned_risk"],
                cash_reserved=r["cash_required"],
                symbol_notional_reserved=notional,
                sector_notional_reserved=notional,
                reserved_at=stamp(),
                stop_ref=r["stop"],
                account_snapshot_id=s["id"],
                preflight_id=p.preflight_id,
                stop_session_id=p.stop_session_id,
                risk_profile_version=p.risk_profile_version,
                risk_trace_json=r,
                quote_context_json=c["price_context"],
                first_reserved_date=plan["first_reserved_date"] or plan["action_market_date"],
                row_version=plan["row_version"] + 1,
            ),
        )
    except Exception as ex:
        if "UNIQUE" in str(ex):
            require(False, "PLAN_ALREADY_RESERVED")
        raise
    if isnew:
        used.append(pid)
        daily[plan["action_market_date"]] = used
        update(
            cx,
            "account_state",
            "account_id",
            a["account_id"],
            dict(
                daily_plan_keys_json=daily,
                recovery_remaining=max(0, a["recovery_remaining"] - 1),
                row_version=a["row_version"] + 1,
            ),
        )
    ack = st["ack_json"]
    insert(
        cx,
        "decisions",
        dict(
            id=uid("decision"),
            actor_id="owner",
            card_id=c["id"],
            card_revision=c["revision"],
            action_market_date=plan["action_market_date"],
            action="PLAN_SAVED",
            reason=ack["reason"],
            expectation=ack["expectation"],
            invalidation=ack["invalidation"],
            emotion=ack["emotion"],
            stop_session_id=st["id"],
            evidence_hash=c["evidence_hash"],
            plan_id=pid,
            server_time=stamp(),
            idempotency_key=key,
        ),
    )
    audit(cx, "PLAN_RESERVED", pid, "正式保留；未执行订单", risk=r["planned_risk"])
    return dict(
        id=pid,
        revision=plan["row_version"] + 1,
        status="RESERVED",
        qty_reserved=str(q),
        risk_reserved=r["planned_risk"],
    )


def cancel(cx, pid, p, state="CANCELLED"):
    plan = get_plan(cx, pid)
    require(plan["row_version"] == p.expected_revision, "REVISION_CONFLICT")
    update(
        cx,
        "plans",
        "id",
        pid,
        dict(
            status=state,
            qty_remaining=0,
            risk_reserved="0",
            cash_reserved="0",
            symbol_notional_reserved="0",
            sector_notional_reserved="0",
            row_version=plan["row_version"] + 1,
        ),
    )
    audit(cx, "PLAN_" + state, pid, p.reason)
    return dict(id=pid, revision=plan["row_version"] + 1, status=state, risk_reserved="0", cash_reserved="0")


def patch(cx, pid, p):
    plan = get_plan(cx, pid)
    require(plan["row_version"] == p.expected_revision, "REVISION_CONFLICT")
    require(plan["status"] == "DRAFT", "STOP_REQUIRED", "已保留计划需取消后按新意图重建")
    entry = p.entry if p.entry is not None else plan["entry_ref"]
    stop = p.stop if p.stop is not None else plan["stop_ref"]
    require(0 < dec(stop) < dec(entry), "INVALID_PRICE_RELATION", status=422)
    require(p.max_qty is None or p.max_qty.isdigit(), "OUT_OF_RANGE", status=422)
    changed = (
        entry != plan["entry_ref"]
        or stop != plan["stop_ref"]
        or (p.structure_low is not None and p.structure_low != plan["structure_low"])
    )
    update(
        cx,
        "plans",
        "id",
        pid,
        dict(
            entry_ref=entry,
            stop_ref=stop,
            structure_low=p.structure_low if p.structure_low is not None else plan["structure_low"],
            max_qty=int(p.max_qty) if p.max_qty is not None else plan["max_qty"],
            user_notes=p.note if p.note is not None else plan["user_notes"],
            row_version=plan["row_version"] + 1,
        ),
    )
    return dict(id=pid, revision=plan["row_version"] + 1, status="DRAFT", requires_stop_refresh=changed)
