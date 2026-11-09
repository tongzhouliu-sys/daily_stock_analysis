from collections import defaultdict

from src.services.signal_scanner.core import digest
from src.services.signal_scanner.domain.features import distances


def environment(spy, vix, previous=None, hard_halt=False):
    previous = previous or {}
    base = "UNKNOWN"
    reason = None
    if hard_halt or (vix is not None and vix >= 35):
        base = "PANIC"
    elif not spy or vix is None or spy.get("ma50") is None or spy.get("ma50_prev5") is None:
        reason = "ENVIRONMENT_INCOMPLETE"
    elif vix >= 25:
        base = "STRESS"
    elif spy["close"] > spy["ma50"] > spy["ma50_prev5"]:
        base = "NORMAL"
    else:
        base = "DEFENSIVE"
    day = spy.get("source_market_date") if spy else None
    count = previous.get("normal_sessions", 0)
    mode = base
    if base == "NORMAL" and (previous.get("mode") in ("PANIC", "STRESS") or previous.get("recovering")):
        count = count + (1 if day != previous.get("source_market_date") else 0)
        mode = "NORMAL" if count >= 2 else "STRESS"
    elif base not in ("NORMAL", "UNKNOWN"):
        count = 0
    if base == "UNKNOWN":
        count = previous.get("normal_sessions", 0)
    return dict(
        mode=mode,
        base_mode=base,
        normal_sessions=count,
        source_market_date=day,
        recovering=base == "NORMAL" and mode != "NORMAL",
        reason_code=reason,
        vix=vix,
    )


def score(f, event, participation, event_group=False):
    fields = ("ma10", "ma20", "ma50", "adx", "di_plus", "di_minus", "rv", "weekly")
    missing = [k for k in fields if f.get(k) is None]
    if participation is None:
        missing.append("participation")
    if missing:
        return dict(
            quality_score=None,
            quality_grade="WATCH",
            effective_grade="WATCH",
            evidence_groups=[],
            score_trace=[],
            reason_codes=["SCORE_INCOMPLETE"],
            missing_inputs=missing,
        )
    s = event["frozen"]
    atr = s.get("confirmation_atr") or s.get("atr")
    anchor = s.get("entry_anchor")
    volume = 25 if f["rv"] >= 2 else 20 if f["rv"] >= 1.5 else 10 if f["rv"] >= 1 else 0
    if event["rule_id"] == "TR-03":
        volume = 25 if f["rv"] >= 1 and s.get("pullback_low") is not None else 0
    checks = [
        ("close_above_ma50", f["close"] > f["ma50"], 10),
        ("ma_stack", f["ma10"] > f["ma20"] > f["ma50"], 10),
        ("trend_strength", f["adx"] >= 25 and f["di_plus"] > f["di_minus"], 10),
        ("volume", True, volume),
        ("confirmed_structure", event["lifecycle"] == "CONFIRMED", 10),
        (
            "entry_distance",
            anchor is not None and atr is not None and anchor <= f["close"] <= anchor + 0.5 * atr,
            10,
        ),
        ("participation", True, participation["points"]),
        ("weekly", True, 10 if f["weekly"] == "BULLISH" else 5 if f["weekly"] == "NEUTRAL" else 0),
    ]
    trace = [
        dict(
            check=k,
            passed=bool(ok),
            points=points if ok else 0,
            actual=f.get(k),
            input_refs=[f["input_hash"]],
        )
        for k, ok, points in checks
    ]
    total = sum(x["points"] for x in trace)
    groups = []
    if event["lifecycle"] == "CONFIRMED":
        groups.append("PRICE")
    if volume > 0:
        groups.append("VOLUME")
    if participation["points"] >= 10 and participation["external_confirmed"]:
        groups.append("PARTICIPATION")
    if event_group:
        groups.append("EVENT")
    grade = "WATCH" if total < 60 else "HIGH" if total >= 80 and len(groups) >= 3 else "TRACK"
    effective = grade
    if (f["weekly"] == "BEARISH" or event["rule_id"] == "MO-01") and grade == "HIGH":
        effective = "TRACK"
    return dict(
        quality_score=total,
        quality_grade=grade,
        effective_grade=effective,
        evidence_groups=groups,
        score_trace=trace,
        reason_codes=["WEEKLY_OPPOSED"] if f["weekly"] == "BEARISH" else [],
        missing_inputs=[],
    )


def aggregate(events, features, instruments, env, as_of, *, production=False):
    available = [
        e
        for e in events
        if e["lifecycle"] in ("PENDING", "CONFIRMED") and (not production or e["stage"] == "APPROVED")
    ]
    byid = {i["id"]: i for i in instruments}
    groups = defaultdict(list)
    confirmed = {
        e["instrument_id"]
        for e in available
        if e["kind"] == "DIRECTION" and e["direction"] == "LONG" and e["lifecycle"] == "CONFIRMED"
    }
    for e in available:
        if e["kind"] == "EVIDENCE":
            continue
        groups[(e["instrument_id"], e["direction"], e["family"], e["trigger_market_date"])].append(e)
    cards = []
    for key, es in sorted(groups.items()):
        es.sort(key=lambda e: (e["rule_id"] != "VP-01", e["rule_id"]))
        main = es[0]
        f = features[key[0]]
        i = byid[key[0]]
        profile = i.get("profile_json", {})
        participation = None
        if i["asset_type"] == "ETF":
            refs = profile.get("comparators", [])
            if len(refs) == 2 and len(set(refs)) == 2 and all(x in features and x != i["id"] for x in refs):
                fs = [features[x] for x in refs]
                if all(x.get("ma50") is not None and x.get("return20") is not None for x in fs):
                    points = sum(5 for x in fs if x["close"] > x["ma50"]) + (
                        5 if all(x["return20"] > 0 for x in fs) else 0
                    )
                    participation = dict(points=points, external_confirmed=True)
        else:
            sector_ref = profile.get("sector_etf_id")
            sector = features.get(sector_ref)
            spy = features.get(profile.get("market_ref_id"))
            ratio_ok = profile.get("relative_strength_above_ma20")
            if i.get("sector_code") and sector and spy and sector.get("ma50") is not None and ratio_ok is not None:
                peers = [
                    x
                    for x in confirmed
                    if x != i["id"] and byid[x].get("sector_code") == i.get("sector_code")
                ]
                participation = dict(
                    points=(5 if sector["close"] > sector["ma50"] else 0)
                    + (5 if ratio_ok else 0)
                    + (5 if peers else 0),
                    external_confirmed=bool(peers),
                )
        warning = main["kind"] == "WARNING"
        scored = dict(
            quality_score=None,
            quality_grade=None,
            effective_grade=None,
            evidence_groups=[],
            score_trace=[],
            reason_codes=[],
        )
        if not warning:
            scored = score(
                f,
                main,
                participation,
                any(
                    e["instrument_id"] == i["id"]
                    and e["rule_id"] == "EV-01"
                    and e["direction"] == main["direction"]
                    and e["lifecycle"] == "CONFIRMED"
                    for e in available
                ),
            )
        missing_inputs = scored.pop("missing_inputs", [])
        if "participation" in missing_inputs:
            missing_inputs.remove("participation")
            if i["asset_type"] == "ETF":
                missing_inputs.append("comparators")
            else:
                if not i.get("sector_code"):
                    missing_inputs.append("sector_mapping")
                sector_feature = features.get(profile.get("sector_etf_id"))
                if not sector_feature or sector_feature.get("ma50") is None:
                    missing_inputs.append("sector_reference")
                if not features.get(profile.get("market_ref_id")):
                    missing_inputs.append("market_reference")
                if profile.get("relative_strength_above_ma20") is None:
                    missing_inputs.append("relative_strength")
            if not missing_inputs:
                missing_inputs.append("participation")
        reasons = scored.pop("reason_codes")
        mode = env["mode"]
        allowed = True
        if (
            main["lifecycle"] != "CONFIRMED"
            or main["stage"] != "APPROVED"
            or main["direction"] != "LONG"
            or main["rule_id"] == "MO-01"
        ):
            allowed = False
            reasons.append("RESEARCH_OR_OBSERVATION")
        if mode not in ("NORMAL", "STRESS"):
            allowed = False
            reasons.append("ENVIRONMENT_" + mode)
        if not warning and (
            "PRICE" not in scored["evidence_groups"] or "VOLUME" not in scored["evidence_groups"]
        ):
            allowed = False
            reasons.append("EVIDENCE_INCOMPLETE")
        if mode == "STRESS" and scored["effective_grade"] != "HIGH":
            allowed = False
        if scored["effective_grade"] == "WATCH":
            allowed = False
        related = [
            e for e in available if e["instrument_id"] == i["id"] and e["rule_id"] in ("VP-03", "VP-04")
        ]
        if related:
            if scored["effective_grade"] == "HIGH":
                scored["effective_grade"] = "TRACK"
            if mode == "STRESS":
                allowed = False
            reasons.append("CAUTION_PATTERN")
        opposite = any(
            e["instrument_id"] == i["id"]
            and e["kind"] == "DIRECTION"
            and e["lifecycle"] == "CONFIRMED"
            and e["direction"] != main["direction"]
            for e in available
        )
        if opposite:
            allowed = False
            reasons.append("DIRECTIONAL_CONFLICT")
        context = dict(
            entry_anchor=main["frozen"].get("entry_anchor"),
            atr_ref=main["frozen"].get("confirmation_atr") or main["frozen"].get("atr"),
            adjustment_epoch=f.get("adjustment_epoch"),
            price_basis="RAW",
            features=f,
            score_missing_inputs=missing_inputs,
            price_context=dict(
                basis="EOD_REFERENCE", observed_at=f.get("bar_end_at"), received_at=as_of, revision=1
            ),
            data_quality="OK",
            event_status="UNVERIFIED",
            **distances(
                f["close"],
                main["frozen"].get("entry_anchor"),
                main["frozen"].get("confirmation_atr") or main["frozen"].get("atr"),
                reason="NOT_APPLICABLE" if warning else None,
            ),
        )
        cards.append(
            dict(
                card_id="card_" + digest([*key, "PRODUCTION" if production else "RESEARCH"])[:24],
                instrument_id=i["id"],
                view_kind="PRODUCTION" if production else "RESEARCH",
                family=main["family"],
                direction=main["direction"],
                trigger_market_date=main["trigger_market_date"],
                lifecycle=main["lifecycle"],
                severity="CAUTION" if warning else None,
                availability="PLAN_ALLOWED"
                if allowed and not warning
                else "OBSERVE"
                if mode in ("NORMAL", "STRESS")
                else "BLOCKED",
                reason_codes=reasons,
                primary_rule_ref=main["event_id"],
                rule_refs=[e["event_id"] for e in es],
                context=context,
                valid_until=min(e["valid_until"] for e in es),
                data_as_of=f.get("bar_end_at", as_of),
                **scored,
            )
        )
    return cards
