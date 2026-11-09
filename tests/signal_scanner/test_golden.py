from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.services.signal_scanner.core import digest, instant, stamp
from src.services.signal_scanner.domain.features import adjust, build_features, distances
from src.services.signal_scanner.domain.paper import paper_entry, paper_exit, pnl
from src.services.signal_scanner.domain.reports import morning_metrics, rolling_statistics
from src.services.signal_scanner.domain.risk import calculate_risk
from src.services.signal_scanner.domain.rules import condition, advance
from src.services.signal_scanner.domain.scoring import environment, score


def base_risk(**extra):
    return dict(
        equity="100000",
        settled_cash="100000",
        day_open_equity="100000",
        month_open_equity="100000",
        pnl_day="0",
        pnl_month="0",
        entry="100",
        stop="94",
        heat_open="0",
        heat_pending="0",
        cash_pending="0",
        symbol_used="0",
        sector_used="0",
        slippage_in="0.05",
        slippage_out="0.05",
        **extra,
    )


def feature(**kwargs):
    f = dict(
        instrument_id="TEST",
        count=280,
        close=101.01,
        open=100,
        high=102,
        low=99,
        volume=200,
        prev_close=100,
        prev_volume=100,
        ma10=100,
        ma20=99,
        ma50=98,
        ma50_prev5=97,
        ma10_prev=97,
        ma50_prev=98,
        atr=2,
        atr_prev=2,
        atr_median60=2,
        adx=25,
        adx_prev=26,
        di_plus=30,
        di_minus=10,
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
        source_market_date="2026-10-22",
        evaluated_at="2026-10-22T20:30:00.000Z",
        input_hash="fixture",
        adjustment_epoch="epoch",
    )
    f.update(kwargs)
    return f


def event(rule="TR-01", **extra):
    e = dict(
        event_id="e",
        instrument_id="TEST",
        rule_id=rule,
        direction="LONG",
        trigger_market_date="2026-10-19",
        confirmation_market_date="2026-10-19",
        confirmed_at="2026-10-19T20:30:00.000Z",
        lifecycle="CONFIRMED",
        stage="RESEARCH",
        valid_until="2026-10-22T20:00:00.000Z",
        expiry_market_date="2026-10-22",
        frozen=dict(
            level=100,
            atr=2,
            entry_anchor=101,
            close=101,
            low=99,
            high=102,
            pullback_low=99,
            pullback_high=102,
        ),
    )
    e.update(extra)
    return e


def test_CT01_constant(cal):
    days = cal.dates("2024-01-02", "2025-12-31")[:280]
    fs = build_features(
        [dict(market_date=d, open=100, high=100, low=100, close=100, volume=1000) for d in days], cal
    )
    f = fs[-1]
    assert (f["ma50"], f["atr"], f["rsi"], f["adx"], f["macd"], f["macd_signal"], f["rv"]) == (
        100,
        0,
        50,
        0,
        0,
        0,
        1,
    )
    assert f["body"] is None and f["clv"] is None


def test_CT02_linear(cal):
    ds = cal.dates("2024-01-02", "2025-12-31")[:280]
    f = build_features(
        [
            dict(market_date=d, open=100 + i, close=100 + i, high=101 + i, low=99 + i, volume=1000)
            for i, d in enumerate(ds)
        ],
        cal,
    )[-1]
    assert f["atr"] == pytest.approx(2) and f["rsi"] == 100 and f["di_plus"] == pytest.approx(50)
    assert f["adx"] == pytest.approx(100) and f["macd"] == pytest.approx(7)
    assert f["ma10"] == f["close"] - 4.5 and f["ma50"] == f["close"] - 24.5


def test_CT03_RV(cal):
    ds = cal.dates("2026-08-03", "2026-10-01")[:21]
    f = build_features(
        [
            dict(market_date=d, open=100, high=101, low=99, close=100, volume=200 if i == 20 else 100)
            for i, d in enumerate(ds)
        ],
        cal,
    )[-1]
    assert f["rv"] == 2


def test_CT04_boundary():
    assert condition("TR-01", feature(close=101))[0] is False
    assert condition("TR-01", feature(close=101.01))[0] is True


def test_CT05_invalidation(cal):
    assert (
        advance(
            event(),
            feature(close=100, evaluated_at="2026-10-20T20:30:00.000Z", source_market_date="2026-10-20"),
            cal,
        )["lifecycle"]
        == "CONFIRMED"
    )
    assert advance(event(), feature(close=99.99), cal)["lifecycle"] == "INVALIDATED"


def test_CT06_pending_confirm_priority(cal):
    r = advance(
        event("TR-02", lifecycle="PENDING", confirmation_market_date=None),
        feature(close=101, ma10=100, ma50=99, rv=1),
        cal,
    )
    assert r["lifecycle"] == "CONFIRMED" and r["confirmation_market_date"] == "2026-10-22"


def test_CT07_failure_before_volume(cal):
    r = advance(event("TR-02", lifecycle="PENDING"), feature(close=97, ma50=98, rv=3), cal)
    assert r["lifecycle"] == "INVALIDATED"


def test_CT08_pullback(cal):
    e = event("TR-03", trigger_market_date="2026-10-20", lifecycle="PENDING")
    assert advance(e, feature(close=102.1, low=99), cal)["lifecycle"] == "CONFIRMED"
    assert advance(e, feature(close=102.1, low=98.99), cal)["lifecycle"] == "INVALIDATED"


def test_CT09_weak_breakout(cal):
    assert condition("VP-02", feature(high=101, rv=0.9))[0]
    e = event("VP-02", trigger_market_date="2026-10-21", lifecycle="PENDING", confirmation_market_date=None)
    assert advance(e, feature(close=99.9), cal)["lifecycle"] == "CONFIRMED"


def test_CT10_groups():
    e = event(frozen=dict(entry_anchor=100, atr=2))
    a = score(
        feature(close=100, ma10=99, ma20=98, ma50=97, rv=2), e, dict(points=0, external_confirmed=False)
    )
    b = score(
        feature(close=100, ma10=99, ma20=98, ma50=97, rv=1.5, adx=20),
        e,
        dict(points=15, external_confirmed=True),
    )
    assert a["quality_score"] == b["quality_score"] == 85
    assert a["quality_grade"] == "TRACK" and b["quality_grade"] == "HIGH"


@pytest.mark.parametrize(
    "vix,expected", [(24.99, "NORMAL"), (25, "STRESS"), (34.99, "STRESS"), (35, "PANIC")]
)
def test_CT11_environment(vix, expected):
    assert environment(feature(), vix)["mode"] == expected


def test_CT12_budget():
    r = calculate_risk(base_risk())
    assert r["q_risk"] == "245" and r["qty"] == "200" and r["planned_risk"] == "1222"


def test_CT13_stress():
    r = calculate_risk(base_risk(environment="STRESS"))
    assert r["qty"] == "122" and r["planned_risk"] == "746.2"


def test_CT14_recovery():
    r = calculate_risk(base_risk(environment="STRESS", recovery_remaining=5))
    assert r["qty"] == "61" and r["planned_risk"] == "374.1"


def test_CT15_day_remainder():
    p = base_risk()
    p.update(equity="99200", pnl_day="-800", heat_open="1800", heat_pending="700")
    r = calculate_risk(p)
    assert r["R_day"] == "0" and r["qty"] == "0"


def test_CT16_cash():
    p = base_risk()
    p["settled_cash"] = "500"
    r = calculate_risk(p)
    assert r["q_cash"] == r["qty"] == "4"


@pytest.mark.parametrize("equity,flow", [("110000", "10000"), ("90000", "-10000")])
def test_CT17_flow(equity, flow):
    assert Decimal(equity) - Decimal(100000) - Decimal(flow) == 0


def test_CT19_real_distance():
    p = base_risk()
    p["stop"] = "92"
    assert calculate_risk(p)["q_risk"] == "184"


def test_CT20_slippage():
    assert pnl(10, "100.05", "89.955") == Decimal("-102.95")


def test_CT21_gap_exit():
    assert paper_exit(dict(open="90", low="88", close="92"), "94", 2) == Decimal("89.955")


def test_CT27_early_close(cal):
    s = cal.session("2026-11-27")
    assert instant(s["close_at"]).astimezone(ZoneInfo("America/New_York")).hour == 13
    assert (instant(s["close_at"]) + timedelta(minutes=30)).astimezone(ZoneInfo("America/New_York")).strftime(
        "%H:%M"
    ) == "13:30"


def test_CT28_split():
    actions = [
        dict(
            event_type="SPLIT",
            known_at="2026-10-19T00:00:00.000Z",
            effective_at="2026-10-20T00:00:00.000Z",
            payload_json=dict(ratio="2"),
        )
    ]
    bars = [
        dict(market_date="2026-10-19", open="100", high="100", low="100", close="100", volume="1000"),
        dict(market_date="2026-10-20", open="50", high="50", low="50", close="50", volume="2000"),
    ]
    r = adjust(bars, actions)
    assert r[0]["close"] == r[1]["close"] == 50 and r[0]["volume"] == r[1]["volume"] == 2000


def test_CT29_full_session(cal):
    assert cal.first_full_after("2026-10-22T13:30:00.000Z") == "2026-10-23"


def test_CT30_no_late_open_fill():
    assert paper_entry("102", "100", "101") is None


def test_CT31_exit_day():
    assert paper_exit(dict(open="100", low="98", close="102"), "94", 19) is None
    assert paper_exit(dict(open="100", low="98", close="102"), "94", 20) == Decimal("101.949")


def test_CT34_features_deterministic(cal):
    ds = cal.dates("2024-01-02", "2025-12-31")[:280]
    bars = [dict(market_date=d, open=100, high=101, low=99, close=100, volume=1000) for d in ds]
    assert digest(build_features(bars, cal)) == digest(build_features(bars, cal))
    old = digest(build_features(bars, cal))
    bars[20]["close"] = 100.5
    assert digest(build_features(bars, cal)) != old


def test_CT35_sessions_events_dst(cal):
    for t, phase in [
        ("13:30", "OPENING_30M"),
        ("14:00", "REGULAR"),
        ("19:00", "POWER_HOUR"),
        ("20:00", None),
    ]:
        c = cal.context(instant("2026-10-22T" + t + ":00.000Z"), extended=True)
        assert c["rth_phase"] == phase
    from src.services.signal_scanner.domain.calendar import dst_notice

    assert dst_notice(instant("2026-10-29T00:00:00.000Z"))["phase"] == "PRE"
    assert dst_notice(instant("2026-11-01T00:00:00.000Z"))["phase"] == "DAY"
    events = [
        dict(
            event_id="e",
            revision=1,
            known_at="2026-10-22T13:00:00.000Z",
            scheduled_at="2026-10-22T15:00:00.000Z",
            event_type="CPI",
            status="SCHEDULED",
        ),
        dict(
            event_id="e",
            revision=2,
            known_at="2026-10-22T16:00:00.000Z",
            scheduled_at="2026-10-22T17:00:00.000Z",
            event_type="CPI",
            status="SCHEDULED",
        ),
    ]
    assert cal.context(instant("2026-10-22T14:00:00.000Z"), events)["next_events"][0]["revision"] == 1


def test_CT36_distances():
    assert distances("101", "100", "2")["distance_to_entry_atr"] == "0.5"
    assert distances("99", "100", "2")["distance_to_entry_pct"] == "-1"
    assert distances("50.5", "50", "1")["distance_to_entry_pct"] == "1"
    assert distances("100", "0", "2")["distance_to_entry_pct"] is None
    assert distances("101", "100", "0")["distance_to_entry_pct"] == "1"
    assert distances("101", "100", "2", reason="STALE_QUOTE")["distance_to_entry_pct"] is None


def test_CT38_rolling():
    rows = [
        dict(
            dataset="SIGNAL_OUTCOME",
            economic_event_id=str(i),
            rule_id="TR-01",
            rule_version="1",
            horizon=10,
            data_mode="REVISED_HISTORY",
            status="COMPLETED",
            net_pnl="1" if i < 30 else "-1" if i < 50 else "0",
            confirmed_at=f"{i:03}",
        )
        for i in range(55)
    ]
    rows += [
        {**rows[0], "economic_event_id": "pending" + str(i), "status": "PENDING", "net_pnl": None}
        for i in range(3)
    ]
    rows += [
        {**rows[0], "economic_event_id": "unresolved" + str(i), "status": "UNRESOLVED", "net_pnl": None}
        for i in range(2)
    ]
    assert rolling_statistics(rows, "50")[0]["sample_size"] == 50
    r = rolling_statistics(rows + [rows[0]], "100")[0]
    assert (r["sample_size"], r["wins"], r["pending"], r["unresolved"]) == (55, 30, 3, 2)


def test_CT39_morning():
    window = dict(start="07:30", end="09:30", timezone="Asia/Singapore")
    rows = []
    for i, t in enumerate(["07:30:00", "09:29:59.999", "09:30:00", "07:29:59.999"]):
        rows.append(
            dict(
                id=str(i),
                actor_id="owner",
                card_id=str(i),
                action_market_date="2026-10-22",
                server_time=stamp(instant("2026-10-22T" + t + "+08:00")),
            )
        )
    rows.append({**rows[0], "id": "later", "server_time": "2026-10-22T12:00:00.000Z"})
    r = morning_metrics(rows, "2026-10-21T16:00:00.000Z", "2026-10-22T16:00:00.000Z", window, "p1")
    assert (
        r["morning_count"] == 2 and r["total_first_decisions"] == 4 and r["morning_decision_share"] == "0.5"
    )
    assert morning_metrics([], "a", "z", window, "p1")["morning_decision_share"] is None
