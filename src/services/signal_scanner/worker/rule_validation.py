"""One persistent, separately launched offline research worker. No daily-scan queue."""

import argparse
from src.services.signal_scanner.platform_support import locking as fcntl
import json
import os
from src.services.signal_scanner.platform_support import peak_rss_bytes, python_command
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from src.services.signal_scanner.core import ROOT, DomainError, canonical, require, stamp, uid
from src.services.signal_scanner.research.reporting import atomic_text
from src.services.signal_scanner.services.rule_validation_web.store import Store


def execute(store, kind, item):
    from src.services.signal_scanner.services.rule_validation_web.catalog import check_dataset, file_hash, freeze, registration_digest
    from src.services.signal_scanner.services.rule_validation_web.query import index_report, raw_rows, summary

    if kind == "validation":
        from src.services.signal_scanner.research.experiments import split_manifest
        from src.services.signal_scanner.research.isolation import offline
        from src.services.signal_scanner.research.runner import validate_input

        ds = store.get("dataset", item["dataset_id"])
        path = freeze(store, item["id"], item["manifest"], ds)
        with offline():
            m, s, cal = validate_input(path)
            split_manifest(m, cal)
            require(
                min(b["market_date"] for rows in s["bars"].values() for b in rows)
                <= cal.shift(m["start_date"], -m["warmup_bars"]),
                "INSUFFICIENT_WARMUP",
            )
        item.update(
            status="SUCCEEDED",
            result_status="VALID",
            data_kind=s["data_kind"],
            manifest_path=path,
            manifest_hash=file_hash(path),
            capabilities={"can_run": True},
            registration_digest=registration_digest(store),
            issues=[],
        )
    elif kind == "run":
        from src.services.signal_scanner.research.runner import run
        from src.services.signal_scanner.services.rule_validation_web.cancellation import requested

        ds = store.get("dataset", item["dataset_id"])
        check_dataset(store, ds)
        v = store.get("validation", item["validation_id"])
        require(file_hash(v["manifest_path"]) == v["manifest_hash"], "VALIDATION_STALE")
        if item.get("parent_run_id"):
            m = json.loads(Path(item["manifest_path"]).read_text())
            m.update(logical_key=item["logical_key"], experiment_id=item["id"])
            item["manifest_path"] = freeze(store, item["id"], m, ds)
        else:
            # Registration may have been written by an interrupted attempt of this same run.
            if item["attempt"] == 1:
                require(v["registration_digest"] == registration_digest(store), "VALIDATION_STALE")

        def check():
            return requested(store, item)

        require(not check(), "RESEARCH_CANCELLED")
        result = run(
            item["manifest_path"],
            store.root / "engine",
            reveal=item["reveal"],
            web_progress=True,
            cancellation_requested=check,
            web_attempt=item["attempt"],
        )
        require(not check(), "RESEARCH_CANCELLED")
        item.update(
            engine_job_id=result["id"],
            phase="INDEXING",
            report_state="INDEXING",
            heartbeat_at=stamp(),
            processed=None,
            total=None,
        )
        with store.write() as cx:
            require(store.get(kind, item["id"], cx)["status"] != "CANCEL_REQUESTED", "RESEARCH_CANCELLED")
            store.put(kind, item, cx)
        indexing_began = time.monotonic()
        index_report(store, item, store.root / "engine", result["report_ref"])
        item["index_elapsed_seconds"] = round(time.monotonic() - indexing_began, 3)
        item.update(status=result["status"], phase="COMPLETED", processed=1, total=1)
    else:
        from src.services.signal_scanner.research.experiments import compare_reports

        a, b = [store.get("run", item[k]) for k in ("left_run_id", "right_run_id")]
        reports = [summary(r) for r in (a, b)]
        require(
            [a["report_hash"], b["report_hash"]] == [item["left_hash"], item["right_hash"]],
            "ARTIFACT_CORRUPT",
        )
        axiskey = {
            "rules": "ruleset_version",
            "universe": "universe_version",
            "data": "snapshot_hash",
            "price_basis": "price_basis",
        }[item["axis"]]
        # Non-changing identity fields must match. Registration identity/hypothesis may differ.
        fields = set(reports[0]["identity"]) - {axiskey, "registration"}
        for field in sorted(fields):
            require(
                reports[0]["identity"].get(field) == reports[1]["identity"].get(field),
                "INCOMPARABLE_EXPERIMENTS",
                "比较存在未声明的口径差异",
                field=field,
            )
        for field in ("data_kind", "report_schema_version"):
            require(reports[0][field] == reports[1][field], "INCOMPARABLE_EXPERIMENTS", field=field)
        for field in ("min_completed_samples", "benchmark_ref", "split_policy"):
            require(
                reports[0]["manifest"][field] == reports[1]["manifest"][field],
                "INCOMPARABLE_EXPERIMENTS",
                field=field,
            )
        require(
            reports[0]["identity"][axiskey] != reports[1]["identity"][axiskey],
            "NO_AXIS_CHANGE",
            "所选轴没有变化",
        )
        full = []
        for r, s in zip((a, b), reports):
            full.append({**s, "events": list(raw_rows(r, "events")), "statistics": []})
        result = compare_reports(*full, item["axis"])
        output = store.root / "comparisons" / (item["id"] + ".sqlite3")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = output.with_suffix(".tmp")
        if temp.exists():
            temp.unlink()
        with sqlite3.connect(temp) as cx:
            cx.execute("CREATE TABLE changes(kind TEXT,seq INTEGER,payload TEXT,PRIMARY KEY(kind,seq))")
            for change_kind in ("added", "removed", "changed"):
                cx.executemany(
                    "INSERT INTO changes VALUES(?,?,?)",
                    ((change_kind, n, canonical(row)) for n, row in enumerate(result[change_kind])),
                )
        temp.replace(output)
        st = output.stat()
        item["index_fingerprint"] = [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
        item.update(
            status="SUCCEEDED",
            summary={k: v for k, v in result.items() if k not in ("added", "removed", "changed")},
            counts={k: len(result[k]) for k in ("added", "removed", "changed")},
            compatibility={"status": "COMPATIBLE", "axis": item["axis"]},
            configuration_diff={axiskey: [reports[0]["identity"][axiskey], reports[1]["identity"][axiskey]]},
        )
    item.update(ended_at=stamp(), heartbeat_at=stamp(), phase="COMPLETED")


def cleanup_staging(store, item, pid=None):
    from src.services.signal_scanner.services.rule_validation_web.store import safe_path

    for p in (store.root / "indices" / (item["id"] + ".tmp"), store.root / "charts" / (item["id"] + ".tmp")):
        p = safe_path(store.root, p)
        if p.is_file():
            p.unlink()
    if pid:
        for p in store.root.rglob(f".*.{pid}.tmp"):
            p = safe_path(store.root, p)
            if p.is_file():
                p.unlink()


def execute_claimed(store, kind, id):
    from src.services.signal_scanner.services.rule_validation_web.cancellation import cancelled, requested

    item = store.get(kind, id)
    began = time.monotonic()
    try:
        require(kind != "run" or not requested(store, item), "RESEARCH_CANCELLED")
        execute(store, kind, item)
    except Exception as ex:  # noqa: BLE001 - persist sanitized failures
        code = ex.code if isinstance(ex, DomainError) else "RESEARCH_IO_OR_EXECUTION_ERROR"
        failed_phase = item.get("phase")
        item.update(
            status="FAILED",
            phase="FAILED",
            ended_at=stamp(),
            error={
                "code": code,
                "message": "研究任务未完成，请查看原因代码并修复后重试",
                "details": ex.details if isinstance(ex, DomainError) else {},
            },
        )
        if kind == "validation":
            item.update(
                result_status="INVALID",
                issues=[{"field": "draft", "severity": "ERROR", "code": code}],
                capabilities={"can_run": False},
            )
        if kind == "run":
            item["report_state"] = "CORRUPT" if failed_phase == "INDEXING" else "NOT_READY"
    item["metrics"] = {
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "peak_process_rss_bytes": peak_rss_bytes(),
    }
    with store.write() as cx:
        latest = store.get(kind, id, cx)
        if kind == "run" and (latest["status"] == "CANCEL_REQUESTED" or requested(store, item)):
            item["cancellation"] = latest.get("cancellation", {})
            cancelled(item)
            cleanup_staging(store, item, os.getpid())
        store.put(kind, item, cx)


def child_environment():
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ", "SYSTEMROOT") if k in os.environ}
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    return env


def supervise(store, kind, item, grace_seconds=30, lease_fd=None):
    """One owned child, cooperative boundary then TERM and KILL after grace."""
    from src.services.signal_scanner.services.rule_validation_web.cancellation import cancelled

    cmd = python_command("src.services.signal_scanner.worker.rule_validation", "--root", str(store.root))
    for root in store.input_roots:
        cmd += ["--input-root", str(root)]
    cmd += ["_execute", kind, item["id"]]
    child = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(lease_fd,) if lease_fd is not None and os.name == "posix" else (),
    )
    cancel_began, method = None, None
    try:
        while child.poll() is None:
            current = store.get(kind, item["id"])
            if current["status"] == "CANCEL_REQUESTED":
                cancel_began = cancel_began or time.monotonic()
                if time.monotonic() - cancel_began >= grace_seconds:
                    method = "TIMEOUT_TERMINATE"
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        method = "TIMEOUT_KILL"
                        child.kill()
                        child.wait(timeout=5)
                    break
            time.sleep(0.2)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    with store.write() as cx:
        current = store.get(kind, item["id"], cx)
        if current["status"] == "CANCEL_REQUESTED":
            cancelled(current, method or "COOPERATIVE")
            cleanup_staging(store, current, child.pid)
            engine = store.root / "engine" / "scanner.sqlite3"
            if engine.is_file() and not engine.is_symlink():
                with sqlite3.connect(engine) as legacy:
                    legacy.execute(
                        "UPDATE job_runs SET status='FAILED',error_json=? WHERE logical_key=? AND status='RUNNING'",
                        (canonical({"code": "RESEARCH_CANCELLED"}), item["logical_key"]),
                    )
            store.put(kind, current, cx)
        elif current["status"] == "RUNNING":
            current.update(
                status="FAILED",
                phase="INTERRUPTED",
                error={"code": "WORKER_CHILD_INTERRUPTED", "message": "独立计算进程已中断，可重试"},
            )
            cleanup_staging(store, current, child.pid)
            store.put(kind, current, cx)


def tick(store, *, supervised=False, lease_fd=None):
    for kind in ("validation", "run", "comparison"):
        with store.write() as cx:
            candidates = sorted(
                [r for r in store.all(kind, cx) if r["status"] == "QUEUED"], key=lambda r: r["created_at"]
            )
            if not candidates:
                continue
            item = candidates[0]
            item.update(
                status="RUNNING",
                heartbeat_at=stamp(),
                phase="VALIDATING"
                if kind == "validation"
                else "COMPARISON"
                if kind == "comparison"
                else "REPLAY",
            )
            store.put(kind, item, cx)
        if supervised:
            supervise(store, kind, item, lease_fd=lease_fd)
        else:
            execute_claimed(store, kind, item["id"])
        return True
    return False


def work(store, once=False):
    require(store.ready, "RESEARCH_NOT_INITIALIZED")
    with (store.root / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DomainError("WORKER_ALREADY_RUNNING") from None
        with (store.root / "execution.lock").open("a+") as execution_lock:
            fcntl.flock(execution_lock, fcntl.LOCK_EX)
        atomic_text(store.root / "worker.pid", str(os.getpid()))
        # Owning the process lock proves no earlier managed worker is still executing.
        with store.write() as cx:
            for kind in ("validation", "run", "comparison"):
                for item in store.all(kind, cx):
                    if item["status"] == "CANCEL_REQUESTED":
                        from src.services.signal_scanner.services.rule_validation_web.cancellation import cancelled

                        cancelled(item, "RECOVERED_AFTER_INTERRUPTION")
                        cleanup_staging(store, item)
                        store.put(kind, item, cx)
                    elif item["status"] == "RUNNING":
                        item.update(
                            status="FAILED",
                            phase="INTERRUPTED",
                            error={
                                "code": "WORKER_INTERRUPTED",
                                "message": "研究进程已中断，可对相同输入重试",
                            },
                        )
                        store.put(kind, item, cx)
        while True:
            active = tick(store, supervised=True, lease_fd=lock.fileno())
            if once:
                return active
            if not active:
                time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="规则验证 Web 独立管理工具")
    parser.add_argument("--root", required=True)
    parser.add_argument("--input-root", action="append", default=[])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    ds = sub.add_parser("register-dataset")
    ds.add_argument("manifest")
    ds.add_argument("--name", required=True)
    imp = sub.add_parser("import-report")
    imp.add_argument("--runtime", required=True)
    imp.add_argument("--ref", required=True)
    imp.add_argument("--name", default="外部历史报告")
    disabled = sub.add_parser("disable-dataset")
    disabled.add_argument("dataset_id")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    sub.add_parser("_worker", help=argparse.SUPPRESS)
    child = sub.add_parser("_execute", help=argparse.SUPPRESS)
    child.add_argument("kind", choices=("validation", "run", "comparison"))
    child.add_argument("id")
    args = parser.parse_args()
    store = Store(args.root, args.input_root)
    if args.command == "init":
        store.initialize()
    elif args.command == "register-dataset":
        from src.services.signal_scanner.services.rule_validation_web.catalog import register_dataset

        print(register_dataset(store, args.manifest, args.name))
    elif args.command == "disable-dataset":
        with store.write() as cx:
            ds = store.get("dataset", args.dataset_id, cx)
            ds["active"] = False
            store.put("dataset", ds, cx)
    elif args.command == "import-report":
        from src.services.signal_scanner.services.rule_validation_web.query import index_report

        id = uid("history")
        r = {
            "id": id,
            "run_id": id,
            "name": args.name,
            "status": "SUCCEEDED",
            "phase": "COMPLETED",
            "external": True,
            "created_at": stamp(),
            "attempt": 1,
            "attempts": [],
            "heartbeat_at": None,
            "engine_job_id": None,
            "report_state": "INDEXING",
        }
        try:
            index_report(store, r, args.runtime, args.ref)
        except DomainError as ex:
            if ex.code != "UNSUPPORTED_VERSION":
                raise
            r.update(
                report_state="UNSUPPORTED_VERSION",
                report_hash=Path(args.ref).parent.name,
                data_kind="UNKNOWN",
                error={
                    "code": ex.code,
                    "message": "完整性已核验；当前版本暂不支持此历史报告，不提供未审查的附件",
                },
            )
        with store.write() as cx:
            store.put("run", r, cx)
        print(id)
    elif args.command == "worker":
        # Fixed executable/entry, no shell, no production env or provider secrets inherited.
        env = child_environment()
        env.update(
            PYTHONPATH=str(ROOT),
            PYTHONDONTWRITEBYTECODE="1",
            RULE_VALIDATION_ONCE="1" if args.once else "0",
        )
        cmd = python_command("src.services.signal_scanner.worker.rule_validation", "--root", str(store.root))
        for root in store.input_roots:
            cmd += ["--input-root", str(root)]
        os.chdir(ROOT)
        os.execve(sys.executable, cmd + ["_worker"], env)
    elif args.command == "_execute":
        with (store.root / "execution.lock").open("a+") as execution_lock:
            fcntl.flock(execution_lock, fcntl.LOCK_EX)
            execute_claimed(store, args.kind, args.id)
    else:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        work(store, os.environ.get("RULE_VALIDATION_ONCE") == "1")


if __name__ == "__main__":
    main()
