"""Deterministic shadow portfolio; consumes frozen inputs and never executes orders."""

from collections import defaultdict
from decimal import Decimal

from src.services.signal_scanner.core import dec, digest, instant, text_decimal
from src.services.signal_scanner.domain.features import adjust, build_features
from src.services.signal_scanner.domain.paper import paper_entry, paper_exit
from src.services.signal_scanner.domain.risk import calculate_risk
from src.services.signal_scanner.domain.scoring import aggregate, environment


def simulate(manifest, output, cal, experiment, feature_series=None):
    cash = dec(experiment["initial_cash"])
    initial = cash
    positions = {}
    closed = []
    trades = []
    curve = []
    skipped = []
    unsettled = []
    receivables = []
    policy = manifest.get("risk_policy", {})
    fee_in = dec(policy.get("fixed_entry_fee", "1"))
    fee_out = dec(policy.get("fixed_exit_fee", "1"))
    slip = dec(experiment["slippage_bps"])
    raw = {iid: {b["market_date"]: b for b in bs} for iid, bs in manifest["bars"].items()}
    series = feature_series or {}
    fmap = {}
    for iid, bs in manifest["bars"].items():
        if iid not in series:
            actions = [e for e in manifest["events"] if e["instrument_id"] == iid]
            series[iid] = build_features(adjust(bs, actions, manifest["cutoff_at"]), cal, iid) if bs else []
        fmap[iid] = {f["source_market_date"]: f for f in series[iid]}
    ids = {i["id"]: i for i in manifest["instruments"]}
    trade_ids = set(manifest["universe"]["value"]["members"])
    byentry = defaultdict(list)
    for e in output["events"]:
        if (
            e["kind"] == "DIRECTION"
            and e["direction"] == "LONG"
            and e.get("confirmation_market_date")
            and e["rule_id"] != "MO-01"
            and e["instrument_id"] in trade_ids
        ):
            byentry[cal.shift(e["confirmation_market_date"], 1)].append(e)
    days = sorted(set(d for bs in raw.values() for d in bs))
    envstate = None
    prev_equity = initial
    monthbase = initial
    month = None
    loss_streak = 0
    cool_until = None
    recover = 0
    month_locked = False
    historical = manifest.get("security_status_history", [])
    settlement = manifest.get("settlement_dates", {})
    coverage = manifest.get("historical_event_coverage", {})

    def record(typ, iid, qty, price, day, amount, fee="0", **extra):
        trades.append(
            dict(
                entry_type=typ,
                instrument_id=iid,
                qty=text_decimal(qty) if qty is not None else None,
                price=text_decimal(price) if price is not None else None,
                occurred_at=cal.session(day)["open_at"]
                if typ == "BUY" or extra.pop("at_open", False)
                else cal.session(day)["close_at"],
                market_date=day,
                cash_amount=text_decimal(amount),
                fee=str(fee),
                settlement_date=settlement.get(day) if typ == "SELL" else None,
                **extra,
            )
        )

    def sell(iid, fill, day, at_open=False):
        nonlocal cash, loss_streak, cool_until
        p = positions.pop(iid)
        exitfee = fee_out + p["qty"] * dec(policy.get("per_share_fee", "0"))
        amount = p["qty"] * fill - exitfee
        sd = settlement.get(day)
        if sd and sd <= day:
            cash += amount
        else:
            unsettled.append(dict(amount=amount, date=sd))
        net = amount - p["cost"] + p["dividends"]
        loss_streak = loss_streak + 1 if net < 0 else 0
        if loss_streak >= 3:
            cool_until = cal.shift(day, 2)
        closed.append(
            dict(
                dataset="PAPER_STRATEGY",
                episode_id=p["episode_id"],
                origin="SHADOW_EXPERIMENT",
                rule_id=p["rule_id"],
                rule_version=p["rule_version"],
                direction="LONG",
                closed_at=cal.session(day)["close_at"],
                net_pnl=text_decimal(net),
                status="COMPLETED",
                costs_version=experiment["costs_version"],
                data_mode=experiment["data_mode"],
                experiment_version=digest(experiment),
            )
        )
        record("SELL", iid, p["qty"], fill, day, amount, exitfee, at_open=at_open)

    for day in days:
        if cal.session(day)["close_at"] > manifest["cutoff_at"]:
            break
        for money in list(unsettled):
            if money["date"] and money["date"] <= day:
                cash += money["amount"]
                unsettled.remove(money)
        for money in list(receivables):
            if money["pay_date"] and money["pay_date"] <= day:
                cash += money["amount"]
                receivables.remove(money)
                record("DIVIDEND", money["instrument_id"], None, None, day, money["amount"])
        for e in manifest["events"]:
            payload = e["payload_json"]
            iid = e.get("instrument_id")
            effective = (e.get("effective_at") or payload.get("ex_date") or "")[:10]
            if iid not in positions or effective != day:
                continue
            if e["event_type"] == "SPLIT":
                ratio = dec(str(payload["ratio"]))
                p = positions[iid]
                p["qty"] *= ratio
                p["stop"] /= ratio
                record(
                    "SPLIT", iid, None, None, day, Decimal(0), payload=dict(split_ratio=text_decimal(ratio))
                )
            if e["event_type"] == "DIVIDEND":
                p = positions[iid]
                amount = p["qty"] * dec(str(payload["amount"]))
                p["dividends"] += amount
                receivables.append(dict(instrument_id=iid, amount=amount, pay_date=payload.get("pay_date")))
        for iid in list(positions):
            bar = raw[iid].get(day)
            if bar and dec(str(bar["open"])) <= positions[iid]["stop"]:
                sell(iid, dec(str(bar["open"])) * (1 - slip / 10000), day, True)
        if month != day[:7]:
            monthbase = prev_equity
            month = day[:7]
            month_locked = False
        daybase = prev_equity
        prevday = cal.shift(day, -1)
        fs = {iid: f[prevday] for iid, f in fmap.items() if prevday in f}
        spy = next((fs[iid] for iid, i in ids.items() if i["current_ticker"] == "SPY" and iid in fs), None)
        vix = next(
            (
                float(e["payload_json"]["value"])
                for e in manifest["events"]
                if e["event_type"] == "VIX_VALUE"
                and e["payload_json"].get("market_date") == prevday
                and e["known_at"] < cal.session(day)["open_at"]
            ),
            None,
        )
        envstate = environment(spy, vix, envstate)
        asof_events = []
        for e in output["events"]:
            history = [h for h in e.get("state_history", []) if h["market_date"] <= prevday]
            state = history[-1] if history else None
            if state and state["lifecycle"] in ("PENDING", "CONFIRMED"):
                asof_events.append({**e, **state})
        current_instruments = []
        for inst in manifest["instruments"]:
            profile = dict(inst["profile_json"])
            sect = series.get(profile.get("sector_etf_id"), [])
            sp = series.get(profile.get("market_ref_id"), [])
            values = {f["source_market_date"]: f["close"] for f in sect if f["source_market_date"] <= prevday}
            ratios = [
                values[f["source_market_date"]] / f["close"]
                for f in sp
                if f["source_market_date"] <= prevday and f["source_market_date"] in values
            ]
            if len(ratios) >= 20:
                profile["relative_strength_above_ma20"] = ratios[-1] > sum(ratios[-20:]) / 20
            current_instruments.append({**inst, "profile_json": profile})
        grade_context = (
            aggregate(asof_events, fs, current_instruments, envstate, cal.session(day)["open_at"])
            if byentry.get(day) and all(e["instrument_id"] in fs for e in asof_events)
            else []
        )
        grades = {c["primary_rule_ref"]: c for c in grade_context}
        if cool_until and day > cool_until:
            recover = 5
            cool_until = None
        daily = 0
        dedup = set()
        for e in sorted(
            byentry.get(day, []), key=lambda x: (x["rule_id"] != "VP-01", x["instrument_id"], x["event_id"])
        ):
            iid = e["instrument_id"]
            why = None
            key = (iid, e["family"])
            if key in dedup:
                continue
            dedup.add(key)
            status = next(
                (
                    s
                    for s in sorted(historical, key=lambda x: x["observed_at"], reverse=True)
                    if s["instrument_id"] == iid
                    and s["observed_at"] < cal.session(day)["open_at"]
                    and (cal.session(day)["open_at"] <= s.get("valid_until", ""))
                ),
                None,
            )
            if e["confirmed_at"] >= cal.session(day)["open_at"]:
                why = "LATE_AVAILABILITY"
            elif not status or status["status"] != "ACTIVE":
                why = "SECURITY_STATUS_UNVERIFIED"
            elif not (
                coverage.get("from", "9999") <= prevday <= coverage.get("to", "0000")
                and coverage.get("status") == "OK"
            ):
                why = "HISTORICAL_EVENTS_UNVERIFIED"
            elif envstate["mode"] not in ("NORMAL", "STRESS"):
                why = "ENVIRONMENT_" + envstate["mode"]
            elif iid in positions:
                why = "POSITION_ALREADY_OPEN"
            elif cool_until:
                why = "LOSS_STREAK_COOLDOWN"
            elif month_locked:
                why = "MONTH_LOSS_LOCK"
            elif daily >= 5:
                why = "DAILY_PLAN_LIMIT"
            graded = grades.get(e["event_id"])
            if (
                not graded
                or "PRICE" not in graded["evidence_groups"]
                or "VOLUME" not in graded["evidence_groups"]
                or graded["effective_grade"] == "WATCH"
            ):
                why = why or "EVIDENCE_INCOMPLETE"
            elif envstate["mode"] == "STRESS" and graded["effective_grade"] != "HIGH":
                why = why or "STRESS_REQUIRES_HIGH"
            # Earnings and macro gates use only versions known before this opening.
            for ev in manifest["events"]:
                if ev["known_at"] >= cal.session(day)["open_at"]:
                    continue
                if ev["event_type"] == "EARNINGS" and ev.get("instrument_id") == iid:
                    announced = ev.get("published_at")
                    expected = ev.get("scheduled_date")
                    if expected:
                        eventday = cal.cal.date_to_session(expected, direction="next").strftime("%Y-%m-%d")
                        if day >= cal.shift(eventday, -1) and (
                            not announced or day <= cal.first_full_after(announced)
                        ):
                            why = "EARNINGS_WINDOW"
                if (
                    ev["event_type"] in ("CPI", "FOMC", "EMPLOYMENT")
                    and ev.get("scheduled_at")
                    and abs(
                        (instant(ev["scheduled_at"]) - instant(cal.session(day)["open_at"])).total_seconds()
                    )
                    <= 1800
                ):
                    why = "MACRO_EVENT_WINDOW"
            bar = raw.get(iid, {}).get(day)
            factor = Decimal(str(e["frozen"].get("price_factor", "1")))
            anchor = Decimal(str(e["frozen"]["entry_anchor"])) / factor
            atr = Decimal(str(e["frozen"].get("confirmation_atr") or e["frozen"]["atr"])) / factor
            fill = (
                paper_entry(str(bar["open"]), text_decimal(anchor), text_decimal(anchor + atr / 2), int(slip))
                if bar
                else None
            )
            if fill is None:
                why = why or "ENTRY_OUTSIDE_RANGE_OR_MISSING"
            if why:
                skipped.append(dict(event_id=e["event_id"], entry_market_date=day, reason=why))
                continue
            markvalue = Decimal(0)
            heat = Decimal(0)
            sector = Decimal(0)
            unmeasurable = False
            targetsector = ids[iid]["sector_code"] or ids[iid]["profile_json"].get("concentration_bucket")
            for pi, p in positions.items():
                pb = raw[pi].get(prevday)
                if not pb:
                    unmeasurable = True
                    continue
                mark = dec(str(pb["close"]))
                notional = mark * p["qty"]
                markvalue += notional
                heat += max(Decimal(0), mark - p["stop"]) * p["qty"] + fee_out
                if (
                    ids[pi]["sector_code"] or ids[pi]["profile_json"].get("concentration_bucket")
                ) == targetsector:
                    sector += notional
            equity = cash + sum((x["amount"] for x in unsettled + receivables), Decimal(0)) + markvalue
            stop = fill - 2 * atr
            if e["rule_id"] == "TR-03":
                stop = min(stop, Decimal(str(e["frozen"]["pullback_low"])) / factor - atr)
            calculated = calculate_risk(
                dict(
                    equity=text_decimal(equity),
                    settled_cash=text_decimal(cash),
                    day_open_equity=text_decimal(daybase),
                    month_open_equity=text_decimal(monthbase),
                    pnl_day=text_decimal(equity - daybase),
                    pnl_month=text_decimal(equity - monthbase),
                    entry=text_decimal(fill),
                    stop=text_decimal(stop),
                    heat_open=text_decimal(heat),
                    heat_pending="0",
                    cash_pending="0",
                    symbol_used="0",
                    sector_used=text_decimal(sector),
                    tick=ids[iid]["price_tick"],
                    environment=envstate["mode"],
                    recovery_remaining=recover,
                    reasons=["POSITION_PRICE_MISSING"] if unmeasurable else [],
                ),
                policy,
            )
            if calculated["status"] != "CALCULATED":
                skipped.append(dict(event_id=e["event_id"], entry_market_date=day, reason="RISK_BLOCKED"))
                continue
            qty = Decimal(calculated["qty"])
            entryfee = fee_in + qty * dec(policy.get("per_share_fee", "0"))
            cost = qty * fill + entryfee
            cash -= cost
            daily += 1
            recover = max(0, recover - 1)
            positions[iid] = dict(
                qty=qty,
                stop=dec(calculated["stop"]),
                cost=cost,
                dividends=Decimal(0),
                entry_date=day,
                episode_id=e["event_id"],
                rule_id=e["rule_id"],
                rule_version=e["rule_version"],
            )
            record(
                "BUY", iid, qty, fill, day, -cost, entryfee, payload=dict(stop_reference=calculated["stop"])
            )
        for iid in list(positions):
            bar = raw[iid].get(day)
            if not bar:
                continue
            p = positions[iid]
            held = cal.distance(p["entry_date"], day) + 1
            fill = paper_exit(bar, text_decimal(p["stop"]), held, int(slip))
            if fill is not None:
                sell(iid, fill, day)
        value = Decimal(0)
        missing = []
        for iid, p in positions.items():
            bar = raw[iid].get(day)
            if bar:
                value += dec(str(bar["close"])) * p["qty"]
            else:
                missing.append(iid)
        equity = cash + sum((x["amount"] for x in unsettled + receivables), Decimal(0)) + value
        curve.append(
            dict(
                market_date=day,
                equity=None if missing else text_decimal(equity),
                cash_settled=text_decimal(cash),
                cash_unsettled=text_decimal(sum((x["amount"] for x in unsettled), Decimal(0))),
                unresolved_instruments=missing,
            )
        )
        if not missing:
            prev_equity = equity
            if equity - monthbase <= -monthbase * dec(policy.get("monthly_loss_limit", "0.10")):
                month_locked = True
    return dict(
        status="COMPUTED"
        if trades
        else "INSUFFICIENT_INPUTS"
        if any(s["reason"].endswith("UNVERIFIED") for s in skipped)
        else "NO_FILLS",
        initial_cash=text_decimal(initial),
        closed_episodes=closed,
        open_positions=[
            dict(instrument_id=i, qty=text_decimal(p["qty"]), entry_date=p["entry_date"])
            for i, p in positions.items()
        ],
        trades=trades,
        equity_curve=curve,
        skipped=skipped,
        settlement_unknown=sum(x["date"] is None for x in unsettled),
        assumptions=[
            "SHADOW研究，不要求人工STOP；不执行真实订单",
            "日线止损优先，单边滑点按冻结协议",
            "缺少已核验结算日的卖出款保持未结算",
            "纸面分红税费采用协议披露的零扣缴假设",
        ],
    )
