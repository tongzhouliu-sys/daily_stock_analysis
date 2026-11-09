#!/usr/bin/env python3
"""规律稳健性验证：因子Q5是否由少数标的/月份驱动；样本内外；组合规则模拟。"""
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

spy_idx = {d: i for i, d in enumerate(tdates)}
def spy_mom(d, n):
    if d not in spy_idx: return None
    i = spy_idx[d]
    return spy[tdates[i]] / spy[tdates[i-n]] - 1 if i >= n else None
for r in done:
    r['spy_mom20'] = spy_mom(r['et_date'], 20)
    r['spy_mom5'] = spy_mom(r['et_date'], 5)

TRAIN_END = '2026-08-31'  # 训练集 6-16~8-31，验证集 9-01~9-25

def st(rows):
    v = [r['ret'] for r in rows]
    if not v: return None
    return {'n': len(v), 'mean': statistics.mean(v), 'med': statistics.median(v), 'win': sum(1 for x in v if x > 0)/len(v)}

def show(t, rows):
    s = st(rows)
    if s and s['n'] >= 5:
        print(f'  {t:34s} n={s["n"]:5d}  mean={s["mean"]*100:+7.2f}%  med={s["med"]*100:+7.2f}%  win={s["win"]*100:5.1f}%')
    elif s:
        print(f'  {t:34s} n={s["n"]:5d}  (样本太少)')

# ===== 1. Q5 档（ann_yield>36 / IV>0.6）构成拆解 =====
q5a = [r for r in done if r['ann_yield'] is not None and r['ann_yield'] > 36]
q5i = [r for r in done if r['iv'] is not None and r['iv'] > 0.6]
print('=== 1. 高收益档/高IV档 构成 ===')
print(f'ann_yield>36: n={len(q5a)}  IV>0.6: n={len(q5i)}  交集: n={sum(1 for r in q5a if r["iv"] and r["iv"]>0.6)}')
for name, grp in [('ann_yield>36', q5a), ('IV>0.6', q5i)]:
    tk = defaultdict(list)
    for r in grp: tk[r['ticker']].append(r['ret'])
    top = sorted(tk.items(), key=lambda kv: -len(kv[1]))[:10]
    print(f'  {name} 标的构成: ' + ', '.join(f'{t}:{len(v)}' for t, v in top))
    print(f'  {name} 按月: ' + ', '.join(f'{m}:n={sum(1 for r in grp if r["et_date"][:7]==m)} mean={statistics.mean([r["ret"] for r in grp if r["et_date"][:7]==m])*100:+.1f}%' for m in sorted({r["et_date"][:7] for r in grp})))
    # 剔除每个标的最大贡献者后的稳健性
    print(f'  {name} 全部: mean={statistics.mean([r["ret"] for r in grp])*100:+.2f}%')
    for t, v in top[:3]:
        sub = [r['ret'] for r in grp if r['ticker'] != t]
        print(f'    剔除{t}后: n={len(sub)} mean={statistics.mean(sub)*100:+.2f}%' if sub else f'    剔除{t}后: 空')

# ===== 2. 60日持有样本聚集检查 =====
print('\n=== 2. 60日持有样本构成 ===')
h60 = []
for r in done:
    ex = exit_after_trading_days(tdates, r['et_date'], 60)
    if ex and ex in closes.get(r['ticker'], {}):
        r['ret60'] = closes[r['ticker']][ex] / r['entry_price'] - 1
        h60.append(r)
print(f'  n={len(h60)}  按月: ' + ', '.join(f'{m}:n={sum(1 for r in h60 if r["et_date"][:7]==m)}' for m in sorted({r["et_date"][:7] for r in h60})))
print(f'  60日均值 {statistics.mean([r["ret60"] for r in h60])*100:+.2f}%')
spy60 = []
for r in h60:
    ex = exit_after_trading_days(tdates, r['et_date'], 60)
    if ex in spy: spy60.append(spy[ex]/spy[r['et_date']]-1)
print(f'  SPY同期 {statistics.mean(spy60)*100:+.2f}%')

# ===== 3. 样本内外：单因子规则 =====
print('\n=== 3. 单因子规则 样本内外（20日持有）===')
def split_show(name, pred):
    tr = [r for r in done if r['et_date'] <= TRAIN_END and pred(r)]
    va = [r for r in done if r['et_date'] > TRAIN_END and pred(r)]
    allb = [r for r in done if pred(r)]
    sa, sb, sc = st(tr), st(va), st(allb)
    fmt = lambda s: f'n={s["n"]:4d} {s["mean"]*100:+6.2f}%' if s else 'n=   0      —'
    print(f'  {name:26s} 训练(6-8月): {fmt(sa)} | 验证(9月): {fmt(sb)} | 全样本 {fmt(sc)}')
    return sa, sb
split_show('ann_yield>36', lambda r: r['ann_yield'] and r['ann_yield'] > 36)
split_show('IV>0.6', lambda r: r['iv'] and r['iv'] > 0.6)
split_show('ann_yield>36 & IV>0.6', lambda r: r['ann_yield'] and r['ann_yield'] > 36 and r['iv'] and r['iv'] > 0.6)
split_show('push=intraday', lambda r: r['push_type'] == 'intraday')
split_show('SPY20动量<0', lambda r: r['spy_mom20'] is not None and r['spy_mom20'] < 0)
split_show('SPY5动量<0', lambda r: r['spy_mom5'] is not None and r['spy_mom5'] < 0)
split_show('高IV & SPY20<0', lambda r: r['iv'] and r['iv'] > 0.6 and r['spy_mom20'] is not None and r['spy_mom20'] < 0)
split_show('高IV & intraday', lambda r: r['iv'] and r['iv'] > 0.6 and r['push_type'] == 'intraday')
split_show('高IV & intraday & SPY20<0', lambda r: r['iv'] and r['iv'] > 0.6 and r['push_type'] == 'intraday' and r['spy_mom20'] is not None and r['spy_mom20'] < 0)
# baseline
split_show('(全部信号)', lambda r: True)
split_show('(SPY同期20日)', lambda r: True)

# ===== 4. 组合规则模拟（等权，全部在场信号） =====
print('\n=== 4. 组合模拟（信号日quote价买入，持有20TD，等权） ===')
def sim(name, pred):
    trades = []
    for r in done:
        if not pred(r): continue
        ex = exit_after_trading_days(tdates, r['et_date'], 20)
        if ex and ex in closes.get(r['ticker'], {}):
            trades.append({'et_date': r['et_date'], 'ticker': r['ticker'], 'exit_date_20td': ex,
                           'entry_price': r['entry_price'], 'ret_20td_quote': closes[r['ticker']][ex]/r['entry_price']-1,
                           'close_on_signal_date': r['entry_price'], 'ret_20td_close': closes[r['ticker']][ex]/r['entry_price']-1})
    series, pf = portfolio_sim(trades, closes, tdates)
    if pf:
        spyr = spy[pf['end']]/spy[pf['start']]-1
        print(f'  {name:28s} n={len(trades):5d} 总收益={pf["total_return"]*100:+7.1f}% 年化={pf["annualized"]*100:+7.1f}% 回撤={pf["max_drawdown"]*100:6.1f}% Sharpe={pf["sharpe"]:5.2f} | SPY同期 {spyr*100:+6.1f}%')
        return pf
    return None
sim('全部信号(基线)', lambda r: True)
sim('IV>0.6', lambda r: r['iv'] and r['iv'] > 0.6)
sim('ann_yield>36', lambda r: r['ann_yield'] and r['ann_yield'] > 36)
sim('intraday', lambda r: r['push_type'] == 'intraday')
sim('IV>0.6 & SPY20<0', lambda r: r['iv'] and r['iv'] > 0.6 and r['spy_mom20'] is not None and r['spy_mom20'] < 0)
sim('高IV & intraday', lambda r: r['iv'] and r['iv'] > 0.6 and r['push_type'] == 'intraday')
sim('40日持有 IV>0.6', lambda r: r['iv'] and r['iv'] > 0.6)
json.dump({'note': 'see stdout'}, open('data/ibkrv5/backtest20d/patterns_robust.json', 'w'))
