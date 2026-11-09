from __future__ import annotations

from decimal import Decimal

from src.services.signal_scanner.core import dec, digest, instant, now, require, stamp, text_decimal, uid
from src.services.signal_scanner.storage.db import audit, insert, many, one, update


def account(cx, aid):
    a = one(cx, "SELECT * FROM account_state WHERE account_id=? AND status=?", (aid, "ACTIVE"))
    require(a, "RESOURCE_NOT_FOUND", status=404)
    return a


def snapshot(cx, aid):
    a = account(cx, aid)
    s = one(cx, "SELECT * FROM account_snapshots WHERE id=?", (a["current_snapshot_id"],))
    require(s, "ACCOUNT_INCOMPLETE")
    return a, s


def create_account(cx, payload, cal):
    require(
        (payload.scope == "PAPER" and payload.origin == "USER_PLAN")
        or (payload.scope == "MANUAL" and payload.origin == "MANUAL"),
        "OUT_OF_RANGE",
        status=422,
    )
    cash = dec(payload.initial_cash, nonnegative=True)
    require(cash > 0, "OUT_OF_RANGE", status=422)
    require(cal.session(payload.start_market_date), "VALIDATION_ERROR", "起始日期必须为交易会话", 422)
    at = instant(payload.as_of) if payload.as_of else now()
    require(at <= now(), "VALIDATION_ERROR", status=422)
    positions = []
    for p in payload.initial_positions:
        require(
            set(p) <= {"instrument_id", "qty", "avg_price", "mark", "stop_reference"},
            "VALIDATION_ERROR",
            status=422,
        )
        require(
            one(cx, "SELECT id FROM instruments WHERE id=?", (p.get("instrument_id"),)),
            "DATA_INCOMPLETE",
            status=422,
        )
        for k in ("qty", "avg_price", "mark"):
            require(k in p and dec(p[k]) > 0, "VALIDATION_ERROR", status=422)
        positions.append(p)
    if payload.scope == "PAPER":
        require(not positions, "OUT_OF_RANGE", "纸面新账户从现金开始", 422)
    aid = uid("acc")
    riskdate = cal.risk_date(at)
    profile = dict(
        name=payload.name,
        initial_cash=text_decimal(cash),
        initial_positions=positions,
        as_of=stamp(at),
        start_market_date=payload.start_market_date,
        initial_risk_date=riskdate,
        initial_month=riskdate[:7],
        day_open_equity=payload.day_open_equity if payload.scope == "MANUAL" else text_decimal(cash),
        month_open_equity=payload.month_open_equity if payload.scope == "MANUAL" else text_decimal(cash),
        funds_complete=payload.funds_complete if payload.scope == "MANUAL" else True,
    )
    for k in ("day_open_equity", "month_open_equity"):
        if profile[k] is not None:
            require(dec(profile[k]) > 0, "OUT_OF_RANGE", status=422)
    insert(
        cx,
        "account_state",
        dict(
            account_id=aid, scope=payload.scope, origin=payload.origin, currency="USD", profile_json=profile
        ),
    )
    s = rebuild(cx, aid, cal)
    audit(
        cx, "ACCOUNT_INITIALIZED", aid, "模拟初始化" if payload.scope == "PAPER" else "用户自报；完整性待核对"
    )
    return dict(id=aid, scope=payload.scope, origin=payload.origin, status="INITIALIZED", snapshot=s)


def facts(cx, aid, at, knowledge_cutoff=None):
    entries = many(
        cx,
        "SELECT * FROM ledger_entries WHERE account_id=? AND occurred_at<=? AND created_at<=? ORDER BY occurred_at,id",
        (aid, stamp(at), stamp(knowledge_cutoff or now())),
    )
    reversed_ids = {e["reverses_entry_id"] for e in entries if e["entry_type"] == "REVERSAL"}
    return [e for e in entries if e["entry_type"] != "REVERSAL" and e["id"] not in reversed_ids]


def derive(cx, a, at, knowledge_cutoff=None):
    profile = a["profile_json"]
    entries = facts(cx, a["account_id"], at, knowledge_cutoff)
    cash = dec(profile["initial_cash"])
    unsettled = Decimal(0)
    positions = {}
    episodes = []
    errors = []
    flows = []
    for p in profile.get("initial_positions", []):
        q = dec(p["qty"])
        positions[p["instrument_id"]] = dict(
            qty=q,
            lots=[dict(qty=q, unit=dec(p["avg_price"]), fee=Decimal(0))],
            episode_id="opening_" + p["instrument_id"],
            episode_pnl=Decimal(0),
            stop_reference=p.get("stop_reference"),
            mark=p["mark"],
        )
    for e in entries:
        typ = e["entry_type"]
        fee = dec(e["fee"])
        amount = dec(e["cash_amount"])
        iid = e["instrument_id"]
        if e["occurred_at"] < profile["as_of"]:
            errors.append("FACT_BEFORE_OPENING_BASELINE")
        if typ == "SELL" and (not e["settlement_date"] or e["settlement_date"] > stamp(at)[:10]):
            unsettled += amount
        else:
            cash += amount
        if typ in ("DEPOSIT", "WITHDRAWAL"):
            flows.append((e["occurred_at"], amount))
        if typ in ("BUY", "SELL", "SPLIT", "DIVIDEND") and iid:
            p = positions.setdefault(
                iid,
                dict(qty=Decimal(0), lots=[], episode_id=None, episode_pnl=Decimal(0), stop_reference=None),
            )
            if typ == "BUY":
                q = dec(e["qty"])
                price = dec(e["price"])
                if p["qty"] == 0:
                    p.update(episode_id="episode_" + e["id"], episode_pnl=Decimal(0))
                p["lots"].append(dict(qty=q, unit=price, fee=fee))
                p["qty"] += q
                if e["payload_json"].get("stop_reference"):
                    p["stop_reference"] = e["payload_json"]["stop_reference"]
            elif typ == "SELL":
                remaining = dec(e["qty"])
                sell_qty = remaining
                cost = Decimal(0)
                buyfees = Decimal(0)
                if remaining > p["qty"]:
                    errors.append("UNSUPPORTED_POSITION")
                while remaining > 0 and p["lots"]:
                    lot = p["lots"][0]
                    taken = min(remaining, lot["qty"])
                    fraction = taken / lot["qty"]
                    cost += taken * lot["unit"]
                    allocated = lot["fee"] * fraction
                    buyfees += allocated
                    lot["qty"] -= taken
                    lot["fee"] -= allocated
                    remaining -= taken
                    if lot["qty"] == 0:
                        p["lots"].pop(0)
                p["qty"] -= sell_qty
                p["episode_pnl"] += amount - cost - buyfees
                if p["qty"] == 0:
                    episodes.append(
                        dict(
                            episode_id=p["episode_id"],
                            instrument_id=iid,
                            closed_at=e["occurred_at"],
                            net_pnl=text_decimal(p["episode_pnl"]),
                            status="COMPLETED",
                            account_id=a["account_id"],
                            origin=a["origin"],
                        )
                    )
                    p["episode_id"] = None
                    p["episode_pnl"] = Decimal(0)
            elif typ == "SPLIT":
                ratio = dec(e["payload_json"]["split_ratio"])
                p["qty"] *= ratio
                for lot in p["lots"]:
                    lot["qty"] *= ratio
                    lot["unit"] /= ratio
            elif typ == "DIVIDEND":
                p["episode_pnl"] += amount
    marks = []
    value = Decimal(0)
    for iid, p in positions.items():
        if p["qty"] == 0:
            continue
        mark = one(
            cx,
            "SELECT * FROM bar_revisions WHERE instrument_id=? AND bar_end_at<=? AND created_at<=? ORDER BY market_date DESC,revision DESC,created_at DESC LIMIT 1",
            (iid, stamp(at), stamp(knowledge_cutoff or now())),
        )
        invalid_mark = bool(mark and (mark["quality_status"] != "OK" or not mark["is_final"]))
        raw = None if invalid_mark else mark["close"] if mark else p.get("mark")
        if raw is None:
            errors.append("POSITION_PRICE_MISSING")
            marks.append(
                dict(
                    instrument_id=iid,
                    qty=text_decimal(p["qty"]),
                    mark=None,
                    mark_as_of=None,
                    avg_price=None,
                    stop_reference=p["stop_reference"],
                    price_ref=None,
                    reason="LATEST_REVISION_INVALID" if invalid_mark else "POSITION_PRICE_MISSING",
                )
            )
            continue
        price = dec(raw)
        value += p["qty"] * price
        instrument = one(cx, "SELECT * FROM instruments WHERE id=?", (iid,))
        marks.append(
            dict(
                instrument_id=iid,
                ticker=instrument["current_ticker"],
                sector_code=instrument["sector_code"],
                concentration_bucket=instrument["profile_json"].get("concentration_bucket"),
                qty=text_decimal(p["qty"]),
                mark=text_decimal(price),
                mark_as_of=mark["bar_end_at"] if mark else profile["as_of"],
                avg_price=text_decimal(sum(x["qty"] * x["unit"] for x in p["lots"]) / p["qty"])
                if p["qty"] > 0
                else None,
                stop_reference=p["stop_reference"],
                price_ref=mark["id"] if mark else "USER_OPENING_MARK",
            )
        )
    if cash < 0:
        errors.append("NEGATIVE_SETTLED_CASH")
    return dict(
        equity=None if "POSITION_PRICE_MISSING" in errors else cash + unsettled + value,
        cash=cash,
        unsettled=unsettled,
        positions=marks,
        episodes=episodes,
        flows=flows,
        errors=sorted(set(errors)),
        head=digest([e["id"] for e in entries]),
    )


def rebuild(cx, aid, cal, at=None):
    at = at or now()
    a = account(cx, aid)
    v = derive(cx, a, at)
    profile = a["profile_json"]
    rd = cal.risk_date(at)
    day_boundary = instant(cal.session(cal.shift(rd, -1))["close_at"])
    month_first = cal.cal.date_to_session(rd[:7] + "-01", direction="next").strftime("%Y-%m-%d")
    month_boundary = instant(cal.session(cal.shift(month_first, -1))["close_at"])

    def boundary_value(boundary):
        if stamp(boundary) < profile["as_of"]:
            return None
        value = derive(cx, a, boundary)["equity"]
        return text_decimal(value) if value is not None else None

    daybase = (
        profile["day_open_equity"] if rd == profile["initial_risk_date"] else boundary_value(day_boundary)
    )
    monthbase = (
        profile["month_open_equity"] if rd[:7] == profile["initial_month"] else boundary_value(month_boundary)
    )
    flowday = sum((x for d, x in v["flows"] if d > stamp(day_boundary)), Decimal(0))
    flowmonth = sum((x for d, x in v["flows"] if d > stamp(month_boundary)), Decimal(0))
    complete = profile["funds_complete"] and daybase is not None and monthbase is not None and not v["errors"]
    old = one(cx, "SELECT MAX(revision) AS n FROM account_snapshots WHERE account_id=?", (aid,))
    rev = (old["n"] or 0) + 1
    values = dict(
        id=uid("snapshot"),
        account_id=aid,
        revision=rev,
        as_of=stamp(at),
        risk_market_date=rd,
        risk_month=rd[:7],
        equity=text_decimal(v["equity"]) if v["equity"] is not None else None,
        cash_settled=text_decimal(v["cash"]),
        cash_unsettled=text_decimal(v["unsettled"]),
        receivables="0",
        positions_json=v["positions"],
        day_open_equity=daybase,
        month_open_equity=monthbase,
        external_flow_day=text_decimal(flowday),
        external_flow_month=text_decimal(flowmonth),
        pnl_day=text_decimal(v["equity"] - dec(daybase) - flowday)
        if daybase is not None and v["equity"] is not None
        else None,
        pnl_month=text_decimal(v["equity"] - dec(monthbase) - flowmonth)
        if monthbase is not None and v["equity"] is not None
        else None,
        ledger_head_ref=v["head"],
        price_refs_json=[x["price_ref"] for x in v["positions"]],
        completeness="COMPLETE" if complete else "INCOMPLETE",
        confirmed_by_user_at=None,
    )
    values["content_hash"] = digest(values)
    insert(cx, "account_snapshots", values)
    locks = list(a["locks_json"])

    def lock(kind, **extra):
        if not any(x["type"] == kind for x in locks):
            locks.append(
                dict(
                    type=kind,
                    triggered_at=stamp(at),
                    risk_market_date=rd,
                    risk_month=rd[:7],
                    source_snapshot=values["id"],
                    **extra,
                )
            )

    from src.services.signal_scanner.services.config import get_config

    policy = get_config(cx, "risk", stamp(at))["value"]
    if values["pnl_day"] is not None and -dec(values["pnl_day"]) >= dec(daybase) * dec(
        policy["daily_loss_limit"]
    ):
        lock("DAY_LOSS")
    if values["pnl_month"] is not None and -dec(values["pnl_month"]) >= dec(monthbase) * dec(
        policy["monthly_loss_limit"]
    ):
        lock("MONTH_LOSS")
    count = 0
    lastgroup = None
    new_groups = a["last_closed_group_id"] is None
    for e in v["episodes"]:
        count = count + 1 if dec(e["net_pnl"]) < 0 else 0
        lastgroup = e["episode_id"]
        if count >= 3 and new_groups:
            lossday = cal.risk_date(instant(e["closed_at"]))
            lock("LOSS_STREAK", cooldown_end_session=cal.shift(lossday, 2))
        if e["episode_id"] == a["last_closed_group_id"]:
            new_groups = True
    recovery = a["recovery_remaining"]
    # Calendar locks expire only with complete, rebuilt baselines. Loss-streak additionally needs user confirmation.
    if complete:
        retained = [
            x
            for x in locks
            if not (
                (x["type"] == "DAY_LOSS" and x["risk_market_date"] != rd)
                or (x["type"] == "MONTH_LOSS" and x["risk_month"] != rd[:7])
            )
        ]
        for removed in locks:
            if removed not in retained:
                audit(cx, "RISK_LOCK_RELEASED", aid, removed["type"])
        locks = retained
    update(
        cx,
        "account_state",
        "account_id",
        aid,
        dict(
            current_snapshot_id=values["id"],
            confirmed_ledger_head=None,
            locks_json=locks,
            consecutive_loss_count=count,
            last_closed_group_id=lastgroup,
            recovery_remaining=recovery,
            row_version=a["row_version"] + 1,
        ),
    )
    return values


def confirm(cx, aid, payload, cal):
    a, s = snapshot(cx, aid)
    require(a["origin"] != "SHADOW_EXPERIMENT", "OUT_OF_RANGE", "研究账户由实验维护", 422)
    require(
        payload.snapshot_id == s["id"] and payload.ledger_head == s["ledger_head_ref"], "REVISION_CONFLICT"
    )
    require(s["completeness"] == "COMPLETE", "ACCOUNT_INCOMPLETE")
    locks = a["locks_json"]
    retained = []
    recovery = a["recovery_remaining"]
    for lock in locks:
        if lock["type"] == "LOSS_STREAK" and stamp() >= cal.session(lock["cooldown_end_session"])["close_at"]:
            recovery = 5
            audit(cx, "RISK_LOCK_RELEASED", aid, "连亏冷却结束，用户确认后进入恢复")
        else:
            retained.append(lock)
    update(
        cx,
        "account_state",
        "account_id",
        aid,
        dict(
            confirmed_ledger_head=s["ledger_head_ref"],
            locks_json=retained,
            recovery_remaining=recovery,
            row_version=a["row_version"] + 1,
        ),
    )
    audit(cx, "ACCOUNT_CONFIRMED", aid, payload.reason, snapshot_id=s["id"])
    return dict(snapshot_id=s["id"], confirmed=True, recovery_remaining=recovery)


def normalize_entry(cx, a, e, cal):
    data = e.model_dump()
    at = instant(e.occurred_at)
    require(at <= now(), "VALIDATION_ERROR", "不能提交未来成交", 422)
    typ = e.entry_type
    fee = dec(e.fee, nonnegative=True)
    amount = Decimal(0)
    if typ in ("BUY", "SELL", "SPLIT", "DIVIDEND"):
        require(
            e.instrument_id and one(cx, "SELECT id FROM instruments WHERE id=?", (e.instrument_id,)),
            "DATA_INCOMPLETE",
            "证券身份不明确",
            422,
        )
    if typ in ("BUY", "SELL"):
        require(e.qty is not None and e.price is not None, "VALIDATION_ERROR", status=422)
        qty, price = dec(e.qty), dec(e.price)
        require(qty > 0 and price > 0, "OUT_OF_RANGE", status=422)
        amount = (-qty * price - fee) if typ == "BUY" else qty * price - fee
    elif typ == "SPLIT":
        require(e.split_ratio is not None and dec(e.split_ratio) > 0, "OUT_OF_RANGE", status=422)
    else:
        require(e.cash_amount is not None, "VALIDATION_ERROR", status=422)
        gross = dec(e.cash_amount, nonnegative=True)
        amount = (-gross - fee) if typ in ("WITHDRAWAL", "FEE") else gross - fee
    if e.stop_reference is not None:
        require(dec(e.stop_reference) > 0, "OUT_OF_RANGE", status=422)
    if e.settlement_date:
        from datetime import date

        try:
            date.fromisoformat(e.settlement_date)
        except ValueError:
            require(False, "VALIDATION_ERROR", status=422)
        require(e.settlement_date >= at.date().isoformat(), "OUT_OF_RANGE", status=422)
    return dict(
        id=uid("entry"),
        account_id=a["account_id"],
        entry_type=typ,
        instrument_id=e.instrument_id,
        plan_id=e.plan_id,
        occurred_at=stamp(at),
        market_date=cal.risk_date(at),
        qty=e.qty,
        price=e.price,
        cash_amount=text_decimal(amount),
        fee=text_decimal(fee),
        currency="USD",
        settlement_date=e.settlement_date,
        settlement_state="UNSETTLED" if typ == "SELL" else "SETTLED",
        origin=a["origin"],
        external_key=e.external_key,
        payload_json=data,
    )


def import_entries(cx, payload, cal):
    a = account(cx, payload.account_id)
    valid = []
    errors = []
    duplicates = 0
    keys = set()
    require(a["origin"] != "SHADOW_EXPERIMENT", "OUT_OF_RANGE", "不能向研究账户导入人工事实", 422)
    for n, e in enumerate(payload.rows):
        if e.external_key in keys:
            errors.append(dict(row=n + 1, code="DUPLICATE_EXTERNAL_KEY"))
            continue
        keys.add(e.external_key)
        old = one(
            cx,
            "SELECT * FROM ledger_entries WHERE account_id=? AND external_key=?",
            (a["account_id"], e.external_key),
        )
        if old:
            if old["payload_json"] == e.model_dump():
                duplicates += 1
                continue
            errors.append(dict(row=n + 1, code="IDEMPOTENCY_CONFLICT"))
            continue
        try:
            valid.append(normalize_entry(cx, a, e, cal))
        except Exception as ex:
            if not hasattr(ex, "code"):
                raise
            errors.append(dict(row=n + 1, code=ex.code))
    if payload.dry_run:
        return dict(
            valid=len(valid), invalid=len(errors), duplicates=duplicates, committed=False, errors=errors
        )
    require(not errors, "VALIDATION_ERROR", "导入批次包含错误，未提交任何行", 422, errors=errors)
    for e in valid:
        insert(cx, "ledger_entries", e)
        if e.get("plan_id") and e["entry_type"] == "BUY":
            p = one(cx, "SELECT * FROM plans WHERE id=?", (e["plan_id"],))
            require(
                p and p["account_id"] == a["account_id"] and p["instrument_id"] == e["instrument_id"],
                "VALIDATION_ERROR",
                status=422,
            )
            quantity = dec(e["qty"])
            if quantity % 1:
                update(
                    cx,
                    "plans",
                    "id",
                    p["id"],
                    dict(
                        status="NEEDS_REVIEW",
                        qty_remaining=0,
                        risk_reserved="0",
                        cash_reserved="0",
                        symbol_notional_reserved="0",
                        sector_notional_reserved="0",
                        row_version=p["row_version"] + 1,
                    ),
                )
                audit(cx, "PLAN_DEVIATION", p["id"], "真实零股成交已记录，整股计划不能自动映射")
                continue
            filled = p["qty_reported_filled"] + int(quantity)
            remaining = (
                max(0, p["qty_reserved"] - filled) if p["status"] in ("RESERVED", "PARTIALLY_FILLED") else 0
            )
            ratio = Decimal(remaining) / p["qty_remaining"] if p["qty_remaining"] else Decimal(0)
            changes = dict(
                qty_reported_filled=filled,
                qty_remaining=remaining,
                status="PARTIALLY_FILLED"
                if remaining
                else ("SIMULATED_FILLED" if a["scope"] == "PAPER" else "USER_REPORTED_FILLED"),
                row_version=p["row_version"] + 1,
            )
            for k in (
                "risk_reserved",
                "cash_reserved",
                "symbol_notional_reserved",
                "sector_notional_reserved",
            ):
                changes[k] = text_decimal(dec(p[k]) * ratio)
            update(cx, "plans", "id", p["id"], changes)
            if filled > p["qty_reserved"]:
                audit(cx, "PLAN_DEVIATION", p["id"], "实际成交超出计划，事实如实入账")
    if valid:
        rebuild(cx, a["account_id"], cal)
    audit(
        cx, "LEDGER_IMPORTED", a["account_id"], payload.import_id, inserted=len(valid), duplicates=duplicates
    )
    return dict(inserted=len(valid), duplicates=duplicates, rejected=0, committed=True, errors=[])


def reverse_entry(cx, eid, payload, cal):
    old = one(cx, "SELECT * FROM ledger_entries WHERE id=?", (eid,))
    require(old, "RESOURCE_NOT_FOUND", status=404)
    require(
        account(cx, old["account_id"])["origin"] != "SHADOW_EXPERIMENT",
        "OUT_OF_RANGE",
        "研究事实通过实验修订，不接受人工冲正",
        422,
    )
    require(
        old["entry_type"] != "REVERSAL"
        and not one(cx, "SELECT id FROM ledger_entries WHERE reverses_entry_id=?", (eid,)),
        "ALREADY_REVERSED",
    )
    reversal = {k: v for k, v in old.items() if k not in ("created_at", "id")}
    reversal.update(
        id=uid("reversal"),
        entry_type="REVERSAL",
        external_key="reversal:" + eid,
        reverses_entry_id=eid,
        cash_amount=text_decimal(-dec(old["cash_amount"])),
        payload_json=dict(reason=payload.reason),
    )
    insert(cx, "ledger_entries", reversal)
    replacement = None
    if payload.replacement:
        a = account(cx, old["account_id"])
        replacement = normalize_entry(cx, a, payload.replacement, cal)
        insert(cx, "ledger_entries", replacement)
    rebuild(cx, old["account_id"], cal)
    audit(cx, "LEDGER_REVERSED", eid, payload.reason)
    return dict(
        reversal_id=reversal["id"],
        replacement_id=replacement["id"] if replacement else None,
        rebuild_status="COMPLETED_REQUIRES_CONFIRMATION",
    )
