from __future__ import annotations

from copy import deepcopy

from src.services.signal_scanner.core import digest
from src.services.signal_scanner.domain.rule_parameters import parameters_for

RULES = {
    "TR-01": ("BREAKOUT", "DIRECTION", "LONG", 3),
    "TR-02": ("MA_CROSS", "DIRECTION", "LONG", 3),
    "TR-03": ("PULLBACK", "DIRECTION", "LONG", 3),
    "VP-01": ("BREAKOUT", "DIRECTION", "LONG", 3),
    "VP-02": ("WEAK_BREAKOUT", "WARNING", "NEUTRAL", 3),
    "VP-03": ("SUPPLY_PRESSURE", "WARNING", "BEARISH", 3),
    "VP-04": ("EFFORT_RESULT", "WARNING", "NEUTRAL", 1),
    "VP-05": ("VOLUME_FADE", "WARNING", "BEARISH", 3),
    "MO-01": ("RANGE_REBOUND", "DIRECTION", "LONG", 2),
    "MO-02": ("MACD", "EVIDENCE", "LONG", 3),
    "EV-01": ("EARNINGS_GAP", "DIRECTION", "LONG", 3),
    "RK-01": ("VOL_SHOCK", "WARNING", "NEUTRAL", 2),
}
RULESET = {
    k: dict(version="1.0.0", stage="RESEARCH", family=v[0], kind=v[1], direction=v[2])
    for k, v in RULES.items()
}
ACTIVE = ("PENDING", "CONFIRMED")


def condition(rule, f, parents=(), announcement=None, parameters=None):
    """Returns a condition plus immutable seed values; never reads future bars."""
    p = parameters_for(rule, parameters)
    C, H, L, RV, A = (f.get(k) for k in ("close", "high", "low", "rv", "atr_prev"))
    req = {
        "TR-01": ["b20", "atr_prev", "adx", "weekly", "rv"],
        "TR-02": ["ma10", "ma50", "ma10_prev", "ma50_prev", "ma50_prev5", "adx"],
        "TR-03": ["atr_prev", "rv", "clv"],
        "VP-01": ["b60", "atr_prev", "adx", "rv", "clv", "weekly", "prev_volume"],
        "VP-02": ["b20", "rv", "atr_prev"],
        "VP-03": ["ma50", "return20", "rv", "body", "clv"],
        "VP-04": ["atr_prev", "body", "rv"],
        "VP-05": ["ma50", "volume_mean20"],
        "MO-01": ["adx", "adx_prev", "rsi", "rsi_prev", "weekly"],
        "MO-02": ["macd", "macd_prev", "macd_signal", "macd_signal_prev"],
        "EV-01": ["prev_close", "rv"],
        "RK-01": [],
    }[rule]
    missing = [k for k in ["close", "high", "low"] + req if f.get(k) is None]
    if f.get("count", 260) < 260:
        missing.append("HISTORY_260")
    if missing:
        return None, dict(missing_dependencies=missing)
    seed = dict(
        close=C,
        high=H,
        low=L,
        atr=A,
        epoch=f.get("adjustment_epoch"),
        entry_anchor=C,
        price_factor=f.get("price_factor", "1"),
    )
    yes = False
    if rule == "TR-01":
        yes = (
            C > f["b20"] + p["atr_buffer"] * A
            and RV >= p["rv_min"]
            and f["adx"] >= p["adx_min"]
            and f["weekly"] != "BEARISH"
        )
        seed["level"] = f["b20"]
    elif rule == "TR-02":
        yes = (
            f["ma10_prev"] <= f["ma50_prev"]
            and f["ma10"] > f["ma50"]
            and f["adx"] >= p["adx_min"]
            and f["ma50"] > f["ma50_prev5"]
        )
    elif rule == "TR-03":
        eligible = [
            e
            for e in parents
            if e["rule_id"] in ("TR-01", "VP-01")
            and e.get("confirmation_market_date")
            and e["lifecycle"] not in ("RETRACTED", "INVALIDATED")
            and not e.get("structure_broken", False)
            and 0 <= e.get("sessions_since_confirmation", 999) <= p["parent_sessions"]
        ]
        eligible.sort(key=lambda e: (e["confirmation_market_date"], e["rule_id"] == "VP-01"), reverse=True)
        if eligible:
            parent = eligible[0]
            B = parent["frozen"]["level"]
            yes = (
                B - p["atr_buffer"] * A <= L <= B + p["atr_buffer"] * A
                and C >= B
                and RV <= p["rv_max"]
                and f["clv"] >= p["clv_min"]
            )
            seed.update(level=B, parent_event_id=parent["event_id"], pullback_low=L, pullback_high=H)
    elif rule == "VP-01":
        yes = (
            C > f["b60"] + p["atr_buffer"] * A
            and RV >= p["rv_min"]
            and f["volume"] > f["prev_volume"]
            and f["clv"] >= p["clv_min"]
            and f["adx"] >= p["adx_min"]
            and f["weekly"] != "BEARISH"
        )
        seed["level"] = f["b60"]
    elif rule == "VP-02":
        yes = H > f["b20"] and RV < p["rv_max"]
        seed["level"] = f["b20"]
    elif rule == "VP-03":
        yes = (
            C > f["ma50"]
            and f["return20"] > p["return_min"]
            and RV >= p["rv_min"]
            and f["body"] < p["body_max"]
            and f["clv"] <= p["clv_max"]
        )
    elif rule == "VP-04":
        yes = H - L > p["range_atr"] * A and f["body"] >= p["body_min"] and RV <= p["rv_max"]
    elif rule == "VP-05":
        cs, vs = f["close3"], f["volume3"]
        yes = len(cs) == 3 and cs[2] > cs[1] > cs[0] and vs[2] < vs[1] < vs[0] and C > f["ma50"]
    elif rule == "MO-01":
        yes = (
            f["adx"] < p["adx_max"]
            and f["adx"] < f["adx_prev"]
            and f["weekly"] != "BEARISH"
            and f["rsi_prev"] < p["rsi_level"] <= f["rsi"]
        )
    elif rule == "MO-02":
        up = f["macd_prev"] <= f["macd_signal_prev"] and f["macd"] > f["macd_signal"]
        down = f["macd_prev"] >= f["macd_signal_prev"] and f["macd"] < f["macd_signal"]
        yes = up or down
        seed["direction"] = "LONG" if up else "BEARISH"
    elif rule == "EV-01" and announcement:
        gap = f["open"] / f["prev_close"] - 1
        yes = abs(gap) >= p["gap_min"] and RV >= p["rv_min"] and (C - f["prev_close"]) * gap > 0
        seed.update(
            level=f["prev_close"],
            gap=gap,
            event_ref=announcement["event_id"],
            direction="LONG" if gap > 0 else "BEARISH",
        )
    elif rule == "RK-01":
        first = A is not None and A > 0 and abs(C - f["prev_close"]) / A >= p["shock_multiple"]
        median = f.get("atr_median60")
        current = f.get("atr")
        # A zero volatility baseline is not evidence of a volatility shock at a flat price.
        second = (
            median is not None
            and median > 0
            and current is not None
            and current >= p["shock_multiple"] * median
        )
        yes = bool(first or second)
    return bool(yes), seed


def advance(event, f, cal, parameters=None):
    e = deepcopy(event)
    r = e["rule_id"]
    p = parameters_for(r, parameters)
    s = e["frozen"]
    d = f["source_market_date"]
    C = f["close"]
    RV = f.get("rv")
    age = cal.distance(e["trigger_market_date"], d)
    if age <= 0 or e["lifecycle"] not in ACTIVE:
        return e

    def end(state="INVALIDATED", resolution=None):
        e["lifecycle"] = state
        e["resolution"] = resolution

    def confirm():
        e["lifecycle"] = "CONFIRMED"
        e["confirmation_market_date"] = d
        e["confirmed_at"] = f["evaluated_at"]
        e["frozen"].update(entry_anchor=C, confirmation_atr=f.get("atr_prev"))
        e["valid_until"] = cal.session(cal.shift(d, p["ttl"]))["close_at"]

    if r in ("TR-01", "VP-01") and C < s["level"]:
        end()
    elif r == "TR-02":
        if f.get("ma10") is None or f.get("ma50") is None:
            return e
        if C < f["ma50"] or (e["lifecycle"] == "PENDING" and f["ma10"] <= f["ma50"]):
            end()
        elif (
            e["lifecycle"] == "PENDING"
            and age <= p["wait"]
            and C >= f["ma10"]
            and RV is not None
            and RV >= p["confirm_rv"]
        ):
            confirm()
    elif r == "TR-03":
        if f["low"] < s["pullback_low"] or C < s["level"] - p["atr_buffer"] * s["atr"]:
            end()
        elif e["lifecycle"] == "PENDING" and age <= p["wait"] and C > s["pullback_high"]:
            confirm()
    elif r == "VP-02" and e["lifecycle"] == "PENDING":
        if C < s["level"] and age <= p["wait"]:
            confirm()
        elif C > s["level"] + p["atr_buffer"] * s["atr"] and RV is not None and RV >= p["recover_rv"]:
            end("EXPIRED", "RECOVERED")
    elif r == "VP-03":
        if e["lifecycle"] == "PENDING" and age == 1:
            if C <= s["close"]:
                confirm()
            else:
                end("EXPIRED", "TIMEOUT")
        elif e["lifecycle"] == "CONFIRMED" and C > s["high"] and RV is not None and RV >= p["recover_rv"]:
            end("EXPIRED", "RECOVERED")
    elif (
        r == "VP-05" and C > f["prev_close"] and f.get("volume_mean20") and f["volume"] >= f["volume_mean20"]
    ):
        end("EXPIRED", "RECOVERED")
    elif r == "MO-01":
        if (f.get("rsi") is not None and f["rsi"] < p["rsi_level"]) or f["low"] < s["low"]:
            end()
        elif (
            e["lifecycle"] == "PENDING"
            and age <= p["wait"]
            and C > s["high"]
            and RV is not None
            and RV >= p["confirm_rv"]
        ):
            confirm()
    elif r == "MO-02":
        if f.get("macd") is not None and f.get("macd_signal") is not None:
            if (e["direction"] == "LONG" and f["macd"] < f["macd_signal"]) or (
                e["direction"] == "BEARISH" and f["macd"] > f["macd_signal"]
            ):
                end("EXPIRED", "RECOVERED")
    elif r == "EV-01" and (
        (e["direction"] == "LONG" and C < s["level"]) or (e["direction"] == "BEARISH" and C > s["level"])
    ):
        end()
    elif r == "RK-01" and d >= e["expiry_market_date"]:
        remains, _ = condition(r, f, parameters=parameters)
        if remains:
            e["expiry_market_date"] = cal.shift(d, p["ttl"])
            e["valid_until"] = cal.session(e["expiry_market_date"])["close_at"]
        else:
            end("EXPIRED", "RECOVERED")
    if e["lifecycle"] == "PENDING" and age >= p["wait"]:
        end("EXPIRED", "TIMEOUT")
    if e["lifecycle"] == "CONFIRMED" and f["evaluated_at"] >= e["valid_until"]:
        end("EXPIRED", "TIMEOUT")
    return e


class RuleEngine:
    """Stateful single-session engine shared by live scans and offline replay."""

    def __init__(self, cal, registry=None, *, effective_from=None):
        self.cal = cal
        self.registry = deepcopy(registry or RULESET)
        self.effective_from = effective_from
        self.events = []
        self.armed = {r: True for r in RULES}
        self.evaluability = {}

    def step(self, f, announcements=(), *, detect=True):
        if f["count"] < 260:
            return
        cal, registry, effective_from = self.cal, self.registry, self.effective_from
        events, armed, evaluability = self.events, self.armed, self.evaluability
        day = f["source_market_date"]
        for i, e in enumerate(events):
            if e["lifecycle"] in ACTIVE:
                old = e["lifecycle"]
                events[i] = advance(e, f, cal, registry[e["rule_id"]].get("parameters"))
                changed = events[i]
                if (changed["lifecycle"], changed.get("resolution"), changed["valid_until"]) != (
                    e["lifecycle"],
                    e.get("resolution"),
                    e["valid_until"],
                ):
                    changed.setdefault("state_history", []).append(
                        dict(
                            market_date=day,
                            lifecycle=changed["lifecycle"],
                            resolution=changed.get("resolution"),
                            valid_until=changed["valid_until"],
                            confirmation_market_date=changed.get("confirmation_market_date"),
                            frozen=deepcopy(changed["frozen"]),
                        )
                    )
                if events[i]["lifecycle"] not in ACTIVE and old in ACTIVE:
                    armed[e["rule_id"]] = False
        parents = [
            {**e, "sessions_since_confirmation": cal.distance(e["confirmation_market_date"], day)}
            for e in events
            if e.get("confirmation_market_date")
        ]
        announcement = next(
            (
                e
                for e in announcements
                if e.get("published_at")
                and e["known_at"] <= f["evaluated_at"]
                and cal.first_full_after(e["published_at"]) == day
            ),
            None,
        )
        for r in RULES:
            if registry[r]["stage"] == "DISABLED":
                continue
            active = any(e["rule_id"] == r and e["lifecycle"] in ACTIVE for e in events)
            condition_met, seed = condition(r, f, parents, announcement, registry[r].get("parameters"))
            evaluability[r] = {
                "status": "NOT_EVALUABLE" if condition_met is None else "EVALUATED",
                "missing_dependencies": seed.get("missing_dependencies", []),
            }
            if condition_met is False and not active:
                armed[r] = True
            if not condition_met or active or not armed[r] or not detect:
                continue
            family, kind, direction, ttl = RULES[r]
            pending = r in ("TR-02", "TR-03", "VP-02", "VP-03", "MO-01")
            params = parameters_for(r, registry[r].get("parameters"))
            ttl, wait = params["ttl"], params["wait"]
            expiry = cal.shift(day, wait if pending else ttl)
            stage = (
                registry[r]["stage"]
                if effective_from is None or f["evaluated_at"] >= effective_from
                else "RESEARCH"
            )
            seed.update(confirmation_atr=f.get("atr_prev") if not pending else None)
            events.append(
                dict(
                    event_id="evt_" + digest([f["instrument_id"], r, registry[r]["version"], day])[:24],
                    rule_id=r,
                    rule_version=registry[r]["version"],
                    instrument_id=f["instrument_id"],
                    family=family,
                    kind=kind,
                    direction=seed.get("direction", direction),
                    trigger_market_date=day,
                    triggered_at=f["evaluated_at"],
                    confirmation_market_date=None if pending else day,
                    confirmed_at=None if pending else f["evaluated_at"],
                    lifecycle="PENDING" if pending else "CONFIRMED",
                    resolution=None,
                    stage=stage,
                    frozen=seed,
                    valid_until=cal.session(expiry)["close_at"],
                    expiry_market_date=expiry,
                    evidence={
                        k: f.get(k) for k in ("close", "rv", "adx", "rsi", "weekly", "atr", "body", "clv")
                    },
                    state_history=[
                        dict(
                            market_date=day,
                            lifecycle="PENDING" if pending else "CONFIRMED",
                            resolution=None,
                            valid_until=cal.session(expiry)["close_at"],
                            confirmation_market_date=None if pending else day,
                            frozen=deepcopy(seed),
                        )
                    ],
                )
            )

    def skip(self, day, clock, reason):
        # An unobserved intervening invalidation cannot be assumed not to exist.
        for e in self.events:
            if e["lifecycle"] in ACTIVE:
                e["lifecycle"] = "EXPIRED"
                e["resolution"] = "DATA_GAP"
                e.setdefault("state_history", []).append(
                    dict(
                        market_date=day,
                        evaluated_at=clock,
                        lifecycle="EXPIRED",
                        resolution="DATA_GAP",
                        reason_code=reason,
                        valid_until=e["valid_until"],
                        frozen=deepcopy(e["frozen"]),
                    )
                )
                self.armed[e["rule_id"]] = False


def run_rules(features, cal, registry=None, announcements=(), *, effective_from=None):
    engine = RuleEngine(cal, registry, effective_from=effective_from)
    for f in features:
        engine.step(f, announcements)
    return engine.events, engine.evaluability
