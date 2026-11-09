"""Batch historical data fetch service - serial processing with rate limiting."""

import csv
import time
from pathlib import Path

from src.services.signal_scanner.adapters.market import ingest, register_instruments
from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
from src.services.signal_scanner.core import DomainError, now, require, stamp, uid
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.source_data import store_events
from src.services.signal_scanner.storage.db import insert, many, one, update
from src.services.signal_scanner.storage.files import Artifacts


def load_universe_csv(csv_path: Path):
    """Load universe from CSV file, sorted by ticker."""
    require(csv_path.is_file(), "FILE_NOT_FOUND", f"CSV文件不存在: {csv_path}", 404)

    tickers = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            require(
                all(k in row for k in ("ticker", "asset_type", "sector")),
                "VALIDATION_ERROR",
                "CSV缺少必需字段: ticker, asset_type, sector",
                422,
            )
            tickers.append({
                "ticker": row["ticker"].strip(),
                "asset_type": row["asset_type"].strip(),
                "sector": row["sector"].strip(),
            })

    tickers.sort(key=lambda x: x["ticker"])
    return tickers


def fetch_single_instrument(cal, adapter, symbol, start_date, end_date):
    """Fetch data for a single instrument with error handling.

    Returns a dict with status, bar_count, first/last date, and the bundle (if any).
    """
    try:
        bundle = adapter.fetch(symbol, start_date, end_date)

        if not bundle["bars"]:
            return {
                "status": "SKIPPED",
                "symbol": symbol,
                "reason": "无数据返回",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": False,
            }

        expected_days = len(cal.dates(start_date, end_date))
        actual_days = len(bundle["bars"])

        if actual_days < expected_days * 0.5:
            return {
                "status": "INSUFFICIENT",
                "symbol": symbol,
                "reason": f"历史数据不足: 期望约{expected_days}天, 实际{actual_days}天",
                "bar_count": actual_days,
                "first_date": bundle["bars"][0]["market_date"],
                "last_date": bundle["bars"][-1]["market_date"],
                "bundle": bundle,
                "retryable": False,
            }

        return {
            "status": "SUCCEEDED",
            "symbol": symbol,
            "bar_count": len(bundle["bars"]),
            "first_date": bundle["bars"][0]["market_date"],
            "last_date": bundle["bars"][-1]["market_date"],
            "bundle": bundle,
            "retryable": False,
        }

    except DomainError as e:
        if e.code in ("SOURCE_PERMISSION_DENIED", "SOURCE_NO_COVERAGE"):
            return {
                "status": "SKIPPED",
                "symbol": symbol,
                "reason": f"来源拒绝或无覆盖: {e.code}",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": False,
            }
        elif e.code == "SOURCE_RATE_LIMITED":
            return {
                "status": "FAILED",
                "symbol": symbol,
                "reason": f"速率限制 (429): {e.code}",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": True,
            }
        elif e.code in ("SOURCE_UNREACHABLE", "SOURCE_UNAVAILABLE", "SOURCE_INVALID_RESPONSE"):
            return {
                "status": "FAILED",
                "symbol": symbol,
                "reason": f"网络或响应错误: {e.code}",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": True,
            }
        elif e.code == "CREDENTIAL_NOT_CONFIGURED":
            return {
                "status": "FAILED",
                "symbol": symbol,
                "reason": "API密钥未配置",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": False,
            }
        else:
            return {
                "status": "FAILED",
                "symbol": symbol,
                "reason": f"未知错误: {e.code}",
                "bar_count": 0,
                "first_date": None,
                "last_date": None,
                "bundle": None,
                "retryable": False,
            }


def run_batch_history_job(settings, job_id, csv_path, start_date, end_date):
    """Execute batch history fetch job.

    Runs as a long-running job with:
    - Serial processing (one symbol at a time)
    - 120-second delay between requests
    - Idempotent resume capability
    - Progress tracking in job_runs.progress_json
    """
    from src.services.signal_scanner.cli import setup

    db = setup(settings)
    files = Artifacts(settings.runtime / "artifacts")
    cal = Calendar()
    adapter = TiingoAdapter(cal)

    universe = load_universe_csv(csv_path)
    total = len(universe)

    with db.read() as cx:
        job = one(cx, "SELECT * FROM job_runs WHERE id=?", (job_id,))
        progress = job["progress_json"] if job["progress_json"] else {}

    if not progress.get("results"):
        progress = {
            "total": total,
            "processed": 0,
            "current_ticker": None,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "insufficient": 0,
            "results": [],
            "started_at": stamp(),
            "csv_path": str(csv_path),
            "start_date": start_date,
            "end_date": end_date,
        }

    results = progress["results"]
    processed_tickers = {r["symbol"]: r for r in results}

    for idx, item in enumerate(universe):
        ticker = item["ticker"]

        existing = processed_tickers.get(ticker)
        if existing:
            status = existing["status"]
            if status in ("SUCCEEDED", "SKIPPED", "INSUFFICIENT"):
                continue
            if status == "FAILED" and not existing.get("retryable"):
                continue
            if status == "FAILED" and existing.get("retryable"):
                results.remove(existing)
                progress["failed"] -= 1
                del processed_tickers[ticker]

        progress["current_ticker"] = ticker
        with db.transaction() as cx:
            update(cx, "job_runs", "id", job_id, {"progress_json": progress})

        result = fetch_single_instrument(cal, adapter, ticker, start_date, end_date)

        if result["status"] == "SUCCEEDED" and result["bundle"]:
            try:
                with db.transaction() as cx:
                    bundle = result["bundle"]
                    register_instruments(cx, bundle["instruments"])
                    counts = ingest(cx, bundle, cal, files)
                    actions = store_events(cx, bundle["actions"], "TIINGO_EOD")
                    result["ingest"] = counts
                    result["corporate_actions"] = actions
            except Exception as e:
                result["status"] = "FAILED"
                result["reason"] = f"入库失败: {type(e).__name__}"
                result["retryable"] = False

        results.append({
            "symbol": result["symbol"],
            "status": result["status"],
            "bar_count": result["bar_count"],
            "first_date": result["first_date"],
            "last_date": result["last_date"],
            "reason": result.get("reason"),
            "retryable": result.get("retryable", False),
            "completed_at": stamp(),
        })

        progress["processed"] = len(results)
        progress["results"] = results

        if result["status"] == "SUCCEEDED":
            progress["succeeded"] += 1
        elif result["status"] == "FAILED":
            progress["failed"] += 1
        elif result["status"] == "SKIPPED":
            progress["skipped"] += 1
        elif result["status"] == "INSUFFICIENT":
            progress["insufficient"] += 1

        with db.transaction() as cx:
            update(cx, "job_runs", "id", job_id, {"progress_json": progress})

        if idx < total - 1:
            time.sleep(120)

    final_status = "SUCCEEDED" if progress["failed"] == 0 else "PARTIAL"

    summary = {
        "kind": "BATCH_HISTORY_REPORT",
        "total_symbols": total,
        "processed": progress["processed"],
        "succeeded": progress["succeeded"],
        "failed": progress["failed"],
        "skipped": progress["skipped"],
        "insufficient": progress["insufficient"],
        "coverage_rate": f"{progress['succeeded'] / total * 100:.1f}%" if total > 0 else "0%",
        "started_at": progress["started_at"],
        "completed_at": stamp(),
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    }

    report_ref = files.save(summary, "batch_history_reports")

    with db.transaction() as cx:
        update(
            cx,
            "job_runs",
            "id",
            job_id,
            {
                "status": final_status,
                "progress_json": progress,
                "output_manifest_ref": report_ref,
                "expected_count": total,
                "ok_count": progress["succeeded"],
                "failed_count": progress["failed"] + progress["skipped"] + progress["insufficient"],
            },
        )

    return summary
