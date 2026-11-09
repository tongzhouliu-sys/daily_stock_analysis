"""Immutable report projections; all numeric reductions reuse Decimal engine code."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.services.signal_scanner.core import DomainError, canonical, require
from src.services.signal_scanner.research.statistics import Accumulator, summarize

from .store import safe_path

SECTIONS = ("events", "outcomes", "assessments", "transitions", "benchmarks", "unconditional")
STRONG = {
    "horizon": {"5", "10", "20"},
    "cost_tier": {"5", "10", "20"},
    "entry_basis": {"NEXT_AVAILABLE_OPEN", "CONFIRM_CLOSE"},
    "split": {"EXPLORATORY", "DEVELOPMENT", "VALIDATION", "SEALED", "EMBARGO"},
    "direction": {"LONG", "BEARISH", "UNDERLYING_RETURN"},
    "research_mode": {"POINT_IN_TIME", "REVISED_HISTORY"},
}
OPTIONAL = {"rule_id", "instrument_id", "year", "environment", "sector", "asset_type", "status"}
DIMENSIONS = {"ECONOMIC_TOTAL", "RULE", "YEAR", "ENVIRONMENT", "SECTOR", "ASSET_TYPE"}


def redact(value):
    if isinstance(value, dict):
        return {
            k: redact(v)
            for k, v in value.items()
            if k not in {"path", "source_path", "manifest_path", "source_root", "snapshot_path"}
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Imported free text is plain text, with local paths and common secret assignments removed.
        return re.sub(
            r"(?:/(?:Users|home|private|tmp|etc)/\S+|(?:sk-[A-Za-z0-9_-]{10,})|(?:api[_-]?key|password|token)\s*[=:]\s*\S+)",
            "[已隐藏]",
            value,
            flags=re.IGNORECASE,
        )
    return value


def fingerprints(folder):
    result = {}
    for p in folder.iterdir():
        require(not p.is_symlink(), "ARTIFACT_CORRUPT", "报告包含符号链接")
        if p.is_file():
            st = p.stat()
            result[p.name] = [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
    return result


def index_report(store, run, runtime, ref):
    runtime = Path(runtime).absolute()
    path = safe_path(runtime, runtime / ref)
    before = fingerprints(path.parent)
    index = store.root / "indices" / (run["id"] + ".sqlite3")
    safe_path(store.root, index)
    index.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = index.with_suffix(".tmp")
    if temp.exists():
        temp.unlink()
    with sqlite3.connect(temp) as cx:
        cx.execute("CREATE TABLE rows(section TEXT,seq INTEGER,payload TEXT,PRIMARY KEY(section,seq))")
        cx.execute("CREATE TABLE evidence(event_id TEXT PRIMARY KEY,payload TEXT)")
        from .streaming import verified_stream

        report = verified_stream(path, cx, redact)
        cx.execute("CREATE INDEX section_event ON rows(section,json_extract(payload,'$.event_id'))")
        cx.execute(
            "CREATE INDEX outcome_axes ON rows(section,json_extract(payload,'$.horizon'),json_extract(payload,'$.cost_tier'),json_extract(payload,'$.entry_basis'),json_extract(payload,'$.split'),json_extract(payload,'$.direction'))"
        )
        cx.execute("""CREATE UNIQUE INDEX economic_label ON rows(
            json_extract(payload,'$.economic_event_id'),json_extract(payload,'$.entry_basis'),
            json_extract(payload,'$.horizon'),json_extract(payload,'$.cost_tier'),
            json_extract(payload,'$.split'),json_extract(payload,'$.research_mode'),json_extract(payload,'$.direction'))
            WHERE section='outcomes'""")
        cx.execute("CREATE TABLE summary(payload TEXT)")
        summary = redact(
            {
                k: report[k]
                for k in (
                    "identity",
                    "coverage",
                    "limitations",
                    "biases",
                    "review",
                    "data_kind",
                    "lifecycle_counts",
                    "generated_at",
                )
            }
        )
        summary.update(
            api_schema_version="1",
            report_schema_version=1,
            business_output_hash=report["business_output_hash"],
            report_hash=report["business_output_hash"],
            manifest=redact(report["input_manifest"]),
            count_unit="ECONOMIC_EVENT",
            counts={
                s: cx.execute("SELECT count(*) FROM rows WHERE section=?", (s,)).fetchone()[0]
                for s in SECTIONS
            },
            defaults={
                "horizon": 20,
                "cost_tier": 10,
                "entry_basis": "NEXT_AVAILABLE_OPEN",
                "direction": "LONG",
                "split": "EXPLORATORY"
                if report["identity"]["split"]["kind"] == "EXPLORATORY"
                else "VALIDATION",
                "research_mode": report["identity"]["research_mode"],
            },
        )
        cx.execute("INSERT INTO summary VALUES(?)", (canonical(summary),))
    require(before == fingerprints(path.parent), "ARTIFACT_CORRUPT")
    temp.replace(index)
    run.update(
        index_path=str(index),
        source_root=str(runtime),
        report_ref=ref,
        fingerprints=before,
        report_hash=report["business_output_hash"],
        report_state="READY",
        data_kind=report["data_kind"],
        identity=summary["identity"],
        start_date=report["identity"]["start_date"],
        end_date=report["identity"]["end_date"],
    )
    st = index.stat()
    run["index_fingerprint"] = [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
    from .charts import build as build_chart

    build_chart(store, run, runtime, report)
    from .exports import publish_exports

    publish_exports(store, run)
    return run


@contextmanager
def connect(run):
    require(
        run.get("report_state") != "UNSUPPORTED_VERSION", "UNSUPPORTED_VERSION", "当前版本暂不支持该历史报告"
    )
    require(run.get("report_state") == "READY", "REPORT_NOT_READY", "报告尚未就绪")
    root = Path(run["source_root"])
    folder = safe_path(root, root / run["report_ref"]).parent
    try:
        require(
            fingerprints(folder) == run["fingerprints"],
            "ARTIFACT_CORRUPT",
            "报告文件已变化，请管理员重新校验",
        )
        p = Path(run["index_path"])
        require(p.is_file() and not p.is_symlink(), "INDEX_UNAVAILABLE", "查询索引不可用，请重新登记")
        st = p.stat()
        require(
            [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns] == run["index_fingerprint"],
            "INDEX_UNAVAILABLE",
            "索引已变化，请重新登记",
        )
        cx = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
        cx.execute("PRAGMA query_only=ON")
        try:
            yield cx
        finally:
            cx.close()
    except OSError:
        raise DomainError("ARTIFACT_CORRUPT", "报告文件不完整") from None


def summary(run):
    with connect(run) as cx:
        return json.loads(cx.execute("SELECT payload FROM summary").fetchone()[0])


def filters_for(run, supplied, *, strong=True):
    require(not set(supplied) - set(STRONG) - OPTIONAL, "UNSUPPORTED_FILTER", "存在不支持的筛选条件", 422)
    f = {**summary(run)["defaults"], **supplied} if strong else dict(supplied)
    for k, values in STRONG.items():
        if k in f:
            require(str(f[k]) in values, "INVALID_FILTER", "筛选值不支持", 422, field=k)
    require(
        not (f.get("split") == "SEALED" and not run["identity"]["sealed_revealed"]),
        "SEALED_NOT_REVEALED",
        "封存段尚未揭示",
        403,
    )
    if "rule_id" in f:
        from src.services.signal_scanner.domain.rules import RULES

        require(f["rule_id"] in RULES, "INVALID_FILTER", status=422)
    for k in ("horizon", "cost_tier"):
        if k in f:
            f[k] = int(f[k])
    return f


def clause(section, f):
    sql, args = ["section=?"], [section]
    for k, v in f.items():
        if k == "rule_id" and section in ("outcomes",):
            sql.append(
                "EXISTS(SELECT 1 FROM json_each(rows.payload,'$.rule_refs') r WHERE json_extract(r.value,'$.rule_id')=?)"
            )
        elif k == "year":
            sql.append("substr(json_extract(payload,'$.confirmation_market_date'),1,4)=?")
        else:
            sql.append(f"json_extract(payload,'$.{k}')=?")
        args.append(v)
    return " AND ".join(sql), args


def raw_rows(run, section, f=None):
    where, args = clause(section, f or {})
    with connect(run) as cx:
        for r in cx.execute("SELECT payload FROM rows WHERE " + where + " ORDER BY seq", args):
            yield json.loads(r[0])


def page(run, section, supplied, cursor=0, limit=50, event_scope="all"):
    require(section in ("events", "outcomes", "assessments", "transitions"), "INVALID_SECTION", status=422)
    require(0 <= cursor and 1 <= limit <= 200, "INVALID_PAGINATION", status=422)
    require(
        event_scope in ("all", "labels") and (event_scope == "all" or section == "events"),
        "INVALID_FILTER",
        status=422,
    )
    if section == "events" and event_scope == "labels":
        f = filters_for(run, supplied)
        label_where, args = clause("outcomes", f)
        where = (
            "section='events' AND json_extract(payload,'$.event_id') IN (SELECT json_extract(ref.value,'$.event_id') FROM rows,json_each(rows.payload,'$.rule_refs') ref WHERE "
            + label_where
            + ")"
        )
        with connect(run) as cx:
            total = cx.execute("SELECT count(*) FROM rows WHERE " + where, args).fetchone()[0]
            items = [
                json.loads(row[0])
                for row in cx.execute(
                    "SELECT payload FROM rows WHERE " + where + " ORDER BY seq LIMIT ? OFFSET ?",
                    [*args, limit, cursor],
                )
            ]
        return {
            "section": section,
            "items": items,
            "total": total,
            "next_cursor": cursor + limit if cursor + limit < total else None,
            "filters": f,
            "event_scope": "labels",
            "report_hash": run["report_hash"],
            "api_schema_version": "1",
        }
    f = filters_for(run, supplied, strong=section == "outcomes")
    allowed = {
        "events": {"rule_id", "instrument_id", "direction", "status"},
        "assessments": {"rule_id", "instrument_id", "status"},
        "transitions": set(),
        "outcomes": set(STRONG) | OPTIONAL,
    }[section]
    require(set(f) <= allowed, "UNSUPPORTED_FILTER", status=422)
    if section == "events" and "status" in f:
        where, args = clause(section, {k: v for k, v in f.items() if k != "status"})
        where += " AND json_extract(payload,'$.lifecycle')=?"
        args.append(f["status"])
    else:
        where, args = clause(section, f)
    with connect(run) as cx:
        n = cx.execute("SELECT count(*) FROM rows WHERE " + where, args).fetchone()[0]
        rows = [
            json.loads(r[0])
            for r in cx.execute(
                "SELECT payload FROM rows WHERE " + where + " ORDER BY seq LIMIT ? OFFSET ?",
                [*args, limit, cursor],
            )
        ]
    require(len(canonical(rows).encode()) <= 950000, "RESPONSE_TOO_LARGE", "请缩小分页大小", 422)
    return {
        "section": section,
        "items": rows,
        "total": n,
        "next_cursor": cursor + limit if cursor + limit < n else None,
        "filters": f,
        "report_hash": run["report_hash"],
        "api_schema_version": "1",
    }


def statistics(run, supplied, dimension="ECONOMIC_TOTAL"):
    require(dimension in DIMENSIONS, "INVALID_DIMENSION", status=422)
    f = filters_for(run, supplied)
    rows = list(raw_rows(run, "outcomes", f))
    minimum = summary(run)["review"]["min_completed_samples"]
    stats = summarize(rows, minimum)
    return {
        "items": [
            r
            for r in stats
            if r["dimension"] == dimension
            and (dimension != "RULE" or not f.get("rule_id") or r["value"] == f["rule_id"])
        ],
        "filters": f,
        "denominators": {
            "label_rows": len(rows),
            "economic_events": len({r["economic_event_id"] for r in rows}),
        },
        "report_hash": run["report_hash"],
        "query_version": "web-stats-1",
        "count_unit": "ECONOMIC_EVENT",
        "quantile_method": "LINEAR_N_MINUS_ONE",
        "min_completed_samples": minimum,
    }


def benchmark(run, supplied, kind, instrument_id=None):
    require(kind in ("SPY", "SECTOR", "UNCONDITIONAL"), "INVALID_BENCHMARK", status=422)
    f = filters_for(run, supplied)
    minimum = summary(run)["review"]["min_completed_samples"]
    if kind == "UNCONDITIONAL":
        require(
            instrument_id and not set(supplied) & OPTIONAL,
            "UNSUPPORTED_UNCONDITIONAL_FILTER",
            "无条件基准仅支持单证券及已登记的强制口径",
            422,
        )
        f["direction"] = "UNDERLYING_RETURN"
        items = [r for r in raw_rows(run, "unconditional", f) if r["value"] == instrument_id]
        return {
            "kind": kind,
            "instrument_id": instrument_id,
            "items": items,
            "filters": f,
            "count_unit": "SECURITY_SESSION",
            "report_hash": run["report_hash"],
        }
    signals = {r["economic_event_id"]: r for r in raw_rows(run, "outcomes", f)}
    selected = [
        b
        for b in raw_rows(
            run, "benchmarks", {k: v for k, v in f.items() if k in ("horizon", "cost_tier", "entry_basis")}
        )
        if b["kind"] == kind and b["economic_event_id"] in signals
    ]
    accs = [Accumulator() for _ in range(3)]
    for b in selected:
        if b["status"] == "PAIRED":
            for acc, value in zip(
                accs, (b["signal_net_return"], b["benchmark"]["net_return"], b["excess_return"])
            ):
                acc.add({"status": "COMPLETED", "gross_return": value, "net_return": value})
    paired = accs[0].counts["COMPLETED"]
    completed = sum(r["status"] == "COMPLETED" for r in signals.values())
    return {
        "kind": kind,
        "filters": f,
        "signal_completed": completed,
        "paired_count": paired,
        "missing_count": completed - paired,
        "items": [
            dict(series=s, **a.result(minimum))
            for s, a in zip(("信号（配对交集）", "基准（配对交集）", "逐对超额"), accs)
        ],
        "report_hash": run["report_hash"],
        "count_unit": "PAIRED_ECONOMIC_EVENT",
    }


def event_detail(run, event_id):
    events = list(raw_rows(run, "events", {"event_id": event_id}))
    require(events, "RESOURCE_NOT_FOUND", "该报告中不存在此事件", 404)
    e = events[0]
    with connect(run) as cx:
        outcomes = [
            json.loads(r[0])
            for r in cx.execute(
                """
            SELECT payload FROM rows WHERE section='outcomes' AND EXISTS(
                SELECT 1 FROM json_each(rows.payload,'$.rule_refs') r
                WHERE json_extract(r.value,'$.event_id')=?) ORDER BY seq""",
                (event_id,),
            )
        ]
    with connect(run) as cx:
        evidence = cx.execute("SELECT payload FROM evidence WHERE event_id=?", (event_id,)).fetchone()
    parent_id = e.get("frozen", {}).get("parent_event_id")
    parent_refs = e.get("parent_refs", [])
    if parent_id:
        parents = list(raw_rows(run, "events", {"event_id": parent_id}))
        parent_refs = [
            {
                "event_id": parent_id,
                "status": "AVAILABLE" if parents else "MISSING_IN_REPORT",
                "event": parents[0] if parents else None,
                "reason": None if parents else "父事件不在当前报告可见范围",
            }
        ]
    return {
        "event": e,
        "transitions": list(raw_rows(run, "transitions", {"event_id": event_id})),
        "confirmation_evidence": json.loads(evidence[0]) if evidence else {"reason": "无确认时点证据"},
        "outcomes": outcomes,
        "parent_refs": parent_refs,
        "report_hash": run["report_hash"],
    }
