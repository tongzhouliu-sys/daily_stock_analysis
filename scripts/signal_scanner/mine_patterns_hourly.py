#!/usr/bin/env python3
"""ibkrv5 商机信号规律挖掘：持有期、push_type、因子分层、市场环境、信号序号。"""
import csv, json, sys, datetime, statistics
from collections import defaultdict
from zoneinfo import ZoneInfo

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_ibkrv5_opportunities_20d import load_data10, load_yahoo_oct, exit_after_trading_days

ET = ZoneInfo('America/New_York')
HORIZONS = [5, 10, 20, 30, 40, 60]

def fnum(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

# ---------- load prices ----------
closes, _ = load_data10()
# also load OHLC for MAE/MFE
ohlc = defaultdict(dict)
import glob, os
for fp in glob.glob('data10/2026-*.csv'):
    d = os.path.basename(fp)[:10]
    with open(fp) as f:
        for row in csv.DictReader(f):
            ohlc[row['ticker']][d] = (float(row['open']), float(row['high']), float(row['low']), float(row['close']))
tickers_all = sorted({r['ticker'] for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv'))} | {'SPY'})
oct_c, oct_d, miss = load_yahoo_oct(tickers_all)
for t, dd in oct_c.items():
    for d, c in dd.items():
        closes[t][d] = c
tdates = sorted(set(closes['SPY'].keys()))
print('trading dates:', tdates[0], '~', tdates[-1], len(tdates))

# ---------- load hourly raw + join returns ----------
raw = {r['id']: r for r in csv.DictReader(open('data/ibkrv5/opportunities_hourly_20260616_20261023.csv', encoding='utf-8-sig'))}
trades = list(csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv')))
done = [r for r in trades if r['ret_20td_quote']]
print('evaluable trades:', len(done))

# enrich each trade with factors
for r in done:
    src = raw.get(r['id'], {})
    r['entry_price'] = fnum(r['entry_price'])
    r['ret_20td_quote'] = fnum(r['ret_20td_quote'])
    r['ann_yield'] = fnum(src.get('ann_yield'))
    r['delta'] = fnum(src.get('delta'))
    iv = fnum(src.get('iv'))
    r['iv'] = iv if iv is not None and iv < 2 else None  # drop junk (99.11)
    r['prem_ratio'] = (fnum(src.get('premium')) / fnum(src.get('strike'))) if src.get('premium') and src.get('strike') else None
    r['moneyness'] = (fnum(src.get('strike')) / r['entry_price']) if src.get('strike') and r['entry_price'] else None
    try:
        r['dte'] = (datetime.date.fromisoformat(src['expiry'][:10]) - datetime.date.fromisoformat(r['et_date'])).days
    except Exception:
        r['dte'] = None

# ---------- SPY momentum at signal date ----------
spy = closes['SPY']
spy_ts = [(d, spy[d]) for d in tdates]
spy_idx = {d: i for i, (d, _) in enumerate(spy_ts)}
def spy_mom(sig_date, n):
    if sig_date not in spy_idx: return None
    i = spy_idx[sig_date]
    if i < n: return None
    return spy[tdates[i]] / spy[tdates[i - n]] - 1
for r in done:
    r['spy_mom20'] = spy_mom(r['et_date'], 20)
    r['spy_mom5'] = spy_mom(r['et_date'], 5)

# ---------- signal sequence number per ticker ----------
seen_t = defaultdict(int)
for r in done:
    seen_t[r['ticker']] += 1
    r['seq_total'] = seen_t[r['ticker']]

def stats(rows, key='ret_20td_quote'):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals: return None
    return {'n': len(vals), 'mean': statistics.mean(vals), 'median': statistics.median(vals),
            'win': sum(1 for v in vals if v > 0) / len(vals)}

def show(title, groups, note=''):
    print(f'\n=== {title} {note} ===')
    for k, rows in groups:
        s = stats(rows)
        if s and s['n'] >= 10:
            print(f'  {str(k):22s} n={s["n"]:5d}  mean={s["mean"]*100:+7.2f}%  med={s["median"]*100:+7.2f}%  win={s["win"]*100:5.1f}%')

results = {}

# ---------- A. horizon sensitivity (per-trade, all signals) ----------
print('\n=== A. 持有期敏感性（全部可评估信号）===')
hor = {}
for h in HORIZONS:
    rets = []
    for r in done:
        ex = exit_after_trading_days(tdates, r['et_date'], h)
        if ex and r['ticker'] in closes and ex in closes[r['ticker']]:
            rets.append(closes[r['ticker']][ex] / r['entry_price'] - 1)
    s = stats([{'ret_20td_quote': v} for v in rets])
    hor[h] = s
    if s: print(f'  {h:3d}交易日: n={s["n"]:5d} mean={s["mean"]*100:+7.2f}% med={s["median"]*100:+7.2f}% win={s["win"]*100:5.1f}%')
# SPY same horizons
print('  SPY 同期（相同信号日）:')
spy_hor = {}
for h in HORIZONS:
    rets = []
    for r in done:
        ex = exit_after_trading_days(tdates, r['et_date'], h)
        if ex and ex in spy:
            rets.append(spy[ex] / spy[r['et_date']] - 1)
    s = stats([{'ret_20td_quote': v} for v in rets])
    spy_hor[h] = s
    if s: print(f'  {h:3d}交易日: n={s["n"]:5d} mean={s["mean"]*100:+7.2f}% med={s["median"]*100:+7.2f}% win={s["win"]*100:5.1f}%')
results['horizon'] = {'signal': hor, 'spy': spy_hor}

# ---------- B. push_type ----------
show('B. push_type（20日）', sorted(((k, [r for r in done if r['push_type'] == k]) for k in {r['push_type'] for r in done})))
results['push_type'] = {k: stats([r for r in done if r['push_type'] == k]) for k in {r['push_type'] for r in done}}

# ---------- C. factor quintiles ----------
def quintiles(rows, key, nq=5, clean=None):
    valid = [r for r in rows if r.get(key) is not None and (clean is None or clean(r))]
    valid.sort(key=lambda r: r[key])
    out = []
    per = len(valid) // nq
    for i in range(nq):
        lo = valid[i * per][key]; hi = valid[min((i + 1) * per, len(valid)) - 1][key]
        out.append((f'Q{i+1} [{lo:.3g}~{hi:.3g}]', valid[i * per:(i + 1) * per] if i < nq - 1 else valid[i * per:]))
    return out

for key, name in [('ann_yield', '年化权利金收益率'), ('prem_ratio', '权利金/行权价'), ('dte', 'DTE 到期天数'),
                  ('moneyness', 'strike/price 虚实值'), ('iv', 'IV')]:
    show(f'C. {name} 五分位', quintiles(done, key))
    results[f'quintile_{key}'] = {q[0]: stats(q[1]) for q in quintiles(done, key)}

# ---------- D. SPY momentum regime ----------
def bucket(rows, key, edges):
    groups = []
    for i, (lo, hi) in enumerate(edges):
        sub = [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        groups.append((f'[{lo:.3g},{hi:.3g})', sub))
    return groups
show('D. SPY 20日动量 regime', bucket(done, 'spy_mom20', [(-1, -0.02), (-0.02, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 1)]))
show('D. SPY 5日动量 regime', bucket(done, 'spy_mom5', [(-1, -0.01), (-0.01, 0.0), (0.0, 0.01), (0.01, 1)]))
results['regime_mom20'] = {g[0]: stats(g[1]) for g in bucket(done, 'spy_mom20', [(-1, -0.02), (-0.02, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 1)])}
results['regime_mom5'] = {g[0]: stats(g[1]) for g in bucket(done, 'spy_mom5', [(-1, -0.01), (-0.01, 0.0), (0.0, 0.01), (0.01, 1)])}

# ---------- E. signal sequence per ticker ----------
show('E. 该标的累计第几次信号', [(k, [r for r in done if r['seq_total'] == k]) for k in range(1, 8)])
show('E. 信号序号分组', [('1-2次', [r for r in done if r['seq_total'] <= 2]),
                      ('3-5次', [r for r in done if 3 <= r['seq_total'] <= 5]),
                      ('6-10次', [r for r in done if 6 <= r['seq_total'] <= 10]),
                      ('>10次', [r for r in done if r['seq_total'] > 10])])
results['seq'] = {k: stats([r for r in done if r['seq_total'] == k]) for k in range(1, 8)}

json.dump(results, open('data/ibkrv5/backtest20d/patterns_scan.json', 'w'), ensure_ascii=False, indent=1, default=str)
print('\nsaved patterns_scan.json')
