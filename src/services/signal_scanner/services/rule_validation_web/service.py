import json
from copy import deepcopy

from src.services.signal_scanner.core import digest, require, stamp, uid
from src.services.signal_scanner.research.versions import code_hash, dependency_hash

from .catalog import build_manifest, default_draft, registration_digest
from .store import public


def queue_room(store, cx):
    active = sum(
        r["status"] in ("QUEUED", "RUNNING", "CANCEL_REQUESTED")
        for kind in ("validation", "run", "comparison")
        for r in store.all(kind, cx)
    )
    require(active < 20, "QUEUE_FULL", "研究队列已满，请稍后重试", 429)


def create_validation(store, p, cx):
    require(
        set(p) <= {"dataset_id", "draft_id", "config_revision", "draft", "config_digest"} and "draft" in p,
        "INVALID_REQUEST",
        "必须提交完整草稿",
        422,
    )
    ds = store.get("dataset", p["dataset_id"], cx)
    queue_room(store, cx)
    id = uid("validation")
    manifest = build_manifest(store, ds, p["draft"], id)
    cfg = digest({"dataset_id": ds["id"], "draft": p["draft"]})
    require(not p.get("config_digest") or p["config_digest"] == cfg, "CONFIG_DIGEST_CONFLICT")
    item = {
        "id": id,
        "validation_id": id,
        "status": "QUEUED",
        "phase": "QUEUED",
        "created_at": stamp(),
        "dataset_id": ds["id"],
        "config_digest": cfg,
        "draft": deepcopy(p["draft"]),
        "manifest": manifest,
        "code_hash": manifest["code_hash"],
        "dependency_hash": manifest["dependency_hash"],
        "registration_digest": registration_digest(store),
        "issues": [],
        "capabilities": {"can_run": False},
    }
    store.put("validation", item, cx)
    return public(item)


def create_run(store, p, cx):
    require(
        set(p) == {"validation_id", "config_digest", "reveal_sealed"} and p["reveal_sealed"] is False,
        "INVALID_REQUEST",
        "封存揭示须通过独立确认操作",
        422,
    )
    v = store.get("validation", p["validation_id"], cx)
    require(
        v["status"] == "SUCCEEDED" and v["result_status"] == "VALID",
        "VALIDATION_REQUIRED",
        "请先完成有效预检",
    )
    require(v["config_digest"] == p["config_digest"], "VALIDATION_STALE", "配置已变化，请重新预检")
    require(
        v["code_hash"] == code_hash()
        and v["dependency_hash"] == dependency_hash()
        and v["registration_digest"] == registration_digest(store),
        "VALIDATION_STALE",
        "版本或登记域已变化，请重新预检",
    )
    existing = [r for r in store.all("run", cx) if r.get("validation_id") == v["id"]]
    if existing:
        return {**public(existing[0]), "reused": True}
    queue_room(store, cx)
    id = uid("web")
    item = {
        "id": id,
        "run_id": id,
        "validation_id": v["id"],
        "name": v["draft"]["name"],
        "draft": v["draft"],
        "dataset_id": v["dataset_id"],
        "status": "QUEUED",
        "phase": "QUEUED",
        "attempt": 1,
        "attempts": [],
        "created_at": stamp(),
        "heartbeat_at": None,
        "processed": None,
        "total": None,
        "manifest_path": v["manifest_path"],
        "report_state": "NOT_READY",
        "external": False,
        "reveal": False,
        "data_kind": v["data_kind"],
        "start_date": v["manifest"]["start_date"],
        "end_date": v["manifest"]["end_date"],
        "logical_key": v["manifest"]["logical_key"],
        "engine_job_id": None,
        "variant_of": v.get("variant_of"),
        "variant_root_id": v.get("variant_root_id"),
        "variant_change": v.get("variant_change"),
    }
    store.put("run", item, cx)
    return public(item)


def retry(store, id, p, cx):
    r = store.get("run", id, cx)
    require(
        not r.get("external")
        and r["status"] in ("FAILED", "CANCELLED")
        and p == {"expected_attempt": r["attempt"]},
        "RETRY_CONFLICT",
        "仅失败且没有活跃执行的相同输入可以重试",
    )
    queue_room(store, cx)
    r["attempts"].append(
        {
            "attempt": r["attempt"],
            "status": r["status"],
            "error": r.get("error"),
            "ended_at": r.get("ended_at"),
            "cancellation": r.get("cancellation"),
        }
    )
    r.pop("cancellation", None)
    for field in ("ended_at", "metrics", "index_elapsed_seconds"):
        r.pop(field, None)
    r.update(heartbeat_at=None, processed=None, total=None)
    r.update(status="QUEUED", phase="QUEUED", attempt=r["attempt"] + 1, error=None, report_state="NOT_READY")
    store.put("run", r, cx)
    return public(r)


def cancel(store, id, p, cx):
    from .cancellation import signal_cancel

    r = store.get("run", id, cx)
    require(not r.get("external") and p["expected_attempt"] == r["attempt"], "CANCEL_CONFLICT")
    if r["status"] in ("CANCELLED", "CANCEL_REQUESTED"):
        return public(r)
    require(r["status"] in ("QUEUED", "RUNNING"), "CANCEL_CONFLICT", "作业已经结束，不能取消")
    r["cancellation"] = {"reason": p["reason"], "requested_at": stamp(), "attempt": r["attempt"]}
    if r["status"] == "QUEUED":
        r.update(status="CANCELLED", phase="CANCELLED", ended_at=stamp(), report_state="NOT_READY")
        r["cancellation"]["method"] = "QUEUE_REMOVAL"
    else:
        r.update(status="CANCEL_REQUESTED", phase="CANCEL_REQUESTED")
        # A durable, attempt-bound intent is safe even if the HTTP caller disconnects.
        # The worker can read it inside the offline guard without a new DB connection.
        signal_cancel(store, r)
    store.put("run", r, cx)
    return public(r)


def reveal(store, id, p, cx):
    from .query import summary

    r = store.get("run", id, cx)
    require(not r.get("external") and r.get("report_state") == "READY", "REVEAL_UNAVAILABLE")
    require(
        p.get("confirmation") == "REVEAL_SEALED"
        and isinstance(p.get("reason"), str)
        and 0 < len(p["reason"]) <= 2000,
        "EXPLICIT_CONFIRMATION_REQUIRED",
        "请输入揭示确认文字及原因",
        422,
    )
    old = [x for x in store.all("run", cx) if x.get("parent_run_id") == id]
    if old:
        return public(old[0])
    require(p.get("expected_registration_revision") == registration_digest(store), "REGISTRATION_CONFLICT")
    report = summary(r)
    require(
        not report["identity"]["sealed_revealed"] and "SEALED" in report["identity"]["split"]["sets"],
        "REVEAL_UNAVAILABLE",
    )
    queue_room(store, cx)
    child = deepcopy(r)
    childid = uid("web")
    # The worker freezes a new logical identity; registered protocol and all parameters remain fixed.
    for k in ("index_path", "source_root", "report_ref", "fingerprints", "report_hash", "identity"):
        child.pop(k, None)
    child.update(
        id=childid,
        run_id=childid,
        parent_run_id=id,
        reveal=True,
        status="QUEUED",
        phase="QUEUED",
        report_state="NOT_READY",
        logical_key=childid,
        attempt=1,
        attempts=[],
        created_at=stamp(),
        engine_job_id=None,
        heartbeat_at=None,
    )
    store.put("run", child, cx)
    return public(child)


def create_comparison(store, p, cx):
    require(
        set(p) == {"left_run_id", "right_run_id", "axis"}
        and p["axis"] in ("rules", "universe", "data", "price_basis"),
        "INVALID_COMPARISON_AXIS",
        status=422,
    )
    require(p["left_run_id"] != p["right_run_id"], "SAME_EXPERIMENT", "请选择两个不同实验", 422)
    from .query import summary

    left, right = [store.get("run", p[k], cx) for k in ("left_run_id", "right_run_id")]
    for r in (left, right):
        summary(r)
    queue_room(store, cx)
    id = uid("comparison")
    item = dict(
        id=id,
        comparison_id=id,
        status="QUEUED",
        phase="QUEUED",
        created_at=stamp(),
        left_hash=left["report_hash"],
        right_hash=right["report_hash"],
        **p,
    )
    store.put("comparison", item, cx)
    return public(item)


def run_status(store, r):
    result = public(r)
    if r["status"] == "RUNNING" and r.get("logical_key") and r.get("phase") == "REPLAY":
        path = store.root / "engine" / "web-progress" / (digest(r["logical_key"]) + ".json")
        if path.is_file():
            progress = json.loads(path.read_text())
            if progress.get("attempt", 1) == r["attempt"]:
                result.update(
                    engine_job_id=progress.get("engine_job_id"),
                    heartbeat_at=progress.get("heartbeat_at"),
                    phase=progress.get("phase", "REPLAY"),
                    processed=progress.get("processed"),
                    total=progress.get("total"),
                )
    if r.get("report_state") == "READY" and not r.get("draft"):
        from .query import summary

        match = next(
            (
                ds
                for ds in store.all("dataset")
                if ds["active"] and ds["snapshot_hash"] == r["identity"]["snapshot_hash"]
            ),
            None,
        )
        if match:
            m = summary(r)["manifest"]
            draft = default_draft(match)
            for field in (
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
                draft[field] = m[field]
            draft.update(
                price_return_kind=m["price_basis"]["return_kind"],
                hypothesis=m["registration"]["hypothesis"],
                name=r["name"],
            )
            result.update(draft=draft, dataset_id=match["id"])
        else:
            result["copy_unavailable"] = (
                "此历史报告的冻结输入尚未登记；请选择已登记的数据包后重新填写并预检。"
            )
    result["registration_revision"] = registration_digest(store)
    if r.get("identity"):
        p = store.root / "engine" / "registrations.json"
        records = json.loads(p.read_text()) if p.is_file() else {}
        reg = records.get(r["identity"]["registration"]["id"], {})
        sealed = r["identity"]["split"]["sets"].get("SEALED")
        overlap = bool(
            sealed
            and any(
                rec.get("revealed_at")
                and rec.get("sealed_range")
                and not (sealed[1] < rec["sealed_range"][0] or sealed[0] > rec["sealed_range"][1])
                for rec in records.values()
            )
        )
        result["current_registration_revealed"] = (
            (bool(reg.get("revealed_at")) or overlap) if not r.get("external") else None
        )
        result["sealed_history_used"] = overlap if not r.get("external") else None
    return result
