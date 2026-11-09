"""Explicit EOD pattern diagnostics; never a substitute for the RTH research gate.

Reuses shared features, rule state transitions, costs and quantiles. Does not
call the formal runner, change its validator, or write scanner business tables.
"""

import argparse
import hashlib
import json
import resource
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.services.signal_scanner.core import canonical, digest, stamp
from src.services.signal_scanner.domain.features import StreamingFeatures, adjust
from src.services.signal_scanner.domain.rules import ACTIVE, RULES, RuleEngine
from src.services.signal_scanner.research.inputs import ResearchCalendar, clock_for
from src.services.signal_scanner.research.isolation import offline
from src.services.signal_scanner.research.outcomes import returns
from src.services.signal_scanner.research.reporting import atomic_text, csv_text
from src.services.signal_scanner.research.statistics import quantile
from src.services.signal_scanner.research.versions import code_hash, dependency_hash, ruleset

MODE = "EOD_EXPLORATORY"
BLOCKS = ["RTH_UNVERIFIED", "EVENT_COVERAGE_UNVERIFIED", "ENVIRONMENT_UNKNOWN", "SECURITY_STATUS_UNVERIFIED"]
COST = {"fee_unit": "BPS_OF_ENTRY", "entry_fee": "0", "exit_fee": "0"}
BASES = ("NEXT_AVAILABLE_OPEN", "CONFIRM_CLOSE")
HORIZONS = (5, 10, 20)
TIERS = (5, 10, 20)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def split_price(bar, field, actions, anchor):
    """Ex-post split units as of the frozen anchor, without changing known_at."""
    value = Decimal(str(bar[field]))
    for action in actions:
        if bar["market_date"] < action["effective_at"][:10] <= anchor:
            value /= Decimal(str(action["payload_json"]["ratio"]))
    return value


def rebase_events(engine, prefix, actions, day):
    # Source known_at is retained; ignoring it here is an explicit revised-history assumption.
    by_date = {b["market_date"]: b for b in prefix}
    for e in engine.events:
        if e["lifecycle"] not in ACTIVE and not (
            e["rule_id"] in ("TR-01", "VP-01")
            and e.get("confirmation_market_date")
            and engine.cal.distance(e["confirmation_market_date"], day) <= 10
        ):
            continue
        ref = by_date.get(e["trigger_market_date"])
        if ref is None:
            continue
        factor = float(adjust([ref, prefix[-1]], actions)[0]["price_factor"])
        previous = float(e["frozen"].get("price_factor", 1))
        if factor == previous:
            continue
        ratio = factor / previous
        e.setdefault("original_frozen", deepcopy(e["frozen"]))
        for field in (
            "close",
            "high",
            "low",
            "atr",
            "entry_anchor",
            "level",
            "pullback_low",
            "pullback_high",
            "confirmation_atr",
        ):
            if e["frozen"].get(field) is not None:
                e["frozen"][field] *= ratio
        e["frozen"]["price_factor"] = str(factor)
        e.setdefault("unit_conversions", []).append(
            {
                "market_date": day,
                "factor": ratio,
                "assumption": "REVISED_SPLIT_EFFECTIVE_DATE",
                "source_action_refs": [
                    {"event_id": a["event_id"], "revision": a["revision"], "known_at": a["known_at"]}
                    for a in actions
                ],
            }
        )


def group_events(events):
    grouped = defaultdict(list)
    for e in events:
        if e.get("confirmed_at") and e["kind"] != "EVIDENCE":
            grouped[(e["instrument_id"], e["direction"], e["family"], e["trigger_market_date"])].append(e)
    result = []
    for key, es in sorted(grouped.items()):
        primary = min(es, key=lambda e: (e["rule_id"] != "VP-01", e["rule_id"]))
        result.append(
            {
                "economic_event_id": "eod_" + digest(key)[:24],
                "primary": primary,
                "rule_refs": sorted({e["rule_id"] for e in es}),
            }
        )
    return result


def label(iid, day, basis, horizon, maps, actions, cal, anchor):
    entry = cal.shift(day, 1) if basis == "NEXT_AVAILABLE_OPEN" else day
    end = cal.shift(entry, horizon - 1 if basis == "NEXT_AVAILABLE_OPEN" else horizon)
    result = {
        "instrument_id": iid,
        "confirmation_market_date": day,
        "entry_basis": basis,
        "horizon": horizon,
        "entry_market_date": entry,
        "exit_market_date": end,
        "status": "PENDING",
        "reason_code": None,
    }
    if end > anchor:
        return result
    needed = cal.dates(entry, end)
    if any(d not in maps[iid] for d in needed):
        return {**result, "status": "UNRESOLVED", "reason_code": "MISSING_LABEL_DATA"}
    a, b = maps[iid][entry], maps[iid][end]
    p0 = split_price(a, "open" if basis == "NEXT_AVAILABLE_OPEN" else "close", actions[iid], anchor)
    pn = split_price(b, "close", actions[iid], anchor)
    return {**result, "status": "COMPLETED", "entry_price": str(p0), "exit_price": str(pn)}


def metric(rows, minimum=30):
    counts = Counter(r["status"] for r in rows)
    completed = [r for r in rows if r["status"] == "COMPLETED"]
    result = {
        "eligible": len(rows),
        "completed": len(completed),
        "pending": counts["PENDING"],
        "unresolved": counts["UNRESOLVED"],
        "status": "SUFFICIENT_DESCRIPTIVE" if len(completed) >= minimum else "INSUFFICIENT",
        "min_completed_samples": minimum,
    }
    for name in ("gross_return", "net_return", "spy_excess_return", "unconditional_excess_return"):
        values = [Decimal(r[name]) for r in completed if r.get(name) is not None]
        result[name] = dict(
            n=len(values),
            mean=str(sum(values) / len(values)) if values else None,
            **{
                f"p{p:02}": str(quantile(values, Decimal(p) / 100)) if values else None
                for p in (5, 25, 50, 75, 95)
            },
        )
    n = len(completed)
    result["positive_net_fraction"] = (
        str(sum(Decimal(r["net_return"]) > 0 for r in completed) / Decimal(n)) if n else None
    )
    result["negative_gross_fraction"] = (
        str(sum(Decimal(r["gross_return"]) < 0 for r in completed) / Decimal(n)) if n else None
    )
    result["unique_instruments"] = len({r["instrument_id"] for r in completed})
    result["unique_confirmation_dates"] = len({r["confirmation_market_date"] for r in completed})
    return result


def execute(manifest_path, output, *, mode):
    if mode != MODE:
        raise ValueError("Explicit EOD_EXPLORATORY mode is required")
    began = time.monotonic()
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    if output == manifest_path.parent or manifest_path.is_relative_to(output):
        raise ValueError("Inputs and output must be separate")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text())
    snapshot_path = (manifest_path.parent / manifest["data_snapshot_ref"]["path"]).resolve()
    if not snapshot_path.is_relative_to(manifest_path.parent):
        raise ValueError("Input path is outside the manifest directory")
    if file_hash(snapshot_path) != manifest["data_snapshot_ref"]["sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    snapshot = json.loads(snapshot_path.read_text())
    if snapshot["data_kind"] != "REAL" or snapshot["session_kind"] != "EOD_DAILY":
        raise ValueError("This diagnostic requires explicitly REAL EOD_DAILY inputs")
    cal = ResearchCalendar()
    start, end = manifest["start_date"], manifest["end_date"]
    anchor = max(b["market_date"] for bars in snapshot["bars"].values() for b in bars)
    ids = sorted({u["instrument_id"] for u in snapshot["universe"]})
    available = [iid for iid in ids if snapshot["bars"].get(iid)]
    benchmark = manifest["benchmark_ref"]["market"]
    if benchmark not in available:
        raise ValueError("SPY benchmark is missing")
    maps = {iid: {b["market_date"]: b for b in snapshot["bars"][iid]} for iid in available}
    if any(len(maps[i]) != len(snapshot["bars"][i]) for i in available):
        raise ValueError("Select a frozen revision explicitly before diagnostic replay")
    actions = {
        iid: [
            e
            for e in snapshot["events"]
            if e.get("instrument_id") == iid and e["event_type"] == "SPLIT" and e["status"] != "CANCELLED"
        ]
        for iid in available
    }
    protocol = {
        "kind": "EOD_PATTERN_DIAGNOSTIC_PROTOCOL",
        "schema_version": 1,
        "mode": MODE,
        "start_date": start,
        "end_date": end,
        "price_data_end": anchor,
        "warmup_bars": manifest["warmup_bars"],
        "source_snapshot_sha256": file_hash(snapshot_path),
        "code_hash": code_hash(),
        "dependency_hash": dependency_hash(),
        "runner_sha256": file_hash(__file__),
        "entry_bases": list(BASES),
        "horizons": list(HORIZONS),
        "slippage_bps_per_side": list(TIERS),
        "fees": COST,
        "split_policy": "EXPLORATORY_ALL_OBSERVED",
        "source_session_kind": "EOD_DAILY",
        "price_basis": "REVISED_SPLIT_ADJUSTED_PRICE_RETURN",
        "formal_validation_passed": False,
        "executable_eligibility": "BLOCKED",
        "blockers": BLOCKS,
        "assumptions": [
            "按冻结历史的每个交易日EOD价格作事后形态评估，不证明该数据在当日收盘后30分钟真实可得。",
            "拆股按冻结供应商事件的effective_at生效；保留原known_at，不视为PIT可知证据。",
            "特征价格和成交量只作拆股单位转换；收益为价格收益，不计分红。",
            "NEXT_AVAILABLE_OPEN假设确认后的下一交易日开盘，CONFIRM_CLOSE仅为独立敏感性口径。",
            "零手续费明确登记；5/10/20bp滑点在入场与终点各扣一次。",
            "VIX、历史财报覆盖、行业映射、证券状态未知；不应用虚构过滤，不产生可交易资格。",
            "当前池回填历史，有幸存者偏差；样本重叠且相关，描述性统计不能证明未来有效性。",
        ],
    }
    atomic_text(output / "protocol.json", canonical(protocol))
    sector = {i["id"]: i.get("sector", "UNKNOWN") for i in snapshot["instruments"]}
    all_events, assessment_counts, quality_failures = [], Counter(), Counter()
    conversions = 0
    observed_security_sessions = 0
    with offline(output) as audit:
        for number, iid in enumerate(available, 1):
            rows = snapshot["bars"][iid]
            if rows != sorted(rows, key=lambda b: b["market_date"]):
                raise ValueError("Bars must be sorted")
            registry = ruleset()
            registry["EV-01"]["stage"] = "DISABLED"
            engine = RuleEngine(cal, registry)
            cursor, old_actions = StreamingFeatures(cal, iid, MODE), []
            for pos, bar in enumerate(rows):
                day = bar["market_date"]
                if day > cal.shift(end, 3):
                    break
                if bar["session_kind"] != "EOD_DAILY" or not bar["is_final"] or bar["quality_status"] != "OK":
                    raise ValueError("Unexpected source session or invalid bar")
                if pos and day != cal.shift(rows[pos - 1]["market_date"], 1):
                    raise ValueError("Missing intermediate source session")
                visible_actions = [a for a in actions[iid] if a["effective_at"][:10] <= day]
                if visible_actions != old_actions:
                    cursor = StreamingFeatures(cal, iid, MODE)
                    for adjusted in adjust(rows[: pos + 1], visible_actions):
                        f = cursor.append(adjusted)
                    rebase_events(engine, rows[: pos + 1], visible_actions, day)
                    conversions += 1
                    old_actions = visible_actions
                else:
                    f = cursor.append(adjust([bar], visible_actions)[0])
                f["evaluated_at"] = clock_for(day, manifest, cal)
                if f["count"] < manifest["warmup_bars"]:
                    continue
                for e in engine.events:
                    if (
                        e["rule_id"] in ("TR-01", "VP-01")
                        and e.get("confirmed_at")
                        and f["close"] < e["frozen"]["level"]
                    ):
                        e["structure_broken"] = True
                engine.step(f, detect=day <= end)
                if start <= day <= end:
                    observed_security_sessions += 1
                    for rule, a in engine.evaluability.items():
                        assessment_counts[(rule, a["status"])] += 1
            for e in engine.events:
                if start <= e["trigger_market_date"] <= end:
                    all_events.append(
                        {
                            **e,
                            "mode": MODE,
                            "source_session_kind": "EOD_DAILY",
                            "sector_current_csv": sector[iid],
                            "executable_eligibility": "BLOCKED",
                            "blocked_reasons": BLOCKS,
                        }
                    )
            if number % 5 == 0 or number == len(available):
                progress = {
                    "phase": "REPLAY",
                    "processed_symbols": number,
                    "total_symbols": len(available),
                    "ticker": iid,
                    "elapsed_seconds": round(time.monotonic() - began, 2),
                }
                atomic_text(output / "progress.json", canonical(progress))
                print(canonical(progress), flush=True)
        all_events.sort(key=lambda e: (e["trigger_market_date"], e["instrument_id"], e["rule_id"]))
        groups = group_events(all_events)
        # Same-security unconditional reference, for every date in the fixed trigger window.
        unconditional, unconditional_means = [], {}
        dates = cal.dates(start, end)
        for iid in available:
            for basis in BASES:
                for horizon in HORIZONS:
                    base_rows = [label(iid, day, basis, horizon, maps, actions, cal, anchor) for day in dates]
                    for tier in TIERS:
                        rows = [
                            {**r, **returns(r["entry_price"], r["exit_price"], tier, COST)}
                            if r["status"] == "COMPLETED"
                            else r
                            for r in base_rows
                        ]
                        m = metric(rows)
                        unconditional_means[(iid, basis, horizon, tier)] = m["net_return"]["mean"]
                        unconditional.append(
                            dict(instrument_id=iid, entry_basis=basis, horizon=horizon, cost_tier=tier, **m)
                        )
        outcomes = []
        for group in groups:
            e = group["primary"]
            for basis in BASES:
                for horizon in HORIZONS:
                    raw = label(
                        e["instrument_id"],
                        e["confirmation_market_date"],
                        basis,
                        horizon,
                        maps,
                        actions,
                        cal,
                        anchor,
                    )
                    spy = label(
                        benchmark, e["confirmation_market_date"], basis, horizon, maps, actions, cal, anchor
                    )
                    for tier in TIERS:
                        row = dict(
                            **raw,
                            economic_event_id=group["economic_event_id"],
                            rule_refs=group["rule_refs"],
                            direction=e["direction"],
                            kind=e["kind"],
                            family=e["family"],
                            trigger_market_date=e["trigger_market_date"],
                            cost_tier=tier,
                            mode=MODE,
                            executable_eligibility="BLOCKED",
                            sector_current_csv=sector[e["instrument_id"]],
                        )
                        if raw["status"] == "COMPLETED":
                            row.update(returns(raw["entry_price"], raw["exit_price"], tier, COST))
                            if spy["status"] == "COMPLETED":
                                spy_return = returns(spy["entry_price"], spy["exit_price"], tier, COST)[
                                    "net_return"
                                ]
                                row.update(
                                    spy_net_return=spy_return,
                                    spy_excess_return=str(Decimal(row["net_return"]) - Decimal(spy_return)),
                                )
                            mean = unconditional_means[(e["instrument_id"], basis, horizon, tier)]
                            row["unconditional_excess_return"] = (
                                str(Decimal(row["net_return"]) - Decimal(mean)) if mean is not None else None
                            )
                        outcomes.append(row)
        stats = []
        for basis in BASES:
            for horizon in HORIZONS:
                for tier in TIERS:
                    selected = [
                        r
                        for r in outcomes
                        if (r["entry_basis"], r["horizon"], r["cost_tier"]) == (basis, horizon, tier)
                    ]
                    for rule in [r for r in RULES if r not in ("MO-02", "EV-01")]:
                        stats.append(
                            dict(
                                dimension="RULE",
                                value=rule,
                                entry_basis=basis,
                                horizon=horizon,
                                cost_tier=tier,
                                **metric([r for r in selected if rule in r["rule_refs"]]),
                            )
                        )
                    directional = [
                        r for r in selected if r["kind"] == "DIRECTION" and r["direction"] == "LONG"
                    ]
                    stats.append(
                        dict(
                            dimension="DIRECTIONAL_ECONOMIC_TOTAL",
                            value="LONG",
                            entry_basis=basis,
                            horizon=horizon,
                            cost_tier=tier,
                            **metric(directional),
                        )
                    )
                    for year in sorted({r["confirmation_market_date"][:4] for r in directional}):
                        stats.append(
                            dict(
                                dimension="DIRECTIONAL_YEAR",
                                value=year,
                                entry_basis=basis,
                                horizon=horizon,
                                cost_tier=tier,
                                **metric(
                                    [r for r in directional if r["confirmation_market_date"].startswith(year)]
                                ),
                            )
                        )
        lifecycle = []
        sessions = observed_security_sessions
        for rule in RULES:
            es = [e for e in all_events if e["rule_id"] == rule]
            lifecycle.append(
                {
                    "rule_id": rule,
                    "kind": RULES[rule][1],
                    "family": RULES[rule][0],
                    "evaluation_status": "NOT_EVALUABLE" if rule == "EV-01" else "EOD_DIAGNOSTIC_ONLY",
                    "reason_code": "EARNINGS_COVERAGE_MISSING" if rule == "EV-01" else None,
                    "triggered": None if rule == "EV-01" else len(es),
                    "confirmed": None if rule == "EV-01" else sum(bool(e.get("confirmed_at")) for e in es),
                    "invalidated": None
                    if rule == "EV-01"
                    else sum(e["lifecycle"] == "INVALIDATED" for e in es),
                    "expired": None if rule == "EV-01" else sum(e["lifecycle"] == "EXPIRED" for e in es),
                    "evaluable_security_sessions": assessment_counts[(rule, "EVALUATED")],
                    "not_evaluable_security_sessions": sessions
                    if rule == "EV-01"
                    else assessment_counts[(rule, "NOT_EVALUABLE")],
                }
            )
        atomic_text(output / "events.json", canonical(all_events))
        atomic_text(
            output / "events.csv",
            csv_text(
                [
                    {
                        k: v
                        for k, v in e.items()
                        if k not in ("state_history", "original_frozen", "unit_conversions")
                    }
                    for e in all_events
                ]
            ),
        )
        atomic_text(output / "outcomes.csv", csv_text(outcomes))
        atomic_text(output / "unconditional.json", canonical(unconditional))
        atomic_text(output / "statistics.json", canonical(stats))
        summary = {
            "kind": "LOCAL_EOD_EXPLORATORY_REPORT",
            "schema_version": 1,
            "mode": MODE,
            "generated_at": stamp(),
            "protocol": protocol,
            "coverage": {
                "requested_symbols": len(ids),
                "available_symbols": len(available),
                "missing_symbols": sorted(set(ids) - set(available)),
                "rows": sum(len(snapshot["bars"][i]) for i in available),
                "first_date": min(maps[available[0]]),
                "last_date": anchor,
                "start_date": start,
                "end_date": end,
                "evaluation_sessions": len(dates),
                "security_sessions": sessions,
                "nominal_calendar_security_sessions": len(dates) * len(available),
                "split_rebuilds": conversions,
                "quality_failures": dict(quality_failures),
            },
            "lifecycle": lifecycle,
            "event_count": len(all_events),
            "economic_event_count": len(groups),
            "outcome_status_counts": dict(Counter(r["status"] for r in outcomes)),
            "outcome_count": len(outcomes),
            "statistics": stats,
            "default_view": {"entry_basis": "NEXT_AVAILABLE_OPEN", "horizon": 20, "cost_tier": 10},
            "maturity": "EXPLORATORY_ONLY_NOT_APPROVED",
            "formal_validation_passed": False,
            "conclusion_status": "PENDING_STATISTICAL_REVIEW",
            "artifacts": [
                "events.json",
                "events.csv",
                "outcomes.csv",
                "statistics.json",
                "unconditional.json",
            ],
        }
        summary["business_output_hash"] = digest({k: v for k, v in summary.items() if k != "generated_at"})
        summary["execution"] = {
            "elapsed_seconds": round(time.monotonic() - began, 3),
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "isolation": dict(audit),
        }
        atomic_text(output / "report.json", canonical(summary))
    atomic_text(output / "isolation.json", canonical(audit))
    print(
        canonical(
            {
                "status": "COMPLETED",
                "mode": MODE,
                "events": len(all_events),
                "economic_events": len(groups),
                "outcomes": len(outcomes),
                "report": str(output / "report.json"),
                "execution": summary["execution"],
            }
        ),
        flush=True,
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", required=True, choices=[MODE])
    args = parser.parse_args()
    execute(args.manifest, args.output, mode=args.mode)


if __name__ == "__main__":
    main()
