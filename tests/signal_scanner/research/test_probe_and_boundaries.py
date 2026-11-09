import asyncio
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from src.services.signal_scanner.cli import fixture
from src.services.signal_scanner.core import DomainError, Settings, instant, stamp
from src.services.signal_scanner.domain.rules import RULESET, condition
from src.services.signal_scanner.research.experiments import split_manifest
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.research.inputs import InputView, clock_for
from src.services.signal_scanner.research.isolation import offline
from src.services.signal_scanner.research.outcomes import label, returns
from src.services.signal_scanner.research.probe import probe_snapshot
from src.services.signal_scanner.research.runner import run, validate_input
from src.services.signal_scanner.worker.jobs import Worker
from src.services.signal_scanner.worker.pipeline import latest_workspace

from .test_rules import feature


def test_probe_four_statuses_integrity_and_semantic_difference(tmp_path):
    settings = Settings(tmp_path / "production-copy")
    fixture(settings)
    worker = Worker(settings)
    with worker.db.read() as cx:
        scan, workspace = latest_workspace(cx, worker.files)
    assert probe_snapshot(worker.files, None, worker.cal)["status"] == "SKIPPED"
    assert probe_snapshot(worker.files, scan, worker.cal)["status"] == "PASS"
    m = worker.files.read(scan["input_manifest_ref"])
    old = deepcopy(m)
    old["algorithm_version"] = "missing-algorithm"
    version_scan = {**scan, "input_manifest_ref": worker.files.save(old)}
    assert probe_snapshot(worker.files, version_scan, worker.cal)["status"] == "VERSION_UNAVAILABLE"
    expected = {**workspace, "business_output_hash": "wrong"}
    wrong = {**scan, "output_manifest_ref": worker.files.save(expected)}
    assert probe_snapshot(worker.files, wrong, worker.cal)["reason"] == "REPLAY_MISMATCH"
    path = worker.files.root / scan["input_manifest_ref"]
    path.write_text("{}")
    assert probe_snapshot(worker.files, scan, worker.cal)["reason"] == "ARTIFACT_CORRUPT"
    path.unlink()
    assert probe_snapshot(worker.files, scan, worker.cal)["reason"] == "MISSING_REFERENCED_INPUT"


def test_async_http_is_blocked(tmp_path):
    async def request():
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("http://127.0.0.1:1")

    # Create the event loop's local self-pipe before activating the network guard.
    loop = asyncio.new_event_loop()
    try:
        with offline(tmp_path) as observed:
            with pytest.raises(BaseExceptionGroup) as error:
                loop.run_until_complete(request())
            assert error.value.subgroup(DomainError) is not None
        assert observed["network_attempts"] > 0
    finally:
        loop.close()


def test_split_price_units_and_dividend_basis(tmp_path, cal):
    p = Path(create_fixture(tmp_path / "input", symbols=1, sessions=30))
    m, s, _ = validate_input(p)
    split = split_manifest(m, cal)
    start = m["start_date"]
    cut = cal.shift(start, 3)
    original = label(
        "SYN000",
        start,
        clock_for(start, m, cal),
        "NEXT_AVAILABLE_OPEN",
        5,
        m,
        InputView(s, m, cal),
        cal,
        split,
    )
    changed = deepcopy(s)
    for b in changed["bars"]["SYN000"]:
        if b["market_date"] >= cut:
            for k in ("open", "high", "low", "close"):
                b[k] = str(Decimal(b[k]) / 2)
            b["volume"] = str(Decimal(b["volume"]) * 2)
    changed["events"].append(
        dict(
            event_id="split",
            revision=1,
            event_type="SPLIT",
            instrument_id="SYN000",
            known_at=cal.session(cut)["open_at"],
            effective_at=cal.session(cut)["open_at"],
            payload_json={"ratio": "2"},
            status="ACTIVE",
        )
    )
    after = label(
        "SYN000",
        start,
        clock_for(start, m, cal),
        "NEXT_AVAILABLE_OPEN",
        5,
        m,
        InputView(changed, m, cal),
        cal,
        split,
    )
    assert returns(original["entry_price"], original["exit_price"], 5, m["cost_assumption"]) == returns(
        after["entry_price"], after["exit_price"], 5, m["cost_assumption"]
    )
    changed["events"].append(
        dict(
            event_id="dividend",
            revision=1,
            event_type="DIVIDEND",
            instrument_id="SYN000",
            known_at=cal.session(cut)["open_at"],
            effective_at=cal.session(cut)["open_at"],
            payload_json={"price_factor": "0.99"},
            status="ACTIVE",
        )
    )
    price = label(
        "SYN000",
        start,
        clock_for(start, m, cal),
        "NEXT_AVAILABLE_OPEN",
        5,
        m,
        InputView(changed, m, cal),
        cal,
        split,
    )
    assert price["entry_price"] == after["entry_price"]
    total = deepcopy(m)
    total["price_basis"]["return_kind"] = "TOTAL_RETURN"
    tr = label(
        "SYN000",
        start,
        clock_for(start, m, cal),
        "NEXT_AVAILABLE_OPEN",
        5,
        total,
        InputView(changed, total, cal),
        cal,
        split,
    )
    assert float(tr["entry_price"]) < float(price["entry_price"])


@pytest.mark.parametrize("rule", list(RULESET))
def test_every_rule_null_price_fails_safely(rule):
    assert condition(rule, feature(close=None))[0] is None


def test_expired_broken_parent_not_reused():
    parent = dict(
        event_id="p",
        rule_id="VP-01",
        confirmation_market_date="2025-01-02",
        lifecycle="EXPIRED",
        sessions_since_confirmation=5,
        structure_broken=True,
        frozen={"level": 100},
    )
    assert condition("TR-03", feature(low=100, rv=0.8), [parent])[0] is False


def test_ev01_full_session_equality_early_close_and_holiday(cal):
    assert cal.session("2025-01-09") is None
    d = "2025-11-28"
    assert cal.session(d)["is_early_close"]
    assert cal.first_full_after(cal.session(d)["open_at"]) == cal.shift(d, 1)
    from datetime import timedelta

    before = stamp(instant(cal.session(d)["open_at"]) - timedelta(seconds=1))
    assert cal.first_full_after(before) == d


def test_production_call_paths_are_not_used(tmp_path, monkeypatch):
    p = create_fixture(tmp_path / "input", symbols=1, sessions=10)
    import src.services.signal_scanner.domain.portfolio
    import src.services.signal_scanner.services.accounts
    import src.services.signal_scanner.worker.pipeline

    def forbidden(*a, **kw):
        pytest.fail("Production/portfolio side effect path reached")

    monkeypatch.setattr(src.services.signal_scanner.domain.portfolio, "simulate", forbidden)
    monkeypatch.setattr(src.services.signal_scanner.services.accounts, "rebuild", forbidden)
    monkeypatch.setattr(src.services.signal_scanner.worker.pipeline, "persist", forbidden)
    monkeypatch.setattr(Worker, "research", forbidden)
    assert run(p, tmp_path / "research")["status"] == "SUCCEEDED"


def test_action_known_but_not_yet_effective_is_not_applied(cal):
    from src.services.signal_scanner.research.inputs import effective_actions

    day = "2025-01-02"
    clock = cal.session(day)["close_at"]
    from datetime import timedelta

    action = dict(
        event_id="future-same-day",
        revision=1,
        event_type="SPLIT",
        instrument_id="TEST",
        known_at=cal.session(day)["open_at"],
        effective_at=stamp(instant(clock) + timedelta(hours=1)),
        payload_json={"ratio": "2"},
    )
    assert effective_actions([action], "TEST", clock) == []
    assert effective_actions([action], "TEST", action["effective_at"]) == [action]


def test_short_history_reports_partial_and_reason(tmp_path):
    from src.services.signal_scanner.core import canonical

    p = Path(create_fixture(tmp_path / "input", symbols=2, sessions=10))
    m, s, cal = validate_input(p)
    s["bars"]["SYN001"] = [b for b in s["bars"]["SYN001"] if b["market_date"] >= m["start_date"]]
    content = canonical(s)
    (p.parent / "snapshot.json").write_text(content)
    import hashlib

    m["data_snapshot_ref"]["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    p.write_text(canonical(m))
    result = run(p, tmp_path / "research")
    report = json.loads((tmp_path / "research" / result["report_ref"]).read_text())
    assert result["status"] == "PARTIAL"
    assert report["coverage"]["failure_reasons"].get("INSUFFICIENT_HISTORY", 0) > 0
