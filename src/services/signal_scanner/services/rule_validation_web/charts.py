"""Frozen-input chart index built only during publication; GET never builds it."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from src.services.signal_scanner.core import canonical, digest, instant, require
from src.services.signal_scanner.domain.features import ALGORITHM, adjust
from src.services.signal_scanner.research.inputs import ResearchCalendar, clock_for, effective_actions

from .catalog import file_hash
from .store import safe_path
from .streaming import Reader


def fingerprint(path):
    st = path.stat()
    return [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def build(store, run, runtime, report):
    run.update(chart_state="UNAVAILABLE", chart_reason="FROZEN_INPUT_UNAVAILABLE")
    m = report["input_manifest"]
    if m["algorithm_version"] != ALGORITHM or m["calendar_version"] != ResearchCalendar().version:
        run["chart_reason"] = "VERSION_UNAVAILABLE"
        return
    if m["research_mode"] != "POINT_IN_TIME":
        run["chart_reason"] = "POINT_IN_TIME_HISTORY_UNVERIFIED"
        return
    identity = digest([report["input_hash"], report["identity"]["sealed_revealed"]])
    folder = safe_path(runtime, Path(runtime) / "frozen" / identity)
    manifest = safe_path(folder, folder / "manifest.json")
    if not manifest.is_file():
        return
    frozen = json.loads(manifest.read_text())
    from src.services.signal_scanner.research.contracts import semantic_manifest

    require(semantic_manifest(frozen) == semantic_manifest(m), "ARTIFACT_CORRUPT")
    source = safe_path(folder, folder / frozen["data_snapshot_ref"]["path"])
    require(
        source.is_file() and file_hash(source) == frozen["data_snapshot_ref"]["sha256"], "ARTIFACT_CORRUPT"
    )
    before = fingerprint(source)
    index = safe_path(store.root, store.root / "charts" / (run["id"] + ".sqlite3"))
    index.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temp = index.with_suffix(".tmp")
    if temp.exists():
        temp.unlink()
    metadata = {}
    with sqlite3.connect(temp) as cx:
        cx.execute(
            "CREATE TABLE bars(iid TEXT,day TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(iid,day,revision))"
        )
        cx.execute("CREATE TABLE events(payload TEXT)")
        with source.open() as stream:
            reader = Reader(stream)
            for key in reader.members():
                if key == "bars":
                    for iid in reader.members():
                        for row in reader.array():
                            cx.execute(
                                "INSERT INTO bars VALUES(?,?,?,?)",
                                (iid, row["market_date"], row["revision"], canonical(row)),
                            )
                elif key == "events":
                    for row in reader.array():
                        if row["event_type"] in ("SPLIT", "DIVIDEND"):
                            cx.execute("INSERT INTO events VALUES(?)", (canonical(row),))
                else:
                    metadata[key] = reader.value()
            require(not reader.peek(), "ARTIFACT_CORRUPT")
        cx.execute("CREATE TABLE meta(payload TEXT)")
        cx.execute(
            "INSERT INTO meta VALUES(?)",
            (
                canonical(
                    {
                        k: metadata.get(k)
                        for k in (
                            "schema_version",
                            "session_kind",
                            "rth_evidence",
                            "historical_membership_verified",
                            "historical_classification_verified",
                        )
                    }
                ),
            ),
        )
    require(
        before == fingerprint(source) and file_hash(source) == frozen["data_snapshot_ref"]["sha256"],
        "ARTIFACT_CORRUPT",
    )
    temp.replace(index)
    run.update(
        chart_state="READY",
        chart_reason=None,
        chart_index_path=str(index),
        chart_source_path=str(source),
        chart_index_fingerprint=fingerprint(index),
        chart_source_fingerprint=before,
    )


def event_chart(run, event_id, as_of="TRIGGER", window=60):
    from .query import event_detail, summary

    require(
        as_of in ("TRIGGER", "CONFIRMATION", "LAST_EVENT_STATE") and 1 <= window <= 120,
        "INVALID_CHART_WINDOW",
        status=422,
    )
    report = summary(run)
    event = event_detail(run, event_id)["event"]
    if run.get("chart_state") != "READY":
        return {
            "availability": "UNAVAILABLE",
            "reason": run.get("chart_reason", "FROZEN_INPUT_UNAVAILABLE"),
            "report_hash": run["report_hash"],
        }
    source, index = Path(run["chart_source_path"]), Path(run["chart_index_path"])
    for p, expected in ((source, run["chart_source_fingerprint"]), (index, run["chart_index_fingerprint"])):
        require(p.is_file() and not p.is_symlink() and fingerprint(p) == expected, "ARTIFACT_CORRUPT")
    m, cal = report["manifest"], ResearchCalendar()
    # A session clock may cross UTC midnight. Its calendar date is not the market date.
    states = [
        {**t, "evaluated_at": t.get("evaluated_at") or clock_for(t["market_date"], m, cal)}
        for t in event["state_history"]
    ]
    if as_of == "CONFIRMATION":
        require(event.get("confirmed_at"), "EVENT_NOT_CONFIRMED", status=422)
        clock = event["confirmed_at"]
        confirmation = next((t for t in states if instant(t["evaluated_at"]) == instant(clock)), None)
        require(confirmation is not None, "EVENT_CLOCK_UNVERIFIED")
        day = confirmation["market_date"]
    elif as_of == "LAST_EVENT_STATE":
        last = max(states, key=lambda t: instant(t["evaluated_at"]), default=None)
        clock = last["evaluated_at"] if last else event["triggered_at"]
        day = last["market_date"] if last else event["trigger_market_date"]
    else:
        clock = event["triggered_at"]
        day = event["trigger_market_date"]
    sealed = report["identity"]["split"]["sets"].get("SEALED")
    require(
        not sealed or report["identity"]["sealed_revealed"] or day < sealed[0], "SEALED_DATA_LEAK", status=403
    )
    with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True) as cx:
        cx.execute("PRAGMA query_only=ON")
        metadata = json.loads(cx.execute("SELECT payload FROM meta").fetchone()[0])
        if not (
            metadata["rth_evidence"]
            and metadata["historical_membership_verified"]
            and metadata["historical_classification_verified"]
        ):
            return {
                "availability": "UNAVAILABLE",
                "reason": "POINT_IN_TIME_HISTORY_UNVERIFIED",
                "report_hash": run["report_hash"],
            }
        grouped = {}
        for (payload,) in cx.execute(
            "SELECT payload FROM bars WHERE iid=? AND day<=? ORDER BY day,revision",
            (event["instrument_id"], day),
        ):
            row = json.loads(payload)
            if (
                row.get("available_at")
                and row.get("available_at_kind") in ("MEASURED", "PROVIDER")
                and instant(row["available_at"]) <= instant(clock)
            ):
                grouped[row["market_date"]] = row
        events = [json.loads(row[0]) for row in cx.execute("SELECT payload FROM events")]
    bars = list(grouped.values())
    if not bars or bars[-1]["market_date"] != day:
        return {
            "availability": "UNAVAILABLE",
            "reason": "DATA_UNAVAILABLE_AT_CLOCK",
            "report_hash": run["report_hash"],
        }
    if any(not b.get("is_final") or b.get("quality_status") != "OK" for b in bars) or [
        b["market_date"] for b in bars
    ] != cal.dates(bars[0]["market_date"], day):
        return {
            "availability": "UNAVAILABLE",
            "reason": "LATEST_REVISION_INVALID_OR_GAP",
            "report_hash": run["report_hash"],
        }
    latest_events = {}
    for e in events:
        if instant(e["known_at"]) <= instant(clock) and e["revision"] > latest_events.get(
            e["event_id"], {}
        ).get("revision", 0):
            latest_events[e["event_id"]] = e
    actions = effective_actions(
        [e for e in latest_events.values() if e.get("status") != "CANCELLED"], event["instrument_id"], clock
    )
    adjusted = adjust(bars, actions, clock)
    original = event.get("original_frozen") or event["state_history"][0]["frozen"]
    trigger = next((b for b in adjusted if b["market_date"] == event["trigger_market_date"]), None)
    factor = (
        Decimal(str(trigger["price_factor"])) / Decimal(str(original.get("price_factor", "1")))
        if trigger
        else None
    )
    thresholds = [
        {
            "field": k,
            "original": str(v),
            "converted": str(Decimal(str(v)) * factor),
            "known_from": event["trigger_market_date"],
            "unit": "USD",
        }
        for k, v in original.items()
        if k in ("level", "close", "high", "low", "entry_anchor", "pullback_low", "pullback_high")
        and v is not None
        and factor is not None
    ]
    visible = adjusted[-window:]
    markers = [
        {
            "market_date": event["trigger_market_date"],
            "kind": "TRIGGER",
            "evaluated_at": event["triggered_at"],
        }
    ]
    markers += [
        {"market_date": t["market_date"], "kind": t["lifecycle"], "evaluated_at": t.get("evaluated_at")}
        for t in event["state_history"]
        if instant(t.get("evaluated_at") or clock_for(t["market_date"], m, cal)) <= instant(clock)
    ]
    require(
        fingerprint(index) == run["chart_index_fingerprint"]
        and fingerprint(source) == run["chart_source_fingerprint"],
        "ARTIFACT_CORRUPT",
    )
    return {
        "availability": "AVAILABLE",
        "report_hash": run["report_hash"],
        "as_of": as_of,
        "evaluation_clock": clock,
        "instrument_id": event["instrument_id"],
        "price_basis": m["price_basis"],
        "unit_contract": {
            "kind": "FEATURE_ADJUSTED_AT_CLOCK",
            "actions": ["SPLIT", "DIVIDEND"],
            "label_return_kind": m["price_basis"]["return_kind"],
        },
        "window": window,
        "bars": [
            {
                **{k: str(b[k]) for k in ("open", "high", "low", "close")},
                "market_date": b["market_date"],
                "revision": b["revision"],
                "available_at": b["available_at"],
                "price_factor": b["price_factor"],
            }
            for b in visible
        ],
        "markers": [t for t in markers if visible[0]["market_date"] <= t["market_date"] <= day],
        "thresholds": thresholds,
        "action_refs": [
            {
                "event_id": e["event_id"],
                "revision": e["revision"],
                "known_at": e["known_at"],
                "effective_at": e["effective_at"],
            }
            for e in actions
        ],
    }
