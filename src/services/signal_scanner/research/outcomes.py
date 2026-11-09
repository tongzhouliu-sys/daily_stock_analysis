from decimal import Decimal

from src.services.signal_scanner.core import DomainError, instant, text_decimal
from src.services.signal_scanner.domain.features import adjust
from src.services.signal_scanner.research.experiments import assignment


def returns(p0, pn, bps, cost):
    a, b = Decimal(str(p0)), Decimal(str(pn))
    s = Decimal(bps) / 10000
    gross = b / a - 1
    slipped = b * (1 - s) / (a * (1 + s)) - 1
    fee = Decimal(cost["entry_fee"]) + Decimal(cost["exit_fee"])
    fee = fee / 10000 if cost["fee_unit"] == "BPS_OF_ENTRY" else fee / Decimal(cost["reference_notional"])
    return dict(
        gross_return=text_decimal(gross),
        net_return=text_decimal(slipped - fee),
        slippage_cost=text_decimal(gross - slipped),
        fee_return=text_decimal(fee),
    )


def label(iid, confirmation, confirmed_at, entry_basis, horizon, m, view, cal, split):
    base = dict(
        instrument_id=iid,
        horizon=horizon,
        entry_basis=entry_basis,
        status="PENDING",
        reason_code=None,
        gross_return=None,
        net_return=None,
    )
    entry = confirmation if entry_basis == "CONFIRM_CLOSE" else cal.shift(confirmation, 1)
    if entry_basis == "NEXT_AVAILABLE_OPEN":
        while instant(cal.session(entry)["open_at"]) <= instant(confirmed_at):
            entry = cal.shift(entry, 1)
    end = cal.shift(entry, horizon if entry_basis == "CONFIRM_CLOSE" else horizon - 1)
    base.update(
        entry_market_date=entry, exit_market_date=end, entry_delay_sessions=cal.distance(confirmation, entry)
    )
    startset = assignment(split, confirmation)
    base["split"] = startset
    if startset == "EMBARGO" or assignment(split, end) != startset:
        # The post-end label buffer belongs to the last set, unless it crosses a real split boundary.
        last = max(hi for lo, hi in split["sets"].values())
        if startset == "EMBARGO" or end <= last:
            return {**base, "excluded_reason": "CROSS_SPLIT_LABEL", "status": "EXCLUDED"}
    clock = m["label_as_of"]
    end_limit = cal.last_completed(instant(clock))
    if not hasattr(view, "label_cache"):
        view.label_cache = {}
    if iid not in view.label_cache:
        raw = view.bars(iid, end_limit, clock)
        from src.services.signal_scanner.research.inputs import effective_actions

        try:
            actions = effective_actions(
                view.events(clock), iid, clock, dividends=m["price_basis"]["return_kind"] == "TOTAL_RETURN"
            )
        except DomainError:
            view.label_cache[iid] = ({b["market_date"]: b for b in raw}, None)
            return {**base, "status": "UNRESOLVED", "reason_code": "CORPORATE_ACTION_TIME_UNVERIFIED"}
        try:
            prices = {b["market_date"]: b for b in adjust(raw, actions, clock)}
        except (ValueError, KeyError):
            prices = None
        view.label_cache[iid] = ({b["market_date"]: b for b in raw}, prices)
    bar_map, prices = view.label_cache[iid]
    observed_end = min(end, end_limit)
    necessary = cal.dates(entry, observed_end) if entry <= observed_end else []
    statuses = [
        x
        for x in view.s.get("security_statuses", [])
        if x["instrument_id"] == iid
        and x["from"] <= observed_end
        and x["to"] >= entry
        and x["status"] in ("HALTED", "DELISTED")
        and instant(x["known_at"]) <= instant(clock)
    ]
    missing = [
        d
        for d in necessary
        if d not in bar_map or not bar_map[d].get("is_final") or bar_map[d].get("quality_status") != "OK"
    ]
    if statuses or missing:
        return {
            **base,
            "status": "UNRESOLVED",
            "reason_code": "SECURITY_UNAVAILABLE" if statuses else "MISSING_LABEL_DATA",
        }
    if instant(cal.session(end)["close_at"]) > instant(clock):
        return base
    if prices is None:
        return {**base, "status": "UNRESOLVED", "reason_code": "BASIS_MISMATCH"}
    if entry not in prices or end not in prices:
        return {**base, "status": "UNRESOLVED", "reason_code": "MISSING_LABEL_DATA"}
    p0 = prices[entry]["close" if entry_basis == "CONFIRM_CLOSE" else "open"]
    pn = prices[end]["close"]
    return {
        **base,
        "status": "COMPLETED",
        "entry_price": str(p0),
        "exit_price": str(pn),
        "label_input_refs": [dict(market_date=d, revision=bar_map[d]["revision"]) for d in necessary],
    }


def event_outcomes(groups, m, view, cal, split):
    results = []
    for group in groups:
        event = group["primary"]
        for basis in m["entry_bases"]:
            for horizon in (5, 10, 20):
                row = label(
                    event["instrument_id"],
                    event["confirmation_market_date"],
                    event["confirmed_at"],
                    basis,
                    horizon,
                    m,
                    view,
                    cal,
                    split,
                )
                for tier in m["cost_assumption"]["slippage_bps"]:
                    r = {
                        **row,
                        "economic_event_id": group["economic_event_id"],
                        "rule_refs": group["rule_refs"],
                        "direction": event["direction"],
                        "trigger_market_date": event["trigger_market_date"],
                        "confirmation_market_date": event["confirmation_market_date"],
                        "cost_tier": tier,
                        "research_mode": m["research_mode"],
                        "costs_version": m["cost_assumption"]["version"],
                        **group["attributes"],
                    }
                    if row["status"] == "COMPLETED":
                        r.update(returns(row["entry_price"], row["exit_price"], tier, m["cost_assumption"]))
                    results.append(r)
    return results
