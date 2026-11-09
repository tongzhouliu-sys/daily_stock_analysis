import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from src.services.signal_scanner.core import instant, stamp
from src.services.signal_scanner.domain.features import StreamingFeatures, build_features
from src.services.signal_scanner.domain.rules import RULESET, RuleEngine, condition
from src.services.signal_scanner.research.fixtures import create_fixture


def feature(**extra):
    value = dict(
        instrument_id="TEST",
        count=280,
        source_market_date="2025-01-02",
        evaluated_at="2025-01-02T21:30:00.000Z",
        close=101.01,
        open=100,
        high=102,
        low=99,
        volume=200,
        prev_close=100,
        prev_volume=100,
        ma10=100,
        ma50=98,
        ma50_prev5=97,
        ma10_prev=97,
        ma50_prev=98,
        atr=2,
        atr_prev=2,
        atr_median60=2,
        adx=25,
        adx_prev=26,
        rsi=40,
        rsi_prev=29,
        macd=1,
        macd_prev=-1,
        macd_signal=0,
        macd_signal_prev=0,
        rv=1.5,
        body=0.7,
        clv=0.8,
        weekly="BULLISH",
        b20=100,
        b60=100,
        return20=0.12,
        close3=[98, 99, 100],
        volume3=[300, 200, 100],
        volume_mean20=200,
        adjustment_epoch="none",
    )
    value.update(extra)
    return value


CASES = [
    ("TR-01", {}, dict(close=101), "b20"),
    ("TR-02", {}, dict(ma10=98), "ma10"),
    ("TR-03", dict(low=100, close=101, rv=0.8, clv=0.6), dict(low=98), "atr_prev"),
    ("VP-01", dict(rv=2), dict(rv=1.99), "b60"),
    ("VP-02", dict(rv=0.99), dict(rv=1), "b20"),
    ("VP-03", dict(rv=2, body=0.39, clv=0.6), dict(body=0.4), "return20"),
    ("VP-04", dict(high=103, low=99, rv=0.8, body=0.6), dict(high=102, low=99), "atr_prev"),
    ("VP-05", {}, dict(volume3=[300, 200, 200]), "ma50"),
    ("MO-01", dict(adx=19, rsi=30), dict(adx=20), "rsi"),
    ("MO-02", {}, dict(macd=0), "macd"),
    ("EV-01", dict(open=104, close=105, rv=2), dict(open=102), "prev_close"),
    ("RK-01", dict(close=104), dict(close=103.99), "close"),
]


@pytest.mark.parametrize("rule,yes,no,missing", CASES)
def test_all_rules_positive_negative_boundary_missing(rule, yes, no, missing):
    parents = [
        dict(
            event_id="parent",
            rule_id="VP-01",
            confirmation_market_date="2024-12-31",
            lifecycle="EXPIRED",
            sessions_since_confirmation=1,
            frozen=dict(level=100),
        )
    ]
    announcement = dict(event_id="earnings")
    assert condition(rule, feature(**yes), parents, announcement)[0] is True
    assert condition(rule, feature(**{**yes, **no}), parents, announcement)[0] is False
    assert condition(rule, feature(**{**yes, missing: None}), parents, announcement)[0] is None


def engine_for(rule, cal):
    registry = deepcopy(RULESET)
    for r in registry:
        registry[r]["stage"] = "RESEARCH" if r == rule else "DISABLED"
    return RuleEngine(cal, registry)


def day_feature(cal, day, **kw):
    return feature(
        source_market_date=day,
        evaluated_at=stamp(instant(cal.session(day)["close_at"]) + timedelta(minutes=30)),
        **kw,
    )


def test_last_waiting_day_confirm_not_backfilled(cal):
    engine = engine_for("TR-02", cal)
    engine.step(day_feature(cal, "2025-01-02"))
    for d in ("2025-01-03", "2025-01-06"):
        engine.step(day_feature(cal, d, rv=0.5, ma10_prev=100))
    engine.step(day_feature(cal, "2025-01-07", rv=1, ma10_prev=100))
    e = engine.events[0]
    assert e["confirmation_market_date"] == "2025-01-07"
    assert e["trigger_market_date"] == "2025-01-02"
    assert e["lifecycle"] == "CONFIRMED"
    engine.step(day_feature(cal, "2025-01-08", ma50=105, ma10=100))
    assert engine.events[0]["lifecycle"] == "INVALIDATED"


def test_vp02_two_stage_recovery_and_rearm(cal):
    engine = engine_for("VP-02", cal)
    engine.step(day_feature(cal, "2025-01-02", rv=0.5))
    assert engine.events[0]["lifecycle"] == "PENDING"
    engine.step(day_feature(cal, "2025-01-03", close=99, rv=0.5))
    assert engine.events[0]["lifecycle"] == "CONFIRMED"
    engine.step(day_feature(cal, "2025-01-06", rv=2))
    engine.step(day_feature(cal, "2025-01-07", rv=2))
    engine.step(day_feature(cal, "2025-01-08", rv=2))
    engine.step(day_feature(cal, "2025-01-10", rv=0.5))
    assert len(engine.events) == 2
    assert engine.events[1]["trigger_market_date"] == "2025-01-10"


@pytest.mark.parametrize("rule,extra", [(r, y) for r, y, _, _ in CASES if r not in ("TR-03", "EV-01")])
def test_rules_expire_or_recover_and_do_not_duplicate_same_day(rule, extra, cal):
    engine = engine_for(rule, cal)
    f = day_feature(cal, "2025-01-02", **extra)
    engine.step(f)
    engine.step(f)
    assert len(engine.events) == 1
    for n in range(1, 10):
        day = cal.shift("2025-01-02", n)
        quiet = {
            **extra,
            "rv": 0.5,
            "close": 100,
            "high": 101,
            "low": 99,
            "ma10_prev": 100,
            "ma50_prev": 98,
            "macd": 0,
            "rsi": 31,
            "atr": 2,
            "atr_prev": 2,
        }
        engine.step(day_feature(cal, day, **quiet))
    assert engine.events[0]["lifecycle"] not in ("PENDING", "CONFIRMED")


def test_stream_equals_batch_and_future_is_not_read(tmp_path, cal):
    p = create_fixture(tmp_path / "input", symbols=1, sessions=20)
    s = json.loads((Path(p).parent / "snapshot.json").read_text())
    bars = s["bars"]["SYN000"]
    batch = build_features(bars, cal, "SYN000", "none")
    stream = StreamingFeatures(cal, "SYN000", "none")
    saved = []
    for b, expected in zip(bars, batch):
        actual = stream.append(b)
        saved.append(deepcopy(actual))
        for k, v in expected.items():
            if k == "input_hash":
                continue
            if isinstance(v, float):
                assert actual[k] == pytest.approx(v, rel=1e-12, abs=1e-12), (b["market_date"], k)
            else:
                assert actual[k] == v, (b["market_date"], k)
    bars[-1]["close"] = "999"
    assert saved[100]["close"] != 999


def _announcement(cal, day):
    return dict(
        event_id="earnings-" + day,
        known_at=cal.session(day)["open_at"],
        published_at=stamp(instant(cal.session(day)["open_at"]) - timedelta(seconds=1)),
    )


@pytest.mark.parametrize("rule,yes,no,missing", CASES)
def test_each_rule_data_gap_and_rearm(rule, yes, no, missing, cal):
    day = "2025-01-02"
    engine = engine_for(rule, cal)
    if rule == "TR-03":
        engine.events.append(
            dict(
                event_id="parent",
                rule_id="VP-01",
                rule_version="1.0.0",
                instrument_id="TEST",
                trigger_market_date="2024-12-31",
                confirmation_market_date="2024-12-31",
                confirmed_at=cal.session("2024-12-31")["close_at"],
                lifecycle="EXPIRED",
                frozen={"level": 100},
                valid_until=cal.session(day)["close_at"],
                state_history=[],
            )
        )
    engine.step(day_feature(cal, day, **yes), [_announcement(cal, day)])
    original = [e for e in engine.events if e["rule_id"] == rule]
    assert len(original) == 1
    frozen = deepcopy(original[0]["frozen"])
    gap = cal.shift(day, 1)
    engine.skip(gap, cal.session(gap)["close_at"], "HALTED_OR_MISSING")
    assert original[0]["lifecycle"] == "EXPIRED" and original[0]["resolution"] == "DATA_GAP"
    assert original[0]["frozen"] == frozen
    same = cal.shift(day, 2)
    engine.step(day_feature(cal, same, **yes), [_announcement(cal, same)])
    assert len([e for e in engine.events if e["rule_id"] == rule]) == 1
    reset = cal.shift(day, 3)
    engine.step(day_feature(cal, reset, **{**yes, **no}), [_announcement(cal, reset)])
    retrigger = cal.shift(day, 4)
    engine.step(day_feature(cal, retrigger, **yes), [_announcement(cal, retrigger)])
    new = [e for e in engine.events if e["rule_id"] == rule]
    assert len(new) == 2 and new[0]["event_id"] != new[1]["event_id"]


@pytest.mark.parametrize("rule", list(RULESET))
def test_each_rule_split_preserves_original_freeze_and_common_units(rule, cal):
    from src.services.signal_scanner.research.runner import _rebase

    engine = engine_for(rule, cal)
    original = {
        "entry_anchor": 100,
        "atr": 2,
        "confirmation_atr": 2,
        "price_factor": "1",
        "high": 102,
        "low": 98,
    }
    engine.events.append(
        dict(
            event_id="active-" + rule,
            rule_id=rule,
            lifecycle="CONFIRMED",
            trigger_market_date="2025-01-02",
            frozen=deepcopy(original),
        )
    )
    bars = [
        dict(market_date=d, open="100", high="102", low="98", close="100", volume="1000")
        for d in ("2025-01-02", "2025-01-03")
    ]
    action = dict(
        event_id="split",
        revision=1,
        instrument_id="TEST",
        event_type="SPLIT",
        known_at=cal.session("2025-01-03")["open_at"],
        effective_at=cal.session("2025-01-03")["open_at"],
        payload_json={"ratio": "2"},
    )
    _rebase(engine, bars, [action], cal.session("2025-01-03")["close_at"], "2025-01-03")
    e = engine.events[0]
    assert e["original_frozen"] == original
    assert e["frozen"]["entry_anchor"] == 50 and e["frozen"]["atr"] == 1
    assert len(e["unit_conversions"]) == 1
