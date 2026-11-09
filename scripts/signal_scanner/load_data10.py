"""Audit and index a raw daily-aggregate delivery without granting RTH/PIT status.

All source strings are retained. The compact full-market library is separate
from scanner instrument identities and the active trading universe.
"""

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import canonical, stamp
from src.services.signal_scanner.domain.calendar import Calendar

FIELDS = ["ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"]


def problems(row, day):
    errors = []
    try:
        values = [float(row[k]) for k in ("open", "high", "low", "close", "volume", "transactions")]
        if not all(math.isfinite(v) for v in values):
            errors.append("NON_FINITE")
        o, h, low, c, v, n = values
        if min(o, h, low, c) <= 0 or v < 0 or n < 0:
            errors.append("INVALID_PRICE_OR_VOLUME")
        if h < max(o, low, c) or low > min(o, h, c):
            errors.append("INVALID_OHLC")
        timestamp = int(row["window_start"])
        local = datetime.fromtimestamp(timestamp // 1_000_000_000, ZoneInfo("America/New_York"))
        if local.date().isoformat() != day or local.hour != 0 or local.minute != 0 or local.second != 0:
            errors.append("WINDOW_DATE_MISMATCH")
        if not row["ticker"] or None in row:
            errors.append("INVALID_FIELDS")
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        errors.append("INVALID_FIELDS")
    return errors


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def audit(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    final = output / "market.sqlite3"
    if final.exists():
        raise ValueError("Library already exists; use a new output directory")
    tmp = output / "market.building.sqlite3"
    if tmp.exists():
        raise ValueError("An unfinished library exists; inspect before retrying")
    began = time.monotonic()
    files = sorted(p for p in source.glob("*.csv") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
    if not files:
        raise ValueError("No dated daily CSV files")
    cal = Calendar()
    expected = cal.dates(files[0].stem, files[-1].stem)
    expected_set = set(expected)
    file_days = {p.stem for p in files}
    cx = sqlite3.connect(tmp)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute(
        "CREATE TABLE bars (market_date TEXT, ticker TEXT, volume TEXT, open TEXT, close TEXT, high TEXT, low TEXT, window_start TEXT, transactions TEXT, PRIMARY KEY(market_date,ticker)) WITHOUT ROWID"
    )
    cx.execute(
        "CREATE TABLE actions (source_file TEXT, source_row INTEGER, payload_json TEXT, PRIMARY KEY(source_file,source_row)) WITHOUT ROWID"
    )
    cx.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    symbols, reasons, daily = {}, Counter(), []
    total = accepted = duplicate = invalid = zero_volume = 0
    with (output / "quarantine.csv").open("w", newline="") as q:
        writer = csv.writer(q)
        writer.writerow(["source_file", "line", "reasons", "row_json"])
        for index, path in enumerate(files):
            count = good = bad = dup = 0
            seen = {}
            batch = []
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != FIELDS:
                    raise ValueError(f"Unexpected schema: {path.name}")
                for line, row in enumerate(reader, 2):
                    count += 1
                    errors = problems(row, path.stem)
                    if path.stem not in expected_set:
                        errors.append("NON_SESSION")
                    ticker = row.get("ticker", "")
                    if ticker in seen:
                        errors.append("DUPLICATE_IDENTICAL" if seen[ticker] == row else "DUPLICATE_CONFLICT")
                        dup += 1
                    else:
                        seen[ticker] = row
                    if errors:
                        bad += 1
                        reasons.update(errors)
                        writer.writerow([path.name, line, "|".join(errors), canonical(row)])
                        continue
                    good += 1
                    zero_volume += float(row["volume"]) == 0
                    item = symbols.setdefault(
                        ticker,
                        {
                            "ticker": ticker,
                            "rows": 0,
                            "first_date": path.stem,
                            "last_date": path.stem,
                            "internal_missing_sessions": 0,
                        },
                    )
                    if item["rows"]:
                        item["internal_missing_sessions"] += max(0, index - item.pop("last_index") - 1)
                    item.update(rows=item["rows"] + 1, last_date=path.stem, last_index=index)
                    batch.append((path.stem, *(row[k] for k in FIELDS)))
            cx.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?)", batch)
            cx.commit()
            total += count
            accepted += good
            invalid += bad
            duplicate += dup
            daily.append(
                {
                    "file": path.name,
                    "sha256": sha(path),
                    "bytes": path.stat().st_size,
                    "rows": count,
                    "accepted": good,
                    "rejected": bad,
                    "duplicates": dup,
                }
            )
            if (index + 1) % 100 == 0:
                progress = {
                    "files": index + 1,
                    "total_files": len(files),
                    "rows": total,
                    "accepted": accepted,
                    "elapsed_seconds": round(time.monotonic() - began, 1),
                }
                (output / "progress.json").write_text(canonical(progress))
                print(canonical(progress), flush=True)
    extras = []
    for path in sorted(source.glob("*.csv")):
        if path in files:
            continue
        count, batch = 0, []
        with path.open(encoding="utf-8-sig", newline="") as f:
            for n, row in enumerate(csv.DictReader(f), 2):
                count += 1
                if path.name.startswith(("splits", "dividends")):
                    batch.append((path.name, n, canonical(row)))
                if len(batch) >= 10000:
                    cx.executemany("INSERT INTO actions VALUES (?,?,?)", batch)
                    batch.clear()
            cx.executemany("INSERT INTO actions VALUES (?,?,?)", batch)
        extras.append({"file": path.name, "sha256": sha(path), "bytes": path.stat().st_size, "rows": count})
    for item in symbols.values():
        item.pop("last_index", None)
    report = {
        "kind": "LOCAL_DAILY_DELIVERY_AUDIT",
        "checked_at": stamp(),
        "source": str(source),
        "first_date": files[0].stem,
        "last_date": files[-1].stem,
        "expected_sessions": len(expected),
        "files": len(files),
        "missing_files": sorted(set(expected) - file_days),
        "non_session_files": sorted(file_days - set(expected)),
        "rows": total,
        "accepted_rows": accepted,
        "quarantined_rows": invalid,
        "duplicate_rows": duplicate,
        "zero_volume_rows": zero_volume,
        "unique_tickers": len(symbols),
        "errors": dict(reasons),
        "symbols_with_internal_gaps": sum(bool(s["internal_missing_sessions"]) for s in symbols.values()),
        "full_span_tickers": sum(s["rows"] == len(expected) for s in symbols.values()),
        "source_session_kind": "EOD_DAILY",
        "rth_verified": False,
        "historical_identity_verified": False,
        "available_at_kind": "UNKNOWN",
        "source_files": daily,
        "supporting_files": extras,
        "elapsed_seconds": round(time.monotonic() - began, 2),
    }
    cx.execute("INSERT INTO metadata VALUES (?,?)", ("audit", canonical(report)))
    cx.commit()
    print("Building ticker index", flush=True)
    cx.execute("CREATE INDEX bars_ticker_date ON bars(ticker,market_date)")
    cx.commit()
    report["integrity_check"] = cx.execute("PRAGMA integrity_check").fetchone()[0]
    if report["integrity_check"] != "ok":
        raise ValueError("Library integrity check failed")
    cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cx.close()
    tmp.rename(final)
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with (output / "symbol-coverage.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ticker", "rows", "first_date", "last_date", "internal_missing_sessions"]
        )
        writer.writeheader()
        writer.writerows(symbols[k] for k in sorted(symbols))
    print(
        canonical({k: v for k, v in report.items() if k not in ("source_files", "supporting_files")}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.source, args.output)
