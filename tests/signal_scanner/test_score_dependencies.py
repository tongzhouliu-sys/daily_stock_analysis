"""Regression coverage for missing context and reference-only securities."""

import copy

from src.services.signal_scanner.core import Settings
from src.services.signal_scanner.cli import fixture
from src.services.signal_scanner.domain.scoring import aggregate
from src.services.signal_scanner.worker.jobs import Worker
from src.services.signal_scanner.worker.pipeline import calculate, latest_workspace
from tests.signal_scanner.test_golden import event, feature


def card_context(profile=None, asset_type="COMMON"):
    features = {"TEST": feature(), "SECTOR": feature(close=90, ma50=100), "SPY": feature()}
    instruments = [dict(id="TEST", asset_type=asset_type, sector_code="TECH", profile_json=profile or {})]
    e = event(kind="DIRECTION", family="BREAKOUT")
    return features, instruments, e


def test_missing_references_report_specific_inputs_and_never_invent_score():
    fs, instruments, e = card_context()
    instruments[0]["sector_code"] = None
    c = aggregate([e], fs, instruments, {"mode": "NORMAL"}, "2026-10-22T21:00:00.000Z")[0]
    assert c["quality_score"] is None and c["quality_grade"] == "WATCH"
    assert c["context"]["score_missing_inputs"] == [
        "sector_mapping",
        "sector_reference",
        "market_reference",
        "relative_strength",
    ]
    assert "SCORE_INCOMPLETE" in c["reason_codes"]


def test_complete_context_with_zero_participation_still_scores():
    fs, instruments, e = card_context(
        dict(sector_etf_id="SECTOR", market_ref_id="SPY", relative_strength_above_ma20=False)
    )
    c = aggregate([e], fs, instruments, {"mode": "NORMAL"}, "2026-10-22T21:00:00.000Z")[0]
    assert c["quality_score"] is not None
    assert c["context"]["score_missing_inputs"] == []
    assert next(x for x in c["score_trace"] if x["check"] == "participation")["points"] == 0


def test_warning_remains_unscored_without_false_missing_data():
    fs, instruments, e = card_context()
    e["kind"] = "WARNING"
    c = aggregate([e], fs, instruments, {"mode": "NORMAL"}, "2026-10-22T21:00:00.000Z")[0]
    assert c["quality_score"] is None and c["severity"] == "CAUTION"
    assert c["context"]["score_missing_inputs"] == []
    assert "SCORE_INCOMPLETE" not in c["reason_codes"]


def test_etf_requires_two_external_comparators():
    fs, instruments, e = card_context(dict(comparators=["TEST", "SECTOR"]), "ETF")
    c = aggregate([e], fs, instruments, {"mode": "NORMAL"}, "2026-10-22T21:00:00.000Z")[0]
    assert c["quality_score"] is None
    assert c["context"]["score_missing_inputs"] == ["comparators"]
    instruments[0]["profile_json"]["comparators"] = ["SECTOR", "SPY"]
    c = aggregate([e], fs, instruments, {"mode": "NORMAL"}, "2026-10-22T21:00:00.000Z")[0]
    assert c["quality_score"] is not None


def test_reference_securities_supply_features_without_rules_or_peer_signals(tmp_path):
    settings = Settings(tmp_path)
    fixture(settings)
    worker = Worker(settings)
    with worker.db.read() as cx:
        job, _ = latest_workspace(cx, worker.files)
    manifest = copy.deepcopy(worker.files.read(job["input_manifest_ref"]))
    out = calculate(manifest, worker.cal)
    members = set(manifest["universe"]["value"]["members"])
    refs = set(out["features"]) - members
    assert refs
    assert all(e["instrument_id"] in members for e in out["events"])
    assert set(out["rule_evaluability"]) == members
    assert out["coverage"]["expected"] == len(members)
