"""One pre-registered parameter at a time, with all sibling attempts retained."""

import json
from copy import deepcopy
from pathlib import Path

from src.services.signal_scanner.core import DomainError, digest, require, stamp, uid
from src.services.signal_scanner.domain.rule_parameters import DEFAULT_PARAMETERS
from src.services.signal_scanner.research.versions import code_hash, dependency_hash, rules_version

from .catalog import check_dataset, file_hash, registration_digest
from .query import redact, summary
from .service import queue_room, run_status
from .store import public, safe_path

SESSION_PARAMETERS = {"ttl", "wait", "parent_sessions"}


def policy(registration):
    choices = []
    for rule, fields in registration.get("allowed_variations", {}).items():
        require(rule in DEFAULT_PARAMETERS and isinstance(fields, dict), "UNREGISTERED_PARAMETER")
        for name, values in fields.items():
            baseline = DEFAULT_PARAMETERS[rule].get(name)
            require(
                baseline is not None and isinstance(values, list) and 1 <= len(values) <= 3,
                "INVALID_SENSITIVITY",
            )
            for v in values:
                require(
                    type(v) in (int, float) and any(abs(v - baseline * f) < 1e-10 for f in (0.8, 1, 1.2)),
                    "INVALID_SENSITIVITY",
                )
                require(name not in SESSION_PARAMETERS or type(v) is int, "INVALID_SENSITIVITY")
            choices.append(
                {
                    "rule_id": rule,
                    "parameter": name,
                    "baseline": baseline,
                    "values": values,
                    "integer": name in SESSION_PARAMETERS,
                }
            )
    return choices


def context(store, id, cx=None):
    source = store.get("run", id, cx)
    require(
        not source.get("external"),
        "VARIANT_SOURCE_UNAVAILABLE",
        "外部历史报告只能只读查看；请先复制为当前实验",
    )
    root = store.get("run", source.get("variant_root_id") or id, cx)
    report = summary(root)
    require(report["identity"]["code_hash"] == code_hash(), "VERSION_UNAVAILABLE")
    v = store.get("validation", root["validation_id"], cx)
    path = safe_path(store.root, Path(v["manifest_path"]))
    require(file_hash(path) == v["manifest_hash"], "VALIDATION_STALE")
    m = json.loads(path.read_text())
    require(
        m["code_hash"] == code_hash() and m["dependency_hash"] == dependency_hash(), "VERSION_UNAVAILABLE"
    )
    ds = store.get("dataset", root["dataset_id"], cx)
    check_dataset(store, ds)
    return root, m


def plan(store, id, cursor=0, limit=30, validation_cursor=0):
    require(cursor >= 0 and validation_cursor >= 0 and 1 <= limit <= 100, "INVALID_PAGINATION", status=422)
    source = store.get("run", id)
    if source.get("external"):
        return {"available": False, "reason": "VARIANT_SOURCE_UNAVAILABLE", "items": []}
    try:
        root, m = context(store, id)
    except DomainError as ex:
        if ex.code != "VERSION_UNAVAILABLE":
            raise
        return {"available": False, "reason": ex.code, "items": []}
    choices = policy(m["registration"])
    family = [r for r in store.all("run") if r["id"] == root["id"] or r.get("variant_root_id") == root["id"]]
    validations = sorted(
        [v for v in store.all("validation") if v.get("variant_root_id") == root["id"]],
        key=lambda v: (v["created_at"], v["id"]),
    )
    family.sort(key=lambda r: (r["created_at"], r["id"]))
    available = any(any(v != c["baseline"] for v in c["values"]) for c in choices)
    return redact(
        {
            "available": available,
            "reason": None if available else "NO_REGISTERED_VARIATIONS",
            "root_run_id": root["id"],
            "report_hash": root["report_hash"],
            "choices": choices,
            "registration": m["registration"],
            "fixed_axes": {
                k: m[k]
                for k in (
                    "start_date",
                    "end_date",
                    "evaluation_clock",
                    "label_as_of",
                    "research_mode",
                    "entry_bases",
                    "cost_assumption",
                    "price_basis",
                    "benchmark_ref",
                    "split_policy",
                    "universe_version",
                    "code_hash",
                    "dependency_hash",
                    "data_snapshot_ref",
                )
            },
            "items": [
                {
                    k: r.get(k)
                    for k in ("id", "name", "created_at", "status", "report_state", "variant_change", "error")
                }
                for r in family[cursor : cursor + limit]
            ],
            "total": len(family),
            "next_cursor": cursor + limit if cursor + limit < len(family) else None,
            "validations": [
                {k: v.get(k) for k in ("id", "status", "variant_change", "created_at", "issues")}
                for v in validations[validation_cursor : validation_cursor + limit]
            ],
            "validation_total": len(validations),
            "next_validation_cursor": validation_cursor + limit
            if validation_cursor + limit < len(validations)
            else None,
            "sealed_history_used": run_status(store, root).get("sealed_history_used"),
            "selection_policy": "ALL_RESULTS_RETAINED_NO_OPTIMIZATION",
        }
    )


def create_validation(store, id, p, cx):
    root, m = context(store, id, cx)
    require(root["report_hash"] == p["expected_report_hash"], "VALIDATION_STALE")
    choices = policy(m["registration"])
    choice = next(
        (c for c in choices if (c["rule_id"], c["parameter"]) == (p["rule_id"], p["parameter"])), None
    )
    require(choice and p["value"] in choice["values"], "UNREGISTERED_PARAMETER")
    require(not choice["integer"] or type(p["value"]) is int, "INVALID_SENSITIVITY")
    require(p["value"] != choice["baseline"], "NO_AXIS_CHANGE")
    queue_room(store, cx)
    id = uid("validation")
    manifest = deepcopy(m)
    manifest.update(experiment_id=id, logical_key=id, revision=1, actor="owner")
    manifest["data_snapshot_ref"] = {"path": "snapshot.json", "sha256": m["data_snapshot_ref"]["sha256"]}
    manifest["parameter_overrides"] = {p["rule_id"]: {p["parameter"]: p["value"]}}
    manifest["ruleset_version"] = rules_version(manifest["parameter_overrides"])
    # Preserve registration id/time/hypothesis and the finite choices frozen before viewing results.
    v = {
        "id": id,
        "validation_id": id,
        "status": "QUEUED",
        "phase": "QUEUED",
        "created_at": stamp(),
        "dataset_id": root["dataset_id"],
        "draft": deepcopy(root["draft"]),
        "manifest": manifest,
        "config_digest": digest({"source": root["report_hash"], "change": p}),
        "code_hash": manifest["code_hash"],
        "dependency_hash": manifest["dependency_hash"],
        "registration_digest": registration_digest(store),
        "issues": [],
        "capabilities": {"can_run": False},
        "variant_of": root["id"],
        "variant_root_id": root["id"],
        "variant_change": {
            "rule_id": p["rule_id"],
            "parameter": p["parameter"],
            "value": p["value"],
            "baseline": choice["baseline"],
        },
    }
    v["draft"]["name"] = f"{root['name'][:70]} · {p['rule_id']} {p['parameter']}={p['value']}"
    store.put("validation", v, cx)
    return public(v)
