"""Attempt-bound cooperative cancellation; signals never address arbitrary PIDs."""

import json

from src.services.signal_scanner.core import canonical
from src.services.signal_scanner.research.reporting import atomic_text

from .store import identifier, safe_path


def signal_path(store, item):
    identifier(item["id"])
    return safe_path(store.root, store.root / "cancellation" / (item["id"] + ".json"))


def signal_cancel(store, item):
    atomic_text(signal_path(store, item), canonical({"id": item["id"], "attempt": item["attempt"]}))


def requested(store, item):
    p = signal_path(store, item)
    if not p.is_file():
        return False
    value = json.loads(p.read_text())
    return value == {"id": item["id"], "attempt": item["attempt"]}


def cancelled(item, method="COOPERATIVE"):
    from src.services.signal_scanner.core import stamp

    item.update(status="CANCELLED", phase="CANCELLED", ended_at=stamp(), report_state="NOT_READY")
    item.setdefault("cancellation", {})["method"] = method
    item["error"] = {"code": "RESEARCH_CANCELLED", "message": "本次计算已取消，完整冻结输入与已见登记保留"}
    for key in (
        "artifacts",
        "index_path",
        "index_fingerprint",
        "chart_index_path",
        "chart_index_fingerprint",
    ):
        item.pop(key, None)
    return item
