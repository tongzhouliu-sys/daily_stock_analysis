"""Reproducible, isolated capacity acceptance. All inputs are SYNTHETIC."""

import argparse
import math
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import canonical, stamp
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft, register_dataset
from src.services.signal_scanner.services.rule_validation_web.service import create_run, create_validation
from src.services.signal_scanner.services.rule_validation_web.store import Store


def launch(store):
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ") if k in os.environ}
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.services.signal_scanner.worker.rule_validation",
            "--root",
            str(store.root),
            "--input-root",
            str(store.input_roots[0]),
            "worker",
            "--once",
        ],
        env=env,
        cwd=ROOT,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=100)
    parser.add_argument("--sessions", type=int, default=1261)
    parser.add_argument("--query-only", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args()
    output = ROOT / "runtime" / f"research-web-capacity-{args.symbols}-{args.sessions}"
    store = Store(output / "web", [output / "inputs"])
    store.initialize()
    if not args.query_only and not args.index_only:
        manifest = create_fixture(output / "inputs" / "large", symbols=args.symbols, sessions=args.sessions)
        dsid = register_dataset(store, manifest, "合成容量验收")
        draft = default_draft(store.get("dataset", dsid))
        draft.update(name="100证券五年工程容量", hypothesis="验证Web容量预算，不形成真实研究结论")
        prior_runs = sorted(
            [r for r in store.all("run") if r["status"] == "SUCCEEDED"], key=lambda r: r["created_at"]
        )
        prior_registration = None
        if prior_runs:
            prior = store.get("validation", prior_runs[0]["validation_id"])
            prior_registration = prior["manifest"]["registration"]
            draft.update(
                registration_id=prior_registration["id"], hypothesis=prior_registration["hypothesis"]
            )
        with store.write() as cx:
            v = create_validation(store, {"dataset_id": dsid, "draft": draft}, cx)
            if prior_registration:
                saved = store.get("validation", v["id"], cx)
                saved["manifest"]["registration"] = prior_registration
                store.put("validation", saved, cx)
        launch(store)
        v = store.get("validation", v["id"])
        assert v["status"] == "SUCCEEDED", v
        with store.write() as cx:
            r = create_run(
                store,
                {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False},
                cx,
            )
        # Explicitly revealed synthetic capacity fixture exercises all five years in one engine execution.
        # This administrative test fixture is never used by the browser's ordinary create-run API.
        with store.write() as cx:
            queued = store.get("run", r["id"], cx)
            queued["reveal"] = True
            store.put("run", queued, cx)
        print(canonical({"phase": "REPLAY_STARTED", "run_id": r["id"], "synthetic_reveal": True}), flush=True)
        launch(store)
    runs = sorted(store.all("run"), key=lambda r: r["created_at"])
    r = runs[-1]
    assert r["status"] == "SUCCEEDED", r
    (output / "run.json").write_text(canonical(r))
    if args.index_only:
        from src.services.signal_scanner.services.rule_validation_web.query import index_report

        cold = Store(output / "cold-index")
        cold.initialize()
        began = time.perf_counter()
        indexed = {"id": "cold-capacity"}
        index_report(cold, indexed, r["source_root"], r["report_ref"])
        result = {
            "report_hash": r["report_hash"],
            "elapsed_seconds": round(time.perf_counter() - began, 3),
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
        }
        (output / "cold-index-measurements.json").write_text(canonical(result))
        print(canonical(result), flush=True)
        return

    # HTTP measurements are in a fresh process so adapter RSS excludes the engine.
    if not args.query_only:
        subprocess.run(
            [
                sys.executable,
                __file__,
                "--symbols",
                str(args.symbols),
                "--sessions",
                str(args.sessions),
                "--query-only",
            ],
            check=True,
        )
        return
    from src.services.signal_scanner.core import Settings
    from src.services.signal_scanner.main import create_app
    from fastapi.testclient import TestClient

    os.environ["RULE_VALIDATION_WEB_ROOT"] = str(store.root)
    app = create_app(Settings(output / "query-app"))
    app.state.auth.provision()
    c = TestClient(app)
    c.post(
        "/api/auth/login",
        json={"password": (output / "query-app" / "首次登录密码.txt").read_text().strip()},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    prefix = f"/api/rule-validation/runs/{r['id']}"
    samples = {}
    max_payload = 0
    for suffix in (
        "/api/rule-validation/runs",
        prefix + "/report",
        prefix + "/statistics",
        prefix + "/statistics?rule_id=TR-01&sector=SYNTHETIC",
        prefix + "/rows?section=events&limit=50",
        prefix + "/benchmarks?kind=SPY",
    ):
        values = []
        for _ in range(30):
            began = time.perf_counter()
            response = c.get(suffix)
            values.append((time.perf_counter() - began) * 1000)
            assert response.status_code == 200, response.text[:200]
            max_payload = max(max_payload, len(response.content))
        samples[suffix.replace(r["id"], "RUN")] = {
            "cold_ms": round(values[0], 3),
            "p95_ms": round(sorted(values)[math.ceil(0.95 * len(values)) - 1], 3),
            "mean_ms": round(statistics.mean(values), 3),
        }
    report = c.get(prefix + "/report").json()["data"]
    result = {
        "generated_at": stamp(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "data_kind": "SYNTHETIC",
        "symbols": args.symbols,
        "sessions": args.sessions,
        "report_hash": r["report_hash"],
        "report_bytes": (Path(r["source_root"]) / r["report_ref"]).stat().st_size,
        "counts": report["counts"],
        "run_metrics": r["metrics"],
        "index_elapsed_seconds": r["index_elapsed_seconds"],
        "samples": samples,
        "max_json_bytes": max_payload,
        "query_process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "caveats": [
            "索引采用逐行解析和规范化临时流，按原协议校验业务hash及完整文件清单。",
            "进程RSS分别测量，未把单进程最大值冒充同时进程树峰值。",
        ],
    }
    (output / "measurements.json").write_text(canonical(result))
    print(canonical(result), flush=True)


if __name__ == "__main__":
    main()
