"""Back up, idempotently import the frozen pool, and explicitly refresh its workspace.

No credentials, network operations, notification delivery or legacy portfolio replay.
The full raw library stays separate; only the existing research pool is activated.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.adapters.market import ingest
from src.services.signal_scanner.cli import activate
from src.services.signal_scanner.core import Settings, canonical, digest, now, stamp
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.services.source_data import record_report, store_events
from src.services.signal_scanner.storage.db import Database, many, one, update
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.jobs import Worker, enqueue
from src.services.signal_scanner.worker.pipeline import latest_workspace

OUT = ROOT / "runtime/data10-update-20261026"
PROTECTED = ("ledger_entries", "account_snapshots", "notifications", "account_state")


def protected_fingerprint(db):
    with db.read() as cx:
        return {
            table: digest([dict(r) for r in cx.execute("SELECT * FROM " + table + " ORDER BY rowid")])
            for table in PROTECTED
        }


def apply(runtime, *, scan):
    runtime = Path(runtime).resolve()
    assert (runtime / "scanner.sqlite3").is_file(), "Only an existing initialized runtime is accepted"
    prepared = json.loads((OUT / "prepared.json").read_text())
    db, cal, files = Database(runtime / "scanner.sqlite3"), Calendar(), Artifacts(runtime / "artifacts")
    before = protected_fingerprint(db)
    with db.read() as cx:
        assert one(cx, "SELECT count(*) n FROM account_state WHERE status='ACTIVE'")["n"] == 0, (
            "Workspace refresh would rebuild active accounts; review scope first"
        )
        assert get_config(cx, "experiment")["value"]["stage"] != "SHADOW", (
            "Do not enter a shadow account workflow"
        )
        previous = {key: get_config(cx, key)["value"] for key in ("provider", "universe")}
    tag = hashlib.sha256(str(runtime).encode()).hexdigest()[:12]
    backup = OUT / "backups" / tag
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_db = backup / "scanner-before.sqlite3"
    if not backup_db.exists():
        source = sqlite3.connect(db.path.as_uri() + "?mode=ro", uri=True)
        dest = sqlite3.connect(backup_db)
        source.backup(dest)
        assert dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        source.close()
        dest.close()
        os.chmod(backup_db, 0o600)
        (backup / "configs-before.json").write_text(canonical(previous))
        os.chmod(backup / "configs-before.json", 0o600)
    total = defaultdict(int)
    cache = {
        "provider": prepared["provider"],
        "feed": prepared["feed"],
        "coverage": "MIXED_SOURCE_RAW_EOD_RTH_UNVERIFIED",
        "instruments": [],
        "bars": [],
        "source_session_kind": "EOD_DAILY",
    }
    for n, ref in enumerate(prepared["bundles"], 1):
        path = Path(ref["path"])
        bundle = json.loads(path.read_text())
        assert digest(bundle) == ref["sha256"], "Frozen bundle changed"
        with db.transaction() as cx:
            counts = ingest(cx, bundle, cal, files)
            assert not counts["quarantined"], counts
        for key in ("inserted", "duplicates", "quarantined"):
            total[key] += counts[key]
        cache["instruments"] += bundle["instruments"]
        cache["bars"] += bundle["bars"][-320:]
        if n % 10 == 0:
            print(canonical(dict(runtime=str(runtime), symbols=n, **total)), flush=True)
    snapshot = json.loads((OUT / "inputs/snapshot.json").read_text())
    manifest = json.loads((OUT / "inputs/manifest.json").read_text())
    assert digest(snapshot) == manifest["data_snapshot_ref"]["sha256"] == prepared["snapshot_sha256"]
    actions = defaultdict(list)
    for e in snapshot["events"]:
        if e["event_type"] == "SPLIT":
            value = {
                k: e.get(k)
                for k in (
                    "event_id",
                    "instrument_id",
                    "event_type",
                    "effective_at",
                    "known_at",
                    "status",
                    "payload_json",
                )
            }
            actions[e["source"]].append(value)
    action_counts = {}
    with db.transaction() as cx:
        for source, es in actions.items():
            action_counts[source] = store_events(cx, es, source)
    # A small latest-data cache is re-ingested by the ordinary scanner; full history is already in DB.
    cache_path = runtime / "imports/data10_merged_eod.json"
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(canonical(cache))
    os.chmod(tmp, 0o600)
    os.replace(tmp, cache_path)
    settings = Settings(runtime)
    observed = stamp()
    capabilities = [
        {
            "capability_id": "EOD_DAILY",
            "support": "SUPPORTED",
            "entitlement": "GRANTED",
            "enabled": True,
            "verified_at": observed,
            "evidence_ref": "用户提供本地数据并明确授权导入；审计见data10-update-20261026/audit.json",
        },
        {
            "capability_id": "RTH_DAILY",
            "support": "UNKNOWN",
            "entitlement": "UNKNOWN",
            "enabled": False,
            "verified_at": None,
            "evidence_ref": None,
        },
    ]
    source_report = {
        "source": "LOCAL_DATA10",
        "observed_at": observed,
        "status": "PARTIAL",
        "reason_code": "RTH_AND_HISTORICAL_COVERAGE_UNVERIFIED",
        "count": prepared["total_rows"],
        "latest_market_date": prepared["price_data_end"],
        "capabilities": capabilities,
        "health": {
            "connection": "UP",
            "permission": "GRANTED",
            "freshness": "PARTIAL",
            "quality": "EOD_ONLY",
        },
        "quality_status": "EOD_ONLY",
        "archive": str(Path(prepared.get("library_root", OUT)) / "market.sqlite3"),
        "coverage": prepared["coverage"],
    }
    with db.transaction() as cx:
        # Existing cache mode explicitly preserves the scanner's EOD trade-eligibility block.
        activate(
            cx,
            "provider",
            {
                "mode": "TIINGO_EOD_CACHE",
                "file": cache_path.name,
                "feed": prepared["feed"],
                "quote_enabled": False,
                "capabilities": capabilities,
            },
        )
        activate(cx, "universe", {"members": sorted(i["id"] for i in cache["instruments"])})
        jid = record_report(cx, files, source_report)
        scan_id = (
            enqueue(
                cx, "DAILY_SCAN", "DATA10_REFRESH:" + prepared["snapshot_sha256"], prepared["price_data_end"]
            )
            if scan
            else None
        )
    scan_result = None
    if scan_id:
        # Execute only the explicitly requested scan, never drain unrelated queued jobs or send messages.
        worker = Worker(settings)
        with db.transaction() as cx:
            job = one(cx, "SELECT * FROM job_runs WHERE id=?", (scan_id,))
            if job["status"] != "SUCCEEDED":
                update(
                    cx,
                    "job_runs",
                    "id",
                    scan_id,
                    {
                        "status": "RUNNING",
                        "lease_owner": worker.owner,
                        "lease_until": stamp(now() + timedelta(minutes=30)),
                        "heartbeat_at": stamp(),
                        "attempt_count": job["attempt_count"] + 1,
                    },
                )
        if job["status"] != "SUCCEEDED":
            try:
                worker.scan(job)
            except Exception as exc:
                with db.transaction() as cx:
                    update(
                        cx,
                        "job_runs",
                        "id",
                        scan_id,
                        {
                            "status": "FAILED",
                            "error_json": {"code": getattr(exc, "code", type(exc).__name__)},
                        },
                    )
                raise
        with db.read() as cx:
            scan_result = one(
                cx, "SELECT id,status,error_json,published_seq FROM job_runs WHERE id=?", (scan_id,)
            )
    after = protected_fingerprint(db)
    assert before == after, "Account/notification records changed unexpectedly"
    with db.read() as cx:
        integrity = cx.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
        rows = many(
            cx,
            "SELECT provider,COUNT(*) rows,COUNT(DISTINCT instrument_id) symbols,MIN(market_date) first_date,MAX(market_date) last_date FROM bar_revisions GROUP BY provider",
        )
        _workspace_job, workspace = latest_workspace(cx, files)
        workspace_summary = (
            {
                k: workspace.get(k)
                for k in (
                    "market_date",
                    "source_market_date",
                    "coverage",
                    "environment",
                    "provider_health",
                    "failures",
                )
            }
            if workspace
            else None
        )
    result = {
        "completed_at": stamp(),
        "runtime": str(runtime),
        "backup": str(backup_db),
        "ingest": dict(total),
        "corporate_actions": action_counts,
        "source_job_id": jid,
        "scan": scan_result,
        "database_integrity": integrity,
        "source_counts": rows,
        "protected_tables_unchanged": before == after,
        "protected_before": before,
        "protected_after": after,
        "workspace": workspace_summary,
    }
    (OUT / ("applied-" + tag + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(canonical(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, default=OUT)
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    OUT = args.input_root.resolve()
    apply(args.runtime, scan=args.scan)
