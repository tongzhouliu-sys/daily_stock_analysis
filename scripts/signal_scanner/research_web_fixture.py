"""Explicit isolated browser fixture. Never points at the normal runtime."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import Settings, canonical
from src.services.signal_scanner.main import create_app
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft, file_hash, register_dataset
from src.services.signal_scanner.services.rule_validation_web.service import create_run, create_validation
from src.services.signal_scanner.services.rule_validation_web.store import Store
from src.services.signal_scanner.worker.rule_validation import tick


def main():
    output = ROOT / "runtime" / "research-web-browser"
    store = Store(output / "web", [output / "inputs"])
    store.initialize()
    manifest = create_fixture(output / "inputs" / "small", symbols=2, sessions=80)
    dsid = register_dataset(store, manifest, "合成工程浏览器验收数据")
    draft = default_draft(store.get("dataset", dsid))
    draft.update(name="浏览器基线实验", hypothesis="核对真实API与图表分母")
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
    # A second real engine execution differs only in frozen data, for a successful single-axis comparison.
    other_path = Path(create_fixture(output / "inputs" / "changed", symbols=2, sessions=80))
    other_manifest = json.loads(other_path.read_text())
    snapshot_path = other_path.parent / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["bars"]["SYN000"][0]["volume"] = "1000001"
    snapshot_path.write_text(canonical(snapshot))
    other_manifest["data_snapshot_ref"]["sha256"] = file_hash(snapshot_path)
    other_path.write_text(canonical(other_manifest))
    other_dsid = register_dataset(store, other_path, "合成工程单轴变化数据")
    draft = default_draft(store.get("dataset", other_dsid))
    draft.update(name="浏览器单轴变化实验", hypothesis="仅改变冻结数据")
    with store.write() as cx:
        other_v = create_validation(store, {"dataset_id": other_dsid, "draft": draft}, cx)
    tick(store)
    other_v = store.get("validation", other_v["id"])
    with store.write() as cx:
        other = create_run(
            store,
            {
                "validation_id": other_v["id"],
                "config_digest": other_v["config_digest"],
                "reveal_sealed": False,
            },
            cx,
        )
    tick(store)
    other = store.get("run", other["id"])
    assert other["status"] == "SUCCEEDED", other
    os.environ["RULE_VALIDATION_WEB_ROOT"] = str(store.root)
    os.environ["RULE_VALIDATION_INPUT_ROOTS"] = str(output / "inputs")
    app = create_app(Settings(output / "app", origin="http://127.0.0.1:8767"))
    app.state.auth.provision()
    (output / "fixture.json").write_text(
        canonical(
            {
                "run_id": r["id"],
                "dataset_id": dsid,
                "report_hash": r["report_hash"],
                "comparison_run_id": other["id"],
            }
        )
    )
    print(canonical({"status": "READY", "run_id": r["id"], "data_kind": "SYNTHETIC"}))


if __name__ == "__main__":
    main()
