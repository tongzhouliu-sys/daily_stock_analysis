#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill Chinese text variants for historical reports (T78).

Examples:
    python scripts/backfill_report_zh_variants.py --dry-run --limit 50
    python scripts/backfill_report_zh_variants.py --apply --since 2026-01-01
    python scripts/backfill_report_zh_variants.py --apply --record-id 17 --record-id 18
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from src.services.history_service import (  # noqa: E402
    HistoryService,
    ReportChineseVariantMissing,
)
from src.services.report_translation_service import ReportTranslationError  # noqa: E402
from src.storage import AnalysisHistory, DatabaseManager  # noqa: E402


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since must be YYYY-MM-DD") from exc


def _record_ids(db: DatabaseManager, *, since: datetime | None, limit: int, explicit: list[int]) -> list[int]:
    with db.get_session() as session:
        if explicit:
            return explicit
        query = select(AnalysisHistory.id).order_by(AnalysisHistory.id.desc()).limit(limit)
        if since is not None:
            query = query.where(AnalysisHistory.created_at >= since)
        return [int(row) for row in session.execute(query).scalars().all()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Chinese variants for historical reports")
    parser.add_argument("--limit", type=int, default=200, help="Maximum records to scan (default 200)")
    parser.add_argument("--since", type=str, default=None, help="Only records created on/after YYYY-MM-DD")
    parser.add_argument("--record-id", type=int, action="append", default=[], help="Explicit record id (repeatable)")
    parser.add_argument("--apply", action="store_true", help="Write variants; without it the script is dry-run")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default when --apply is omitted)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if a ready variant exists")
    args = parser.parse_args(argv)
    if args.dry_run:
        args.apply = False

    since = _parse_since(args.since)
    db = DatabaseManager.get_instance()
    service = HistoryService(db)
    ids = _record_ids(db, since=since, limit=max(1, args.limit), explicit=args.record_id)

    stats = {"ready": 0, "cached": 0, "skipped": 0, "failed": 0, "not_needed": 0}
    for record_id in ids:
        try:
            if not args.apply:
                print(f"[dry-run] record {record_id}")
                stats["skipped"] += 1
                continue
            result = service.ensure_chinese_variant(str(record_id), force=args.force)
            status = str(result.get("status") or "ready")
            if status == "not_needed":
                stats["not_needed"] += 1
            elif result.get("cached"):
                stats["cached"] += 1
            else:
                stats["ready"] += 1
            print(f"[{status}] record {record_id}")
        except ReportChineseVariantMissing as exc:
            stats["skipped"] += 1
            print(f"[skip] record {record_id}: {exc.reason}")
        except ReportTranslationError as exc:
            stats["failed"] += 1
            print(f"[fail] record {record_id}: {exc.error_code}")
        except Exception as exc:  # noqa: BLE001 - keep the batch running
            stats["failed"] += 1
            print(f"[fail] record {record_id}: {exc}")

    print(
        "done: "
        + ", ".join(f"{key}={value}" for key, value in stats.items())
        + ("" if args.apply else " (dry-run, use --apply to write)")
    )
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
