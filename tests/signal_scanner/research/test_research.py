import hashlib
import json
import socket
import subprocess
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from src.services.signal_scanner.core import DomainError, canonical, instant, stamp
from src.services.signal_scanner.research.contracts import REQUIRED, validate
from src.services.signal_scanner.research.experiments import (
    assignment,
    register,
    split_manifest,
)
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.research.inputs import InputView, clock_for, read_snapshot, validate_snapshot
from src.services.signal_scanner.research.isolation import offline
from src.services.signal_scanner.research.outcomes import label, returns
from src.services.signal_scanner.research.reporting import business_hash
from src.services.signal_scanner.research.runner import replay, run, validate_input
from src.services.signal_scanner.research.statistics import quantile, summarize
from src.services.signal_scanner.research.versions import rules_version


@pytest.fixture
def sample(tmp_path):
    p = Path(create_fixture(tmp_path / "input", symbols=1, sessions=40))
    m, s, cal = validate_input(p)
    return p, m, s, cal


def write_input(p, m, s):
    content = canonical(s)
    m["data_snapshot_ref"]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    (p.parent / "snapshot.json").write_text(content)
    p.write_text(canonical(m))


@pytest.mark.parametrize("key", REQUIRED)
def test_required_inputs_fail_closed(sample, key):
    _, m, _, cal = sample
    m.pop(key)
    with pytest.raises(DomainError, match="MISSING_REQUIRED_INPUT"):
        validate(m, cal)


def test_unknown_version_and_unregistered_parameter(sample):
    _, m, _, cal = sample
    m["ruleset_version"] = "invented"
    with pytest.raises(DomainError, match="VERSION_UNAVAILABLE"):
        validate(m, cal)
    m["ruleset_version"] = rules_version()
    m["parameter_overrides"] = {"TR-01": {"rv_min": 1.8}}
    with pytest.raises(DomainError, match="UNREGISTERED_PARAMETER"):
        validate(m, cal)
    m["registration"]["allowed_variations"] = {"TR-01": {"rv_min": [1.2, 1.5, 1.8]}}
    m["ruleset_version"] = rules_version(m["parameter_overrides"])
    assert validate(m, cal)


def test_pit_missing_availability_and_rth_fail_closed(sample):
    _, m, s, cal = sample
    s["bars"]["SYN000"][0]["available_at"] = None
    with pytest.raises(DomainError, match="POINT_IN_TIME_HISTORY_UNVERIFIED"):
        validate_snapshot(s, m, cal)
    m["research_mode"] = "REVISED_HISTORY"
    assert validate_snapshot(s, m, cal)
    s["session_kind"] = "EOD_DAILY"
    with pytest.raises(DomainError, match="RTH_UNVERIFIED"):
        validate_snapshot(s, m, cal)


def test_asof_revision_latest_invalid_is_not_fallback(sample):
    _, m, s, cal = sample
    b = deepcopy(s["bars"]["SYN000"][280])
    day = b["market_date"]
    clock = clock_for(day, m, cal)
    b.update(revision=2, quality_status="BAD", available_at=stamp(instant(clock) + timedelta(days=2)))
    s["bars"]["SYN000"].append(b)
    v = InputView(s, m, cal)
    rows = v.bars("SYN000", day, clock)
    assert rows[-1]["revision"] == 1
    rows = v.bars("SYN000", day, b["available_at"])
    assert rows[-1]["revision"] == 2
    assert v.quality(rows, day) == "LATEST_REVISION_INVALID"


def test_no_future_revisions_events_or_actions_change_past(sample):
    _, m, s, cal = sample
    with offline():
        a = replay(m, s, cal)
    changed = deepcopy(s)
    changed["bars"]["SYN000"][-1]["close"] = "1000"
    future = deepcopy(changed["bars"]["SYN000"][285])
    future.update(
        revision=2, close="777", high="778", available_at=stamp(instant(m["label_as_of"]) + timedelta(days=1))
    )
    changed["bars"]["SYN000"].append(future)
    changed["events"].append(
        dict(
            event_id="future-split",
            revision=1,
            event_type="SPLIT",
            instrument_id="SYN000",
            known_at=stamp(instant(m["label_as_of"]) + timedelta(days=1)),
            effective_at=cal.session(m["start_date"])["open_at"],
            payload_json={"ratio": "10"},
            status="ACTIVE",
        )
    )
    with offline():
        b = replay(m, changed, cal)
    assert a["events"] == b["events"]
    assert a["daily"] == b["daily"]
    assert a["confirmation_evidence"] == b["confirmation_evidence"]


def test_determinism_idempotency_isolation_and_frozen_reproduction(sample, tmp_path):
    p, m, s, _ = sample
    before = p.read_bytes(), (p.parent / "snapshot.json").read_bytes()
    result = run(p, tmp_path / "research")
    same = run(p, tmp_path / "research")
    assert same["reused"] and same["id"] == result["id"]
    report = json.loads((tmp_path / "research" / result["report_ref"]).read_text())
    assert report["business_output_hash"] == business_hash(report)
    assert not report["execution"]["network_attempts"]
    assert report["data_kind"] == "SYNTHETIC"
    again = run(p, tmp_path / "research2")
    assert result["business_output_hash"] == again["business_output_hash"]
    assert before == (p.read_bytes(), (p.parent / "snapshot.json").read_bytes())
    frozen = next((tmp_path / "research" / "frozen").iterdir()) / "manifest.json"
    third = run(frozen, tmp_path / "research3")
    assert third["business_output_hash"] == result["business_output_hash"]
    m["cost_assumption"]["entry_fee"] = "1"
    write_input(p, m, s)
    with pytest.raises(DomainError, match="IDEMPOTENCY_INPUT_CONFLICT"):
        run(p, tmp_path / "research")
    from src.services.signal_scanner.storage.db import Database, many

    with Database(tmp_path / "research" / "scanner.sqlite3").read() as cx:
        for table in (
            "signal_cards",
            "rule_events",
            "account_state",
            "ledger_entries",
            "plans",
            "notifications",
            "config_versions",
        ):
            assert not many(cx, "SELECT * FROM " + table)
        assert all(j["published_seq"] is None for j in many(cx, "SELECT * FROM job_runs"))


def test_runtime_production_and_symlink_refused(sample, tmp_path):
    p, _, _, _ = sample
    prod = tmp_path / "production"
    prod.mkdir()
    (prod / "scanner.sqlite3").write_text("production-marker")
    with pytest.raises(DomainError, match="NOT_RESEARCH_RUNTIME"):
        run(p, prod)
    link = tmp_path / "link"
    link.symlink_to(prod, target_is_directory=True)
    with pytest.raises(DomainError, match="OUTPUT_PATH_FORBIDDEN"):
        run(p, link)
    assert (prod / "scanner.sqlite3").read_text() == "production-marker"


def test_network_subprocess_files_and_escaped_symlink_are_blocked(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "escape").symlink_to(outside, target_is_directory=True)
    with offline(allowed) as obs:
        for fn in (
            lambda: socket.create_connection(("127.0.0.1", 1)),
            lambda: httpx.get("http://localhost:1"),
            lambda: httpx.post("http://localhost:1"),
            lambda: subprocess.run(["true"]),
            lambda: (outside / "bad").write_text("bad"),
            lambda: (allowed / "escape" / "bad").write_text("bad"),
        ):
            with pytest.raises(DomainError):
                fn()
        (allowed / "ok").write_text("ok")
    assert obs["network_attempts"] >= 3 and obs["subprocess_attempts"] == 1 and obs["forbidden_writes"] == 2
    assert not (outside / "bad").exists()


def test_input_integrity_and_paths(sample):
    p, m, _, _ = sample
    with pytest.raises(DomainError, match="INPUT_PATH_FORBIDDEN"):
        read_snapshot(p.parent, {"path": "../escape", "sha256": "0" * 64})
    (p.parent / "snapshot.json").write_text("{}")
    with pytest.raises(DomainError, match="ARTIFACT_CORRUPT"):
        read_snapshot(p.parent, m["data_snapshot_ref"])


def test_labels_pending_unresolved_next_open_and_costs(sample):
    _, m, s, cal = sample
    split = split_manifest(m, cal)
    view = InputView(s, m, cal)
    day = m["start_date"]
    clock = clock_for(day, m, cal)
    r = label("SYN000", day, clock, "NEXT_AVAILABLE_OPEN", 5, m, view, cal, split)
    assert r["entry_market_date"] == cal.shift(day, 1) and r["exit_market_date"] == cal.shift(day, 5)
    assert r["status"] == "COMPLETED"
    late = cal.session(cal.shift(day, 1))["open_at"]
    r2 = label("SYN000", day, late, "NEXT_AVAILABLE_OPEN", 5, m, view, cal, split)
    assert r2["entry_market_date"] == cal.shift(day, 2)
    short = {**m, "label_as_of": clock_for(cal.shift(day, 3), m, cal)}
    assert (
        label("SYN000", day, clock, "NEXT_AVAILABLE_OPEN", 5, short, InputView(s, short, cal), cal, split)[
            "status"
        ]
        == "PENDING"
    )
    broken = deepcopy(s)
    broken["bars"]["SYN000"] = [b for b in broken["bars"]["SYN000"] if b["market_date"] != cal.shift(day, 2)]
    unresolved = label(
        "SYN000", day, clock, "NEXT_AVAILABLE_OPEN", 5, m, InputView(broken, m, cal), cal, split
    )
    assert unresolved["status"] == "UNRESOLVED" and unresolved["net_return"] is None
    values = [returns("100", "110", cost, m["cost_assumption"]) for cost in (5, 10, 20)]
    assert values[0]["gross_return"] == "0.1"
    assert (
        Decimal(values[0]["net_return"]) > Decimal(values[1]["net_return"]) > Decimal(values[2]["net_return"])
    )
    assert Decimal(returns("100", "100", 5, m["cost_assumption"])["net_return"]) < 0


def test_statistics_excludes_pending_unresolved_and_excluded():
    base = dict(
        horizon=5,
        entry_basis="NEXT_AVAILABLE_OPEN",
        cost_tier=5,
        split="EXPLORATORY",
        direction="LONG",
        research_mode="POINT_IN_TIME",
        confirmation_market_date="2025-01-02",
        sector="TECH",
        asset_type="COMMON",
        rule_refs=[dict(rule_id="TR-01", rule_version="1"), dict(rule_id="VP-01", rule_version="1")],
    )
    rows = [
        {
            **base,
            "status": status,
            "gross_return": "0.1" if status == "COMPLETED" else None,
            "net_return": "0.09" if status == "COMPLETED" else None,
        }
        for status in ("COMPLETED", "PENDING", "UNRESOLVED", "EXCLUDED")
    ]
    stats = summarize(rows, 30)
    total = next(r for r in stats if r["dimension"] == "ECONOMIC_TOTAL")
    assert (
        total["completed"] == 1
        and total["pending"] == 1
        and total["unresolved"] == 1
        and total["excluded"] == 1
    )
    assert total["status"] == "INSUFFICIENT" and total["net"]["mean"] == "0.09"
    assert quantile(["0", "10"], 0.25) == Decimal("2.5")


def test_temporal_split_embargo_and_sealed_reuse(sample, tmp_path):
    _, m, _, cal = sample
    m["start_date"] = "2020-01-02"
    m["end_date"] = "2026-10-23"
    m["split_policy"] = {"kind": "CHRONOLOGICAL_60_20_20", "embargo_sessions": 20}
    split = split_manifest(m, cal)
    assert len(split["embargo"]) == 40
    assert assignment(split, split["sets"]["SEALED"][0]) == "SEALED"
    runtime = tmp_path / "registry"
    runtime.mkdir()
    register(runtime, m, split, True)
    changed = deepcopy(m)
    changed["registration"]["id"] = "new-tuning"
    with pytest.raises(DomainError, match="SEALED_RANGE_ALREADY_REVEALED"):
        register(runtime, changed, split, True)


def test_report_immutable_across_run_identity_and_detects_csv_corruption(sample, tmp_path):
    p, m, s, cal = sample
    first = run(p, tmp_path / "research")
    folder = tmp_path / "research" / Path(first["report_ref"]).parent
    original = (folder / "report.json").read_bytes()
    m["logical_key"] = "another-audit-identity"
    m["actor"] = "another-local-reviewer"
    write_input(p, m, s)
    second = run(p, tmp_path / "research")
    assert first["business_output_hash"] == second["business_output_hash"]
    assert (folder / "report.json").read_bytes() == original
    (folder / "events.csv").write_text("tampered")
    with pytest.raises(DomainError, match="ARTIFACT_CORRUPT"):
        run(p, tmp_path / "research")


def test_known_late_membership_not_backfilled(sample):
    _, m, s, cal = sample
    from src.services.signal_scanner.research.inputs import active_members, attributes

    day = m["start_date"]
    clock = clock_for(day, m, cal)
    s["universe"][0]["known_at"] = m["label_as_of"]
    assert "SYN000" not in active_members(s, day, clock)
    inst = next(i for i in s["instruments"] if i["id"] == "SYN000")
    inst["history"][0]["known_at"] = m["label_as_of"]
    assert attributes(s, "SYN000", day, clock)["sector"] == "UNKNOWN"


def test_atomic_publication_failure_can_resume(sample, tmp_path, monkeypatch):
    p, _, _, _ = sample
    import src.services.signal_scanner.research.reporting as reporting

    original = reporting.atomic_text

    def fail_index(path, content):
        if Path(path).name == "files.json":
            raise DomainError("SIMULATED_WRITE_FAILURE")
        return original(path, content)

    monkeypatch.setattr(reporting, "atomic_text", fail_index)
    with pytest.raises(DomainError, match="SIMULATED_WRITE_FAILURE"):
        run(p, tmp_path / "research")
    monkeypatch.setattr(reporting, "atomic_text", original)
    resumed = run(p, tmp_path / "research")
    assert resumed["status"] == "SUCCEEDED"
    assert run(p, tmp_path / "research")["reused"]


def test_dual_breakout_deduplicated_and_identity_benchmark(tmp_path):
    p = Path(create_fixture(tmp_path / "input", symbols=1, sessions=150))
    m, s, cal = validate_input(p)
    # Force a precise, causally visible strong breakout after warmup.
    day = cal.shift(m["start_date"], 30)
    rows = s["bars"]["SYN000"]
    index = next(i for i, b in enumerate(rows) if b["market_date"] == day)
    row = rows[index]
    row.update(open="150", high="161", low="149", close="160", volume="10000000")
    m["benchmark_ref"]["market"] = "SYN000"
    write_input(p, m, s)
    result = run(p, tmp_path / "research")
    report = json.loads((tmp_path / "research" / result["report_ref"]).read_text())
    double = [
        g for g in report["aggregation"] if {r["rule_id"] for r in g["rule_refs"]} == {"TR-01", "VP-01"}
    ]
    assert double
    group = double[0]
    assert group["evidence_groups"] == ["PRICE"]
    labels = [r for r in report["outcomes"] if r["economic_event_id"] == group["economic_event_id"]]
    assert len(labels) == 18  # two entry conventions, three horizons, three cost tiers
    paired = [
        b
        for b in report["benchmarks"]
        if b["economic_event_id"] == group["economic_event_id"]
        and b["kind"] == "SPY"
        and b["status"] == "PAIRED"
    ]
    assert paired and all(Decimal(b["excess_return"]) == 0 for b in paired)
    assert all(a["market_date"] <= m["end_date"] for a in report["assessments"])
    assert all(
        sum(a["count"] for a in report["assessments"] if a["rule_id"] == rule) == 150
        for rule in ("TR-01", "VP-01")
    )


def test_archived_source_and_dependencies_can_reproduce_in_separate_process(sample, tmp_path):
    p, m, s, cal = sample
    result = run(p, tmp_path / "research")
    frozen = next((tmp_path / "research" / "frozen").iterdir()) / "manifest.json"
    archived = tmp_path / "research" / "source" / m["code_hash"]
    assert (archived / "src" / "services" / "signal_scanner" / "storage" / "schema.sql").is_file()
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": str(archived), "PYTHONDONTWRITEBYTECODE": "1"}
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.services.signal_scanner.research.cli",
            "run",
            str(frozen),
            "--runtime",
            str(tmp_path / "restored"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert output.returncode == 0, output.stderr
    assert json.loads(output.stdout)["business_output_hash"] == result["business_output_hash"]
