"""Freeze verified scoring references, then apply them with backup and an explicit scan.

The trade universe, rule stages, account data and notification settings are preserved.
Industry membership is current classification, never historical PIT classification.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.adapters.market import ingest
from src.services.signal_scanner.adapters.tiingo_adapter import TiingoAdapter
from src.services.signal_scanner.core import Settings, canonical, digest, now, stamp
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.config import get_config
from src.services.signal_scanner.services.source_data import read_private_env, store_events
from src.services.signal_scanner.storage.db import Database, audit, many, one, update
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.jobs import Worker, enqueue
from src.services.signal_scanner.worker.pipeline import latest_workspace

SECTORS = {
    "XLK": "Information Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}
PROTECTED = ("ledger_entries", "account_snapshots", "account_state", "notifications")


def holdings(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        strings = [
            "".join(e.itertext()) for e in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)
        ]
        rows = []
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(".//m:sheetData/m:row", ns):
            value = {}
            for c in row.findall("m:c", ns):
                v = c.find("m:v", ns)
                text = v.text if v is not None else "".join(c.itertext())
                value["".join(x for x in c.get("r") if x.isalpha())] = (
                    strings[int(text)] if c.get("t") == "s" else text
                )
            rows.append(value)
    assert rows[3]["B"] == "Ticker", "Unexpected official holdings schema"
    day = datetime.strptime(rows[2]["B"], "As of %d-%b-%Y").date().isoformat()
    return day, {r["B"].replace("/", ".") for r in rows[4:] if r.get("B")}


def instrument(row):
    return dict(
        id=row["id"],
        ticker=row["current_ticker"],
        name=row["display_name"],
        type=row["asset_type"],
        currency=row["currency"],
        venue=row["primary_venue"],
        sector=row["sector_code"],
        roles=row["reference_roles_json"],
        profile=dict(row["profile_json"]),
    )


def prepare(runtime, output, sources):
    db, cal = Database(runtime / "scanner.sqlite3"), Calendar()
    with db.read() as cx:
        provider = get_config(cx, "provider")["value"]
        universe = get_config(cx, "universe")
        rows = many(cx, "SELECT * FROM instruments WHERE status='ACTIVE'")
        target = one(
            cx,
            "SELECT market_date FROM job_runs WHERE job_name='DAILY_SCAN' AND published_seq IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
        )["market_date"]
    assert provider["mode"] == "TIINGO_EOD_CACHE"
    cache_path = runtime / "imports" / provider["file"]
    cache = json.loads(cache_path.read_text())
    original_hash = digest(cache)
    members = set(universe["value"]["members"])
    wanted = {r["current_ticker"] for r in rows if r["id"] in members and r["asset_type"] != "ETF"}
    mapping, evidence = {}, {}
    for ticker, sector in SECTORS.items():
        path = sources / (ticker + ".xlsx")
        day, tickers = holdings(path)
        assert day <= target and cal.distance(day, target) <= 1, "Classification snapshot is not aligned"
        evidence[ticker] = dict(
            source_url=f"https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker.lower()}.xlsx",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            as_of=day,
        )
        for symbol in tickers:
            if symbol not in wanted:
                continue
            assert symbol not in mapping, f"Ambiguous sector membership: {symbol}"
            mapping[symbol] = ticker
    version = digest(evidence)
    metadata = []
    for row in rows:
        item = instrument(row)
        if row["id"] not in members:
            continue
        if row["asset_type"] == "ETF":
            assert item["ticker"] == "SPY", "A separate comparator policy is needed for this ETF"
            item["profile"].update(comparators=["US:QQQ", "US:IWM"], scoring_mapping_version=version)
        else:
            ref = mapping.get(item["ticker"])
            assert ref, f"No official industry membership: {item['ticker']}"
            item["sector"] = SECTORS[ref]
            item["profile"].update(
                sector_etf_id="US:" + ref,
                market_ref_id="US:SPY",
                concentration_bucket=SECTORS[ref],
                scoring_mapping_version=version,
                classification_source=evidence[ref],
                classification_scope="CURRENT_NOT_HISTORICAL_PIT",
            )
        metadata.append(item)
    assert {i["id"] for i in metadata} == members
    adapter = TiingoAdapter(cal)
    refs, bars, actions, reports = [], [], [], []
    for ticker in [*SECTORS, "QQQ", "IWM"]:
        pack = adapter.fetch(ticker, cal.shift(target, -319), target)
        assert pack["source_report"]["status"] == "SUCCEEDED", f"Incomplete reference history: {ticker}"
        item = pack["instruments"][0]
        item.update(
            type="ETF", roles=["SECTOR"] if ticker in SECTORS else ["MARKET"], sector=SECTORS.get(ticker)
        )
        item["profile"].update(concentration_bucket=SECTORS.get(ticker, "BROAD_MARKET"), reference_only=True)
        refs.append(item)
        bars.extend(pack["bars"])
        actions.extend(pack["actions"])
        reports.append(
            dict(
                ticker=ticker,
                rows=len(pack["bars"]),
                first_date=pack["bars"][0]["market_date"],
                last_date=pack["bars"][-1]["market_date"],
                response_hash=pack["raw_response_hash"],
            )
        )
        print(canonical(reports[-1]), flush=True)
    ref_ids = {i["id"] for i in refs}
    cache["instruments"] = metadata + refs
    cache["bars"] = [b for b in cache["bars"] if b["instrument_id"] not in ref_ids] + bars
    prepared = dict(
        target_date=target,
        cache_file=provider["file"],
        previous_cache_hash=original_hash,
        universe_hash=digest(universe),
        members=sorted(members),
        bundle=cache,
        actions=actions,
        metadata_version=version,
        official_sources=evidence,
        reference_coverage=reports,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "prepared.json").write_text(canonical(dict(payload=prepared, sha256=digest(prepared))))
    print(canonical(dict(prepared=True, stocks=len(metadata) - 1, references=len(refs), bars=len(bars))))


def fingerprint(db):
    with db.read() as cx:
        return {
            t: digest([dict(r) for r in cx.execute("SELECT * FROM " + t + " ORDER BY rowid")])
            for t in PROTECTED
        }


def apply(runtime, output, scan=True):
    frozen = json.loads((output / "prepared.json").read_text())
    p = frozen["payload"]
    assert digest(p) == frozen["sha256"], "Prepared inputs changed"
    db, cal = Database(runtime / "scanner.sqlite3"), Calendar()
    files = Artifacts(runtime / "artifacts")
    cache_path = runtime / "imports" / p["cache_file"]
    old_cache = cache_path.read_bytes()
    assert digest(json.loads(old_cache)) in (p["previous_cache_hash"], digest(p["bundle"])), (
        "Cache changed since preparation"
    )
    with db.read() as cx:
        assert digest(get_config(cx, "universe")) == p["universe_hash"], "Trade universe changed"
        assert one(cx, "SELECT count(*) n FROM account_state WHERE status='ACTIVE'")["n"] == 0
        assert get_config(cx, "experiment")["value"]["stage"] != "SHADOW"
    before = fingerprint(db)
    tag = hashlib.sha256(str(runtime).encode()).hexdigest()[:12]
    backup = output / "backups" / (tag + "-" + now().strftime("%Y%m%dT%H%M%S"))
    backup.mkdir(parents=True, mode=0o700)
    with (
        sqlite3.connect(db.path.as_uri() + "?mode=ro", uri=True) as src,
        sqlite3.connect(backup / "scanner.sqlite3") as dst,
    ):
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    shutil.copy2(cache_path, backup / cache_path.name)
    tmp = cache_path.with_suffix(".repair.tmp")
    tmp.write_text(canonical(p["bundle"]))
    os.chmod(tmp, 0o600)
    try:
        with db.transaction() as cx:
            counts = ingest(cx, p["bundle"], cal, files)
            assert counts["quarantined"] == 0, counts
            action_counts = store_events(cx, p["actions"], "TIINGO")
            ref = files.save(dict(version=p["metadata_version"], sources=p["official_sources"]), "inputs")
            audit(
                cx,
                "SCORING_REFERENCES_REPAIRED",
                p["metadata_version"],
                "补齐经核验的行业映射与参照ETF；保留100标的池",
                input_ref=ref,
            )
            os.replace(tmp, cache_path)
    except Exception:
        cache_path.write_bytes(old_cache)
        tmp.unlink(missing_ok=True)
        raise
    jid = None
    if scan:
        worker = Worker(Settings(runtime))
        with db.transaction() as cx:
            jid = enqueue(cx, "DAILY_SCAN", "QUALITY_REPAIR:" + frozen["sha256"], p["target_date"])
            job = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
            run = job["status"] not in ("SUCCEEDED", "PARTIAL")
            if run:
                update(
                    cx,
                    "job_runs",
                    "id",
                    jid,
                    dict(
                        status="RUNNING",
                        lease_owner=worker.owner,
                        lease_until=stamp(now() + timedelta(hours=2)),
                        heartbeat_at=stamp(),
                        attempt_count=job["attempt_count"] + 1,
                    ),
                )
        if run:
            print(canonical(dict(scan_started=jid)), flush=True)
            try:
                worker.scan(job)
            except Exception:
                with db.transaction() as cx:
                    update(
                        cx,
                        "job_runs",
                        "id",
                        jid,
                        dict(status="FAILED", error_json=dict(code="QUALITY_REPAIR_SCAN_FAILED")),
                    )
                raise
    with db.read() as cx:
        _, workspace = latest_workspace(cx, files)
        cards = [
            one(
                cx, "SELECT * FROM signal_cards WHERE card_id=? AND revision=?", (r["card_id"], r["revision"])
            )
            for r in workspace["cards"]
        ]
        cards = [c for c in cards if c["lifecycle"] in ("PENDING", "CONFIRMED")]
        result = dict(
            runtime=str(runtime),
            backup=str(backup),
            import_counts=counts,
            action_counts=action_counts,
            scan_id=jid,
            trade_pool=len(p["members"]),
            cards=len(cards),
            scored=sum(c["quality_score"] is not None for c in cards),
            warnings=sum(c["severity"] == "CAUTION" for c in cards),
            incomplete=sum("SCORE_INCOMPLETE" in c["reason_codes_json"] for c in cards),
            scores=[
                dict(instrument=c["instrument_id"], family=c["family"], score=c["quality_score"])
                for c in cards
                if c["quality_score"] is not None
            ],
        )
        assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert digest(get_config(cx, "universe")) == p["universe_hash"]
    assert before == fingerprint(db), "Protected business state changed"
    result["protected_state_preserved"] = True
    (output / ("result-" + tag + ".json")).write_text(canonical(result))
    print(canonical(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "apply"])
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/quality-score-repair")
    parser.add_argument("--sources", type=Path, default=ROOT / "runtime/quality-score-repair/sources")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        read_private_env(args.env_file)
    if args.mode == "prepare":
        prepare(args.runtime.resolve(), args.output.resolve(), args.sources.resolve())
    else:
        apply(args.runtime.resolve(), args.output.resolve())
