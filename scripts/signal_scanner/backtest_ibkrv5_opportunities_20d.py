#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ibkrv5 商机表回测：按商机表时间买入股票，持有 20 天，计算收益。

输入:
- data/ibkrv5/opportunities_hourly_20260616_20261023.csv  小时级商机表(主)
- data/ibkrv5/recommendations_20260427_20261023.csv       每日扫描归档推荐(商机页数据源, 辅)
- data10/YYYY-MM-DD.csv                                   本地日线(覆盖至 2026-09-30)
- data/ibkrv5/backtest20d/yahoo_oct2026/*.json            10 月行情(Yahoo Finance 公开接口,
                                                          与 data10 重叠日 0 差异, 缓存到本地)

口径:
- 小时级商机表: 每一行 = 一个买入信号, 买入价 = 该行 price(信号时刻标的报价),
  卖出价 = 信号交易日(美东)之后第 20 个交易日收盘价; 收益 = 卖出/买入 - 1(价格收益, 不含股息)。
- 推荐表: 扫描在收盘后运行, 买入价 = 信号日收盘价, 卖出价 = 之后第 20 个交易日收盘价。
- 10 月数据来自 Yahoo(2026-10-01~10-23), 因此 20 个交易日窗口可覆盖到 2026-09-25 的信号;
  更晚的信号标记 pending, 不计入统计。
- 组合模拟: 任一交易日, 将资金等分给所有"在场"信号(买入日~卖出日), 按日均权重组,
  输出权益曲线/年化/波动/最大回撤; 对照组为同期持有 SPY。

输出:
- data/ibkrv5/backtest20d/trades_hourly.csv
- data/ibkrv5/backtest20d/trades_recommendations.csv
- data/ibkrv5/backtest20d/portfolio_daily.csv       组合模拟每日收益
- data/ibkrv5/backtest20d/summary.json
"""

import csv
import glob
import json
import os
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ET = ZoneInfo("America/New_York")
HOLD_TRADING_DAYS = 20
HOLD_CALENDAR_DAYS = 20

OPP_CSV = os.path.join(ROOT, "data/ibkrv5/opportunities_hourly_20260616_20261023.csv")
REC_CSV = os.path.join(ROOT, "data/ibkrv5/recommendations_20260427_20261023.csv")
OUT_DIR = os.path.join(ROOT, "data/ibkrv5/backtest20d")
YAHOO_DIR = os.path.join(OUT_DIR, "yahoo_oct2026")
YAHOO_P1 = int(datetime(2026, 9, 25, tzinfo=timezone.utc).timestamp())
YAHOO_P2 = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp())


# ---------- 行情加载 ----------

def load_data10():
    closes = defaultdict(dict)
    dates = set()
    for fp in sorted(glob.glob(os.path.join(ROOT, "data10/2026-*.csv"))):
        d = os.path.basename(fp)[:10]
        dates.add(d)
        with open(fp) as f:
            for row in csv.DictReader(f):
                closes[row["ticker"]][d] = float(row["close"])
    return closes, sorted(dates)


def fetch_yahoo(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={YAHOO_P1}&period2={YAHOO_P2}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def load_yahoo_oct(tickers):
    """读取/拉取 10 月行情, 返回 closes 增量(仅 2026-10-* 日期)。"""
    os.makedirs(YAHOO_DIR, exist_ok=True)
    oct_closes = defaultdict(dict)
    oct_dates = set()
    missing = []
    for t in tickers:
        cache = os.path.join(YAHOO_DIR, f"{t}.json")
        if os.path.exists(cache):
            with open(cache) as f:
                j = json.load(f)
        else:
            try:
                j = fetch_yahoo(t)
                with open(cache, "w") as f:
                    json.dump(j, f)
            except Exception as e:
                missing.append(t)
                continue
        try:
            res = j["chart"]["result"][0]
            for x, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
                if c is None:
                    continue
                d = datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%d")
                if d >= "2026-10-01":
                    oct_closes[t][d] = float(c)
                    oct_dates.add(d)
        except Exception:
            missing.append(t)
    return oct_closes, sorted(oct_dates), missing


# ---------- 工具 ----------

def et_date_of_ts(ts_str):
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(ET)
    return dt.strftime("%Y-%m-%d")


def first_trading_index(trading_dates, start_date):
    for i, d in enumerate(trading_dates):
        if d >= start_date:
            return i
    return None


def exit_after_trading_days(trading_dates, start_date, n):
    i = first_trading_index(trading_dates, start_date)
    if i is None:
        return None
    j = i + n
    return trading_dates[j] if j < len(trading_dates) else None


def exit_after_calendar_days(trading_dates, start_date, n):
    target = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")
    for d in trading_dates:
        if d >= target:
            return d
    return None


def trade_stats(rets):
    n = len(rets)
    if n == 0:
        return None
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    s = sorted(rets)
    return {
        "n": n,
        "mean": statistics.mean(rets),
        "median": statistics.median(rets),
        "stdev": statistics.pstdev(rets) if n > 1 else 0.0,
        "win_rate": len(wins) / n,
        "sum": sum(rets),
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
        "min": s[0],
        "max": s[-1],
        "p10": s[max(0, int(n * 0.10))],
        "p25": s[max(0, int(n * 0.25))],
        "p75": s[min(n - 1, int(n * 0.75))],
        "p90": s[min(n - 1, int(n * 0.90))],
    }


def fmt_stats(tag, st):
    if not st:
        return f"{tag}: 无有效交易"
    return (
        f"{tag}: n={st['n']} 平均={st['mean']*100:+.2f}% 中位={st['median']*100:+.2f}% "
        f"胜率={st['win_rate']*100:.1f}% 盈亏比={st['profit_factor'] if st['profit_factor'] is None else round(st['profit_factor'],2)} "
        f"最好={st['max']*100:+.1f}% 最差={st['min']*100:+.1f}%"
    )


# ---------- 回测 ----------

def backtest_hourly(closes, trading_dates):
    with open(OPP_CSV, encoding="utf-8-sig") as f:
        signals = list(csv.DictReader(f))
    trades = []
    for row in signals:
        tk = row["ticker"]
        et = et_date_of_ts(row["ts"])
        entry_px = float(row["price"]) if row["price"] else None
        rec = {
            "id": row["id"],
            "ts_utc": row["ts"],
            "et_date": et,
            "ticker": tk,
            "push_type": row["push_type"],
            "factor_score": row["factor_score"],
            "entry_price": entry_px,
            "close_on_signal_date": closes.get(tk, {}).get(et),
        }
        ex = exit_after_trading_days(trading_dates, et, HOLD_TRADING_DAYS)
        ex_px = closes.get(tk, {}).get(ex) if ex else None
        rec["exit_date_20td"] = ex or ""
        rec["exit_price_20td"] = ex_px
        rec["ret_20td_quote"] = (ex_px / entry_px - 1) if (ex_px and entry_px) else None
        cl = closes.get(tk, {}).get(et)
        rec["ret_20td_close"] = (ex_px / cl - 1) if (ex_px and cl) else None
        ex2 = exit_after_calendar_days(trading_dates, et, HOLD_CALENDAR_DAYS)
        ex2_px = closes.get(tk, {}).get(ex2) if ex2 else None
        rec["exit_date_20cd"] = ex2 or ""
        rec["ret_20cd_quote"] = (ex2_px / entry_px - 1) if (ex2_px and entry_px) else None
        trades.append(rec)
    return trades


def backtest_recommendations(closes, trading_dates):
    with open(REC_CSV, encoding="utf-8-sig") as f:
        signals = list(csv.DictReader(f))
    trades = []
    for row in signals:
        tk = row["ticker"]
        d = row["date"]
        cl = closes.get(tk, {}).get(d)
        ex = exit_after_trading_days(trading_dates, d, HOLD_TRADING_DAYS)
        ex_px = closes.get(tk, {}).get(ex) if ex else None
        trades.append({
            "id": row["id"],
            "date": d,
            "ticker": tk,
            "structure": row["structure"],
            "status": row["status"],
            "entry_close": cl,
            "exit_date_20td": ex or "",
            "exit_price_20td": ex_px,
            "ret_20td": (ex_px / cl - 1) if (ex_px and cl) else None,
        })
    return trades


# ---------- 组合模拟 ----------

def portfolio_sim(trades, closes, trading_dates, use_close_entry=False):
    """资金等分给所有在场信号, 每日等权。返回 (每日收益序列, 统计)。"""
    tset = set(trading_dates)
    # 每个交易日: 在场信号的当日收益
    daily = defaultdict(list)
    for t in trades:
        if use_close_entry:
            if t.get("ret_20td_close") is None:
                continue
        else:
            if t.get("ret_20td_quote") is None:
                continue
        tk, et, exd = t["ticker"], t["et_date"], t["exit_date_20td"]
        if not exd or exd not in tset:
            continue
        px = closes.get(tk, {})
        entry = t["entry_price"] if not use_close_entry else t["close_on_signal_date"]
        prev_ref = None
        first_seen = False
        for d in trading_dates:
            if d < et:
                continue
            if d > exd:
                break
            if d not in px:
                continue
            ref = entry if not first_seen else px[prev_ref]
            if d == et and use_close_entry:
                ref = px.get(et, entry)
                if ref is None:
                    continue
            daily[d].append(px[d] / ref - 1)
            first_seen = True
            prev_ref = d
    series = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for d in trading_dates:
        if d in daily and daily[d]:
            r = statistics.mean(daily[d])
            equity *= (1 + r)
            peak = max(peak, equity)
            max_dd = min(max_dd, equity / peak - 1)
            series.append((d, r, equity))
    if not series:
        return [], None
    rets = [r for _, r, _ in series]
    n = len(rets)
    total = equity - 1
    ann = (1 + total) ** (252 / n) - 1
    vol = statistics.pstdev(rets) * (252 ** 0.5)
    sharpe = (statistics.mean(rets) * 252) / (statistics.pstdev(rets) * (252 ** 0.5)) if statistics.pstdev(rets) > 0 else None
    stats = {
        "days": n, "start": series[0][0], "end": series[-1][0],
        "total_return": total, "annualized": ann, "annual_vol": vol,
        "sharpe": sharpe, "max_drawdown": max_dd,
        "avg_positions": statistics.mean([len(v) for v in daily.values()]) if daily else 0,
    }
    return series, stats


def group_stats(trades, keyfn, ret_key):
    groups = defaultdict(list)
    for t in trades:
        r = t.get(ret_key)
        if r is not None:
            groups[keyfn(t)].append(r)
    return {k: trade_stats(v) for k, v in sorted(groups.items())}


def spy_forward(closes, trading_dates, dates):
    spy = closes.get("SPY", {})
    out = {}
    for d in dates:
        ex = exit_after_trading_days(trading_dates, d, HOLD_TRADING_DAYS)
        if ex and d in spy and ex in spy:
            out[d] = spy[ex] / spy[d] - 1
    return out


def spy_hold(closes, trading_dates, d0, d1):
    spy = closes.get("SPY", {})
    if d0 in spy and d1 in spy:
        return spy[d1] / spy[d0] - 1
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("加载 data10 日线 ...")
    closes, td_dates = load_data10()
    print(f"  标的 {len(closes)} 个, 交易日 {len(td_dates)} 天 ({td_dates[0]} ~ {td_dates[-1]})")

    # 商机表 ticker + SPY
    with open(OPP_CSV, encoding="utf-8-sig") as f:
        opp_tickers = sorted({r["ticker"] for r in csv.DictReader(f)})
    need = sorted(set(opp_tickers) | {"SPY"})

    print("拉取 10 月行情 (Yahoo Finance, 缓存到 yahoo_oct2026/) ...")
    oct_closes, oct_dates, missing = load_yahoo_oct(need)
    print(f"  10 月交易日 {len(oct_dates)} 天 ({oct_dates[0]} ~ {oct_dates[-1]}), 缺失 {len(missing)} 个: {missing}")
    for t, dd in oct_closes.items():
        for d, c in dd.items():
            closes[t][d] = c
    trading_dates = sorted(set(td_dates) | set(oct_dates))
    print(f"  合并后交易日 {len(trading_dates)} 天 ({trading_dates[0]} ~ {trading_dates[-1]})")

    # ---------- 小时级商机表 ----------
    print("\n回测小时级商机表 ...")
    hourly = backtest_hourly(closes, trading_dates)
    done = [t for t in hourly if t["ret_20td_quote"] is not None]
    pending = [t for t in hourly if t["ret_20td_quote"] is None]
    print(f"  信号 {len(hourly)} 个, 完整可评估 {len(done)} 个, 窗口不足/缺数据 {len(pending)} 个")
    main_st = trade_stats([t["ret_20td_quote"] for t in done])
    print(" ", fmt_stats("主口径(每行1笔, 商机价买入, 持有20个交易日)", main_st))

    seen = {}
    for t in hourly:
        k = (t["ticker"], t["et_date"])
        if k not in seen or t["ts_utc"] < seen[k]["ts_utc"]:
            seen[k] = t
    dedup = [t for t in seen.values() if t["ret_20td_quote"] is not None]
    dedup_st = trade_stats([t["ret_20td_quote"] for t in dedup])
    print(" ", fmt_stats("去重口径(每标的每天仅首条)", dedup_st))

    done_close = [t for t in hourly if t["ret_20td_close"] is not None]
    close_st = trade_stats([t["ret_20td_close"] for t in done_close])
    print(" ", fmt_stats("对照口径(信号日收盘价买入)", close_st))

    done_cd = [t for t in hourly if t["ret_20cd_quote"] is not None]
    cd_st = trade_stats([t["ret_20cd_quote"] for t in done_cd])
    print(" ", fmt_stats("对照口径(持有20个自然日)", cd_st))

    pt_stats = {}
    for pt in ["intraday", "scan", "edge", "blocked"]:
        sub = [t["ret_20td_quote"] for t in done if t["push_type"] == pt]
        pt_stats[pt] = trade_stats(sub)
        print(" ", fmt_stats(f"push_type={pt}", pt_stats[pt]))

    # 基准
    et_dates = sorted({t["et_date"] for t in done})
    bm = spy_forward(closes, trading_dates, et_dates)
    bm_st = trade_stats(list(bm.values()))
    print(" ", fmt_stats("SPY 基准(同期信号日, 收盘买/20TD收盘卖)", bm_st))
    pairs = []
    for t in done_close:
        b = bm.get(t["et_date"])
        if b is not None:
            pairs.append((t["ret_20td_close"], b))
    beat = sum(1 for a, b in pairs if a > b)
    print(f"  跑赢 SPY 的交易占比: {beat}/{len(pairs)} = {beat/len(pairs)*100:.1f}%")

    # 组合模拟
    series, pf_st = portfolio_sim(done, closes, trading_dates)
    print(" ", fmt_stats("组合模拟(资金等分给所有在场信号)", None) if not pf_st else
          f"组合模拟: 天数 {pf_st['days']}, 区间 {pf_st['start']}~{pf_st['end']}, "
          f"总收益 {pf_st['total_return']*100:+.1f}%, 年化 {pf_st['annualized']*100:+.1f}%, "
          f"年化波动 {pf_st['annual_vol']*100:.1f}%, Sharpe {pf_st['sharpe']:.2f}, "
          f"最大回撤 {pf_st['max_drawdown']*100:.1f}%, 平均同时持仓 {pf_st['avg_positions']:.0f} 个")
    spy_hold_ret = spy_hold(closes, trading_dates, pf_st["start"], pf_st["end"]) if pf_st else None
    print(f"  同期 SPY 买入持有: {spy_hold_ret*100:+.1f}%" if spy_hold_ret is not None else "")

    # ---------- 推荐表 ----------
    print("\n回测每日推荐表(商机页数据源) ...")
    recs = backtest_recommendations(closes, trading_dates)
    rec_done = [t for t in recs if t["ret_20td"] is not None]
    rec_pending = [t for t in recs if t["ret_20td"] is None]
    print(f"  信号 {len(recs)} 个, 完整可评估 {len(rec_done)} 个, 窗口不足 {len(rec_pending)} 个")
    rec_st = trade_stats([t["ret_20td"] for t in rec_done])
    print(" ", fmt_stats("推荐表(收盘买, 持有20个交易日)", rec_st))
    bm_rec = spy_forward(closes, trading_dates, sorted({t["date"] for t in rec_done}))
    bm_rec_st = trade_stats(list(bm_rec.values()))
    print(" ", fmt_stats("SPY 基准(推荐表同期)", bm_rec_st))
    series_rec, pf_rec = portfolio_sim(
        [{"et_date": t["date"], "ticker": t["ticker"], "exit_date_20td": t["exit_date_20td"],
          "entry_price": t["entry_close"], "ret_20td_quote": t["ret_20td"],
          "close_on_signal_date": t["entry_close"], "ret_20td_close": t["ret_20td"]} for t in rec_done],
        closes, trading_dates, use_close_entry=True)
    if pf_rec:
        print(f"  推荐表组合模拟: 总收益 {pf_rec['total_return']*100:+.1f}%, 年化 {pf_rec['annualized']*100:+.1f}%, "
              f"最大回撤 {pf_rec['max_drawdown']*100:.1f}%, Sharpe {pf_rec['sharpe']:.2f}")

    # ---------- 汇总 ----------
    summary = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data10_through": td_dates[-1],
            "yahoo_through": oct_dates[-1] if oct_dates else None,
            "trading_days_through": trading_dates[-1],
            "hold_trading_days": HOLD_TRADING_DAYS,
            "hold_calendar_days_variant": HOLD_CALENDAR_DAYS,
            "price_note": "价格收益,不含股息; 10 月行情来自 Yahoo Finance(与 data10 重叠日 0 差异)",
            "portfolio_note": "组合模拟=资金等分给所有在场信号,每日等权,忽略资金上限与交易成本",
        },
        "hourly": {
            "signals_total": len(hourly),
            "signals_complete": len(done),
            "signals_pending": len(pending),
            "pending_from": min((t["et_date"] for t in pending), default=None),
            "main_quote_entry_20td": main_st,
            "dedup_first_per_ticker_day": dedup_st,
            "close_entry_20td": close_st,
            "quote_entry_20cd": cd_st,
            "by_push_type": pt_stats,
            "by_month": group_stats(done, lambda t: t["et_date"][:7], "ret_20td_quote"),
            "by_ticker": group_stats(done, lambda t: t["ticker"], "ret_20td_quote"),
            "spy_same_dates_20td": bm_st,
            "beat_spy_share": beat / len(pairs) if pairs else None,
            "portfolio": pf_st,
            "spy_hold_same_period": spy_hold_ret,
        },
        "recommendations": {
            "signals_total": len(recs),
            "signals_complete": len(rec_done),
            "signals_pending": len(rec_pending),
            "close_entry_20td": rec_st,
            "by_month": group_stats(rec_done, lambda t: t["date"][:7], "ret_20td"),
            "by_ticker": group_stats(rec_done, lambda t: t["ticker"], "ret_20td"),
            "spy_same_dates_20td": bm_rec_st,
            "portfolio": pf_rec,
        },
    }

    with open(os.path.join(OUT_DIR, "trades_hourly.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(hourly[0].keys()))
        w.writeheader()
        w.writerows(hourly)
    with open(os.path.join(OUT_DIR, "trades_recommendations.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)
    with open(os.path.join(OUT_DIR, "portfolio_daily.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "daily_ret", "equity"])
        w.writerows(series)
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print("\n== 按月(主口径) ==")
    for m, st in summary["hourly"]["by_month"].items():
        print(f"  {m}: n={st['n']:5d} 平均={st['mean']*100:+6.2f}% 中位={st['median']*100:+6.2f}% 胜率={st['win_rate']*100:5.1f}%")
    print("\n== 推荐表按月 ==")
    for m, st in summary["recommendations"]["by_month"].items():
        print(f"  {m}: n={st['n']:5d} 平均={st['mean']*100:+6.2f}% 胜率={st['win_rate']*100:5.1f}%")
    print("\n== 信号数最多的 15 个标的(主口径) ==")
    for tk, st in sorted(summary["hourly"]["by_ticker"].items(), key=lambda kv: -kv[1]["n"])[:15]:
        print(f"  {tk:6s} n={st['n']:5d} 平均={st['mean']*100:+6.2f}% 胜率={st['win_rate']*100:5.1f}%")
    print("\n== 平均收益最好/最差的标的(n>=20, 主口径) ==")
    elig = [(tk, st) for tk, st in summary["hourly"]["by_ticker"].items() if st["n"] >= 20]
    for tk, st in sorted(elig, key=lambda kv: -kv[1]["mean"])[:10]:
        print(f"  {tk:6s} n={st['n']:5d} 平均={st['mean']*100:+6.2f}% 胜率={st['win_rate']*100:5.1f}%")
    print("  ...")
    for tk, st in sorted(elig, key=lambda kv: kv[1]["mean"])[:10]:
        print(f"  {tk:6s} n={st['n']:5d} 平均={st['mean']*100:+6.2f}% 胜率={st['win_rate']*100:5.1f}%")

    print(f"\n输出: {OUT_DIR}/trades_hourly.csv, trades_recommendations.csv, portfolio_daily.csv, summary.json")


if __name__ == "__main__":
    main()
