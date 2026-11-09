"""Independent reconciliation of delivered rows against frozen data and calendar."""
import csv, hashlib, json, math, statistics
from collections import defaultdict, Counter
from pathlib import Path
R=Path(__file__).resolve().parents[2]; out=R/'runtime/three-dimensional-rebound-20261030'; src=R/'runtime/data10-update-20261026/reproducible/inputs/snapshot.json'
s=json.loads(src.read_text()); summ=json.loads((out/'summary.json').read_text()); rows=list(csv.DictReader((out/'trades.csv').open())); curve=list(csv.DictReader((out/'equity.csv').open())); errors=[]
days=[x['market_date'] for x in s['bars']['US:SPY']]; ix={d:i for i,d in enumerate(days)}; acts=defaultdict(list)
for e in s['events']:
 if e['event_type']=='SPLIT' and e['status']!='CANCELLED':acts[e['instrument_id']].append((e['effective_at'][:10],float(e['payload_json']['ratio'])))
by={k:{b['market_date']:b for b in v} for k,v in s['bars'].items()}
def price(iid,d,field):
 return float(by[iid][d][field])/math.prod(r for ad,r in acts[iid] if d<ad<='2026-10-23')
def check(cond,msg):
 if not cond:errors.append(msg)
indicator_cache={}
for sym in {t['ticker'] for t in rows}:
 iid='US:'+sym; records=sorted(by[iid]); vals=[]; rsis=[]; gains=[]; losses=[]; avg_gain=avg_loss=None; previous=None; prior_ix=None
 for d in records:
  c=price(iid,d,'close'); ci=ix[d]
  if prior_ix is not None and ci!=prior_ix+1:gains=[];losses=[];avg_gain=avg_loss=None;previous=None
  rsi=None
  if previous is not None:
   change=c-previous; gain=max(change,0);loss=max(-change,0)
   if avg_gain is None:
    gains.append(gain);losses.append(loss)
    if len(gains)==14:avg_gain=sum(gains)/14;avg_loss=sum(losses)/14
   else:avg_gain=(13*avg_gain+gain)/14;avg_loss=(13*avg_loss+loss)/14
   if avg_gain is not None:rsi=50 if avg_gain==avg_loss==0 else 100 if avg_loss==0 else 100-100/(1+avg_gain/avg_loss)
  vals.append(c);rsis.append(rsi);previous=c;prior_ix=ci
 indicator_cache[sym]=(records,vals,rsis,{d:j for j,d in enumerate(records)})
for n,t in enumerate(rows,1):
 sym='US:'+t['ticker']; sig=t['signal_date']; ent=t['entry_date']; ex=t['exit_date']; tag=f'{n}:{sym}:{sig}'; entry=float(t['entry_price']); exit=float(t['exit_price']);
 check(ix[ent]==ix[sig]+1,tag+' next open'); check(ix[ex]-ix[ent]+1==int(t['holding_sessions'])<=4,tag+' duration');
 check(math.isclose(entry,price(sym,ent,'open'),rel_tol=1e-10),tag+' entry');check(math.isclose(float(t['signal_low']),price(sym,sig,'low'),rel_tol=1e-10),tag+' signal low');check(math.isclose(float(t['stop']),float(t['signal_low'])*.99,rel_tol=1e-10),tag+' stop');check(math.isclose(float(t['gross_return']),exit/entry-1,abs_tol=1e-10),tag+' return')
 dates,vals,rsis,loc=indicator_cache[t['ticker']]; j=loc[sig]; w=loc[t['watch_date']]
 for pos,label in ((j,'signal'),(w,'watch')):
  window=vals[pos-19:pos+1]; lower=sum(window)/20-2*statistics.pstdev(window); sma=sum(vals[pos-199:pos+1])/200
  check(len(window)==20 and ix[dates[pos]]-ix[dates[pos-199]]==199,tag+' '+label+' continuity');check(vals[pos]>sma,tag+' '+label+' sma200')
  if label=='watch':check(rsis[pos]<30 and vals[pos]<=lower,tag+' left signal')
  else:check(rsis[pos-1] is not None and rsis[pos-1]<=30 and rsis[pos]>30 and vals[pos]>lower,tag+' right signal')
 reason=t['reason']
 if reason=='SMA20_GAP':check(math.isclose(exit,price(sym,ex,'open'),rel_tol=1e-10),tag+' gap exit')
 elif reason=='SMA20_TOUCH':
  prev=days[ix[ex]-19:ix[ex]]; target=sum(price(sym,d,'close') for d in prev)/19
  check(math.isclose(exit,target,rel_tol=1e-10) and price(sym,ex,'high')>=target and price(sym,ex,'open')<target,tag+' touch')
 elif reason.endswith('NEXT_OPEN'):check(math.isclose(exit,price(sym,ex,'open'),rel_tol=1e-10),tag+' next open exit')
 elif reason.startswith('DAY4_CLOSE'):check(math.isclose(exit,price(sym,ex,'close'),rel_tol=1e-10) and int(t['holding_sessions'])==4,tag+' time exit')
 else:errors.append(tag+' unknown exit')
r=[float(x['exit_price'])*.999/(float(x['entry_price'])*1.001)-1 for x in rows]
check(math.isclose(sum(r)/len(r),summ['variants']['right_10']['stats_10bps_side']['mean'],abs_tol=1e-12),'mean');check(sum(x>0 for x in r)==round(len(r)*summ['variants']['right_10']['stats_10bps_side']['win_rate']),'wins')
peak=100000.;dd=0.
for row in curve:
 eq=float(row['equity']);peak=max(peak,eq);dd=min(dd,eq/peak-1)
check(math.isclose(dd,summ['portfolio']['max_drawdown'],abs_tol=1e-12),'drawdown');check(math.isclose(float(curve[-1]['equity']),summ['portfolio']['final'],abs_tol=1e-7),'equity');check(hashlib.sha256(src.read_bytes()).hexdigest()==summ['snapshot_sha256'],'source hash');check(hashlib.sha256((R/'scripts/backtest_three_dimensional_rebound.py').read_bytes()).hexdigest()==summ['script_sha256'],'script hash');
result={'status':'PASS' if not errors else 'FAIL','trades_checked':len(rows),'equity_days_checked':len(curve),'errors':errors,'reason_counts':dict(Counter(t['reason'] for t in rows)),'scope':'Independently recalculated signal RSI and Bollinger conditions, row-level prices, timing, target and equity summary; does not validate source completeness, missed signals or the portfolio ledger.'};(out/'verification.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));print(json.dumps(result,indent=2,ensure_ascii=False));raise SystemExit(bool(errors))
