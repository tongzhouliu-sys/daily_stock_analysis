"""Read-only screen projection. No provider requests, rule calculations or account writes."""

from src.services.signal_scanner.core import now, stamp
from src.services.signal_scanner.domain.features import adjust
from src.services.signal_scanner.domain.rules import RULESET
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.services.source_data import current_environment, latest_sources
from src.services.signal_scanner.storage.db import many, one
from src.services.signal_scanner.worker.pipeline import latest_workspace, load_events, source_failure


def screen_data(cx, files, cal):
    job, workspace = latest_workspace(cx, files)
    sources = latest_sources(cx, files)
    scan = one(
        cx,
        "SELECT * FROM job_runs WHERE job_name='DAILY_SCAN' AND published_seq IS NOT NULL AND input_manifest_ref IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
    )
    manifest = files.read(scan["input_manifest_ref"]) if scan else None
    events = load_events(cx, stamp())
    instruments = {i["id"]: i for i in many(cx, "SELECT * FROM instruments")}
    spy_id = next((iid for iid, i in instruments.items() if i["current_ticker"] == "SPY"), None)
    bars = manifest.get("bars", {}).get(spy_id, []) if manifest else []
    chart = (
        adjust(
            bars,
            [
                e
                for e in manifest["events"]
                if e.get("instrument_id") == spy_id and e["event_type"] in ("SPLIT", "DIVIDEND")
            ],
            manifest["cutoff_at"],
        )
        if bars
        else []
    )
    chart = [
        dict(
            date=b["market_date"],
            open=str(b["open"]),
            high=str(b["high"]),
            low=str(b["low"]),
            close=str(b["close"]),
            volume=str(b["volume"]),
        )
        for b in chart
    ]
    current_events = {
        e["event_id"]: e for e in many(cx, "SELECT * FROM rule_events ORDER BY event_id,revision")
    }
    active = [
        e
        for e in current_events.values()
        if e["lifecycle"] in ("PENDING", "CONFIRMED") and e["valid_until"] > stamp()
    ]
    rules = []
    cfg = get_config(cx, "rules")["value"]["rules"]
    for rid, spec in RULESET.items():
        es = [e for e in active if e["rule_id"] == rid]
        rules.append(
            dict(
                id=rid,
                kind=spec["kind"],
                family=spec["family"],
                stage=cfg[rid]["stage"],
                confirmed=sum(e["lifecycle"] == "CONFIRMED" for e in es),
                pending=sum(e["lifecycle"] == "PENDING" for e in es),
            )
        )
    cards = []
    if workspace:
        for ref in workspace["cards"]:
            row = one(
                cx,
                "SELECT * FROM signal_cards WHERE card_id=? AND revision=?",
                (ref["card_id"], ref["revision"]),
            )
            inst = instruments[row["instrument_id"]]
            cards.append(
                dict(
                    id=row["card_id"],
                    ticker=inst["current_ticker"],
                    family=row["family"],
                    grade=row["effective_grade"],
                    score=row["quality_score"],
                    severity=row["severity"],
                    context=dict(score_missing_inputs=row["context_json"].get("score_missing_inputs", [])),
                    status=row["lifecycle"],
                    availability=row["availability"],
                    view=row["view_kind"],
                )
            )
    cards.sort(key=lambda c: (c["score"] is None, -(c["score"] or 0), c["ticker"], c["id"]))
    states = many(cx, "SELECT * FROM account_state WHERE status='ACTIVE'")
    profile = get_config(cx, "risk")
    fred_values = [
        dict(date=e["payload_json"]["market_date"], value=e["payload_json"]["value"])
        for e in events
        if e["event_type"] == "VIX_VALUE" and e.get("source") == "FRED:VIXCLS"
    ]
    source_fault = source_failure(cx)
    return dict(
        generated_at=stamp(),
        snapshot_id=job["id"] if job else None,
        snapshot_published_at=job["published_at"] if job else None,
        source_market_date=workspace["source_market_date"] if workspace else None,
        data_as_of=workspace["data_as_of"] if workspace else None,
        data_mode=manifest.get("data_mode") if manifest else None,
        fixture_mode=bool(workspace and workspace.get("provider_health", {}).get("feed") == "FICTIONAL_ONLY"),
        coverage=workspace["coverage"] if workspace else dict(expected=0, ok=0, failed=0),
        latest_source_failed=bool(source_fault),
        source_failure=source_fault["error_json"] if source_fault else None,
        environment=current_environment(workspace, sources),
        sources=sources,
        spy=dict(
            ticker="SPY",
            prices=chart,
            features=workspace.get("features", {}).get(spy_id) if workspace else None,
            price_basis="ADJUSTED_CORPORATE_ACTIONS",
            scope="EOD_PROVIDER_DECLARED"
            if manifest and manifest["provider"]["value"]["mode"] == "TIINGO_EOD_CACHE"
            else "RTH",
        ),
        vix_history=sorted(fred_values, key=lambda x: x["date"])[-320:],
        rules=rules,
        cards=cards,
        counts=dict(
            research=sum(c["view"] == "RESEARCH" for c in cards),
            production=sum(c["view"] == "PRODUCTION" for c in cards),
            plan_allowed=sum(
                c["availability"] == "PLAN_ALLOWED" and c["view"] == "PRODUCTION" for c in cards
            ),
            accounts=len(states),
            bar_revisions=one(cx, "SELECT COUNT(*) AS n FROM bar_revisions")["n"],
            market_events=one(cx, "SELECT COUNT(*) AS n FROM market_event_revisions")["n"],
        ),
        risk=dict(
            policy_version=profile["version"],
            policy=profile["value"],
            account_status="UNKNOWN" if not states else "REQUIRES_ACCOUNT_REVIEW",
            locks=[
                dict(account_id=a["account_id"], scope=a["scope"], type=lock["type"])
                for a in states
                for lock in a["locks_json"]
            ],
        ),
        events=[
            dict(
                id=e["event_id"],
                type=e["event_type"],
                ticker=instruments.get(e.get("instrument_id"), {}).get("current_ticker"),
                date=e.get("scheduled_date") or (e.get("effective_at") or "")[:10],
                status=e["status"],
                published_at=e.get("published_at"),
                payload=e["payload_json"],
                source=e["source"],
            )
            for e in sorted(
                events, key=lambda x: x.get("scheduled_date") or x.get("effective_at") or "", reverse=True
            )
            if e["event_type"] in ("EARNINGS", "SPLIT", "DIVIDEND")
        ][:12],
        jobs=[
            dict(
                id=j["id"],
                name=j["job_name"],
                status=j["status"],
                created_at=j["created_at"],
                error=j["error_json"],
            )
            for j in many(cx, "SELECT * FROM job_runs ORDER BY created_at DESC LIMIT 7")
        ],
        session_context=cal.context(now(), events),
        order_execution="FORBIDDEN",
        scope_note="只读已存快照；自动刷新不采集供应商、不扫描、不下单",
    )
