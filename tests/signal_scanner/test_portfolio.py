from src.services.signal_scanner.core import stamp, instant
from src.services.signal_scanner.domain.portfolio import simulate
from src.services.signal_scanner.domain.risk import DEFAULT_POLICY
from src.services.signal_scanner.domain.paper import outcomes


def portfolio_inputs(cal):
    days = cal.dates("2026-06-01", "2026-07-31")[:22]
    instruments = []
    bars = {}
    series = {}
    for iid in ("TEST", "SPY", "QQQ"):
        instruments.append(
            dict(
                id=iid,
                current_ticker=iid,
                asset_type="ETF",
                price_tick="0.01",
                sector_code=None,
                profile_json=dict(comparators=["SPY", "QQQ"], concentration_bucket="INDEX"),
            )
        )
        bars[iid] = [
            dict(market_date=d, open="100", high="103", low="99", close="102", volume="1000000") for d in days
        ]
        series[iid] = [
            dict(
                instrument_id=iid,
                source_market_date=d,
                input_hash="fixture",
                close=102,
                ma10=101,
                ma20=100,
                ma50=99,
                ma50_prev5=98,
                adx=25,
                di_plus=30,
                di_minus=10,
                rv=2,
                weekly="BULLISH",
                return20=0.1,
                adjustment_epoch="fixture",
            )
            for d in days
        ]
    cutoff = stamp(instant(cal.session(days[-1])["close_at"]))
    events = [
        dict(
            event_id="vix" + d,
            event_type="VIX_VALUE",
            instrument_id=None,
            known_at=stamp(instant(cal.session(d)["close_at"])),
            payload_json=dict(value="18", market_date=d),
        )
        for d in days
    ]
    end = cal.session(cal.shift(days[0], 3))["close_at"]
    e = dict(
        event_id="signal",
        instrument_id="TEST",
        rule_id="TR-01",
        rule_version="1.0.0",
        family="BREAKOUT",
        kind="DIRECTION",
        direction="LONG",
        stage="SHADOW",
        trigger_market_date=days[0],
        confirmation_market_date=days[0],
        confirmed_at=stamp(instant(cal.session(days[0])["close_at"])),
        lifecycle="EXPIRED",
        valid_until=end,
        frozen=dict(entry_anchor=100, atr=2, confirmation_atr=2, price_factor="1"),
        state_history=[
            dict(
                market_date=days[0],
                lifecycle="CONFIRMED",
                valid_until=end,
                frozen=dict(entry_anchor=100, atr=2, confirmation_atr=2, price_factor="1"),
            )
        ],
    )
    manifest = dict(
        bars=bars,
        instruments=instruments,
        cutoff_at=cutoff,
        events=events,
        universe=dict(value=dict(members=["TEST"])),
        risk_policy=DEFAULT_POLICY,
        settlement_dates={d: cal.shift(d, 1) for d in days},
        security_status_history=[
            dict(
                instrument_id="TEST",
                status="ACTIVE",
                observed_at="2026-01-01T00:00:00.000Z",
                valid_until="2027-01-01T00:00:00.000Z",
            )
        ],
        historical_event_coverage=dict(status="OK", **{"from": days[0], "to": days[-1]}),
    )
    experiment = dict(
        initial_cash="100000", slippage_bps="5", costs_version="c1", data_mode="REVISED_HISTORY"
    )
    return manifest, dict(events=[e]), experiment, series, days


def test_shadow_enters_then_exits_20_sessions_without_future_lifecycle_leak(cal):
    m, o, e, fs, ds = portfolio_inputs(cal)
    r = simulate(m, o, cal, e, fs)
    assert r["status"] == "COMPUTED", r["skipped"]
    assert [t["entry_type"] for t in r["trades"]] == ["BUY", "SELL"]
    assert r["trades"][0]["market_date"] == ds[1]
    assert r["trades"][1]["market_date"] == ds[20]
    assert len(r["closed_episodes"]) == 1 and not r["open_positions"]


def test_shadow_does_not_fill_without_historical_status(cal):
    m, o, e, fs, ds = portfolio_inputs(cal)
    m["security_status_history"] = []
    r = simulate(m, o, cal, e, fs)
    assert r["status"] == "INSUFFICIENT_INPUTS" and not r["trades"]


def test_shadow_gap_stop_uses_open_not_stop(cal):
    m, o, e, fs, ds = portfolio_inputs(cal)
    m["bars"]["TEST"][2].update(open="90", low="88", close="92")
    r = simulate(m, o, cal, e, fs)
    assert r["trades"][1]["price"] == "89.955"


def test_signal_outcome_split_neutral_and_bearish_not_short(cal):
    m, o, e, fs, ds = portfolio_inputs(cal)
    evt = o["events"][0]
    evt["direction"] = "BEARISH"
    for b in m["bars"]["TEST"]:
        if b["market_date"] >= ds[3]:
            b.update(open="50", high="51", low="49", close="50")
        else:
            b.update(open="100", high="102", low="99", close="100")
    action = dict(
        event_type="SPLIT",
        instrument_id="TEST",
        known_at="2026-05-01T00:00:00.000Z",
        effective_at=ds[3] + "T00:00:00.000Z",
        payload_json=dict(ratio="2"),
    )
    r = outcomes(o["events"], m["bars"], cal, m["cutoff_at"], actions=[action])
    complete = [x for x in r if x["status"] == "COMPLETED"]
    assert complete and all(-0.01 < float(x["net_return"]) < 0 for x in complete)
