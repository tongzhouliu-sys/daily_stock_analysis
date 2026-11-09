"""Synthetic 300-instrument, data-ready calculation + SQLite publication benchmark."""

import argparse
import copy
import json
import math
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import canonical, stamp, uid
from src.services.signal_scanner.storage.db import Database, insert, one
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.worker.pipeline import calculate, persist


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    source = ROOT / "runtime" / "browser-test"
    files = Artifacts(source / "artifacts")
    with Database(source / "scanner.sqlite3").read() as cx:
        job = one(
            cx,
            "SELECT * FROM job_runs WHERE job_name='DAILY_SCAN' AND status='SUCCEEDED' AND input_manifest_ref IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
        )
    require_input = job and job["input_manifest_ref"]
    if not require_input:
        raise SystemExit("请先建立独立的runtime/browser-test虚构验证环境。")
    original = files.read(job["input_manifest_ref"])
    manifest = copy.deepcopy(original)
    template = next(i for i in original["instruments"] if i["id"] == "DEMO")
    raw = original["bars"]["DEMO"]
    manifest["instruments"] = [i for i in original["instruments"] if i["id"] not in ("DEMO", "DEMO2")]
    manifest["bars"] = {i["id"]: original["bars"][i["id"]] for i in manifest["instruments"]}
    members = []
    for n in range(300):
        iid = f"BENCH{n:03}"
        inst = {**template, "id": iid, "current_ticker": iid, "display_name": "虚构容量验证 " + iid}
        manifest["instruments"].append(inst)
        manifest["bars"][iid] = [{**b, "instrument_id": iid, "id": iid + ":" + b["market_date"]} for b in raw]
        members.append(iid)
    manifest["universe"]["value"]["members"] = members
    manifest["previous_events"] = []
    manifest["previous_environment"] = None
    statuses = manifest["security_statuses"]
    status = statuses["DEMO"]
    manifest["security_statuses"] = {i: {**status, "instrument_id": i} for i in members}
    samples = []
    cal = Calendar()
    with tempfile.TemporaryDirectory(dir=ROOT / "runtime", prefix="benchmark-") as folder:
        db = Database(Path(folder) / "db.sqlite3")
        db.migrate()
        artifacts = Artifacts(Path(folder) / "artifacts")
        with db.transaction() as cx:
            for i in manifest["instruments"]:
                insert(cx, "instruments", i)
        for index in range(args.samples):
            jid = uid("benchmark")
            with db.transaction() as cx:
                insert(
                    cx,
                    "job_runs",
                    dict(id=jid, logical_key=jid, revision=1, job_name="DAILY_SCAN", status="RUNNING"),
                )
            start = time.perf_counter()
            output = calculate(manifest, cal)
            assert output["coverage"]["ok"] == 300 and output["coverage"]["failed"] == 0
            assert len(output["events"]) >= 300
            with db.transaction() as cx:
                persist(cx, output, manifest, jid, artifacts)
            elapsed = time.perf_counter() - start
            samples.append(elapsed)
            print(f"sample {index + 1}/{args.samples}: {elapsed:.3f}s", flush=True)
    ordered = sorted(samples)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    report = dict(
        generated_at=stamp(),
        scope="Synthetic data-ready calculation, two-pass aggregation, artifact write and SQLite publication; excludes supplier fetch",
        instruments=300,
        bars_per_instrument=len(raw),
        samples=len(samples),
        seconds=samples,
        p95_seconds=p95,
        target_seconds=300,
        passed=p95 <= 300,
        python=platform.python_version(),
        system=platform.system(),
        fictional=True,
        limitation="本机少量样本的经验分位；不是生产VM持续SLA或策略收益证据",
    )
    (ROOT / "docs" / "performance_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(canonical(report))


if __name__ == "__main__":
    main()
