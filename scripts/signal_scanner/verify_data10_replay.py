"""Independently reconcile frozen EOD replay returns, coverage and aggregate means."""

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.signal_scanner.core import canonical, digest
from src.services.signal_scanner.domain.features import adjust, build_features
from src.services.signal_scanner.research.inputs import ResearchCalendar


def verify(folder):
    folder = Path(folder).resolve()
    out = folder / "replay"
    r = json.loads((out / "report.json").read_text())
    events = json.loads((out / "events.json").read_text())
    s = json.loads((folder / "inputs/snapshot.json").read_text())
    m = json.loads((folder / "inputs/manifest.json").read_text())
    assert digest(s) == m["data_snapshot_ref"]["sha256"] == r["protocol"]["source_snapshot_sha256"]
    assert (
        digest({k: v for k, v in r.items() if k not in ("generated_at", "execution", "business_output_hash")})
        == r["business_output_hash"]
    )
    cal = ResearchCalendar()
    observed_sessions = sum(
        1
        for bars in s["bars"].values()
        for pos, bar in enumerate(bars)
        if pos + 1 >= m["warmup_bars"] and m["start_date"] <= bar["market_date"] <= m["end_date"]
    )
    for rule in r["lifecycle"]:
        if rule["rule_id"] != "EV-01":
            assert (
                rule["evaluable_security_sessions"] + rule["not_evaluable_security_sessions"]
                == observed_sessions
            )
    maps = {iid: {b["market_date"]: b for b in rows} for iid, rows in s["bars"].items()}
    actions = defaultdict(list)
    for event in s["events"]:
        if event["event_type"] == "SPLIT" and event["status"] != "CANCELLED":
            actions[event["instrument_id"]].append(event)

    def price(iid, day, field):
        value = Decimal(maps[iid][day][field])
        for e in actions[iid]:
            if day < e["effective_at"][:10] <= r["coverage"]["last_date"]:
                value /= Decimal(str(e["payload_json"]["ratio"]))
        return value

    statuses, stats, checked, split_rows = Counter(), defaultdict(list), 0, 0
    with (out / "outcomes.csv").open() as f:
        for row in csv.DictReader(f):
            statuses[row["status"]] += 1
            assert row["executable_eligibility"] == "BLOCKED"
            rules = json.loads(row["rule_refs"])
            assert "MO-02" not in rules
            if row["status"] != "COMPLETED":
                assert not row.get("net_return"), "Missing outcomes are not zero returns"
                continue
            iid, entry, end = row["instrument_id"], row["entry_market_date"], row["exit_market_date"]
            h, bps, basis = int(row["horizon"]), int(row["cost_tier"]), row["entry_basis"]
            assert end == cal.shift(entry, h - 1 if basis == "NEXT_AVAILABLE_OPEN" else h)
            assert entry == (
                cal.shift(row["confirmation_market_date"], 1)
                if basis == "NEXT_AVAILABLE_OPEN"
                else row["confirmation_market_date"]
            )
            field = "open" if basis == "NEXT_AVAILABLE_OPEN" else "close"
            slip = Decimal(bps) / 10000
            expected = price(iid, end, "close") * (1 - slip) / (price(iid, entry, field) * (1 + slip)) - 1
            assert abs(expected - Decimal(row["net_return"])) < Decimal("1e-24")
            spy = (
                price("US:SPY", end, "close") * (1 - slip) / (price("US:SPY", entry, field) * (1 + slip)) - 1
            )
            assert abs(expected - spy - Decimal(row["spy_excess_return"])) < Decimal("1e-24")
            checked += 1
            split_rows += int(any(entry < e["effective_at"][:10] <= end for e in actions[iid]))
            for rule in rules:
                stats[(rule, basis, h, bps)].append(Decimal(row["net_return"]))
            if row["kind"] == "DIRECTION" and row["direction"] == "LONG":
                stats[("TOTAL", basis, h, bps)].append(Decimal(row["net_return"]))
    assert sum(statuses.values()) == r["outcome_count"] == r["economic_event_count"] * 18
    assert dict(statuses) == r["outcome_status_counts"]
    assert len(events) == r["event_count"] == sum(x["triggered"] or 0 for x in r["lifecycle"])
    count = 0
    for stat in r["statistics"]:
        if stat["dimension"] not in ("RULE", "DIRECTIONAL_ECONOMIC_TOTAL"):
            continue
        key = (
            stat["value"] if stat["dimension"] == "RULE" else "TOTAL",
            stat["entry_basis"],
            stat["horizon"],
            stat["cost_tier"],
        )
        values = stats[key]
        assert stat["completed"] == len(values)
        if values:
            assert abs(sum(values) / len(values) - Decimal(stat["net_return"]["mean"])) < Decimal("1e-24")
            assert Decimal(stat["positive_net_fraction"]) == sum(x > 0 for x in values) / Decimal(len(values))
        count += 1
    selected = []
    for rule in ("TR-01", "TR-02", "TR-03", "VP-01", "VP-02", "VP-03", "VP-04", "VP-05", "MO-02", "RK-01"):
        candidates = [e for e in events if e["rule_id"] == rule]
        selected.extend(candidates[:: max(1, len(candidates) // 3)][:3])
    for event in selected:
        iid, day = event["instrument_id"], event["trigger_market_date"]
        prefix = [b for b in s["bars"][iid] if b["market_date"] <= day]
        visible = [a for a in actions[iid] if a["effective_at"][:10] <= day]
        feature = build_features(adjust(prefix, visible), cal, iid, "EOD_EXPLORATORY")[-1]
        for field, value in event["evidence"].items():
            if isinstance(value, (int, float)):
                assert math.isclose(value, feature[field], rel_tol=1e-10, abs_tol=1e-10)
            else:
                assert value == feature[field]
    isolation = json.loads((out / "isolation.json").read_text())
    assert (
        isolation["network_attempts"]
        == isolation["subprocess_attempts"]
        == isolation["forbidden_writes"]
        == 0
    )
    result = {
        "status": "PASS",
        "nominal_security_calendar_slots": r["coverage"].get("nominal_calendar_security_sessions", r["coverage"]["security_sessions"]),
        "observed_security_sessions_after_warmup": observed_sessions,
        "coverage_note": "Actual sessions exclude short histories, warmup, and missing tails; nominal slots are reported separately.",
        "report_business_hash_verified": True,
        "snapshot_hash_verified": True,
        "independently_recomputed_completed_returns": checked,
        "outcome_statuses": dict(statuses),
        "cross_split_return_rows": split_rows,
        "reconciled_statistic_groups": count,
        "batch_vs_streaming_trigger_samples": len(selected),
        "isolation": isolation,
    }
    (folder / "verification.json").write_text(canonical(result))
    print(canonical(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    verify(parser.parse_args().folder)
