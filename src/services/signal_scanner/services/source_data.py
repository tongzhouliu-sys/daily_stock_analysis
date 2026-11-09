"""Persist external-source reports in existing D06 and source facts in D03/D04."""

import os
from pathlib import Path

from src.services.signal_scanner.adapters.base import source_report, unavailable
from src.services.signal_scanner.core import DomainError, canonical, digest, now, stamp, uid
from src.services.signal_scanner.storage.db import insert, one
from src.services.signal_scanner.storage.files import Artifacts

SOURCE_CAPS = {"TIINGO": "EOD_DAILY", "FRED": "VIX_HISTORY", "FINNHUB": "EARNINGS_CALENDAR"}


def read_private_env(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise DomainError("PRIVATE_CONFIG_PERMISSIONS", status=503)
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, separator, value = line.partition("=")
            if separator:
                os.environ[key.strip()] = value.strip()


def store_events(cx, events, source):
    counts = dict(inserted=0, duplicates=0)
    for e in events:
        value = {k: v for k, v in e.items() if k not in ("known_at", "revision", "id", "created_at")}
        h = digest(value)
        old = one(
            cx,
            "SELECT * FROM market_event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1",
            (e["event_id"],),
        )
        if old and old["content_hash"] == h:
            counts["duplicates"] += 1
            continue
        insert(
            cx,
            "market_event_revisions",
            dict(
                id=uid("source_event"),
                event_id=e["event_id"],
                revision=old["revision"] + 1 if old else 1,
                instrument_id=e.get("instrument_id"),
                event_type=e["event_type"],
                scheduled_date=e.get("scheduled_date"),
                scheduled_at=e.get("scheduled_at"),
                published_at=e.get("published_at"),
                effective_at=e.get("effective_at"),
                known_at=e.get("known_at", stamp()),
                source=source,
                status=e["status"],
                payload_json=e["payload_json"],
                content_hash=h,
            ),
        )
        counts["inserted"] += 1
    return counts


def record_report(cx, files, report, input_ref=None):
    jid = uid("source")
    ref = files.save(report, "sources")
    insert(
        cx,
        "job_runs",
        dict(
            id=jid,
            logical_key=jid,
            revision=1,
            job_name="SOURCE_" + report["source"],
            artifact_kind="DATA_SOURCE",
            market_date=report.get("latest_market_date"),
            status="SKIPPED" if report["status"] == "DISABLED" else report["status"],
            input_manifest_ref=input_ref,
            output_manifest_ref=ref,
            expected_count=report.get("count", 0),
            ok_count=report.get("count", 0) if report["status"] == "SUCCEEDED" else 0,
            failed_count=len(report.get("invalid_rows", [])),
            error_json={"code": report["reason_code"]} if report.get("reason_code") else {},
        ),
    )
    return jid


def latest_sources(cx, files):
    result = []
    capabilities = dict(SOURCE_CAPS)
    if one(cx, "SELECT id FROM job_runs WHERE job_name='SOURCE_ALPHA_VANTAGE' LIMIT 1"):
        capabilities["ALPHA_VANTAGE"] = "EOD_DAILY"
    for source, cap in capabilities.items():
        row = one(
            cx,
            "SELECT * FROM job_runs WHERE job_name=? AND output_manifest_ref IS NOT NULL ORDER BY created_at DESC,id DESC LIMIT 1",
            ("SOURCE_" + source,),
        )
        value = files.read(row["output_manifest_ref"]) if row else unavailable(source, cap)
        if not row:
            value["observed_at"] = None
        value = {**value, "health": dict(value["health"]), "job_id": row["id"] if row else None}
        if source in ("TIINGO", "FRED", "ALPHA_VANTAGE") and value.get("latest_market_date"):
            from src.services.signal_scanner.domain.calendar import Calendar

            if value["latest_market_date"] < Calendar().last_completed(now()):
                value["health"]["freshness"] = "STALE"
        result.append(value)
    return result


def current_environment(workspace, sources):
    environment = (
        dict(workspace["environment"]) if workspace else dict(mode="UNKNOWN", reason_code="NO_WORKSPACE")
    )
    fred = next((s for s in sources if s["source"] == "FRED" and s.get("job_id")), None)
    if fred and (fred["status"] != "SUCCEEDED" or fred["health"]["freshness"] != "OK"):
        environment.update(
            mode="UNKNOWN",
            base_mode="UNKNOWN",
            vix=None,
            reason_code=fred.get("reason_code") or "VIX_SOURCE_UNAVAILABLE",
        )
    return environment


def load_tiingo(settings, *, scan=False, adapter=None):
    from src.services.signal_scanner.cli import activate, setup
    from src.services.signal_scanner.adapters.market import ingest
    from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
    from src.services.signal_scanner.domain.calendar import Calendar
    from src.services.signal_scanner.services.config import get_config
    from src.services.signal_scanner.worker.jobs import Worker, enqueue

    db = setup(settings)
    files = Artifacts(settings.runtime / "artifacts")
    cal = Calendar()
    end = cal.last_completed(now())
    start = cal.shift(end, -319)
    adapter = adapter or TiingoAdapter(cal)
    try:
        bundle = adapter.fetch("SPY", start, end)
        report = bundle["source_report"]
        with db.transaction() as cx:
            counts = ingest(cx, bundle, cal, files)
            actions = store_events(cx, bundle["actions"], "TIINGO_EOD")
            report = {**report, "ingest": counts, "corporate_actions": actions}
            record_report(cx, files, report, files.save(bundle, "inputs"))
        if scan and report["status"] == "SUCCEEDED":
            path = settings.runtime / "imports" / "tiingo_spy.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(canonical(bundle))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            with db.transaction() as cx:
                pool = get_config(cx, "universe")["value"]["members"]
                if not pool:
                    activate(cx, "universe", dict(members=["US:SPY"]))
                activate(
                    cx,
                    "provider",
                    dict(
                        mode="TIINGO_EOD_CACHE",
                        file=path.name,
                        feed="EOD_RAW",
                        quote_enabled=False,
                        capabilities=report["capabilities"]
                        + [
                            dict(
                                capability_id="RTH_DAILY",
                                support="UNKNOWN",
                                entitlement="UNKNOWN",
                                enabled=False,
                                verified_at=None,
                                evidence_ref=None,
                            )
                        ],
                    ),
                )
                jid = enqueue(cx, "DAILY_SCAN", "TIINGO_MANUAL:" + uid(), end)
            w = Worker(settings)
            while w.run_one():
                pass
            with db.read() as cx:
                job = one(cx, "SELECT status,error_json FROM job_runs WHERE id=?", (jid,))
            report = {**report, "scan": job}
        return report

    except DomainError as e:
        report = source_report(
            "TIINGO", "EOD_DAILY", bool(getattr(adapter, "key", None)), reason=e.code, quality="UNKNOWN"
        )
        with db.transaction() as cx:
            record_report(cx, files, report)
        return report


def scan_cached(settings, cal):
    from src.services.signal_scanner.worker.jobs import Worker, enqueue

    worker = Worker(settings)
    with worker.db.transaction() as cx:
        jid = enqueue(cx, "DAILY_SCAN", "SOURCE_REFRESH:" + uid(), cal.last_completed(now()))
    while worker.run_one():
        pass
    with worker.db.read() as cx:
        return one(cx, "SELECT status,error_json FROM job_runs WHERE id=?", (jid,))


def load_fred(settings, *, scan=False, adapter=None):
    from src.services.signal_scanner.cli import setup
    from src.services.signal_scanner.adapters.fred_vix_adapter import FredVixAdapter
    from src.services.signal_scanner.domain.calendar import Calendar

    db = setup(settings)
    files = Artifacts(settings.runtime / "artifacts")
    cal = Calendar()
    end = cal.last_completed(now())
    start = cal.shift(end, -319)
    adapter = adapter or FredVixAdapter(cal)
    try:
        result = adapter.fetch(start, end)
        report = result["source_report"]
        with db.transaction() as cx:
            counts = store_events(cx, result["events"], "FRED:VIXCLS")
            report = {**report, "ingest": counts}
            record_report(cx, files, report, files.save(result, "inputs"))
    except DomainError as e:
        report = source_report(
            "FRED", "VIX_HISTORY", True, reason=e.code, quality="UNKNOWN", access_mode="PUBLIC_CSV_NO_KEY"
        )
        with db.transaction() as cx:
            record_report(cx, files, report)
    if scan:
        report = {**report, "scan": scan_cached(settings, cal)}
    return report


def load_finnhub(settings, *, symbols=None, adapter=None):
    from datetime import timedelta
    from src.services.signal_scanner.cli import setup
    from src.services.signal_scanner.adapters.finnhub_earnings_adapter import FinnhubEarningsAdapter
    from src.services.signal_scanner.adapters.market import register_instruments

    db = setup(settings)
    files = Artifacts(settings.runtime / "artifacts")
    adapter = adapter or FinnhubEarningsAdapter()
    symbols = symbols or ["AAPL", "MSFT", "NVDA"]
    start = (now() - timedelta(days=14)).date().isoformat()
    end = (now() + timedelta(days=45)).date().isoformat()
    try:
        result = adapter.fetch(symbols, start, end)
        report = result["source_report"]
        with db.transaction() as cx:
            # Register only actual calendar identities, without silently expanding the trade universe.
            for symbol in symbols:
                if any(e["instrument_id"] == "US:" + symbol for e in result["events"]):
                    if not one(cx, "SELECT id FROM instruments WHERE id=?", ("US:" + symbol,)):
                        register_instruments(
                            cx,
                            [
                                dict(
                                    id="US:" + symbol,
                                    ticker=symbol,
                                    name=symbol + " · 财报验证对象",
                                    type="COMMON",
                                    currency="USD",
                                    venue="US_EQUITIES",
                                    roles=["EVENT_PROBE"],
                                    profile=dict(
                                        metadata_scope="CALENDAR_SYMBOL_ONLY", primary_venue_verified=False
                                    ),
                                )
                            ],
                        )
            counts = store_events(cx, result["events"], "FINNHUB")
            report = {**report, "ingest": counts}
            record_report(cx, files, report, files.save(result, "inputs"))
        return report
    except DomainError as e:
        report = source_report(
            "FINNHUB",
            "EARNINGS_CALENDAR",
            bool(getattr(adapter, "key", None)),
            reason=e.code,
            quality="UNKNOWN",
            symbols=symbols,
            coverage_by_symbol={s: "UNKNOWN" for s in symbols},
        )
        with db.transaction() as cx:
            record_report(cx, files, report)
        return report
