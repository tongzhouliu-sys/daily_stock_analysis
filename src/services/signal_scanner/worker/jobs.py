from __future__ import annotations

from src.services.signal_scanner.platform_support import locking as fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import zipfile
from datetime import timedelta

import httpx

from src.services.signal_scanner.core import DomainError, canonical, digest, instant, now, require, stamp, uid
from src.services.signal_scanner.adapters.market import AlpacaRTH, capability_enabled, ingest, load_bundle
from src.services.signal_scanner.domain.calendar import Calendar, SG
from src.services.signal_scanner.domain.features import ALGORITHM
from src.services.signal_scanner.domain.paper import outcomes
from src.services.signal_scanner.services.accounts import rebuild
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.services.source_data import latest_sources
from src.services.signal_scanner.storage.db import Database, audit, insert, many, one, update
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.pipeline import business_view, calculate, latest_workspace, load_events, overnight, persist


def enqueue(cx, name, key, market_date=None, progress=None, replay=False):
    old = one(cx, "SELECT * FROM job_runs WHERE logical_key=? ORDER BY revision DESC LIMIT 1", (key,))
    if old:
        return old["id"]
    jid = uid("job")
    insert(
        cx,
        "job_runs",
        dict(
            id=jid,
            logical_key=key,
            revision=(old["revision"] + 1) if old else 1,
            job_name=name,
            market_date=market_date,
            scheduled_slot=key,
            ruleset_hash=get_config(cx, "rules")["version"],
            status="QUEUED",
            progress_json=progress or {},
            replay=replay,
        ),
    )
    return jid


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.files = Artifacts(settings.runtime / "artifacts")
        self.cal = Calendar()
        self.owner = uid("worker")

    def tick(self):
        at = now()
        local = at.astimezone(SG)
        completed = self.cal.last_completed(at)
        s = self.cal.session(completed)
        with self.db.transaction() as cx:
            expired = many(cx, "SELECT * FROM job_runs WHERE status='RUNNING' AND lease_until<?", (stamp(),))
            for j in expired:
                update(
                    cx,
                    "job_runs",
                    "id",
                    j["id"],
                    dict(
                        status="QUEUED", lease_owner=None, error_json=dict(reason="RECOVERED_EXPIRED_LEASE")
                    ),
                )
            cx.execute(
                "UPDATE notifications SET status='UNKNOWN' WHERE status='SENDING' AND lease_until<?",
                (stamp(),),
            )
            daily_auto = (
                os.environ.get("SCANNER_DAILY_MARKET") == "true"
                and get_config(cx, "provider")["value"]["mode"] == "TIINGO_EOD_CACHE"
            )
            fresh_token = None
            if daily_auto:
                from src.services.signal_scanner.services.daily_market import scan_token

                fresh_token = scan_token(self.settings, completed)
            suffix = ":FRESH:" + fresh_token if fresh_token else ""
            for name, offset in (("DAILY_SCAN", 30), ("VERIFY", 90)):
                if at >= instant(s["close_at"]) + timedelta(minutes=offset):
                    if daily_auto and not fresh_token:
                        continue
                    key = f"{name}:{completed}:{get_config(cx, 'rules')['version']}" + suffix
                    enqueue(cx, name, key, completed)
            enqueue(
                cx,
                "STATUS",
                "STATUS:" + stamp(at.replace(minute=5, second=0, microsecond=0))
                if at.minute >= 5
                else "STATUS:bootstrap:" + local.date().isoformat(),
                completed,
            )
            scan_key = f"DAILY_SCAN:{completed}:{get_config(cx, 'rules')['version']}" + suffix
            digest_ready = not daily_auto or (
                fresh_token and one(cx, "SELECT id FROM job_runs WHERE logical_key=? "
                                    "AND published_seq IS NOT NULL", (scan_key,))
            )
            if local.hour >= 8 and digest_ready:
                key = "DIGEST:" + completed + ":" + local.date().isoformat() + suffix
                enqueue(cx, "DIGEST", key, completed)
            if (local.hour, local.minute) >= (9, 30):
                enqueue(cx, "BACKUP", "BACKUP:" + local.date().isoformat(), completed)
            if local.weekday() == 5 and local.hour >= 10:
                enqueue(cx, "WEEKLY_REPORT", "WEEKLY:" + local.strftime("%G-W%V") + ":SYSTEM", completed)
        self.run_one()
        self.deliver()

    def claim(self):
        with self.db.transaction() as cx:
            j = one(cx, "SELECT * FROM job_runs WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1")
            if not j:
                return None
            update(
                cx,
                "job_runs",
                "id",
                j["id"],
                dict(
                    status="RUNNING",
                    lease_owner=self.owner,
                    lease_until=stamp(now() + timedelta(seconds=120)),
                    heartbeat_at=stamp(),
                    attempt_count=j["attempt_count"] + 1,
                ),
            )
            j["status"] = "RUNNING"
            return j

    def run_one(self):
        j = self.claim()
        if not j:
            return False
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(30):
                with self.db.transaction() as cx:
                    cx.execute(
                        "UPDATE job_runs SET lease_until=?,heartbeat_at=? WHERE id=? AND lease_owner=? AND status=?",
                        (stamp(now() + timedelta(seconds=120)), stamp(), j["id"], self.owner, "RUNNING"),
                    )

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            name = j["job_name"]
            if name in ("DAILY_SCAN", "VERIFY"):
                self.scan(j)
            elif name == "STATUS":
                self.status(j)
            elif name == "DIGEST":
                self.digest(j)
            elif name == "BACKUP":
                self.backup(j)
            elif name == "WEEKLY_REPORT":
                self.weekly(j)
            elif name in ("REPLAY", "PAPER"):
                self.research(j)
            elif name == "PRIVACY_EXPORT":
                self.privacy_export(j)
            elif name == "PRIVACY_DELETE":
                self.privacy_delete(j)
            else:
                raise DomainError("UNSUPPORTED_JOB", status=422)
        except Exception as ex:
            # No external payload, secret or arbitrary exception text enters persisted logs.
            with self.db.transaction() as cx:
                update(
                    cx,
                    "job_runs",
                    "id",
                    j["id"],
                    dict(
                        status="FAILED",
                        error_json=dict(
                            code=ex.code if isinstance(ex, DomainError) else "INTERNAL_JOB_ERROR",
                            exception_type=type(ex).__name__,
                        ),
                    ),
                )
            if not isinstance(ex, DomainError):
                print("Worker failed: " + type(ex).__name__, flush=True)
        finally:
            stopped.set()
            thread.join(timeout=1)
        return True

    def fetch(self, j):
        with self.db.read() as cx:
            source = get_config(cx, "provider")
            instruments = many(cx, "SELECT * FROM instruments WHERE status='ACTIVE'")
            history = many(
                cx,
                "SELECT status,error_json,created_at FROM job_runs WHERE job_name IN ('DAILY_SCAN','VERIFY') AND id!=? ORDER BY created_at DESC LIMIT 3",
                (j["id"],),
            )
        if len(history) == 3 and all(
            x["status"] == "FAILED" and x["error_json"].get("code", "").startswith("SOURCE_") for x in history
        ):
            require(
                now() - instant(history[0]["created_at"]) > timedelta(minutes=30),
                "SOURCE_BREAKER_OPEN",
                status=503,
            )
        value = source["value"]
        require(value["mode"] != "DISABLED", "SOURCE_NOT_CONFIGURED", "行情源尚未配置", 503)
        require(
            capability_enabled(value, "EOD_DAILY" if value["mode"] == "TIINGO_EOD_CACHE" else "RTH_DAILY"),
            "SOURCE_PERMISSION_UNVERIFIED",
            "先核验并启用RTH日线能力",
            503,
        )
        if value["mode"] in ("LOCAL_FILE", "TIINGO_EOD_CACHE"):
            bundle = load_bundle(self.settings.runtime, value["file"])
        else:
            required = [c for c in value["capabilities"] if c["capability_id"] == "RTH_DAILY"]
            require(
                required
                and required[0]["enabled"]
                and required[0]["support"] == "SUPPORTED"
                and required[0]["entitlement"] == "GRANTED",
                "SOURCE_PERMISSION_UNVERIFIED",
                status=503,
            )
            target = j["market_date"]
            start = self.cal.session(target)["open_at"]
            end = self.cal.session(target)["close_at"]
            bundle = AlpacaRTH(value["feed"] or "sip").fetch(instruments, start, end, self.cal)
        with self.db.transaction() as cx:
            report = ingest(cx, bundle, self.cal, self.files)
        statuses = self.refresh_events()
        return statuses, bundle, report

    def refresh_events(self):
        with self.db.read() as cx:
            eventconf = get_config(cx, "events")
        statuses = {}
        if eventconf["value"]["mode"] == "LOCAL_FILE":
            pack = load_bundle(self.settings.runtime, eventconf["value"]["file"])
            from src.services.signal_scanner.core import reject_credentials

            reject_credentials(pack)
            statuses = {s["instrument_id"]: s for s in pack.get("security_statuses", [])}
            with self.db.transaction() as cx:
                for e in pack.get("events", []):
                    require(
                        e.get("known_at") and instant(e["known_at"]) <= now(), "VALIDATION_ERROR", status=422
                    )
                    for field in ("scheduled_at", "published_at", "effective_at"):
                        if e.get(field):
                            e[field] = stamp(instant(e[field]))
                    if e["event_type"] == "VIX_VALUE":
                        require(0 < float(e["payload"]["value"]) < 200, "VALIDATION_ERROR", status=422)
                    h = digest(e)
                    old = one(
                        cx,
                        "SELECT * FROM market_event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1",
                        (e["event_id"],),
                    )
                    if old and old["content_hash"] == h:
                        continue
                    insert(
                        cx,
                        "market_event_revisions",
                        dict(
                            id=uid("eventinput"),
                            event_id=e["event_id"],
                            revision=old["revision"] + 1 if old else 1,
                            instrument_id=e.get("instrument_id"),
                            event_type=e["event_type"],
                            scheduled_date=e.get("scheduled_date"),
                            scheduled_at=e.get("scheduled_at"),
                            published_at=e.get("published_at"),
                            effective_at=e.get("effective_at"),
                            known_at=stamp(instant(e["known_at"])),
                            source=e.get("source", "LOCAL_DECLARED"),
                            status=e.get("status", "SCHEDULED"),
                            payload_json=e.get("payload", {}),
                            content_hash=h,
                        ),
                    )
        return statuses

    def scan(self, j):
        statuses, bundle, ingest_report = self.fetch(j)
        cutoff = stamp()
        with self.db.read() as cx:
            pool = get_config(cx, "universe")
            require(pool["value"]["members"], "UNIVERSE_NOT_CONFIGURED", status=503)
            source = get_config(cx, "provider")
            insts = many(cx, "SELECT * FROM instruments WHERE status='ACTIVE'")
            data = {}
            for i in insts:
                bs = many(
                    cx,
                    "SELECT * FROM bar_revisions WHERE instrument_id=? AND provider=? AND feed=? AND market_date<=? ORDER BY market_date,revision",
                    (i["id"], bundle["provider"], bundle["feed"], j["market_date"]),
                )
                latest = {b["market_date"]: b for b in bs}
                data[i["id"]] = list(latest.values())
            oldjob, space = latest_workspace(cx, self.files)
            events = load_events(cx, cutoff)
            previous_events = []
            for e in many(cx, "SELECT * FROM rule_events ORDER BY event_id,revision"):
                previous_events.append(
                    dict(
                        event_id=e["event_id"],
                        instrument_id=e["instrument_id"],
                        rule_id=e["rule_id"],
                        rule_version=e["rule_version"],
                        family=e["family"],
                        kind=e["kind"],
                        direction=e["direction"],
                        trigger_market_date=e["trigger_market_date"],
                        triggered_at=e["triggered_at"],
                        stage=e["stage"],
                        confirmed_at=e["confirmed_at"],
                        confirmation_market_date=e["confirmation_market_date"],
                        lifecycle=e["lifecycle"],
                        resolution=e["resolution"],
                        valid_until=e["valid_until"],
                        frozen=e["frozen_json"],
                        evidence=e["evidence_json"],
                    )
                )
            previous_events = list({e["event_id"]: e for e in previous_events}.values())
            manifest = dict(
                schema_version="scan-input-1",
                cutoff_at=cutoff,
                source_market_date=j["market_date"],
                instruments=insts,
                bars=data,
                events=events,
                universe=pool,
                provider=source,
                source_reports=latest_sources(cx, self.files),
                event_config=get_config(cx, "events"),
                security_statuses=statuses,
                rules=get_config(cx, "rules"),
                calendar_version=self.cal.version,
                algorithm_version=ALGORITHM,
                previous_events=previous_events,
                previous_environment=space["environment"] if space else None,
                session_context=self.cal.context(instant(cutoff), events),
                data_mode="REVISED_HISTORY",
                risk_policy=get_config(cx, "risk")["value"],
                settlement_dates={
                    s["market_date"]: s["settlement_date"]
                    for s in many(
                        cx,
                        "SELECT * FROM market_sessions WHERE settlement_date IS NOT NULL ORDER BY verified_at",
                    )
                },
                security_status_history=[],
                historical_event_coverage={},
                source_failures={
                    e["instrument_id"]: e["reason"]
                    for e in ingest_report["invalid_records"]
                    if e["instrument_id"]
                },
                provider_health=dict(
                    connection="UP",
                    permission="UNKNOWN" if source["value"]["mode"] == "LOCAL_FILE" else "GRANTED",
                    freshness="PENDING",
                    quality="PENDING",
                    observed_at=cutoff,
                    feed=bundle["feed"],
                ),
            )
        if manifest["event_config"]["value"]["mode"] == "LOCAL_FILE":
            pack = load_bundle(self.settings.runtime, manifest["event_config"]["value"]["file"])
            manifest["security_status_history"] = pack.get("security_status_history", [])
            manifest["historical_event_coverage"] = pack.get("historical_event_coverage", {})
        inp_ref = self.files.save(manifest, "inputs")
        output = calculate(manifest, self.cal)
        with self.db.transaction() as cx:
            lease = one(cx, "SELECT * FROM job_runs WHERE id=?", (j["id"],))
            require(lease["lease_owner"] == self.owner and lease["lease_until"] > stamp(), "LEASE_EXPIRED")
            require(
                get_config(cx, "rules")["version"] == manifest["rules"]["version"]
                and get_config(cx, "universe")["version"] == pool["version"],
                "REVISION_CONFLICT",
            )
            update(cx, "job_runs", "id", j["id"], dict(input_manifest_ref=inp_ref))
            persist(cx, output, manifest, j["id"], self.files)
            for a in many(cx, "SELECT account_id FROM account_state WHERE status='ACTIVE'"):
                rebuild(cx, a["account_id"], self.cal)
            experiment = get_config(cx, "experiment")
            if experiment["value"]["stage"] == "SHADOW":
                enqueue(
                    cx,
                    "PAPER",
                    "PAPER:" + experiment["version"] + ":" + digest(manifest),
                    j["market_date"],
                    dict(
                        mode="SHADOW", experiment_config_version=experiment["version"], source_scan_id=j["id"]
                    ),
                )

    def status(self, j):
        statuses = self.refresh_events()
        with self.db.read() as cx:
            old, workspace = latest_workspace(cx, self.files)
            events = load_events(cx, stamp())
        if not workspace:
            with self.db.transaction() as cx:
                update(
                    cx,
                    "job_runs",
                    "id",
                    j["id"],
                    dict(status="SKIPPED", error_json=dict(code="NO_PUBLISHED_WORKSPACE")),
                )
            return
        space = {**workspace, "session_context": self.cal.context(now(), events)}
        with self.db.transaction() as cx:
            refs = []
            event_config = get_config(cx, "events")["value"]
            from src.services.signal_scanner.worker.pipeline import gate_events

            for r in space.get("cards", []):
                raw = one(
                    cx,
                    "SELECT * FROM signal_cards WHERE card_id=? AND revision=?",
                    (r["card_id"], r["revision"]),
                )
                if raw and raw["lifecycle"] in ("PENDING", "CONFIRMED") and raw["valid_until"] <= stamp():
                    vals = {k: v for k, v in raw.items() if k not in ("id", "created_at")}
                    vals.update(
                        id=uid("cardrev"),
                        revision=raw["revision"] + 1,
                        lifecycle="EXPIRED",
                        availability="BLOCKED",
                        input_run_id=j["id"],
                        reason_codes_json=raw["reason_codes_json"] + ["SIGNAL_EXPIRED"],
                        content_hash=digest([raw["content_hash"], "EXPIRED"]),
                    )
                    insert(cx, "signal_cards", vals)
                    r = {**r, "revision": vals["revision"]}
                elif raw and raw["lifecycle"] in ("PENDING", "CONFIRMED"):
                    status, verified = gate_events(
                        raw["instrument_id"], events, event_config, statuses, self.cal, now()
                    )
                    context = {**raw["context_json"], "event_status": status, "status_verified_at": verified}
                    reason = [
                        x
                        for x in raw["reason_codes_json"]
                        if x not in ("EVENT_OR_STATUS_UNVERIFIED", "EVENT_BLOCKED")
                    ]
                    availability = raw["availability"]
                    if status != "CLEAR":
                        availability = "BLOCKED"
                        reason.append("EVENT_BLOCKED")
                    if context != raw["context_json"] or availability != raw["availability"]:
                        vals = {k: v for k, v in raw.items() if k not in ("id", "created_at")}
                        vals.update(
                            id=uid("cardrev"),
                            revision=raw["revision"] + 1,
                            input_run_id=j["id"],
                            context_json=context,
                            availability=availability,
                            reason_codes_json=reason,
                            evidence_hash=digest([raw["evidence_hash"], status])
                            if status != raw["context_json"].get("event_status")
                            else raw["evidence_hash"],
                            content_hash=digest([raw["content_hash"], context, availability]),
                        )
                        insert(cx, "signal_cards", vals)
                        r = {**r, "revision": vals["revision"]}
                refs.append(r)
            space["cards"] = refs
            space["overnight_checklist"] = overnight(cx, events, space["session_context"], stamp())
            ref = self.files.save(space)
            seq = one(cx, "SELECT COALESCE(MAX(published_seq),0)+1 AS n FROM job_runs")["n"]
            update(
                cx,
                "job_runs",
                "id",
                j["id"],
                dict(
                    status="SUCCEEDED",
                    artifact_kind="WORKSPACE",
                    output_manifest_ref=ref,
                    published_seq=seq,
                    published_at=stamp(),
                ),
            )
            for p in many(
                cx,
                "SELECT * FROM plans WHERE status IN ('RESERVED','PARTIALLY_FILLED') AND valid_until<=?",
                (stamp(),),
            ):
                update(
                    cx,
                    "plans",
                    "id",
                    p["id"],
                    dict(
                        status="EXPIRED",
                        qty_remaining=0,
                        risk_reserved="0",
                        cash_reserved="0",
                        symbol_notional_reserved="0",
                        sector_notional_reserved="0",
                        row_version=p["row_version"] + 1,
                    ),
                )

    def digest(self, j):
        from src.services.signal_scanner.services.plans import card

        with self.db.transaction() as cx:
            job, space = latest_workspace(cx, self.files)
            preferences = get_config(cx, "preferences")
            context = self.cal.context(
                now(), load_events(cx, stamp()), preferences["value"]["display_timezone"]
            )
            cards = [card(cx, r["card_id"], r["revision"]) for r in space.get("cards", [])] if space else []
            production = [
                c for c in cards if c["view_kind"] == "PRODUCTION" and c["availability"] == "PLAN_ALLOWED"
            ]
            production.sort(
                key=lambda c: (
                    {"HIGH": 0, "TRACK": 1, "WATCH": 2}.get(c["effective_grade"], 3),
                    -(c["quality_score"] or 0),
                    c["primary_rule"] != "EV-01",
                    c["id"],
                )
            )
            notice = context.get("dst_notice")
            if notice:
                old = one(
                    cx,
                    "SELECT id FROM audit_events WHERE action='DST_HINT_EMBEDDED' AND object_id=?",
                    (notice["key"],),
                )
                if old:
                    notice = None
                else:
                    audit(
                        cx,
                        "DST_HINT_EMBEDDED",
                        notice["key"],
                        "摘要内嵌时区提示",
                        object_type="DST_NOTICE",
                        artifact_id=j["id"],
                    )
            artifact = dict(
                kind="DIGEST",
                generated_at=stamp(),
                source_market_date=context["source_market_date"],
                action_market_date=context["action_market_date"],
                data_as_of=space["data_as_of"] if space else None,
                coverage=space["coverage"] if space else None,
                partial=space["partial"] if space else True,
                environment=space["environment"] if space else dict(mode="UNKNOWN"),
                session_context=context,
                overnight_checklist=space["overnight_checklist"]
                if space
                else [dict(positions_status="UNKNOWN")],
                dst_notice=notice,
                production_candidates=production[:5],
                research_count=sum(c["view_kind"] == "RESEARCH" for c in cards),
                message="常规会话结果＋截至时点事件；未配置扩展行情，不代表完整盘后回顾",
                state="UNAVAILABLE" if not space else "READY" if cards else "EMPTY_VALID",
                read_target_minutes=10,
            )
            ref = self.files.save(artifact, "digests")
            update(
                cx,
                "job_runs",
                "id",
                j["id"],
                dict(status="SUCCEEDED", artifact_kind="DIGEST", output_manifest_ref=ref),
            )
            pref = preferences["value"]
            local = now().astimezone(SG)
            if pref["external_channel"] != "NONE":
                existing = one(
                    cx,
                    "SELECT n.id FROM notifications n JOIN job_runs r ON r.id=n.artifact_id WHERE n.channel='WEBHOOK' AND n.message_type='DIGEST' AND n.status!='SUPPRESSED' AND r.market_date=?",
                    (context["source_market_date"],),
                )
                suppressed = bool(
                    pref["paused"] or local.hour != 8 or local.minute >= 30 or not production or existing
                )
                insert(
                    cx,
                    "notifications",
                    dict(
                        id=uid("notification"),
                        artifact_id=j["id"],
                        artifact_revision=j["revision"],
                        recipient_id="owner",
                        channel="WEBHOOK",
                        message_type="DIGEST",
                        status="SUPPRESSED" if suppressed else "PENDING",
                        delivery_id=uid("delivery"),
                        expires_at=stamp(now() + timedelta(minutes=max(0, 30 - local.minute))),
                    ),
                )

    def research(self, j):
        with self.db.read() as cx:
            selected = j["progress_json"].get("source_scan_id")
            scan = (
                one(cx, "SELECT * FROM job_runs WHERE id=? AND input_manifest_ref IS NOT NULL", (selected,))
                if selected
                else one(
                    cx,
                    "SELECT * FROM job_runs WHERE job_name='DAILY_SCAN' AND input_manifest_ref IS NOT NULL AND published_seq IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
                )
            )
            require(scan, "DATA_INCOMPLETE", "尚无可回放输入")
        manifest = self.files.read(scan["input_manifest_ref"])
        feature_series = {}
        output = calculate(manifest, self.cal, feature_series)
        records = outcomes(
            output["events"], manifest["bars"], self.cal, manifest["cutoff_at"], actions=manifest["events"]
        )
        from src.services.signal_scanner.domain.portfolio import simulate

        with self.db.read() as cx:
            version = j["progress_json"].get("experiment_config_version")
            frozen = (
                one(
                    cx,
                    "SELECT payload_json FROM config_versions WHERE config_key='experiment' AND version=?",
                    (version,),
                )
                if version
                else None
            )
            experiment = frozen["payload_json"] if frozen else get_config(cx, "experiment")["value"]
        requested_mode = experiment["data_mode"]
        # Current-history bars cannot prove what a provider actually exposed on a historical date.
        experiment = {**experiment, "data_mode": manifest.get("data_mode", "REVISED_HISTORY")}
        strategy = simulate(manifest, output, self.cal, experiment, feature_series)
        artifact = dict(
            kind="RESEARCH_REPORT",
            generated_at=stamp(),
            rule_hash=digest(manifest["rules"]),
            input_hash=digest(manifest),
            data_mode="REVISED_HISTORY",
            requested_data_mode=requested_mode,
            degradation_reason="POINT_IN_TIME_HISTORY_UNVERIFIED"
            if requested_mode != experiment["data_mode"]
            else None,
            biases=[
                "来源历史可见时间不完整时仅作事后修订研究",
                "当前池可能有幸存者偏差",
                "重叠持有期及同业事件非独立样本",
            ],
            business_output_hash=digest(business_view(output)),
            outcomes=records,
            approval="RESEARCH",
            paper_strategy=strategy,
        )
        if j["progress_json"].get("mode") == "SHADOW":
            aid = "shadow_" + digest([experiment, manifest])[:24]
            with self.db.transaction() as cx:
                if not one(cx, "SELECT account_id FROM account_state WHERE account_id=?", (aid,)):
                    start = next(bs[0]["market_date"] for bs in manifest["bars"].values() if bs)
                    riskday = self.cal.risk_date(instant(self.cal.session(start)["open_at"]))
                    profile = dict(
                        name="SHADOW冻结实验",
                        initial_cash=experiment["initial_cash"],
                        initial_positions=[],
                        as_of=self.cal.session(start)["open_at"],
                        start_market_date=start,
                        initial_risk_date=riskday,
                        initial_month=riskday[:7],
                        day_open_equity=experiment["initial_cash"],
                        month_open_equity=experiment["initial_cash"],
                        funds_complete=True,
                    )
                    insert(
                        cx,
                        "account_state",
                        dict(
                            account_id=aid,
                            scope="PAPER",
                            origin="SHADOW_EXPERIMENT",
                            currency="USD",
                            profile_json=profile,
                        ),
                    )
                    for index, t in enumerate(strategy["trades"]):
                        insert(
                            cx,
                            "ledger_entries",
                            dict(
                                id=uid("shadow_entry"),
                                account_id=aid,
                                entry_type=t["entry_type"],
                                instrument_id=t["instrument_id"],
                                occurred_at=t["occurred_at"],
                                market_date=t["market_date"],
                                qty=t["qty"],
                                price=t["price"],
                                cash_amount=t["cash_amount"],
                                fee=t["fee"],
                                currency="USD",
                                settlement_date=t.get("settlement_date"),
                                settlement_state="UNSETTLED" if t["entry_type"] == "SELL" else "SETTLED",
                                origin="SHADOW_EXPERIMENT",
                                external_key=aid + ":" + str(index),
                                payload_json=t.get("payload", {}),
                            ),
                        )
                    rebuild(cx, aid, self.cal)
            artifact["shadow_account_id"] = aid
            for r in strategy["closed_episodes"]:
                r["account_id"] = aid
        self.finish_artifact(j, artifact, "REPORT")

    def finish_artifact(self, j, artifact, kind="REPORT", status="SUCCEEDED"):
        ref = self.files.save(artifact, "reports")
        with self.db.transaction() as cx:
            update(
                cx,
                "job_runs",
                "id",
                j["id"],
                dict(status=status, artifact_kind=kind, output_manifest_ref=ref),
            )

    def weekly(self, j):
        from src.services.signal_scanner.domain.reports import morning_metrics

        checks = []
        probe = dict(status="SKIPPED", reason="NO_ELIGIBLE_SNAPSHOT")
        with self.db.read() as cx:
            since = stamp(now() - timedelta(days=7))
            runs = many(cx, "SELECT * FROM job_runs WHERE created_at>=?", (since,))
            scan = one(
                cx,
                "SELECT * FROM job_runs WHERE job_name='DAILY_SCAN' AND status='SUCCEEDED' AND input_manifest_ref IS NOT NULL AND published_seq IS NOT NULL AND created_at>=? ORDER BY published_seq DESC LIMIT 1",
                (since,),
            )
            preferences = get_config(cx, "preferences")
            decisions = many(cx, "SELECT * FROM decisions ORDER BY server_time,id")
            backup = one(
                cx,
                "SELECT * FROM job_runs WHERE job_name='BACKUP' AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
            )
            provider = get_config(cx, "provider")
            configs = many(
                cx, "SELECT * FROM config_versions WHERE config_key='provider' ORDER BY effective_from"
            )
            instruments = many(cx, "SELECT * FROM instruments")
            revisions = many(cx, "SELECT * FROM bar_revisions WHERE created_at>=?", (since,))
            actions = many(
                cx,
                "SELECT * FROM market_event_revisions WHERE event_type IN ('SPLIT','DIVIDEND') AND created_at>=?",
                (since,),
            )
        scan_runs = [r for r in runs if r["job_name"] in ("DAILY_SCAN", "VERIFY")]
        refs = [r["id"] for r in scan_runs]

        def check(code, status, reason, **details):
            checks.append(
                dict(
                    code=code,
                    status=status,
                    scope="已登记配置及本机本周输入；不代表上游许可或外部完整性认证",
                    as_of=stamp(),
                    reason=reason,
                    evidence_refs=refs,
                    **details,
                )
            )

        changed = [c for c in configs if c["created_at"] >= since]
        has_baseline = any(c["created_at"] < since for c in configs)
        check(
            "CAPABILITY_CHANGE",
            "WARN" if changed else "PASS" if has_baseline else "NOT_CHECKED",
            "配置发生变更，须核对来源证据"
            if changed
            else "已与本机期初配置比较"
            if has_baseline
            else "尚无期初配置基准",
            provider_version=provider["version"],
            changed_versions=[c["version"] for c in changed],
        )
        completed = [r for r in scan_runs if r["status"] in ("SUCCEEDED", "PARTIAL", "FAILED")]
        bad = [r for r in completed if r["status"] != "SUCCEEDED" or r["failed_count"]]
        check(
            "SESSION_COVERAGE",
            "WARN" if bad else "PASS" if completed else "NOT_CHECKED",
            "逐运行检查预期、成功及失败数；不推断未配置证券已覆盖",
            completed_runs=len(completed),
            runs=[
                dict(
                    id=r["id"],
                    market_date=r["market_date"],
                    status=r["status"],
                    expected=r["expected_count"],
                    ok=r["ok_count"],
                    failed=r["failed_count"],
                )
                for r in completed
            ],
        )
        quarantined = [r for r in revisions if r["quality_status"] != "OK"]
        check(
            "REVISIONS_AND_ACTIONS",
            "WARN" if quarantined else "PASS" if revisions or actions else "NOT_CHECKED",
            "检查本机收到的修订与行动；未收到不等于外部没有行动",
            bar_revisions=len(revisions),
            revised_bars=sum(r["revision"] > 1 for r in revisions),
            quarantined=len(quarantined),
            actions=len(actions),
        )
        identities = [(i["current_ticker"], i["primary_venue"]) for i in instruments]
        duplicate = len(identities) != len(set(identities))
        check(
            "IDENTITY_MAPPING",
            "WARN" if duplicate else "PASS" if identities else "NOT_CHECKED",
            "检查稳定证券ID及当前代码/交易所重复；历史别名仍以导入事实为准",
            instrument_count=len(identities),
            duplicate_current_mapping=duplicate,
        )
        bases = {}
        for b in revisions:
            bases.setdefault(b["instrument_id"], set()).add((b["provider"], b["feed"], b["coverage"]))
        mixed = [iid for iid, values in bases.items() if len(values) > 1]
        check(
            "VOLUME_BASIS",
            "WARN" if mixed else "PASS" if bases else "NOT_CHECKED",
            "检查本周每证券来源、feed与成交量口径的一致性；不证明供应商覆盖声明",
            mixed_instruments=mixed,
            checked_instruments=len(bases),
        )
        measured = [b for b in revisions if b["available_at_kind"] == "PROVIDER" and b["available_at"]]
        lags = [(instant(b["available_at"]) - instant(b["bar_end_at"])).total_seconds() for b in measured]
        check(
            "SOURCE_LAG",
            "PASS" if lags and len(measured) == len(revisions) else "NOT_CHECKED",
            "仅对供应商提供的可用时点计算延迟；本机导入时点不能替代",
            measured_rows=len(measured),
            unknown_rows=len(revisions) - len(measured),
            max_seconds=max(lags) if lags else None,
        )
        event_config = manifest = None
        if scan:
            try:
                manifest = self.files.read(scan["input_manifest_ref"])
                event_config = manifest["event_config"]["value"]
            except DomainError:
                pass
        statuses = list((manifest or {}).get("security_statuses", {}).values())
        fresh_events = bool(
            event_config
            and event_config.get("coverage") == "OK"
            and event_config.get("verified_at")
            and 0 <= (now() - instant(event_config["verified_at"])).total_seconds() <= 3600
        )
        fresh_statuses = bool(statuses) and all(
            0 <= (now() - instant(s["observed_at"])).total_seconds() <= 3600 for s in statuses
        )
        check(
            "EVENT_COVERAGE",
            "PASS"
            if fresh_events and fresh_statuses
            else "WARN"
            if event_config and event_config.get("mode") != "DISABLED"
            else "NOT_CHECKED",
            "核对冻结输入的覆盖声明与证券状态时效；独立外部完整性验收仍需G0",
            declared_coverage=event_config.get("coverage") if event_config else "UNKNOWN",
            status_count=len(statuses),
        )
        disk = shutil.disk_usage(self.settings.runtime)
        checks.append(
            dict(
                code="DISK_SPACE",
                status="PASS" if disk.free > 1000000000 else "WARN",
                free_bytes=disk.free,
                as_of=stamp(),
                evidence_refs=["local-disk-stat"],
            )
        )
        checks.append(
            dict(
                code="BACKUP_LAST_SUCCESS",
                status="PASS"
                if backup and instant(backup["created_at"]) > now() - timedelta(days=1)
                else "WARN",
                as_of=stamp(),
                evidence_refs=[backup["id"]] if backup else [],
            )
        )
        from src.services.signal_scanner.research.probe import probe_snapshot

        probe = probe_snapshot(self.files, scan, self.cal)
        checks.append(
            dict(
                code="REFERENCED_FILES",
                status="FAIL"
                if probe["status"] == "FAIL"
                else "PASS"
                if probe["status"] == "PASS"
                else "NOT_CHECKED",
                as_of=stamp(),
                evidence_refs=[scan["id"]] if scan else [],
            )
        )
        artifact = dict(
            kind="WEEKLY_REPORT",
            generated_at=stamp(),
            scope="SYSTEM",
            data_health=checks,
            replay_probe=probe,
            process_metrics=morning_metrics(
                decisions, since, stamp(), preferences["value"]["morning_window"], preferences["version"]
            ),
        )
        self.finish_artifact(
            j,
            artifact,
            status="PARTIAL"
            if any(c["status"] in ("FAIL", "NOT_CHECKED", "WARN") for c in checks)
            else "SUCCEEDED",
        )

    def backup(self, j):
        folder = self.settings.runtime / "backups"
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / (j["id"] + ".sqlite3")
        source = self.db.connect()
        target = sqlite3.connect(path)
        try:
            source.backup(target)
            require(target.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_INVALID")
        finally:
            source.close()
            target.close()
        os.chmod(path, 0o600)
        # Preserve content-addressed referenced files and settings together, not only the SQLite main file.
        manifest = dict(database=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), files=[])
        archive = folder / (j["id"] + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(path, "scanner.sqlite3")
            for p in (self.settings.runtime / "artifacts").rglob("*.json"):
                ref = str(p.relative_to(self.settings.runtime))
                data = p.read_bytes()
                h = hashlib.sha256(data).hexdigest()
                require(h == p.stem, "BACKUP_INVALID", "被引用的内容文件已损坏")
                z.writestr(ref, data)
                manifest["files"].append(dict(path=ref, sha256=h))
            with self.db.read() as cx:
                sources = [get_config(cx, key)["value"] for key in ("provider", "events")]
            from src.services.signal_scanner.adapters.market import input_path
            from src.services.signal_scanner.core import reject_credentials

            for config in sources:
                if config["mode"] != "LOCAL_FILE":
                    continue
                original = input_path(self.settings.runtime, config["file"])
                paths = [original]
                if original.suffix == ".csv":
                    paths.append(input_path(self.settings.runtime, original.stem + ".meta.json"))
                for p in paths:
                    data = p.read_bytes()
                    if p.suffix == ".json":
                        reject_credentials(json.loads(data))
                    ref = "imports/" + p.name
                    if not any(f["path"] == ref for f in manifest["files"]):
                        z.writestr(ref, data)
                        manifest["files"].append(dict(path=ref, sha256=hashlib.sha256(data).hexdigest()))
            z.writestr("manifest.json", canonical(manifest))
        os.chmod(archive, 0o600)
        with zipfile.ZipFile(archive) as z:
            require(z.testzip() is None, "BACKUP_INVALID")
        self.finish_artifact(j, dict(kind="BACKUP", archive=archive.name, manifest=manifest, verified=True))
        archives = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = set(archives[:7])
        weeks = set()
        from datetime import datetime

        for item in archives:
            week = datetime.fromtimestamp(item.stat().st_mtime, tz=SG).strftime("%G-W%V")
            if week not in weeks and len(weeks) < 4:
                keep.add(item)
                weeks.add(week)
        for item in archives:
            if item not in keep:
                item.unlink()
                item.with_suffix(".sqlite3").unlink(missing_ok=True)

    def privacy_export(self, j):
        with self.db.read() as cx:
            value = {
                t: many(cx, f"SELECT * FROM {t}")
                for t in (
                    "decisions",
                    "decision_sessions",
                    "plans",
                    "ledger_entries",
                    "account_snapshots",
                    "account_state",
                )
            }
        artifact = dict(kind="PRIVACY_EXPORT", generated_at=stamp(), data=value)
        self.finish_artifact(j, artifact, "EXPORT")

    def privacy_delete(self, j):
        p = j["progress_json"]
        scope = p["scope"]
        obsolete = []
        with self.db.transaction() as cx:
            require(
                one(
                    cx,
                    "SELECT id FROM job_runs WHERE id=? AND artifact_kind='EXPORT' AND status='SUCCEEDED'",
                    (p["export_id"],),
                ),
                "DATA_INCOMPLETE",
                "先完成个人数据导出",
            )
            if scope in ("EMOTION_AND_NOTES", "PERSONAL_DATA"):
                cx.execute(
                    "UPDATE decisions SET reason='[已删除]',expectation='[已删除]',invalidation='[已删除]',emotion=NULL"
                )
                cx.execute("UPDATE plans SET user_notes='' ")
                cx.execute("UPDATE decision_sessions SET ack_json='{}',status='STALE',current_step='S'")
                cx.execute("DELETE FROM idempotency_requests")
            if scope in ("ACCOUNT_DATA", "PERSONAL_DATA"):
                ids = (
                    [p["account_id"]]
                    if scope == "ACCOUNT_DATA"
                    else [
                        a["account_id"]
                        for a in many(cx, "SELECT account_id FROM account_state WHERE status='ACTIVE'")
                    ]
                )
                for aid in ids:
                    cx.execute(
                        "DELETE FROM decisions WHERE plan_id IN (SELECT id FROM plans WHERE account_id=?)",
                        (aid,),
                    )
                    for table in ("ledger_entries", "account_snapshots"):
                        cx.execute(f"DELETE FROM {table} WHERE account_id=?", (aid,))
                    cx.execute("DELETE FROM plans WHERE account_id=?", (aid,))
                    cx.execute(
                        "UPDATE account_state SET status='CLOSED',profile_json='{}',current_snapshot_id=NULL,confirmed_ledger_head=NULL WHERE account_id=?",
                        (aid,),
                    )
            kinds = (
                ("EXPORT",) if scope == "EMOTION_AND_NOTES" else ("EXPORT", "WORKSPACE", "DIGEST", "REPORT")
            )
            placeholders = ",".join("?" for _ in kinds)
            for artifact in many(
                cx,
                f"SELECT id,output_manifest_ref FROM job_runs WHERE artifact_kind IN ({placeholders}) AND output_manifest_ref IS NOT NULL",
                kinds,
            ):
                obsolete.append(artifact["output_manifest_ref"])
                update(
                    cx,
                    "job_runs",
                    "id",
                    artifact["id"],
                    dict(output_manifest_ref=None, status="SKIPPED", error_json=dict(code="PRIVACY_REMOVED")),
                )
            if scope != "EMOTION_AND_NOTES":
                enqueue(cx, "DAILY_SCAN", "PRIVACY_REBUILD:" + j["id"], self.cal.last_completed(now()))
            audit(cx, "PRIVACY_DELETED", j["id"], "用户请求；备份随轮转到期", scope=scope)
        for ref in set(obsolete):
            path = (self.files.root / ref).resolve()
            require(path.is_relative_to(self.files.root), "RESOURCE_NOT_FOUND", status=404)
            path.unlink(missing_ok=True)
        self.finish_artifact(
            j, dict(kind="PRIVACY_DELETE", scope=scope, status="DELETED", backup_policy="ROTATION_APPLIES")
        )

    def deliver(self):
        with self.db.transaction() as cx:
            n = one(
                cx,
                "SELECT * FROM notifications WHERE status IN ('PENDING','FAILED') AND expires_at>? AND attempt_count<3 AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT 1",
                (stamp(), stamp()),
            )
            if not n:
                return
            url = os.environ.get("SCANNER_WEBHOOK_URL")
            if not url or not url.startswith("https://"):
                update(
                    cx,
                    "notifications",
                    "id",
                    n["id"],
                    dict(status="SUPPRESSED", last_error_json=dict(code="CHANNEL_NOT_CONFIGURED")),
                )
                return
            j = one(cx, "SELECT * FROM job_runs WHERE id=?", (n["artifact_id"],))
            artifact = self.files.read(j["output_manifest_ref"])
            update(
                cx,
                "notifications",
                "id",
                n["id"],
                dict(
                    status="SENDING",
                    attempt_count=n["attempt_count"] + 1,
                    lease_until=stamp(now() + timedelta(seconds=60)),
                ),
            )
        state = "FAILED"
        error = None
        try:
            r = httpx.post(
                url,
                json=dict(
                    delivery_id=n["delivery_id"],
                    title="交易信号扫描器晨间摘要",
                    source_market_date=artifact["source_market_date"],
                    coverage=artifact["coverage"],
                    message=artifact["message"],
                ),
                timeout=15,
                follow_redirects=False,
            )
            state = "ACCEPTED" if 200 <= r.status_code < 300 else "FAILED"
            error = None if state == "ACCEPTED" else dict(code="CHANNEL_REJECTED", http_status=r.status_code)
        except httpx.TimeoutException:
            state = "UNKNOWN"
            error = dict(code="TIMEOUT_ACCEPTANCE_UNKNOWN")
        except httpx.RequestError:
            error = dict(code="CHANNEL_UNREACHABLE")
        with self.db.transaction() as cx:
            update(
                cx,
                "notifications",
                "id",
                n["id"],
                dict(
                    status=state,
                    last_error_json=error or {},
                    next_attempt_at=stamp(now() + timedelta(minutes=2)),
                ),
            )

    def run(self):
        lockpath = self.settings.runtime / "worker.lock"
        lockpath.parent.mkdir(exist_ok=True, parents=True)
        with lockpath.open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                heart = self.settings.runtime / "worker-heartbeat.json"
                heart.write_text(canonical(dict(pid=os.getpid(), at=stamp())))
                self.tick()
                time.sleep(3)
