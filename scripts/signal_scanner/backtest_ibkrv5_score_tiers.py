#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ibkrv5 商机评分(factor_score)分层回测：只买高评分商机的股票、持有 20 天，收益如何。

背景(见 doc/ibkrv5_商机表20天持有回测.md):
- 小时级商机表 7938 行中只有 1123 行有 factor_score, 且全部是 2026-10 的 blocked 行,
  20 个交易日窗口未闭合(行情只到 10-23), 无法评估 -> 评分分层只能在 recommendations 表上做。
- recommendations 表 1645 行中 1330 行有评分(2026-06-16 起), 范围 43.3~93.8, 中位 70.0。

口径与基础回测一致:
- 买入价 = 信号日收盘价(每日扫描在收盘后运行), 卖出价 = 之后第 20 个交易日收盘价。
- 分层: <50 / 50-60 / 60-70 / 70-80 / >=80 及十分位; 用户关心的 >60 汇总单独给出。
- 每层输出逐笔统计 + 等权组合模拟 + 同期 SPY; 另算 score 与 20 日收益的相关性(IC)。

输出:
- data/ibkrv5/backtest20d/score_tiers.json
- data/ibkrv5/backtest20d/trades_recommendations_scored.csv  带评分分层的逐笔明细
"""

import csv
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_ibkrv5_opportunities_20d import (  # noqa: E402
    ROOT, OUT_DIR, load_data10, load_yahoo_oct, exit_after_trading_days,
    trade_stats, portfolio_sim, fmt_stats,
)

REC_CSV = os.path.join(ROOT, "data/ibkrv5/recommendations_20260427_20261023.csv")


def build_trades(closes, trading_dates):
    with open(REC_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    trades = []
    for r in rows:
        tk, d = r["ticker"], r["date"]
        cl = closes.get(tk, {}).get(d)
        ex = exit_after_trading_days(trading_dates, d, 20)
        ex_px = closes.get(tk, {}).get(ex) if ex else None
        score = float(r["factor_score"]) if r["factor_score"] else None
        t = {
            "id": r["id"], "date": d, "ticker": tk, "structure": r["structure"],
            "status": r["status"], "rank": r["rank"], "score": score,
            "ann_yield": r["ann_yield"], "ivr": r["ivr"],
            "sim_outcome": r["sim_outcome"],
            "entry_close": cl, "exit_date_20td": ex or "", "exit_price_20td": ex_px,
            "ret_20td": (ex_px / cl - 1) if (ex_px and cl) else None,
        }
        if score is not None:
            t["tier"] = ("<50" if score < 50 else "50-60" if score < 60 else
                         "60-70" if score < 70 else "70-80" if score < 80 else ">=80")
            t["decile"] = min(10, max(1, int((score - 43.3) / (93.8 - 43.3) * 10) + 1))
        trades.append(t)
    return trades


def pearson(xs, ys):
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def tier_portfolio(trades, closes, trading_dates):
    pts = [{"et_date": t["date"], "ticker": t["ticker"], "exit_date_20td": t["exit_date_20td"],
            "entry_price": t["entry_close"], "ret_20td_quote": t["ret_20td"],
            "close_on_signal_date": t["entry_close"], "ret_20td_close": t["ret_20td"]}
           for t in trades if t["ret_20td"] is not None]
    return portfolio_sim(pts, closes, trading_dates, use_close_entry=True)


def main():
    closes, td = load_data10()
    tickers = sorted({r["ticker"] for r in csv.DictReader(open(REC_CSV, encoding="utf-8-sig"))} | {"SPY"})
    oct_c, oct_d, _ = load_yahoo_oct(tickers)
    for t, dd in oct_c.items():
        for d, c in dd.items():
            closes[t][d] = c
    trading_dates = sorted(set(td) | set(oct_d))

    trades = build_trades(closes, trading_dates)
    scored = [t for t in trades if t["score"] is not None]
    sc_done = [t for t in scored if t["ret_20td"] is not None]
    print(f"recommendations: {len(trades)} 行, 有评分 {len(scored)}, 有评分且可评估 {len(sc_done)}")
    print(f"评分范围 {min(t['score'] for t in scored):.1f} ~ {max(t['score'] for t in scored):.1f}, "
          f"中位 {statistics.median([t['score'] for t in scored]):.1f}")

    # SPY 基准(同信号日)
    spy = closes["SPY"]
    spy_fwd = {}
    for t in sc_done:
        d, ex = t["date"], t["exit_date_20td"]
        if ex in spy:
            spy_fwd[d] = spy[ex] / spy[d] - 1
    spy_st = trade_stats(list(spy_fwd.values()))
    print(" ", fmt_stats("SPY 基准(同信号日, 收盘买/20TD卖)", spy_st))

    out = {"meta": {
        "hold_trading_days": 20,
        "entry": "信号日收盘价", "exit": "之后第20个交易日收盘价",
        "scored_rows": len(scored), "scored_evaluable": len(sc_done),
        "spy_same_dates": spy_st,
    }}

    # ---- 用户问题: >60 ----
    gt60 = [t for t in sc_done if t["score"] > 60]
    st = trade_stats([t["ret_20td"] for t in gt60])
    print("\n== score > 60 ==")
    print(" ", fmt_stats(">60 逐笔", st))
    _, pf = tier_portfolio(gt60, closes, trading_dates)
    print(f"  >60 组合模拟: 总收益 {pf['total_return']*100:+.1f}%, 年化 {pf['annualized']*100:+.1f}%, "
          f"Sharpe {pf['sharpe']:.2f}, 最大回撤 {pf['max_drawdown']*100:.1f}%")
    # 对照: <=60
    le60 = [t for t in sc_done if t["score"] <= 60]
    st2 = trade_stats([t["ret_20td"] for t in le60])
    print(" ", fmt_stats("<=60 逐笔(对照)", st2))
    _, pf2 = tier_portfolio(le60, closes, trading_dates)
    print(f"  <=60 组合模拟: 总收益 {pf2['total_return']*100:+.1f}%, Sharpe {pf2['sharpe']:.2f}")
    # 跑赢SPY比例
    beat = sum(1 for t in gt60 if t["date"] in spy_fwd and t["ret_20td"] > spy_fwd[t["date"]])
    print(f"  >60 跑赢 SPY 占比: {beat}/{len(gt60)} = {beat/len(gt60)*100:.1f}%")
    out["gt60"] = {"stats": st, "portfolio": pf, "beat_spy": beat / len(gt60),
                   "le60": {"stats": st2, "portfolio": pf2}}

    # ---- 阈值扫描 ----
    print("\n== 阈值扫描 ==")
    sweep = {}
    for th in [40, 50, 55, 60, 65, 70, 75, 80, 85]:
        sub = [t for t in sc_done if t["score"] > th]
        s = trade_stats([t["ret_20td"] for t in sub])
        _, p = tier_portfolio(sub, closes, trading_dates)
        sweep[th] = {"n": s["n"], "mean": s["mean"], "median": s["median"],
                     "win_rate": s["win_rate"], "pf": s["profit_factor"],
                     "portfolio_total": p["total_return"], "portfolio_sharpe": p["sharpe"]}
        print(f"  >{th}: n={s['n']:4d} 平均={s['mean']*100:+6.2f}% 中位={s['median']*100:+6.2f}% "
              f"胜率={s['win_rate']*100:5.1f}% 组合={p['total_return']*100:+6.1f}%")
    out["sweep"] = sweep

    # ---- 分层 ----
    print("\n== 评分分层 ==")
    tiers = {}
    for tr in ["<50", "50-60", "60-70", "70-80", ">=80"]:
        sub = [t for t in sc_done if t["tier"] == tr]
        s = trade_stats([t["ret_20td"] for t in sub])
        _, p = tier_portfolio(sub, closes, trading_dates)
        tiers[tr] = {"n": s["n"], "mean": s["mean"], "median": s["median"],
                     "win_rate": s["win_rate"], "pf": s["profit_factor"],
                     "portfolio_total": p["total_return"], "portfolio_sharpe": p["sharpe"]}
        print(f"  {tr:6s}: n={s['n']:4d} 平均={s['mean']*100:+6.2f}% 中位={s['median']*100:+6.2f}% "
              f"胜率={s['win_rate']*100:5.1f}% 组合={p['total_return']*100:+6.1f}%")
    out["tiers"] = tiers

    # ---- 十分位 ----
    print("\n== 十分位(按评分等分10组) ==")
    dec = {}
    for k in range(1, 11):
        sub = [t for t in sc_done if t["decile"] == k]
        if not sub:
            continue
        s = trade_stats([t["ret_20td"] for t in sub])
        sc_range = (min(t["score"] for t in sub), max(t["score"] for t in sub))
        dec[k] = {"n": s["n"], "mean": s["mean"], "win_rate": s["win_rate"],
                  "score_range": sc_range}
        print(f"  D{k:2d} ({sc_range[0]:.1f}~{sc_range[1]:.1f}): n={s['n']:4d} "
              f"平均={s['mean']*100:+6.2f}% 胜率={s['win_rate']*100:5.1f}%")
    out["deciles"] = dec

    # ---- 相关性(IC) ----
    xs = [t["score"] for t in sc_done]
    ys = [t["ret_20td"] for t in sc_done]
    ic_p = pearson(xs, ys)
    ic_s = pearson(ranks(xs), ranks(ys))
    print(f"\nIC: Pearson={ic_p:.4f} Spearman={ic_s:.4f} (n={len(xs)})")
    # 按月 IC
    monthly_ic = {}
    for m in sorted({t["date"][:7] for t in sc_done}):
        sub = [t for t in sc_done if t["date"][:7] == m]
        if len(sub) >= 10:
            monthly_ic[m] = pearson([t["score"] for t in sub], [t["ret_20td"] for t in sub])
    print("按月 IC:", {k: round(v, 3) for k, v in monthly_ic.items() if v is not None})
    out["ic"] = {"pearson": ic_p, "spearman": ic_s, "monthly": monthly_ic}

    # ---- rank 分层(Top-N vs 未排名) ----
    print("\n== rank 分层 ==")
    rk = {}
    for label, sel in [("rank1-3", lambda r: r["rank"] in ("1", "2", "3")),
                       ("rank4-6", lambda r: r["rank"] in ("4", "5", "6")),
                       ("未排名(-1)", lambda r: r["rank"] == "-1")]:
        sub = [t for t in sc_done if sel(t)]
        if not sub:
            continue
        s = trade_stats([t["ret_20td"] for t in sub])
        _, p = tier_portfolio(sub, closes, trading_dates)
        rk[label] = {"n": s["n"], "mean": s["mean"], "win_rate": s["win_rate"],
                     "portfolio_total": p["total_return"]}
        print(f"  {label:10s}: n={s['n']:4d} 平均={s['mean']*100:+6.2f}% 胜率={s['win_rate']*100:5.1f}% "
              f"组合={p['total_return']*100:+6.1f}%")
    out["by_rank"] = rk

    # ---- 落盘 ----
    with open(os.path.join(OUT_DIR, "score_tiers.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT_DIR, "trades_recommendations_scored.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(scored[0].keys()))
        w.writeheader()
        w.writerows(scored)
    print(f"\n输出: {OUT_DIR}/score_tiers.json, trades_recommendations_scored.csv")


if __name__ == "__main__":
    main()
