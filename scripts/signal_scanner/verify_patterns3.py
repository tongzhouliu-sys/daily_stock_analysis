#!/usr/bin/env python3
"""最终规律组合与样本内外验证：regime/DTE/IVR 规则叠加，训练6-8月、验证9月。"""
import csv, json, sys, statistics, datetime
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
tickers_all = sorted({r['ticker'] for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv'))} | {'SPY'})
oct_c, oct_d, miss = load_yahoo_oct(tickers_all)
for t, dd in oct_c.items():
    for d, c in dd.items(): closes[t][d] = c
tdates = sorted(set(closes['SPY'].keys()))
spy = closes['SPY']
spy_idx = {d: i for i, d in enumerate(tdates)}

# ---------- 推荐表全量 enrich ----------
rec = {}
for line in open('data/ibkrv5/recommendations_full_20260427_20261023.jsonl'):
    d = json.loads(line); rec[d['id']] = d
rec_tr = [r for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_recommendations.csv')) if r['ret_20td']]
rows = []
for r in rec_tr:
    src = rec.get(int(r['id']), {})
    ex = r['exit_date_20td']
    rows.append({'id': r['id'], 'ret': float(r['ret_20td']), 'date': r['date'], 'ticker': r['ticker'],
                 'exit': ex, 'entry': float(r['entry_close']),
                 'vix': fnum(src.get('vix')), 'dte': fnum(src.get('dte')), 'ivr': fnum(src.get('ivr')),
                 'rank': fnum(src.get('rank')), 'sim': src.get('sim_outcome'),
                 'spy_mom20': (spy[tdates[spy_idx[r['date']]]] / spy[tdates[spy_idx[r['date']]-20]] - 1) if r['date'] in spy_idx and spy_idx[r['date']] >= 20 else None})

def st(g):
    v = [x['ret'] for x in g]
    if not v: return None
    return {'n': len(v), 'mean': statistics.mean(v), 'med': statistics.median(v), 'win': sum(1 for x in v if x > 0)/len(v)}

TRAIN_END = '2026-08-31'
def show(name, pred, base=rows):
    tr = [r for r in base if r['date'] <= TRAIN_END and pred(r)]
    va = [r for r in base if r['date'] > TRAIN_END and pred(r)]
    sa, sb = st(tr), st(va)
    f = lambda s: f'n={s["n"]:4d} {s["mean"]*100:+6.2f}% (胜率{s["win"]*100:.0f}%)' if s else 'n=   0      —'
    print(f'  {name:36s} 训练: {f(sa)} | 9月验证: {f(sb)}')
    return sa, sb

print('=== 推荐表：单规则样本内外 ===')
show('(全部推荐)', lambda r: True)
show('VIX>=18', lambda r: r['vix'] and r['vix'] >= 18)
show('VIX>=20', lambda r: r['vix'] and r['vix'] >= 20)
show('dte>35', lambda r: r['dte'] and r['dte'] > 35)
show('ivr<50', lambda r: r['ivr'] and r['ivr'] < 50)
show('rank 1-2', lambda r: r['rank'] and r['rank'] <= 2)
show('SPY20动量<0', lambda r: r['spy_mom20'] is not None and r['spy_mom20'] < 0)
show('CSP sim=win', lambda r: r['sim'] == 'win')
print('=== 推荐表：规则叠加 ===')
show('VIX>=18 & dte>35', lambda r: r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35)
show('VIX>=18 & ivr<50', lambda r: r['vix'] and r['vix'] >= 18 and r['ivr'] and r['ivr'] < 50)
show('VIX>=18 & dte>35 & ivr<50', lambda r: r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['ivr'] and r['ivr'] < 50)
show('VIX>=18 & dte>35 & sim=win', lambda r: r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['sim'] == 'win')
show('VIX>=18 & dte>35 & rank<=2', lambda r: r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['rank'] and r['rank'] <= 2)

# ---------- hourly 表：regime + DTE 规则 ----------
print('\n=== hourly 表：regime + DTE 规则（信号日quote价买入） ===')
raw = {r['id']: r for r in csv.DictReader(open('data/ibkrv5/opportunities_hourly_20260616_20261023.csv', encoding='utf-8-sig'))}
done = [r for r in csv.DictReader(open('data/ibkrv5/backtest20d/trades_hourly.csv')) if r['ret_20td_quote']]
hrows = []
for r in done:
    src = raw.get(r['id'], {})
    try: dte = (datetime.date.fromisoformat(src['expiry'][:10]) - datetime.date.fromisoformat(r['et_date'])).days
    except Exception: dte = None
    hrows.append({'ret': float(r['ret_20td_quote']), 'date': r['et_date'], 'ticker': r['ticker'],
                  'exit': r['exit_date_20td'], 'entry': float(r['entry_price']),
                  'dte': dte, 'ptype': r['push_type'],
                  'spy_mom20': (spy[tdates[spy_idx[r['et_date']]]] / spy[tdates[spy_idx[r['et_date']]-20]] - 1) if r['et_date'] in spy_idx and spy_idx[r['et_date']] >= 20 else None})
show('(全部信号)', lambda r: True, base=hrows)
show('dte>39', lambda r: r['dte'] and r['dte'] > 39, base=hrows)
show('SPY20动量<0', lambda r: r['spy_mom20'] is not None and r['spy_mom20'] < 0, base=hrows)
show('dte>39 & SPY20<0', lambda r: r['dte'] and r['dte'] > 39 and r['spy_mom20'] is not None and r['spy_mom20'] < 0, base=hrows)
show('dte>39 & SPY20<0 & intraday', lambda r: r['dte'] and r['dte'] > 39 and r['spy_mom20'] is not None and r['spy_mom20'] < 0 and r['ptype'] == 'intraday', base=hrows)

# ---------- 组合模拟：规则叠加 ----------
print('\n=== 组合模拟（等权，收盘价入场，20TD持有） ===')
def sim(name, sel, base):
    trades = [{'et_date': r['date'], 'ticker': r['ticker'], 'exit_date_20td': r['exit'],
               'entry_price': r['entry'], 'ret_20td_quote': r['ret'],
               'close_on_signal_date': r['entry'], 'ret_20td_close': r['ret']} for r in sel]
    series, pf = portfolio_sim(trades, closes, tdates)
    if pf:
        spyr = spy[pf['end']]/spy[pf['start']]-1
        print(f'  {name:34s} n={len(trades):4d} 总收益={pf["total_return"]*100:+7.1f}% 年化={pf["annualized"]*100:+7.1f}% 回撤={pf["max_drawdown"]*100:6.1f}% Sharpe={pf["sharpe"]:5.2f} | SPY同期 {spyr*100:+6.1f}%')
        return pf, series
    return None, None

pf_all, _ = sim('全部推荐(基线)', rows, rows)
pf_v, s_v = sim('VIX>=18', [r for r in rows if r['vix'] and r['vix'] >= 18], rows)
pf_d, s_d = sim('dte>35', [r for r in rows if r['dte'] and r['dte'] > 35], rows)
pf_i, s_i = sim('ivr<50', [r for r in rows if r['ivr'] and r['ivr'] < 50], rows)
pf_vd, s_vd = sim('VIX>=18 & dte>35', [r for r in rows if r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35], rows)
pf_vdi, s_vdi = sim('VIX>=18 & dte>35 & ivr<50', [r for r in rows if r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['ivr'] and r['ivr'] < 50], rows)
pf_vds, s_vds = sim('VIX>=18 & dte>35 & sim=win', [r for r in rows if r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['sim'] == 'win'], rows)
pf_h, s_h = sim('hourly: dte>39 & SPY20<0', [r for r in hrows if r['dte'] and r['dte'] > 39 and r['spy_mom20'] is not None and r['spy_mom20'] < 0], hrows)

# 保存最终策略权益曲线
if s_vdi:
    with open('data/ibkrv5/backtest20d/portfolio_daily_rule_vixdteivr.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['date', 'daily_ret', 'equity']); w.writerows(s_vdi)
if s_vds:
    with open('data/ibkrv5/backtest20d/portfolio_daily_rule_vixdtesim.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['date', 'daily_ret', 'equity']); w.writerows(s_vds)
if s_h:
    with open('data/ibkrv5/backtest20d/portfolio_daily_rule_hourly.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['date', 'daily_ret', 'equity']); w.writerows(s_h)

# 训练/验证分段收益（最终规则）
print('\n=== 最终规则分段（VIX>=18 & dte>35 & ivr<50） ===')
sel = [r for r in rows if r['vix'] and r['vix'] >= 18 and r['dte'] and r['dte'] > 35 and r['ivr'] and r['ivr'] < 50]
for label, lo, hi in [('训练 4-27~8-31', '2026-04-01', '2026-08-31'), ('验证 9-01~9-25', '2026-09-01', '2026-09-30')]:
    sub = [r['ret'] for r in sel if lo <= r['date'] <= hi]
    if sub: print(f'  {label}: n={len(sub)} mean={statistics.mean(sub)*100:+.2f}% med={statistics.median(sub)*100:+.2f}% win={sum(1 for x in sub if x>0)/len(sub)*100:.1f}%')
