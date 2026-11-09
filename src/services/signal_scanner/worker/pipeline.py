from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.services.signal_scanner.core import digest, instant, stamp, uid
from src.services.signal_scanner.domain.features import adjust, build_features
from src.services.signal_scanner.domain.rules import run_rules
from src.services.signal_scanner.domain.scoring import aggregate, environment
from src.services.signal_scanner.storage.db import audit, insert, many, one, update


def latest_workspace(cx, files):
    job = one(
        cx,
        "SELECT * FROM job_runs WHERE artifact_kind='WORKSPACE' AND published_seq IS NOT NULL AND output_manifest_ref IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
    )
    return (job, files.read(job["output_manifest_ref"])) if job else (None, None)


def source_failure(cx):
    last = one(
        cx,
        "SELECT * FROM job_runs WHERE job_name IN ('DAILY_SCAN','VERIFY') AND status IN ('SUCCEEDED','PARTIAL','FAILED') ORDER BY created_at DESC,id DESC LIMIT 1",
    )
    return last if last and last["status"] == "FAILED" else None


def load_events(cx, cutoff):
    records = many(
        cx, "SELECT * FROM market_event_revisions WHERE known_at<=? ORDER BY event_id,revision", (cutoff,)
    )
    latest = {e["event_id"]: e for e in records}
    return [e for e in latest.values() if e["status"] != "CANCELLED"]


def gate_events(iid, events, event_config, statuses, cal, at):
    status = statuses.get(iid)
    observed = status.get("observed_at") if status else None
    # A known imminent event blocks even when broader coverage remains unverified.
    for e in events:
        if e["event_type"] != "EARNINGS" or e.get("instrument_id") != iid:
            continue
        if e.get("known_at") and instant(e["known_at"]) > at:
            continue
        if not e.get("scheduled_date"):
            return "UNVERIFIED", observed
        key = cal.cal.date_to_session(e["scheduled_date"], direction="next").strftime("%Y-%m-%d")
        start = instant(cal.session(cal.shift(key, -1))["open_at"])
        if not e.get("published_at"):
            if at >= start:
                return "BLOCKED", observed
        else:
            end = instant(cal.session(cal.first_full_after(e["published_at"]))["close_at"]) + timedelta(
                minutes=30
            )
            if start <= at < end:
                return "BLOCKED", observed
    if (
        event_config.get("coverage") != "OK"
        or not event_config.get("verified_at")
        or (at - instant(event_config["verified_at"])).total_seconds() > 3600
    ):
        return "UNVERIFIED", observed
    if (
        not status
        or status.get("status") != "ACTIVE"
        or (at - instant(status["observed_at"])).total_seconds() > 3600
    ):
        return "UNVERIFIED", observed
    return "CLEAR", observed


def calculate(manifest, cal, feature_sink=None):
    """Offline engine: the manifest is the entire read set. No storage/network calls."""
    features = {}
    series = {}
    failures = {}
    events = []
    per_rule = {}
    byid = {i["id"]: i for i in manifest["instruments"]}
    trade_ids = set(manifest["universe"]["value"]["members"])
    for iid, bars in manifest["bars"].items():
        if iid in manifest.get("source_failures", {}):
            failures[iid] = manifest["source_failures"][iid]
            continue
        if not bars:
            failures[iid] = "NO_DATA"
            continue
        if any(b.get("quality_status", "OK") != "OK" or not b.get("is_final", True) for b in bars):
            failures[iid] = "LATEST_REVISION_INVALID"
            continue
        if bars[-1]["market_date"] != manifest["source_market_date"]:
            failures[iid] = "DATA_STALE"
            continue
        expected = cal.dates(bars[0]["market_date"], bars[-1]["market_date"])
        if [b["market_date"] for b in bars] != expected:
            failures[iid] = "MISSING_INTERMEDIATE_SESSION"
            continue
        try:
            corrected = adjust(
                bars, [e for e in manifest["events"] if e["instrument_id"] == iid], manifest["cutoff_at"]
            )
            fs = build_features(
                corrected,
                cal,
                iid,
                digest(
                    [
                        e
                        for e in manifest["events"]
                        if e["instrument_id"] == iid and e["event_type"] in ("SPLIT", "DIVIDEND")
                    ]
                ),
            )
        except ValueError as ex:
            failures[iid] = str(ex)
            continue
        for f, b in zip(fs, bars):
            f["evaluated_at"] = b.get("available_at") or stamp(
                instant(b["bar_end_at"]) + timedelta(minutes=90)
            )
        if len(fs) < 260:
            failures[iid] = "INSUFFICIENT_HISTORY"
            continue
        # Current live confirmations are timestamped when the snapshot became available.
        fs[-1]["evaluated_at"] = manifest["cutoff_at"]
        features[iid] = fs[-1]
        series[iid] = fs
        # Reference ETFs supply context only; their signals cannot create peer resonance.
        if iid not in trade_ids:
            continue
        es, ev = run_rules(
            fs,
            cal,
            manifest["rules"]["value"]["rules"],
            [e for e in manifest["events"] if e["instrument_id"] == iid and e["event_type"] == "EARNINGS"],
            effective_from=manifest["rules"]["effective_from"],
        )
        events.extend(es)
        per_rule[iid] = ev
    events.sort(key=lambda e: e["event_id"])
    spy_id = next((i["id"] for i in manifest["instruments"] if i["current_ticker"] == "SPY"), None)
    vix_id = next((i["id"] for i in manifest["instruments"] if i["current_ticker"] == "VIX"), None)
    spy = features.get(spy_id)
    vix = features.get(vix_id)
    hard = any(
        e["event_type"] == "MARKET_HALT"
        and e["status"] == "ACTIVE"
        and e["payload_json"].get("until", manifest["cutoff_at"]) >= manifest["cutoff_at"]
        for e in manifest["events"]
    )
    value_event = next(
        (
            e
            for e in manifest["events"]
            if e["event_type"] == "VIX_VALUE"
            and e["payload_json"].get("market_date") == manifest["source_market_date"]
        ),
        None,
    )
    vix_value = vix["close"] if vix else float(value_event["payload_json"]["value"]) if value_event else None
    fred = next(
        (s for s in manifest.get("source_reports", []) if s["source"] == "FRED" and s.get("job_id")), None
    )
    if fred:
        value_event = next(
            (
                e
                for e in reversed(manifest["events"])
                if e["event_type"] == "VIX_VALUE"
                and e.get("source") == "FRED:VIXCLS"
                and e["payload_json"].get("market_date") == manifest["source_market_date"]
            ),
            None,
        )
        vix_value = float(value_event["payload_json"]["value"]) if value_event else None
    unavailable_fred = fred and (fred["status"] != "SUCCEEDED" or fred["health"]["freshness"] != "OK")
    if unavailable_fred:
        vix_value = None
    env = environment(spy, vix_value, manifest.get("previous_environment"), hard)
    env.update(
        vix_as_of=value_event["payload_json"]["market_date"] if value_event else None,
        vix_observed_at=value_event["known_at"] if value_event else None,
        vix_source=value_event.get("source") if value_event else None,
        vix_event_ref=dict(event_id=value_event["event_id"], revision=value_event["revision"])
        if value_event
        else None,
    )
    if unavailable_fred:
        env["reason_code"] = fred.get("reason_code") or "VIX_SOURCE_UNAVAILABLE"
    if fred and fred.get("latest_market_date") and fred.get("latest_value"):
        aligned = next(
            (f for f in series.get(spy_id, []) if f["source_market_date"] == fred["latest_market_date"]), None
        )
        if aligned:
            historical_mode = environment(aligned, float(fred["latest_value"]))
            env["last_aligned"] = dict(
                market_date=fred["latest_market_date"],
                mode=historical_mode["mode"],
                vix=fred["latest_value"],
                spy_close=str(aligned["close"]),
                reference_only=True,
            )
    previous_events = {e["event_id"]: e for e in manifest.get("previous_events", [])}
    for e in events:
        old = previous_events.get(e["event_id"])
        if old:
            e["stage"] = old["stage"]
            if old.get("confirmed_at") and old.get("confirmation_market_date") == e.get(
                "confirmation_market_date"
            ):
                e["confirmed_at"] = old["confirmed_at"]
    current_ids = {e["event_id"] for e in events}
    for old in previous_events.values():
        if (
            old["event_id"] not in current_ids
            and old["instrument_id"] in features
            and old["lifecycle"] != "RETRACTED"
            and old["rule_version"] == manifest["rules"]["value"]["rules"][old["rule_id"]]["version"]
        ):
            events.append(
                {
                    **old,
                    "lifecycle": "RETRACTED",
                    "resolution": "SOURCE_REVISION",
                    "expiry_market_date": old["valid_until"][:10],
                }
            )
    events.sort(key=lambda e: e["event_id"])
    # Sector-relative strength is derived from both aligned input series, never a UI assertion.
    instruments = []
    for inst in manifest["instruments"]:
        inst = {**inst, "profile_json": dict(inst["profile_json"])}
        p = inst["profile_json"]
        sector = series.get(p.get("sector_etf_id"))
        sp = series.get(p.get("market_ref_id"))
        if sector and sp:
            ratios = {a["source_market_date"]: a["close"] for a in sector}
            aligned = [
                ratios[x["source_market_date"]] / x["close"] for x in sp if x["source_market_date"] in ratios
            ]
            p["relative_strength_above_ma20"] = (
                aligned[-1] > sum(aligned[-20:]) / 20 if len(aligned) >= 20 else None
            )
        instruments.append(inst)
    cards = []
    for production in (False, True):
        cards.extend(
            aggregate(events, features, instruments, env, manifest["cutoff_at"], production=production)
        )
    for c in cards:
        inst = byid[c["instrument_id"]]
        f = features[c["instrument_id"]]
        status, observed = gate_events(
            c["instrument_id"],
            manifest["events"],
            manifest["event_config"]["value"],
            manifest.get("security_statuses", {}),
            cal,
            instant(manifest["cutoff_at"]),
        )
        c["context"].update(
            event_status=status,
            status_verified_at=observed,
            environment_mode=env["mode"],
            source_market_date=manifest["source_market_date"],
            source=manifest["provider"]["value"],
            data_mode=manifest["data_mode"],
        )
        if manifest["provider"]["value"]["mode"] == "TIINGO_EOD_CACHE":
            c["availability"] = "BLOCKED"
            c["reason_codes"].append("SOURCE_SESSION_SCOPE_UNVERIFIED")
        if status != "CLEAR":
            c["availability"] = "BLOCKED"
            c["reason_codes"].append("EVENT_OR_STATUS_UNVERIFIED")
        bars = manifest["bars"][c["instrument_id"]]
        if any(
            b.get("quality_json", {}).get("provider_fields", {}).get("source_provider") == "ALPHA_VANTAGE"
            or b.get("provider_fields", {}).get("source_provider") == "ALPHA_VANTAGE"
            for b in bars[-20:]
        ):
            c["availability"] = "BLOCKED"
            c["reason_codes"].append("CORPORATE_ACTIONS_UNVERIFIED")
            c["context"].update(fallback_source="ALPHA_VANTAGE", corporate_action_coverage="UNVERIFIED")
        liquidity = sum(Decimal(b["close"]) * Decimal(b["volume"]) for b in bars[-20:]) / 20
        if (
            Decimal(bars[-1]["close"]) < 5
            or liquidity < 50000000
            or (
                inst["asset_type"] != "ETF"
                and Decimal(str(inst["profile_json"].get("market_cap", 0))) < 5000000000
            )
        ):
            c["availability"] = "BLOCKED"
            c["reason_codes"].append("UNIVERSE_LIQUIDITY_OR_METADATA")
    if feature_sink is not None:
        feature_sink.update(series)
    return dict(
        schema_version="business-output-1",
        source_market_date=manifest["source_market_date"],
        environment=env,
        features=features,
        events=events,
        cards=[c for c in cards if c["instrument_id"] in trade_ids],
        rule_evaluability=per_rule,
        failures=failures,
        coverage=dict(
            expected=len(trade_ids),
            ok=sum(i in features for i in trade_ids),
            failed=sum(i not in features for i in trade_ids),
        ),
        data_mode=manifest["data_mode"],
    )


def business_view(output):
    """Keep semantic input times; remove publication/run identities only."""

    def clean(x):
        if isinstance(x, list):
            return [clean(v) for v in x]
        if isinstance(x, dict):
            return {
                k: clean(v)
                for k, v in x.items()
                if k
                not in (
                    "confirmed_at",
                    "triggered_at",
                    "evaluated_at",
                    "received_at",
                    "revision",
                    "input_run_id",
                )
            }
        return x

    return clean(output)


def persist(cx, output, manifest, jid, files):
    event_refs = {}
    known = set()
    for e in output["events"]:
        e = dict(e)
        old = one(
            cx, "SELECT * FROM rule_events WHERE event_id=? ORDER BY revision DESC LIMIT 1", (e["event_id"],)
        )
        if old:
            # Approval is prospective, and original actual confirmation time is immutable.
            e["stage"] = old["stage"]
            if old["confirmed_at"] and e.get("confirmation_market_date") == old["confirmation_market_date"]:
                e["confirmed_at"] = old["confirmed_at"]
        semantic = {k: v for k, v in e.items() if k not in ("triggered_at", "confirmed_at")}
        h = digest(semantic)
        if old and old["content_hash"] == h:
            event_refs[e["event_id"]] = dict(event_id=e["event_id"], revision=old["revision"])
            continue
        rev = old["revision"] + 1 if old else 1
        insert(
            cx,
            "rule_events",
            dict(
                id=uid("eventrev"),
                event_id=e["event_id"],
                revision=rev,
                instrument_id=e["instrument_id"],
                rule_id=e["rule_id"],
                rule_version=e["rule_version"],
                family=e["family"],
                kind=e["kind"],
                direction=e["direction"],
                trigger_market_date=e["trigger_market_date"],
                confirmation_market_date=e.get("confirmation_market_date"),
                triggered_at=e["triggered_at"],
                confirmed_at=e.get("confirmed_at"),
                lifecycle=e["lifecycle"],
                resolution=e.get("resolution"),
                valid_until=e["valid_until"],
                frozen_json=e["frozen"],
                evidence_json=e["evidence"],
                stage=e["stage"],
                input_run_id=jid,
                content_hash=h,
            ),
        )
        event_refs[e["event_id"]] = dict(event_id=e["event_id"], revision=rev)
        known.add(e["event_id"])
    refs = []
    for c in output["cards"]:
        h = digest(c)
        old = one(
            cx, "SELECT * FROM signal_cards WHERE card_id=? ORDER BY revision DESC LIMIT 1", (c["card_id"],)
        )
        if old and old["content_hash"] == h:
            refs.append(dict(card_id=c["card_id"], revision=old["revision"]))
            continue
        rev = old["revision"] + 1 if old else 1
        insert(
            cx,
            "signal_cards",
            dict(
                id=uid("cardrev"),
                card_id=c["card_id"],
                revision=rev,
                view_kind=c["view_kind"],
                instrument_id=c["instrument_id"],
                family=c["family"],
                direction=c["direction"],
                trigger_market_date=c["trigger_market_date"],
                rule_refs_json=[event_refs[e] for e in c["rule_refs"]],
                primary_rule_ref=c["primary_rule_ref"],
                quality_score=c["quality_score"],
                quality_grade=c["quality_grade"],
                effective_grade=c["effective_grade"],
                evidence_groups_json=c["evidence_groups"],
                lifecycle=c["lifecycle"],
                severity=c["severity"],
                availability=c["availability"],
                reason_codes_json=c["reason_codes"],
                score_trace_json=c["score_trace"],
                context_json=c["context"],
                evidence_hash=digest(
                    [
                        c["primary_rule_ref"],
                        c["reason_codes"],
                        [event_refs[e] for e in c["rule_refs"]],
                        c["score_trace"],
                    ]
                ),
                valid_until=c["valid_until"],
                data_as_of=c["data_as_of"],
                input_run_id=jid,
                content_hash=h,
            ),
        )
        refs.append(dict(card_id=c["card_id"], revision=rev))
    oldjob, oldspace = latest_workspace(cx, files)
    if oldspace:
        current = {r["card_id"] for r in refs}
        # Preserve failed symbols explicitly; do not substitute a healthy empty list.
        for ref in oldspace.get("cards", []):
            old = one(
                cx,
                "SELECT * FROM signal_cards WHERE card_id=? AND revision=?",
                (ref["card_id"], ref["revision"]),
            )
            if old and old["instrument_id"] in output["failures"] and ref["card_id"] not in current:
                refs.append({**ref, "carried_forward": True})
            elif old and ref["card_id"] not in current and old["lifecycle"] in ("CONFIRMED", "PENDING"):
                main = next((e for e in output["events"] if e["event_id"] == old["primary_rule_ref"]), None)
                terminal = (
                    main["lifecycle"]
                    if main and main["lifecycle"] in ("RETRACTED", "INVALIDATED", "EXPIRED")
                    else "EXPIRED"
                )
                values = {k: v for k, v in old.items() if k not in ("id", "created_at")}
                values.update(
                    id=uid("cardrev"),
                    revision=old["revision"] + 1,
                    lifecycle=terminal,
                    availability="BLOCKED",
                    input_run_id=jid,
                    reason_codes_json=old["reason_codes_json"] + ["SIGNAL_" + terminal],
                    content_hash=digest([old["content_hash"], terminal]),
                )
                insert(cx, "signal_cards", values)
    artifact = dict(
        kind="WORKSPACE",
        source_market_date=manifest["source_market_date"],
        data_as_of=manifest["session_context"]["previous_rth_close"]["utc"],
        generated_at=manifest["cutoff_at"],
        environment=output["environment"],
        features=output["features"],
        coverage=output["coverage"],
        partial=bool(output["coverage"]["failed"]),
        cards=refs,
        failures=output["failures"],
        input_hash=digest(manifest),
        session_context=manifest["session_context"],
        overnight_checklist=overnight(
            cx, manifest["events"], manifest["session_context"], manifest["cutoff_at"]
        ),
        provider_health={
            **manifest["provider_health"],
            "freshness": "STALE" if output["coverage"]["failed"] else "OK",
            "quality": "DEGRADED"
            if output["failures"]
            else "ROWS_VALID_SCOPE_UNVERIFIED"
            if manifest["provider"]["value"]["mode"] == "TIINGO_EOD_CACHE"
            else "OK",
        },
        business_output_hash=digest(business_view(output)),
        business_output_schema_version="business-output-1",
    )
    ref = files.save(artifact)
    seq = one(cx, "SELECT COALESCE(MAX(published_seq),0)+1 AS n FROM job_runs")["n"]
    update(
        cx,
        "job_runs",
        "id",
        jid,
        dict(
            status="PARTIAL" if artifact["partial"] else "SUCCEEDED",
            artifact_kind="WORKSPACE",
            output_manifest_ref=ref,
            input_hash=digest(manifest),
            published_seq=seq,
            published_at=stamp(),
            expected_count=output["coverage"]["expected"],
            ok_count=output["coverage"]["ok"],
            failed_count=output["coverage"]["failed"],
        ),
    )
    # Data changes revoke pending authorization, without deleting actual positions.
    for p in many(cx, "SELECT * FROM plans WHERE status IN ('RESERVED','PARTIALLY_FILLED')"):
        active = next((c for c in output["cards"] if c["card_id"] == p["card_id"]), None)
        if (
            not active
            or active["availability"] != "PLAN_ALLOWED"
            or active["context"]["adjustment_epoch"] != p["adjustment_epoch"]
        ):
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
            audit(cx, "PLAN_NEEDS_REVIEW", p["id"], "数据/资格变化，释放未成交占用；真实成交保留")
    return artifact


def overnight(cx, events, context, cutoff):
    groups = []
    states = many(cx, "SELECT * FROM account_state WHERE status='ACTIVE'")
    if not states:
        return [
            dict(
                account_id=None,
                positions_status="UNKNOWN",
                confirmation_status="UNVERIFIED",
                items=[dict(code="ACCOUNT_COMPLETENESS", status="UNKNOWN", reason="尚未建立账户")],
                broker_order_verified=False,
            )
        ]
    for a in states:
        s = one(cx, "SELECT * FROM account_snapshots WHERE id=?", (a["current_snapshot_id"],))
        positions = s["positions_json"] if s else []
        confirmed = bool(
            s and s["completeness"] == "COMPLETE" and a["confirmed_ledger_head"] == s["ledger_head_ref"]
        )
        items = [
            dict(
                code="ACCOUNT_COMPLETENESS",
                status="NO_KNOWN_ISSUE" if confirmed else "UNKNOWN",
                reason="账户已核对" if confirmed else "账户完整性未确认",
            )
        ]
        for p in positions:
            stop = p.get("stop_reference")
            fresh = bool(
                p.get("mark_as_of")
                and p.get("mark") is not None
                and p["mark_as_of"][:10] >= context["source_market_date"]
            )
            items.append(
                dict(
                    code="STOP_REFERENCE",
                    instrument_id=p["instrument_id"],
                    status="KNOWN_RISK"
                    if fresh and stop and Decimal(p["mark"]) <= Decimal(stop)
                    else "UNKNOWN"
                    if not fresh or not stop
                    else "NO_KNOWN_ISSUE",
                    observed_at=p["mark_as_of"],
                    reason="参考止损仅为记录，未核验券商订单",
                    stop_reference=stop,
                )
            )
        ids = {p["instrument_id"] for p in positions}
        for e in events:
            if (
                e["instrument_id"] in ids
                and e.get("scheduled_at")
                and cutoff <= e["scheduled_at"] <= context["next_rth_open"]["utc"]
            ):
                items.append(
                    dict(
                        code="NEXT_SESSION_EVENT",
                        status="KNOWN_RISK",
                        instrument_id=e["instrument_id"],
                        event_id=e["event_id"],
                        observed_at=e["known_at"],
                    )
                )
        pending = many(
            cx,
            "SELECT id,status FROM plans WHERE account_id=? AND status IN ('RESERVED','PARTIALLY_FILLED','NEEDS_REVIEW')",
            (a["account_id"],),
        )
        groups.append(
            dict(
                account_id=a["account_id"],
                scope=a["scope"],
                origin=a["origin"],
                account_snapshot_id=s["id"] if s else None,
                ledger_head=s["ledger_head_ref"] if s else None,
                confirmation_status="CONFIRMED" if confirmed else "UNVERIFIED",
                positions_status="HAS_RECORDED_POSITIONS"
                if positions
                else "CONFIRMED_EMPTY"
                if confirmed
                else "UNKNOWN",
                as_of=cutoff,
                items=items,
                pending_plans=pending,
                broker_order_verified=False,
            )
        )
    return groups
