from decimal import Decimal

from src.services.signal_scanner.core import dec, text_decimal


def paper_entry(open_price, lower, upper, slippage_bps=5):
    p = dec(open_price)
    lower = dec(lower)
    upper = dec(upper)
    fill = p * (1 + Decimal(slippage_bps) / 10000)
    if not lower <= p <= upper or fill > upper:
        return None
    return fill


def paper_exit(bar, stop, holding_session, slippage_bps=5):
    o, l, c = (dec(str(bar[k])) for k in ("open", "low", "close"))
    s = dec(stop)
    price = o if o <= s else s if l <= s else c if holding_session >= 20 else None
    return price * (1 - Decimal(slippage_bps) / 10000) if price is not None else None


def pnl(qty, entry, exit, fee="2", dividends="0"):
    return dec(str(qty)) * (dec(str(exit)) - dec(str(entry))) + dec(dividends) - dec(fee)


def outcomes(events, bars_by_instrument, cal, cutoff, slippage_bps="5", actions=()):
    from src.services.signal_scanner.domain.features import adjust

    results = []
    slip = dec(slippage_bps) / 10000
    for e in events:
        if e["kind"] != "DIRECTION" or not e.get("confirmation_market_date") or e["lifecycle"] == "RETRACTED":
            continue
        source = bars_by_instrument.get(e["instrument_id"], [])
        adjusted = (
            adjust(source, [a for a in actions if a.get("instrument_id") == e["instrument_id"]], cutoff)
            if source
            else []
        )
        bars = {b["market_date"]: b for b in adjusted}
        entrydate = cal.shift(e["confirmation_market_date"], 1)
        for horizon in (5, 10, 20):
            enddate = cal.shift(entrydate, horizon - 1)
            entrybar = bars.get(entrydate)
            endbar = bars.get(enddate)
            r = dict(
                dataset="SIGNAL_OUTCOME",
                economic_event_id=f"{e['instrument_id']}:{e['family']}:{e['confirmation_market_date']}:{e['direction']}",
                rule_id=e["rule_id"],
                rule_version=e["rule_version"],
                direction=e["direction"],
                horizon=horizon,
                confirmed_at=e["confirmed_at"],
                costs_version="costs-v1",
                data_mode="REVISED_HISTORY",
                status="PENDING",
                net_pnl=None,
                exit_market_date=enddate,
                entry_market_date=entrydate,
                notional_assumption="10000",
                stage=e["stage"],
            )
            if cal.session(enddate)["close_at"] <= cutoff:
                if not entrybar or not endbar:
                    r["status"] = "UNRESOLVED"
                elif e["confirmed_at"] >= cal.session(entrydate)["open_at"]:
                    r.update(status="SKIPPED", reason="LATE_AVAILABILITY")
                else:
                    entry = dec(str(entrybar["open"])) * (1 + slip)
                    exit = dec(str(endbar["close"])) * (1 - slip)
                    q = int(Decimal(10000) / entry)
                    profit = q * (exit - entry) - 2
                    r.update(
                        status="COMPLETED",
                        net_pnl=text_decimal(profit),
                        net_return=text_decimal(profit / (q * entry)) if q else None,
                        price_basis="ADJUSTED_CORPORATE_ACTIONS",
                        note="固定名义额的标的后续收益分布，非止损策略或实际收益；BEARISH同样记录标的涨跌，不模拟卖空收益",
                    )
            results.append(r)
    # BREAKOUT has one economic event. Select VP-01 as prescribed; do not count TR-01 twice.
    unique = {}
    for r in sorted(results, key=lambda x: x["rule_id"] != "VP-01"):
        unique.setdefault((r["economic_event_id"], r["horizon"]), r)
    return list(unique.values())
