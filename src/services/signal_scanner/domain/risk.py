from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from src.services.signal_scanner.core import dec, text_decimal, require

DEFAULT_POLICY = dict(
    risk_pct="0.015",
    daily_loss_limit="0.03",
    monthly_loss_limit="0.10",
    portfolio_heat_limit="0.03",
    symbol_notional_limit="0.20",
    sector_notional_limit="0.40",
    fixed_entry_fee="1",
    fixed_exit_fee="1",
    per_share_fee="0",
    slippage_bps="5",
)


def validate_policy(value):
    p = {**DEFAULT_POLICY, **value}
    allowed = set(DEFAULT_POLICY)
    require(not set(p) - allowed, "VALIDATION_ERROR", status=422)
    for key in p:
        p[key] = text_decimal(dec(p[key], nonnegative=True))
    require(dec(p["risk_pct"]) <= Decimal(".02"), "OUT_OF_RANGE", "单笔风险上限为2%", 422)
    for key in (
        "daily_loss_limit",
        "monthly_loss_limit",
        "portfolio_heat_limit",
        "symbol_notional_limit",
        "sector_notional_limit",
    ):
        require(dec(p[key]) <= 1, "OUT_OF_RANGE", status=422)
    return p


def calculate_risk(inp, policy=None):
    p = {k: dec(v) for k, v in validate_policy(policy or {}).items()}
    required = (
        "equity",
        "settled_cash",
        "day_open_equity",
        "month_open_equity",
        "pnl_day",
        "pnl_month",
        "entry",
        "stop",
        "heat_open",
        "heat_pending",
        "cash_pending",
        "symbol_used",
        "sector_used",
    )
    missing = [k for k in required if inp.get(k) is None]
    reasons = list(inp.get("reasons", []))
    if missing:
        reasons.append("ACCOUNT_INCOMPLETE")
    if inp.get("environment", "NORMAL") not in ("NORMAL", "STRESS"):
        reasons.append("MARKET_STATUS_UNKNOWN")
    if reasons:
        return dict(
            status="BLOCKED", qty=None, q_final=None, all_reasons=sorted(set(reasons)), missing_fields=missing
        )
    d = {k: dec(inp[k]) for k in required}
    require(
        d["equity"] > 0 and d["day_open_equity"] > 0 and d["month_open_equity"] > 0,
        "OUT_OF_RANGE",
        status=422,
    )
    tick = dec(inp.get("tick", "0.01"))
    require(tick > 0, "OUT_OF_RANGE", status=422)
    entry = d["entry"]
    raw_stop = d["stop"]
    stop = (raw_stop / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    require(0 < stop < entry, "INVALID_PRICE_RELATION", "止损必须大于0且低于入场价", 422)
    env = Decimal(".5") if inp.get("environment") == "STRESS" else Decimal(1)
    recovery = Decimal(".5") if inp.get("recovery_remaining", 0) > 0 else Decimal(1)
    risk_open = d["heat_open"] + d["heat_pending"]
    budgets = dict(
        R_single=d["equity"] * p["risk_pct"] * env * recovery,
        R_heat=max(Decimal(0), d["equity"] * p["portfolio_heat_limit"] - risk_open),
        R_day=max(
            Decimal(0),
            d["day_open_equity"] * p["daily_loss_limit"] - max(Decimal(0), -d["pnl_day"]) - risk_open,
        ),
        R_month=max(
            Decimal(0),
            d["month_open_equity"] * p["monthly_loss_limit"] - max(Decimal(0), -d["pnl_month"]) - risk_open,
        ),
    )
    budget = min(budgets.values())
    slip_in = (
        dec(inp["slippage_in"]) if inp.get("slippage_in") is not None else entry * p["slippage_bps"] / 10000
    )
    slip_out = (
        dec(inp["slippage_out"]) if inp.get("slippage_out") is not None else entry * p["slippage_bps"] / 10000
    )
    per_risk = entry - stop + slip_in + slip_out + 2 * p["per_share_fee"]
    fixed = p["fixed_entry_fee"] + p["fixed_exit_fee"]
    floor = lambda x: int(max(Decimal(0), x).to_integral_value(rounding=ROUND_FLOOR))
    limits = dict(
        q_risk=floor((budget - fixed) / per_risk),
        q_cash=floor(
            (d["settled_cash"] - d["cash_pending"] - p["fixed_entry_fee"])
            / (entry + slip_in + p["per_share_fee"])
        ),
        q_symbol=floor((d["equity"] * p["symbol_notional_limit"] - d["symbol_used"]) / entry),
        q_sector=floor((d["equity"] * p["sector_notional_limit"] - d["sector_used"]) / entry),
    )
    q = min(limits.values())
    if inp.get("max_qty") is not None:
        require(isinstance(inp["max_qty"], int) and inp["max_qty"] >= 0, "OUT_OF_RANGE", status=422)
        q = min(q, inp["max_qty"])
    loss = q * per_risk + fixed if q else Decimal(0)
    cash = q * (entry + slip_in + p["per_share_fee"]) + p["fixed_entry_fee"] if q else Decimal(0)
    return dict(
        status="CALCULATED" if q else "CALCULATED_NO_CAPACITY",
        qty=str(q),
        q_final=str(q),
        **{k: str(v) for k, v in limits.items()},
        **{k: text_decimal(v) for k, v in budgets.items()},
        R_allow=text_decimal(budget),
        entry=text_decimal(entry),
        stop=text_decimal(stop),
        stop_before_rounding=text_decimal(raw_stop),
        planned_risk=text_decimal(loss),
        planned_loss=text_decimal(loss),
        cash_required=text_decimal(cash),
        environment_factor=text_decimal(env),
        recovery_factor=text_decimal(recovery),
        binding_limits=[k for k, v in limits.items() if v == min(limits.values())],
        costs=dict(
            slippage_in=text_decimal(slip_in),
            slippage_out=text_decimal(slip_out),
            fixed_fee=text_decimal(fixed),
        ),
        all_reasons=[] if q else ["INSUFFICIENT_BUDGET"],
    )
