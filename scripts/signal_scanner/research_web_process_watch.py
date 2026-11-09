"""Measure the complete isolated capacity process tree plus its Web reader."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "runtime" / "research-web-capacity-100-1261"


def snapshot():
    rows = []
    for line in subprocess.check_output(["ps", "-Ao", "pid=,ppid=,rss=,command="], text=True).splitlines():
        pieces = line.strip().split(None, 3)
        if len(pieces) == 4:
            rows.append((int(pieces[0]), int(pieces[1]), int(pieces[2]) * 1024, pieces[3]))
    descendants = {os.getpid()}
    while True:
        added = {pid for pid, ppid, _, _ in rows if ppid in descendants} - descendants
        if not added:
            break
        descendants |= added
    selected = [
        {"pid": pid, "ppid": ppid, "rss_bytes": rss}
        for pid, ppid, rss, command in rows
        if pid in descendants or ("uvicorn app.main:create_app" in command and "--port 18767" in command)
    ]
    return {"at": time.time(), "total_rss_bytes": sum(r["rss_bytes"] for r in selected), "processes": selected}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    samples = []
    with (OUTPUT / "watched-benchmark.log").open("w") as log:
        for extra in ([], ["--index-only"]):
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/research_web_benchmark.py"), *extra],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                samples.append(snapshot())
                time.sleep(1)
            if process.returncode:
                raise SystemExit(process.returncode)
    result = {
        "observed_peak_rss_bytes": max(s["total_rss_bytes"] for s in samples),
        "sample_interval_seconds": 1,
        "scope": "entire capacity harness/worker/indexer plus isolated Web reader; includes cold indexing",
        "samples": samples,
    }
    (OUTPUT / "process-tree-full.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
