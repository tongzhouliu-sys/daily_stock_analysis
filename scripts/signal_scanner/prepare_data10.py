"""Freeze the existing 100-symbol research pool plus audited ten-year history."""

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import canonical, digest, stamp, text_decimal
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.research.versions import code_hash, dependency_hash

OUT = ROOT / "runtime/data10-update-20261026"
PROVIDER = "LOCAL_DATA10_TIINGO_MERGED"
FEED = "EOD_RAW"


def prepare(library_root):
    library_root = Path(library_root).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inputs").mkdir(exist_ok=True)
    (OUT / "bundles").mkdir(exist_ok=True)
    audit_path = library_root / "audit-extended.json"
    audit = json.loads((audit_path if audit_path.exists() else library_root / "audit.json").read_text())
    assert audit["integrity_check"] == "ok" and not audit["missing_files"]
    baseline = ROOT / "runtime/research-real-100-20261026/inputs"
    snapshot = json.loads((baseline / "snapshot.json").read_text())
    manifest = json.loads((baseline / "manifest.json").read_text())
    cal = Calendar()
    source = sqlite3.connect((library_root / "market.sqlite3").as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    db = sqlite3.connect((ROOT / "runtime/scanner.sqlite3").as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    instruments = {r["id"]: dict(r) for r in db.execute("SELECT * FROM instruments")}
    # Freeze from the live development facts; retain the immutable baseline only as protocol template.
    tiingo = {}
    for row in db.execute(
        "SELECT * FROM bar_revisions WHERE provider='TIINGO' AND quality_status='OK' ORDER BY instrument_id,market_date,revision"
    ):
        tiingo[(row["instrument_id"], row["market_date"])] = dict(row)
    events = []
    for row in db.execute("SELECT * FROM market_event_revisions ORDER BY event_id,revision"):
        row = dict(row)
        row["payload_json"] = json.loads(row["payload_json"])
        events.append(row)
    db.close()
    # Resolve source revisions first; avoid applying one split twice across providers.
    events = list({e["event_id"]: e for e in events}.values())
    split_keys = {
        (e["instrument_id"], e["effective_at"][:10], Decimal(str(e["payload_json"]["ratio"])))
        for e in events
        if e["event_type"] == "SPLIT"
    }
    wanted = {i["ticker"]: i for i in snapshot["instruments"]}
    imported_splits, action_conflicts = [], []
    for filename in ("splits.csv", "splits_new.csv"):
        with (ROOT / "data10" / filename).open(newline="") as f:
            for n, row in enumerate(csv.DictReader(f), 2):
                ticker, day = row["ticker"], row["execution_date"]
                if ticker not in wanted or not audit["first_date"] <= day <= audit["last_date"]:
                    continue
                ratio = Decimal(row["split_to"]) / Decimal(row["split_from"])
                if not ratio.is_finite() or ratio <= 0:
                    raise ValueError("Invalid corporate action ratio")
                key = ("US:" + ticker, day, ratio)
                if key in split_keys:
                    continue
                if any(k[:2] == key[:2] for k in split_keys):
                    action_conflicts.append(row)
                    continue
                split_keys.add(key)
                imported_splits.append(
                    {
                        "event_id": "data10-split-" + row["id"],
                        "instrument_id": key[0],
                        "event_type": "SPLIT",
                        "revision": 1,
                        "effective_at": day + "T00:00:00.000Z",
                        "known_at": stamp(),
                        "status": "PUBLISHED",
                        "source": "DATA10_RAW",
                        "payload_json": {
                            "ratio": text_decimal(ratio),
                            "source_file": filename,
                            "source_line": n,
                            "source_record": row,
                            "historical_known_at_verified": False,
                        },
                    }
                )
    if action_conflicts:
        raise ValueError(f"Conflicting split ratios: {action_conflicts}")
    events += imported_splits
    bars, bundles, coverage, differences = {}, [], [], []
    totals = Counter()
    last = max(b["market_date"] for b in tiingo.values())
    for ticker, inst in sorted(wanted.items()):
        iid = inst["id"]
        raw = {
            r["market_date"]: dict(r)
            for r in source.execute("SELECT * FROM bars WHERE ticker=? ORDER BY market_date", (ticker,))
        }
        existing = {d: r for (i, d), r in tiingo.items() if i == iid}
        chosen, source_counts = [], Counter()
        for day in sorted(set(raw) | set(existing)):
            if day in existing:
                b = existing[day]
                provenance = {
                    "provider": b["provider"],
                    "feed": b["feed"],
                    "source_row_id": b["id"],
                    "content_hash": b["content_hash"],
                }
                if day in raw:
                    gap = abs(Decimal(raw[day]["close"]) / Decimal(b["close"]) - 1)
                    if gap > Decimal("0.01"):
                        differences.append(
                            {
                                "ticker": ticker,
                                "market_date": day,
                                "raw_close": raw[day]["close"],
                                "tiingo_close": b["close"],
                                "relative_difference": str(gap),
                            }
                        )
                source_counts["TIINGO"] += 1
            else:
                b = raw[day]
                provenance = {
                    "provider": "DATA10_RAW",
                    "source_file": day + ".csv",
                    "ticker": ticker,
                    "window_start": b["window_start"],
                    "transactions": b["transactions"],
                }
                source_counts["DATA10_RAW"] += 1
            chosen.append(
                dict(
                    instrument_id=iid,
                    market_date=day,
                    session="EOD",
                    is_final=True,
                    available_at=None,
                    provider_fields=provenance,
                    **{k: b[k] for k in ("open", "high", "low", "close", "volume")},
                )
            )
        days = {b["market_date"] for b in chosen}
        missing = sorted(set(cal.dates(chosen[0]["market_date"], chosen[-1]["market_date"])) - days)
        if missing:
            raise ValueError(f"Internal gap for {ticker}: {missing}")
        totals.update(source_counts)
        coverage.append(
            {
                "ticker": ticker,
                "rows": len(chosen),
                "first_date": chosen[0]["market_date"],
                "last_date": chosen[-1]["market_date"],
                "internal_missing_sessions": missing,
                "trailing_missing_sessions": cal.distance(chosen[-1]["market_date"], last),
                "source_counts": dict(source_counts),
            }
        )
        old = instruments.get(iid)
        metadata = {
            "id": iid,
            "ticker": ticker,
            "name": old["display_name"] if old else ticker,
            "type": inst["asset_type"],
            "currency": "USD",
            "venue": old["primary_venue"] if old else "UNKNOWN",
            "sector": old["sector_code"] if old else inst["sector"],
            "roles": json.loads(old["reference_roles_json"]) if old else ["TRADE"],
            "profile": json.loads(old["profile_json"]) if old else {},
        }
        bundle = {
            "provider": PROVIDER,
            "feed": FEED,
            "coverage": "MIXED_SOURCE_RAW_EOD_RTH_UNVERIFIED",
            "instruments": [metadata],
            "bars": chosen,
            "source_session_kind": "EOD_DAILY",
            "rth_verified": False,
        }
        path = OUT / "bundles" / (ticker + ".json")
        path.write_text(canonical(bundle))
        bundles.append({"path": str(path), "sha256": digest(bundle), "ticker": ticker, "rows": len(chosen)})
        bars[iid] = [
            {
                **b,
                "revision": 1,
                "session_kind": "EOD_DAILY",
                "quality_status": "OK",
                "available_at_kind": "UNKNOWN",
                "bar_end_at": cal.session(b["market_date"])["close_at"],
                "provider": PROVIDER,
                "feed": FEED,
            }
            for b in chosen
        ]
    source.close()
    snapshot.update(
        bars=bars,
        events=events,
        adjustment_epoch="data10-tiingo-split-revised-history-20261026",
        source_provenance={
            "kind": "AUDITED_DATA10_PLUS_EXISTING_TIINGO",
            "primary_overlap_source": "TIINGO",
            "raw_library": str(library_root / "market.sqlite3"),
            "rth_scope_verified": False,
            "historical_available_at_verified": False,
            "identity_history_verified": False,
        },
    )
    first = min(b["market_date"] for bs in bars.values() for b in bs)
    snapshot["universe"] = [
        dict(
            instrument_id=i["id"],
            **{"from": bars[i["id"]][0]["market_date"], "to": last},
            membership_basis="CURRENT_100_SYMBOL_POOL_NOT_HISTORICAL",
        )
        for i in wanted.values()
    ]
    (OUT / "inputs/snapshot.json").write_text(canonical(snapshot))
    start, end = cal.shift(first, 260), cal.shift(last, -23)
    manifest.update(
        experiment_id="data10-100-eod-20261026",
        logical_key="data10-100-eod-20261026",
        start_date=start,
        end_date=end,
        label_as_of=cal.session(last)["close_at"],
        code_hash=code_hash(),
        dependency_hash=dependency_hash(),
        data_snapshot_ref={"path": "snapshot.json", "sha256": digest(snapshot)},
    )
    manifest["price_basis"]["adjustment_epoch"] = snapshot["adjustment_epoch"]
    manifest["registration"].update(
        id=manifest["experiment_id"],
        registered_at=stamp(),
        hypothesis="审计十年原始日线，保留Tiingo更新尾部，使用当前100标的池进行明确的EOD探索性回放。",
    )
    (OUT / "inputs/manifest.json").write_text(canonical(manifest))
    (OUT / "splits-to-import.json").write_text(canonical(imported_splits))
    (OUT / "overlap-differences.json").write_text(canonical(differences))
    summary = {
        "prepared_at": stamp(),
        "provider": PROVIDER,
        "feed": FEED,
        "coverage": coverage,
        "totals": dict(totals),
        "total_rows": sum(totals.values()),
        "symbols": len(bars),
        "start_date": start,
        "end_date": end,
        "price_data_end": last,
        "new_splits": len(imported_splits),
        "split_conflicts": action_conflicts,
        "overlap_close_differences_over_one_percent": len(differences),
        "bundles": bundles,
        "snapshot_sha256": digest(snapshot),
        "library_root": str(library_root),
    }
    (OUT / "prepared.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(canonical({k: v for k, v in summary.items() if k not in ("coverage", "bundles")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--library-root", type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.output.resolve()
    prepare(args.library_root)
