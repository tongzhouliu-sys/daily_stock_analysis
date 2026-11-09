"""SYNTHETIC P1 browser inputs, separate app/queue and actual frozen weekly probe."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.cli import fixture
from src.services.signal_scanner.core import Settings, canonical, now, uid
from src.services.signal_scanner.main import create_app
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft, register_dataset
from src.services.signal_scanner.services.rule_validation_web.service import create_run, create_validation
from src.services.signal_scanner.services.rule_validation_web.store import Store
from src.services.signal_scanner.storage.db import one
from src.services.signal_scanner.worker.jobs import Worker, enqueue
from src.services.signal_scanner.worker.rule_validation import tick


def main():
    output = ROOT / "runtime/research-web-p1-browser"
    store = Store(output / "web", [output / "inputs"])
    store.initialize()
    path = Path(create_fixture(output / "inputs" / "registered", symbols=2, sessions=100))
    m = json.loads(path.read_text())
    m["registration"]["allowed_variations"] = {
        "TR-01": {"rv_min": [1.2, 1.5, 1.8]},
        "TR-03": {"parent_sessions": [8, 10, 12]},
    }
    path.write_text(canonical(m))
    dsid = register_dataset(store, path, "P1预登记合成工程数据")
    cancel_path = create_fixture(output / "inputs" / "cancel", symbols=10, sessions=252)
    cancel_dsid = register_dataset(store, cancel_path, "P1取消工程数据（10证券）")
    draft = default_draft(store.get("dataset", dsid))
    draft.update(name="P1浏览器基准实验", hypothesis="逐时点图、预登记参数和取消完整性工程核对")
    with store.write() as cx:
        v = create_validation(store, {"dataset_id": dsid, "draft": draft}, cx)
    tick(store)
    v = store.get("validation", v["id"])
    assert v["status"] == "SUCCEEDED", v
    with store.write() as cx:
        r = create_run(
            store, {"validation_id": v["id"], "config_digest": v["config_digest"], "reveal_sealed": False}, cx
        )
    tick(store)
    r = store.get("run", r["id"])
    assert r["status"] == "SUCCEEDED", r
    report = json.loads((Path(r["source_root"]) / r["report_ref"]).read_text())
    eid = next(e["event_id"] for e in report["events"] if e.get("confirmed_at"))
    settings = Settings(output / "app", origin="http://127.0.0.1:18771")
    fixture(settings)  # Existing named SYNTHETIC app fixture, not the normal runtime.
    w = Worker(settings)
    with w.db.transaction() as cx:
        jid = enqueue(cx, "WEEKLY_REPORT", "P1-WEEKLY:" + uid(), w.cal.last_completed(now()))
    assert w.run_one()
    with w.db.read() as cx:
        week = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
    weekly = w.files.read(week["output_manifest_ref"])
    assert weekly["replay_probe"]["status"] == "PASS", weekly["replay_probe"]
    os.environ["RULE_VALIDATION_WEB_ROOT"] = str(store.root)
    os.environ["RULE_VALIDATION_INPUT_ROOTS"] = str(output / "inputs")
    app = create_app(settings)
    app.state.auth.provision()
    (output / "fixture.json").write_text(
        canonical(
            {
                "run_id": r["id"],
                "dataset_id": dsid,
                "cancel_dataset_id": cancel_dsid,
                "event_id": eid,
                "report_hash": r["report_hash"],
                "weekly_id": jid,
                "data_kind": "SYNTHETIC",
            }
        )
    )
    print(
        canonical(
            {
                "status": "READY",
                "data_kind": "SYNTHETIC",
                "run_id": r["id"],
                "weekly_probe": weekly["replay_probe"]["status"],
            }
        )
    )


if __name__ == "__main__":
    main()
