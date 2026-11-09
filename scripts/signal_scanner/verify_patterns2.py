#!/usr/bin/env python3
"""高IV规律的个股固定效应检验 + 10月行情冲击评估 + 推荐表分层 + 止损测试。"""
import csv, json, sys, datetime, statistics
from collections import defaultdict

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ibkrv5_opportunities_20d import load_data10, load_yahoo_oct, exit_after_trading_days, portfolio_sim

def fnum(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

closes, _ = load_data10()
ohlc = defaultdict(dict)
import glob, os
for fp in glob.glob('data10/2026-*.csv'):
    d = os.path.basename(fp)[:10]
    with open(fp) as f:
        for row in csv.DictReader(f):
            ohlc[row['ticker']][d] = (float(row['high']), float(row['low']))
tickers_all = sorted({r['ticker'] for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv'))} | {'SPY'})
oct_c, oct_d, miss = load_yahoo_oct(tickers_all)
for t, dd in oct_c.items():
    for d, c in dd.items(): closes[t][d] = c
tdates = sorted(set(closes['SPY'].keys()))
spy = closes['SPY']

raw = {r['id']: r for r in csv.DictReader(open('data/ibkrv5/opportunities_hourly_20260616_20261023.csv', encoding='utf-8-sig'))}
done = [r for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv')) if r['ret_20td_quote']]
for r in done:
    src = raw.get(r['id'], {})
    r['entry_price'] = fnum(r['entry_price']); r['ret'] = fnum(r['ret_20td_quote'])
    r['ann_yield'] = fnum(src.get('ann_yield'))
    iv = fnum(src.get('iv')); r['iv'] = iv if iv is not None and iv < 2 else None

def st(rows):
    v = [r['ret'] for r in rows]
    if not v: return None
    return {'n': len(v), 'mean': statistics.mean(v), 'med': statistics.median(v), 'win': sum(1 for x in v if x > 0)/len(v)}

# ===== 1. 个股固定效应：同标的内 高IV vs 低IV =====
print('=== 1. 个股固定效应检验（同标的内部比较） ===')
by_tk = defaultdict(list)
for r in done: by_tk[r['ticker']].append(r)
diffs = []
print('  标的      高IV均收益   低IV均收益   标的内差   n(高/低)')
for t, rows in sorted(by_tk.items()):
    hi = [r['ret'] for r in rows if r['iv'] and r['iv'] > 0.6]
    lo = [r['ret'] for r in rows if r['iv'] and r['iv'] <= 0.6]
    if len(hi) >= 10 and len(lo) >= 10:
        d = statistics.mean(hi) - statistics.mean(lo)
        diffs.append(d)
        print(f'  {t:6s}   {statistics.mean(hi)*100:+7.2f}%   {statistics.mean(lo)*100:+7.2f}%   {d*100:+7.2f}%   {len(hi):3d}/{len(lo):3d}')
print(f'  标的内差: 均值 {statistics.mean(diffs)*100:+.2f}pp, 中位 {statistics.median(diffs)*100:+.2f}pp, 正差标的 {sum(1 for d in diffs if d>0)}/{len(diffs)}')

# ===== 2. 行业/板块构成：高IV池 vs 全池，7-9月涨幅 =====
print('\n=== 2. 高IV池标的 6-16~9-25 区间涨幅（个股Beta评估） ===')
hi_tk = sorted({r['ticker'] for r in done if r['iv'] and r['iv'] > 0.6})
print(f'  高IV池 {len(hi_tk)} 个标的: {hi_tk}')
gains = []
for t in hi_tk:
    px = closes.get(t, {})
    days = [d for d in tdates if d in px and '2026-06-16' <= d <= '2026-09-25']
    if len(days) >= 2:
        g = px[days[-1]] / px[days[0]] - 1
        gains.append(g)
        print(f'    {t:6s} {g*100:+8.1f}%')
spy_days = [d for d in tdates if '2026-06-16' <= d <= '2026-09-25']
print(f'    SPY 同期 { (spy[spy_days[-1]]/spy[spy_days[0]]-1)*100:+.1f}%  高IV池等权 {statistics.mean(gains)*100:+.1f}%')

# ===== 3. 10月行情冲击：这些票9月底到10月23日跌了多少 =====
print('\n=== 3. 10月行情冲击评估（9-30 -> 10-23 收盘） ===')
oct_loss = []
for t in hi_tk:
    px = closes.get(t, {})
    if '2026-09-30' in px and '2026-10-23' in px:
        g = px['2026-10-23'] / px['2026-09-30'] - 1
        oct_loss.append(g)
        print(f'    {t:6s} {g*100:+8.1f}%')
print(f'    高IV池等权 {statistics.mean(oct_loss)*100:+.1f}%  SPY {(spy["2026-10-23"]/spy["2026-09-30"]-1)*100:+.1f}%')
# 若把IV>0.6策略延到10月（信号9-28~10-9入场、持有20TD在10月底结束——窗口未闭合，用代理：按池内10月跌幅估算）
print(f'    >>> 若9月底持有该池至今: 等权 {statistics.mean(oct_loss)*100:+.1f}%（策略在10月大概率由盈转亏）')

# ===== 4. 推荐表分层（jsonl 因子 × 已知收益） =====
print('\n=== 4. 推荐表分层（rank / sequence_n / regime_t0 / consec_down_days / sim_outcome） ===')
rec = {}
for line in open('data/ibkrv5/recommendations_full_20260427_20261023.jsonl'):
    d = json.loads(line)
    rec[d['id']] = d
rec_tr = [r for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_recommendations.csv')) if r['ret_20td']]
print('  recommendations 可评估:', len(rec_tr), '（其中 hourly 表交集已单列）')
rows = []
for r in rec_tr:
    src = rec.get(int(r['id']), {})
    rows.append({'ret': float(r['ret_20td']), 'date': r['date'], 'ticker': r['ticker'],
                 'rank': fnum(src.get('rank')), 'seq': fnum(src.get('sequence_n')),
                 'regime': src.get('regime_t0'), 'consec': fnum(src.get('consec_down_days')),
                 'vix': fnum(src.get('vix')), 'sim': src.get('sim_outcome'),
                 'score': fnum(src.get('factor_score')), 'ivr': fnum(src.get('ivr')),
                 'dte': fnum(src.get('dte')), 'earn': fnum(src.get('days_to_earnings_t0'))})
def show(t, grp):
    s = st([{'ret': x['ret']} for x in grp])
    if s and s['n'] >= 8:
        print(f'  {t:30s} n={s["n"]:4d}  mean={s["mean"]*100:+6.2f}%  med={s["med"]*100:+6.2f}%  win={s["win"]*100:5.1f}%')
show('rank 1-2', [r for r in rows if r['rank'] and r['rank'] <= 2])
show('rank 3-4', [r for r in rows if r['rank'] and 3 <= r['rank'] <= 4])
show('rank 5+', [r for r in rows if r['rank'] and r['rank'] >= 5])
show('sequence_n=1（首次）', [r for r in rows if r['seq'] == 1])
show('sequence_n>=2（重复）', [r for r in rows if r['seq'] and r['seq'] >= 2])
for rg in ['trending_up', 'trending_down', 'choppy', 'high_vol', 'low_vol']:
    show(f'regime={rg}', [r for r in rows if r['regime'] == rg])
show('consec_down=0', [r for r in rows if r['consec'] == 0])
show('consec_down>=1', [r for r in rows if r['consec'] and r['consec'] >= 1])
show('consec_down>=3', [r for r in rows if r['consec'] and r['consec'] >= 3])
show('VIX<18', [r for r in rows if r['vix'] and r['vix'] < 18])
show('VIX 18-22', [r for r in rows if r['vix'] and 18 <= r['vix'] <= 22])
show('VIX>22', [r for r in rows if r['vix'] and r['vix'] > 22])
show('CSP sim=win', [r for r in rows if r['sim'] == 'win'])
show('CSP sim=loss', [r for r in rows if r['sim'] == 'loss'])
show('ivr>=50', [r for r in rows if r['ivr'] and r['ivr'] >= 50])
show('ivr<50', [r for r in rows if r['ivr'] and r['ivr'] < 50])
show('dte<=35', [r for r in rows if r['dte'] and r['dte'] <= 35])
show('dte>35', [r for r in rows if r['dte'] and r['dte'] > 35])
show('近财报(<14天)', [r for r in rows if r['earn'] is not None and r['earn'] < 14])
show('财报远(>=14天)', [r for r in rows if r['earn'] is not None and r['earn'] >= 14])

# ===== 5. 止损/止盈测试（IV>0.6 信号，20日窗口，用 high/low） =====
print('\n=== 5. 止损测试（IV>0.6 信号，信号日收盘入场，20TD窗口，日线low触及止损） ===')
iv_rows = [r for r in done if r['iv'] and r['iv'] > 0.6 and r['et_date'] <= '2026-09-05']  # exit<=9-30 保证有low数据
print(f'  可用样本: {len(iv_rows)}')
for sl in [None, 0.05, 0.08, 0.10, 0.15]:
    for tp in [None]:
        rets = []
        for r in iv_rows:
            ex = exit_after_trading_days(tdates, r['et_date'], 20)
            if not ex: continue
            entry = r['entry_price']
            path = []
            di = tdates.index(r['et_date'])
            stopped = False
            for d in tdates[di:tdates.index(ex)+1]:
                if d not in ohlc.get(r['ticker'], {}): continue
                hi, lo = ohlc[r['ticker']][d]
                if sl and lo <= entry * (1 - sl):
                    rets.append(-sl); stopped = True; break
                path.append(d)
            if not stopped:
                rets.append(closes[r['ticker']][ex] / entry - 1)
        print(f'  止损{("%d%%" % (sl*100)) if sl else "无":>5s}: n={len(rets):4d} mean={statistics.mean(rets)*100:+6.2f}% med={statistics.median(rets)*100:+6.2f}% win={sum(1 for x in rets if x>0)/len(rets)*100:5.1f}% 触发止损={sum(1 for x in rets if x<=-sl-1e-9) if sl else 0}')

json.dump({'note': 'stdout'}, open('data/ibkrv5/backtest20d/patterns_verify2.json', 'w'))
