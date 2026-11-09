import json
from pathlib import Path

from src.services.signal_scanner.core import digest, require, stamp


def split_manifest(m, cal):
    days = cal.dates(m["start_date"], m["end_date"])
    if m["split_policy"]["kind"] == "EXPLORATORY":
        return dict(
            kind="EXPLORATORY",
            sets={"EXPLORATORY": [days[0], days[-1]]},
            embargo=[],
            reason="LESS_THAN_FIVE_YEARS",
        )
    a, b = int(len(days) * 0.6), int(len(days) * 0.8)
    require(a > 20 and b - a > 20 and len(days) - b > 20, "INSUFFICIENT_SPLIT")
    return dict(
        kind="CHRONOLOGICAL_60_20_20",
        sets={
            "DEVELOPMENT": [days[0], days[a - 1]],
            "VALIDATION": [days[a + 20], days[b - 1]],
            "SEALED": [days[b + 20], days[-1]],
        },
        embargo=days[a : a + 20] + days[b : b + 20],
        raw_boundaries=[days[a], days[b]],
    )


def assignment(split, day):
    for name, (lo, hi) in split["sets"].items():
        if lo <= day <= hi:
            return name
    return "EMBARGO"


def register(runtime, m, split, reveal=False):
    """Called while the runtime's exclusive lock is held; never rewrites a reveal."""
    path = Path(runtime) / "registrations.json"
    records = json.loads(path.read_text()) if path.exists() else {}
    regid = m["registration"]["id"]
    protocol = digest(
        {
            k: m[k]
            for k in (
                "registration",
                "start_date",
                "end_date",
                "split_policy",
                "cost_assumption",
                "price_basis",
                "benchmark_ref",
                "evaluation_clock",
                "entry_bases",
                "universe_version",
            )
        }
    )
    old = records.get(regid)
    if old:
        require(old["protocol_hash"] == protocol, "REGISTRATION_CONFLICT")
    sealed = split["sets"].get("SEALED")
    if reveal and sealed:
        for key, value in records.items():
            prior = value.get("sealed_range")
            if prior and value.get("revealed_at") and key != regid:
                require(sealed[1] < prior[0] or sealed[0] > prior[1], "SEALED_RANGE_ALREADY_REVEALED")
        if old and old.get("revealed_at"):
            require(
                m["ruleset_version"] == old["ruleset_version"]
                and m["data_snapshot_ref"]["sha256"] == old["snapshot_hash"],
                "SEALED_RANGE_ALREADY_REVEALED",
            )
    record = old or dict(
        protocol_hash=protocol,
        sealed_range=sealed,
        created_at=stamp(),
        ruleset_version=m["ruleset_version"],
        snapshot_hash=m["data_snapshot_ref"]["sha256"],
    )
    if reveal and sealed:
        record.setdefault("revealed_at", stamp())
    records[regid] = record
    from src.services.signal_scanner.research.reporting import atomic_text

    atomic_text(path, json.dumps(records, ensure_ascii=False, sort_keys=True))
    return dict(registration_id=regid, sealed_revealed=bool(record.get("revealed_at")))


def compare_reports(left, right, axis):
    axes = {
        "rules": "ruleset_version",
        "universe": "universe_version",
        "data": "snapshot_hash",
        "price_basis": "price_basis",
    }
    require(axis in axes, "INVALID_COMPARISON_AXIS")
    for k in (
        "calendar_version",
        "start_date",
        "end_date",
        "research_mode",
        "entry_bases",
        "cost_assumption",
    ):
        require(left["identity"][k] == right["identity"][k], "INCOMPARABLE_EXPERIMENTS", field=k)
    key = axes[axis]
    for fixed in set(axes.values()) - {key}:
        require(
            left["identity"][fixed] == right["identity"][fixed], "UNDECLARED_COMPARISON_AXIS", field=fixed
        )

    def events(r):
        return {(e["instrument_id"], e["rule_id"], e["trigger_market_date"]): e for e in r["events"]}

    a, b = events(left), events(right)
    common = sorted(set(a) & set(b))
    return dict(
        kind="VERSION_COMPARISON",
        axis=axis,
        left=left["business_output_hash"],
        right=right["business_output_hash"],
        added=[b[k] for k in sorted(set(b) - set(a))],
        removed=[a[k] for k in sorted(set(a) - set(b))],
        changed=[dict(key=list(k), before=a[k], after=b[k]) for k in common if digest(a[k]) != digest(b[k])],
        shared_instruments=sorted({k[0] for k in a} & {k[0] for k in b}),
        left_statistics=left["statistics"],
        right_statistics=right["statistics"],
        all_results_retained=True,
        sealed_reuse="HISTORICAL_COMPARISON_ONLY",
    )
