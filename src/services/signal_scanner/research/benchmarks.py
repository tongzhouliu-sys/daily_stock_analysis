from src.services.signal_scanner.research.outcomes import label, returns


def benchmarks(rows, m, view, cal, split, progress=None):
    paired = []
    cache = {}
    for r in rows:
        for kind, iid in (("SPY", m["benchmark_ref"]["market"]), ("SECTOR", r.get("sector_etf_id"))):
            k = (
                iid,
                r["confirmation_market_date"],
                r["entry_market_date"],
                r["entry_basis"],
                r["horizon"],
                r["cost_tier"],
            )
            if k not in cache:
                base = None
                if iid:
                    # Pair by actual entry/exit dates, including delayed availability.
                    confirmation = r["confirmation_market_date"]
                    confirmed_at = view.cal.session(confirmation)["close_at"]
                    if r["entry_basis"] == "NEXT_AVAILABLE_OPEN":
                        confirmed_at = view.cal.session(cal.shift(r["entry_market_date"], -1))["close_at"]
                    base = label(
                        iid, confirmation, confirmed_at, r["entry_basis"], r["horizon"], m, view, cal, split
                    )
                    if base["status"] == "COMPLETED":
                        base.update(
                            returns(
                                base["entry_price"], base["exit_price"], r["cost_tier"], m["cost_assumption"]
                            )
                        )
                cache[k] = base
            base = cache[k]
            match = bool(
                base
                and base["status"] == "COMPLETED"
                and r["status"] == "COMPLETED"
                and base["entry_market_date"] == r["entry_market_date"]
                and base["exit_market_date"] == r["exit_market_date"]
            )
            from decimal import Decimal

            paired.append(
                dict(
                    economic_event_id=r["economic_event_id"],
                    kind=kind,
                    horizon=r["horizon"],
                    entry_basis=r["entry_basis"],
                    cost_tier=r["cost_tier"],
                    status="PAIRED" if match else "UNPAIRED",
                    reason_code=None if match else "BENCHMARK_OR_SIGNAL_UNAVAILABLE",
                    signal_net_return=r.get("net_return"),
                    benchmark=base,
                    excess_return=str(Decimal(r["net_return"]) - Decimal(base["net_return"]))
                    if match
                    else None,
                )
            )

    def unconditional_rows():
        from src.services.signal_scanner.research.inputs import active_members, clock_for

        days = cal.dates(m["start_date"], m["end_date"])
        for index, day in enumerate(days):
            if progress:
                progress(
                    dict(processed=index, total=len(days), phase="UNCONDITIONAL_BENCHMARK", market_date=day)
                )
            for iid in sorted(active_members(view.s, day, clock_for(day, m, cal))):
                for basis in m["entry_bases"]:
                    for horizon in (5, 10, 20):
                        r = label(iid, day, cal.session(day)["close_at"], basis, horizon, m, view, cal, split)
                        if r["split"] == "SEALED" and not m.get("sealed_revealed", False):
                            continue
                        for tier in m["cost_assumption"]["slippage_bps"]:
                            row = {
                                **r,
                                "cost_tier": tier,
                                "research_mode": m["research_mode"],
                                "direction": "UNDERLYING_RETURN",
                            }
                            if r["status"] == "COMPLETED":
                                row.update(
                                    returns(r["entry_price"], r["exit_price"], tier, m["cost_assumption"])
                                )
                            yield row

    from src.services.signal_scanner.research.statistics import summarize

    return paired, summarize(unconditional_rows(), m["min_completed_samples"], unconditional=True)
