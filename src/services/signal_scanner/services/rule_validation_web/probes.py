"""Read only the already-produced weekly artifacts; never execute a new probe."""

import hashlib
import json
import re
import sqlite3

from src.services.signal_scanner.core import DomainError, require

from .query import redact
from .store import safe_path

STATES = {"PASS", "FAIL", "SKIPPED", "VERSION_UNAVAILABLE"}


def history(settings, cursor=0, limit=30):
    require(cursor >= 0 and 1 <= limit <= 100, "INVALID_PAGINATION", status=422)
    if not settings.db_path.is_file():
        return {"items": [], "total": 0, "next_cursor": None, "source": "EXISTING_WEEKLY_REPORTS_ONLY"}
    with sqlite3.connect(settings.db_path.as_uri() + "?mode=ro", uri=True) as cx:
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA query_only=ON")
        total = cx.execute("SELECT count(*) FROM job_runs WHERE job_name='WEEKLY_REPORT'").fetchone()[0]
        jobs = [
            dict(r)
            for r in cx.execute(
                "SELECT id,status,created_at,output_manifest_ref FROM job_runs WHERE job_name='WEEKLY_REPORT' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, cursor),
            )
        ]
    result = []
    for job in jobs:
        item = {
            "id": job["id"],
            "job_status": job["status"],
            "created_at": job["created_at"],
            "status": None,
            "expected_hash": None,
            "actual_hash": None,
            "network_calls": None,
            "account_writes": None,
            "source_scan_ids": [],
            "integrity": "NOT_READY",
        }
        try:
            ref = job["output_manifest_ref"]
            require(isinstance(ref, str), "WEEKLY_REPORT_NOT_READY")
            path = safe_path(settings.runtime / "artifacts", settings.runtime / "artifacts" / ref)
            require(path.is_file() and re.fullmatch(r"[a-f0-9]{64}", path.stem), "MISSING_REFERENCED_INPUT")
            require(path.stat().st_size <= 8 * 1024 * 1024, "WEEKLY_REPORT_TOO_LARGE")
            content = path.read_bytes()
            require(hashlib.sha256(content).hexdigest() == path.stem, "ARTIFACT_CORRUPT")
            report = json.loads(content)
            require(report.get("kind") == "WEEKLY_REPORT", "INVALID_WEEKLY_REPORT")
            probe = report.get("replay_probe", {})
            require(probe.get("status") in STATES, "INVALID_PROBE_STATUS")
            item.update(
                {
                    k: probe.get(k)
                    for k in (
                        "status",
                        "reason",
                        "input_hash",
                        "expected_hash",
                        "actual_hash",
                        "network_calls",
                        "account_writes",
                    )
                }
            )
            item.update(
                integrity="VERIFIED",
                generated_at=report.get("generated_at"),
                artifact_hash=path.stem,
                source_scan_ids=[
                    ref
                    for c in report.get("data_health", [])
                    if c.get("code") == "REFERENCED_FILES"
                    for ref in c.get("evidence_refs", [])
                ],
            )
        except (DomainError, OSError, ValueError, TypeError, AttributeError) as ex:
            code = ex.code if isinstance(ex, DomainError) else "INVALID_WEEKLY_REPORT"
            item.update(
                status=None if code == "WEEKLY_REPORT_NOT_READY" else "FAIL",
                reason=code,
                integrity="NOT_READY" if code == "WEEKLY_REPORT_NOT_READY" else "INVALID",
            )
        result.append(redact(item))
    return {
        "items": result,
        "total": total,
        "next_cursor": cursor + limit if cursor + limit < total else None,
        "source": "EXISTING_WEEKLY_REPORTS_ONLY",
        "does_not_run_probe": True,
    }
