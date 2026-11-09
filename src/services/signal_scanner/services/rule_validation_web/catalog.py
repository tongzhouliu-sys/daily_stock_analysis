import hashlib
import json
from copy import deepcopy
from pathlib import Path

from src.services.signal_scanner.core import canonical, digest, require, stamp, uid
from src.services.signal_scanner.domain.features import ALGORITHM
from src.services.signal_scanner.research.inputs import ResearchCalendar, read_snapshot
from src.services.signal_scanner.research.reporting import atomic_text
from src.services.signal_scanner.research.versions import ENGINE_VERSION, code_hash, dependency_hash, rules_version

from .store import safe_path


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def register_dataset(store, manifest_path, name):
    p = Path(manifest_path).absolute()
    roots = [r for r in store.input_roots if p.is_relative_to(r)]
    require(roots, "INPUT_PATH_FORBIDDEN", "请先配置输入根白名单")
    p = safe_path(roots[0], p)
    require(
        not p.parent.resolve().is_relative_to(store.root.resolve())
        and not store.root.resolve().is_relative_to(p.parent.resolve()),
        "INPUT_OUTPUT_OVERLAP",
    )
    m = json.loads(p.read_text())
    snapshot_path = safe_path(p.parent, p.parent / m["data_snapshot_ref"]["path"])
    s = read_snapshot(p.parent, m["data_snapshot_ref"])
    id = "dataset_" + digest([str(p), file_hash(snapshot_path)])[:32]
    row = {
        "id": id,
        "dataset_id": id,
        "name": name,
        "data_kind": s["data_kind"],
        "snapshot_hash": file_hash(snapshot_path),
        "source": "已登记的本地冻结数据",
        "manifest_path": str(p),
        "snapshot_path": str(snapshot_path),
        "manifest_hash": file_hash(p),
        "template": m,
        "created_at": stamp(),
        "coverage": {
            "start_date": m["start_date"],
            "end_date": m["end_date"],
            "instruments": len({u["instrument_id"] for u in s["universe"]}),
        },
        "evidence": {
            "rth": bool(s.get("rth_evidence")),
            "pit": bool(
                s.get("historical_membership_verified") and s.get("historical_classification_verified")
            ),
        },
        "return_kind": s["price_return_kind"],
        "active": True,
    }
    with store.write() as cx:
        store.put("dataset", row, cx)
    return id


def check_dataset(store, ds):
    p = Path(ds["manifest_path"])
    roots = [r for r in store.input_roots if p.is_relative_to(r)]
    require(roots, "INPUT_PATH_FORBIDDEN")
    safe_path(roots[0], p)
    safe_path(p.parent, ds["snapshot_path"])
    require(
        file_hash(p) == ds["manifest_hash"] and file_hash(ds["snapshot_path"]) == ds["snapshot_hash"],
        "VALIDATION_STALE",
        "冻结输入发生变化，请重新登记并预检",
    )
    require(ds["active"], "DATASET_DISABLED")


def default_draft(ds):
    m = ds["template"]
    return dict(
        name="规则验证实验",
        hypothesis="",
        registration_id="protocol-" + uid()[:12],
        **{
            k: deepcopy(m[k])
            for k in (
                "start_date",
                "end_date",
                "warmup_bars",
                "evaluation_clock",
                "label_as_of",
                "research_mode",
                "entry_bases",
                "cost_assumption",
                "split_policy",
                "min_completed_samples",
            )
        },
        price_return_kind=m["price_basis"]["return_kind"],
    )


def registration_digest(store):
    p = store.root / "engine" / "registrations.json"
    return file_hash(p) if p.is_file() else digest({})


def build_manifest(store, ds, draft, id):
    require(set(draft) == set(default_draft(ds)), "INVALID_DRAFT_FIELDS", "请提交完整的表单配置", 422)
    require(isinstance(draft["name"], str) and 0 < len(draft["name"]) <= 120, "INVALID_NAME", status=422)
    require(
        isinstance(draft["hypothesis"], str) and 0 < len(draft["hypothesis"]) <= 2000,
        "INVALID_HYPOTHESIS",
        status=422,
    )
    m = deepcopy(ds["template"])
    for k in (
        "start_date",
        "end_date",
        "warmup_bars",
        "evaluation_clock",
        "label_as_of",
        "research_mode",
        "entry_bases",
        "cost_assumption",
        "split_policy",
        "min_completed_samples",
    ):
        m[k] = deepcopy(draft[k])
    m["cost_assumption"]["version"] = "two-sided-slippage-fees-v1"
    m["price_basis"]["return_kind"] = draft["price_return_kind"]
    m.update(
        experiment_id=id,
        logical_key=id,
        revision=1,
        actor="owner",
        code_hash=code_hash(),
        dependency_hash=dependency_hash(),
        algorithm_version=ALGORITHM,
        engine_version=ENGINE_VERSION,
        calendar_version=ResearchCalendar().version,
        ruleset_version=rules_version(),
        parameter_overrides={},
    )
    m["registration"] = {
        "id": draft["registration_id"],
        "hypothesis": draft["hypothesis"],
        "registered_at": stamp(),
        "sealed_policy": "EXPLICIT_REVEAL_ONCE",
        "allowed_variations": deepcopy(ds["template"]["registration"].get("allowed_variations", {})),
    }
    m["data_snapshot_ref"] = {"path": "snapshot.json", "sha256": ds["snapshot_hash"]}
    return m


def freeze(store, id, manifest, ds):
    import shutil

    folder = store.root / "drafts" / id
    safe_path(store.root, folder)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    safe_path(store.root, folder / "snapshot.json")
    check_dataset(store, ds)
    shutil.copyfile(ds["snapshot_path"], folder / "snapshot.json")
    require(file_hash(folder / "snapshot.json") == ds["snapshot_hash"], "VALIDATION_STALE")
    atomic_text(folder / "manifest.json", canonical(manifest))
    return str(folder / "manifest.json")
