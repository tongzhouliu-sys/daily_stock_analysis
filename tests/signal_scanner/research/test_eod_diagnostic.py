"""Meaningful boundaries of the explicit local EOD diagnostic wrapper."""

import importlib.util
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from src.services.signal_scanner.core import DomainError
from src.services.signal_scanner.domain.rules import RuleEngine
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.research.inputs import ResearchCalendar
from src.services.signal_scanner.research.runner import validate_input

spec = importlib.util.spec_from_file_location(
    "eod_diagnostic", Path(__file__).resolve().parents[3] / "scripts" / "signal_scanner" / "replay_local_eod.py"
)
eod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eod)


def action(day, ratio):
    return {
        "event_id": "split",
        "revision": 1,
        "event_type": "SPLIT",
        "effective_at": day + "T13:30:00.000Z",
        "known_at": "2026-10-25T12:00:00.000Z",
        "payload_json": {"ratio": ratio},
    }


def test_split_crossing_label_preserves_eod_and_known_at():
    cal = ResearchCalendar()
    days = cal.dates("2024-06-07", "2024-06-14")
    bars = {
        day: {
            "market_date": day,
            "open": "100" if n == 0 else "10",
            "close": "100" if n == 0 else "11",
            "session_kind": "EOD_DAILY",
        }
        for n, day in enumerate(days)
    }
    actions = [action("2024-06-10", "10")]
    before = deepcopy(actions)
    row = eod.label("A", days[0], "CONFIRM_CLOSE", 5, {"A": bars}, {"A": actions}, cal, days[-1])
    assert row["status"] == "COMPLETED"
    assert Decimal(row["entry_price"]) == Decimal(10)
    assert Decimal(row["exit_price"]) == Decimal(11)
    assert Decimal(
        eod.returns(row["entry_price"], row["exit_price"], 0, eod.COST)["gross_return"]
    ) == Decimal("0.1")
    assert actions == before
    assert all(b["session_kind"] == "EOD_DAILY" for b in bars.values())
    pending = eod.label("A", days[-1], "NEXT_AVAILABLE_OPEN", 5, {"A": bars}, {"A": actions}, cal, days[-1])
    assert pending["status"] == "PENDING" and "net_return" not in pending


def test_economic_attribution_dedup_and_evidence_exclusion():
    base = {
        "instrument_id": "A",
        "direction": "LONG",
        "family": "BREAKOUT",
        "trigger_market_date": "2026-01-05",
        "confirmed_at": "2026-01-05T21:30:00Z",
        "kind": "DIRECTION",
    }
    events = [
        {**base, "rule_id": "TR-01"},
        {**base, "rule_id": "VP-01"},
        {**base, "rule_id": "MO-02", "kind": "EVIDENCE"},
    ]
    grouped = eod.group_events(events)
    assert len(grouped) == 1
    assert grouped[0]["primary"]["rule_id"] == "VP-01"
    assert grouped[0]["rule_refs"] == ["TR-01", "VP-01"]


def test_active_event_units_rebased_once_source_frozen_preserved():
    cal = ResearchCalendar()
    engine = RuleEngine(cal)
    original = {"level": 100.0, "close": 101.0, "atr": 2.0, "price_factor": "1"}
    engine.events = [
        {
            "rule_id": "TR-01",
            "lifecycle": "CONFIRMED",
            "trigger_market_date": "2024-06-07",
            "confirmation_market_date": "2024-06-07",
            "frozen": deepcopy(original),
        }
    ]
    prefix = [
        dict(market_date=d, **{k: v for k in ("open", "high", "low", "close")}, volume="100")
        for d, v in (("2024-06-07", "100"), ("2024-06-10", "10"))
    ]
    actions = [action("2024-06-10", "10")]
    eod.rebase_events(engine, prefix, actions, "2024-06-10")
    event = engine.events[0]
    assert event["frozen"]["level"] == 10
    assert event["frozen"]["atr"] == pytest.approx(0.2)
    assert event["original_frozen"] == original
    eod.rebase_events(engine, prefix, actions, "2024-06-10")
    assert len(event["unit_conversions"]) == 1
    assert event["unit_conversions"][0]["source_action_refs"][0]["known_at"] == actions[0]["known_at"]


def test_no_samples_and_warning_returns_are_not_fabricated_or_short_flipped():
    empty = eod.metric([])
    assert empty["net_return"]["mean"] is None
    assert empty["positive_net_fraction"] is None
    result = eod.returns("100", "90", 10, eod.COST)
    m = eod.metric(
        [dict(status="COMPLETED", instrument_id="A", confirmation_market_date="2026-01-05", **result)]
    )
    assert Decimal(m["net_return"]["mean"]) < 0
    assert m["negative_gross_fraction"] == "1"
    assert m["status"] == "INSUFFICIENT"


def test_formal_rth_validation_still_rejects_eod(tmp_path):
    import json

    path = Path(create_fixture(tmp_path / "input", symbols=1, sessions=20))
    manifest = json.loads(path.read_text())
    snap_path = path.parent / "snapshot.json"
    snapshot = json.loads(snap_path.read_text())
    snapshot.update(session_kind="EOD_DAILY", rth_evidence=None)
    snap_path.write_text(eod.canonical(snapshot))
    manifest["data_snapshot_ref"]["sha256"] = eod.file_hash(snap_path)
    path.write_text(eod.canonical(manifest))
    with pytest.raises(DomainError) as exc:
        validate_input(path)
    assert exc.value.code == "RTH_UNVERIFIED"
    with pytest.raises(ValueError, match="Explicit EOD_EXPLORATORY"):
        eod.execute(path, tmp_path / "output", mode="POINT_IN_TIME")
