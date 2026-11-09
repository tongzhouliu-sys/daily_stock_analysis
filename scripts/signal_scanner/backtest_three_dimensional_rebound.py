"""Offline EOD price-only exploratory replay; no business DB, network, or orders."""
import csv, hashlib, json, math
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'runtime/data10-update-20261026/reproducible/inputs/snapshot.json'
OUT=ROOT/'runtime/three-dimensional-rebound-20261030'
OUT.mkdir(parents=True, exist_ok=True)
START,END='2017-06-12','2026-09-22'
s=json.loads(SRC.read_text())
days=[b['market_date'] for b in s['bars']['US:SPY']]
idx={d:i for i,d in enumerate(days)}
actions=defaultdict(list)
for e in s['events']:
 if e['event_type']=='SPLIT' and e['status']!='CANCELLED': actions[e['instrument_id']].append((e['effective_at'][:10],float(e['payload_json']['ratio'])))

def wilder(c, sessions):
 out=np.full(len(c),np.nan)
 if len(c)<15:return out
 changes=np.diff(c); gain=np.maximum(changes,0); loss=np.maximum(-changes,0)
 def val(a,b): return 50. if a==b==0 else 100. if b==0 else 100.-100./(1.+a/b)
 start=0
 for end in list(np.flatnonzero(np.diff(sessions)!=1)+1)+[len(c)]:
  if end-start>=15:
   a,b=gain[start:start+14].mean(),loss[start:start+14].mean();out[start+14]=val(a,b)
   for i in range(start+15,end):
    a=(13*a+gain[i-1])/14; b=(13*b+loss[i-1])/14; out[i]=val(a,b)
  start=end
 return out

def load():
 frames={}; audits=Counter()
 for iid,raw in s['bars'].items():
  if iid=='US:SPY': continue # reference ETF, not a constituent
  raw=sorted(raw,key=lambda x:x['market_date']); rows=[]
  for b in raw:
   d=b['market_date']; fac=math.prod(r for dt,r in actions[iid] if d<dt<='2026-10-23')
   try: o,h,l,c=[float(b[k])/fac for k in ('open','high','low','close')]
   except (TypeError,ValueError): audits['bad_price']+=1; continue
   if d not in idx or min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c,h): audits['bad_or_calendar']+=1;continue
   rows.append((d,idx[d],o,h,l,c))
  f=pd.DataFrame(rows,columns=['date','ci','open','high','low','close']); f['rsi']=wilder(f.close.to_numpy(),f.ci.to_numpy()); f['sma200']=f.close.rolling(200).mean(); f['lower']=f.close.rolling(20).mean()-2*f.close.rolling(20).std(ddof=0); f['target']=f.close.shift(1).rolling(19).mean(); f['contiguous200']=(f.ci-f.ci.shift(199)==199); frames[iid]=f
 return frames,audits

def run(frames,watch,variant='right'):
 trades=[]; pool=Counter(); pending=0
 for iid,f in frames.items():
  a=f.to_dict('records'); observation=None; busy=-1
  for i in range(200,len(a)-1):
   x=a[i]; prev=a[i-1]; d=x['date']
   if d<START or d>END: continue
   if not x['contiguous200']: observation=None; pool['missing_200_session_history']+=1;continue
   if i<=busy:continue
   if observation is not None and x['ci']-observation['ci']>watch: observation=None;pool['expired']+=1
   if x['close']<=x['sma200']:
    if observation is not None:pool['removed_sma200']+=1
    observation=None;continue
   left=x['rsi']<30 and x['close']<=x['lower']
   if left and observation is None:
    observation={'ci':x['ci'],'date':d};pool['entries']+=1
   confirmation=(observation is not None and x['ci']>observation['ci'] and prev['rsi']<=30 and x['rsi']>30 and x['close']>x['lower'])
   hit=left if variant=='left' else confirmation
   if not hit:continue
   pool['signals']+=1
   # Cannot fill on the same final close; next session open. Require continuous bars through exit.
   j=i+1
   if a[j]['ci']!=x['ci']+1:pool['missing_next_open']+=1; observation=None;continue
   ent=a[j]; stop=x['low']*.99; exit_i=None; reason=None; price=None; phase=None
   for k in range(j,min(j+5,len(a))):
    z=a[k]
    if z['ci']!=ent['ci']+(k-j): break
    if z['open']>=z['target']:exit_i=k;price=z['open'];reason='SMA20_GAP';phase='open';break
    if z['high']>=z['target']:exit_i=k;price=z['target'];reason='SMA20_TOUCH';phase='intraday';break
    cut=z['close']<stop; sma=z['close']<z['sma200']
    if k-j==3:
     exit_i=k;price=z['close'];reason='DAY4_CLOSE'+('_STOP' if cut else '_SMA200' if sma else '');phase='close';break
    if cut or sma:
     if k+1<len(a) and a[k+1]['ci']==z['ci']+1:
      exit_i=k+1;price=a[k+1]['open'];reason='STOP_NEXT_OPEN' if cut else 'SMA200_NEXT_OPEN';phase='open'
     break
   if exit_i is None:pool['unresolved_gap_or_end']+=1;observation=None;pending+=1;continue
   t={'ticker':iid[3:],'watch_date':observation['date'] if observation else d,'signal_date':d,'entry_date':ent['date'],'exit_date':a[exit_i]['date'],'entry_price':ent['open'],'exit_price':price,'signal_low':x['low'],'stop':stop,'holding_sessions':a[exit_i]['ci']-ent['ci']+1,'reason':reason,'exit_phase':phase,'gross_return':price/ent['open']-1}
   trades.append(t);busy=exit_i;observation=None
 return trades,pool,pending

def stats(trades,bps=10):
 r=np.array([(t['exit_price']*(1-bps/10000))/(t['entry_price']*(1+bps/10000))-1 for t in trades]); w=r[r>0];l=r[r<0]
 return {'n':len(r),'win_rate':float((r>0).mean()) if len(r) else None,'mean':float(r.mean()) if len(r) else None,'median':float(np.median(r)) if len(r) else None,'win_loss_ratio':float(w.mean()/-l.mean()) if len(w) and len(l) else None,'profit_factor':float(w.sum()/-l.sum()) if len(l) else None,'mean_holding':float(np.mean([t['holding_sessions'] for t in trades])) if trades else None,'reasons':dict(Counter(t['reason'] for t in trades)),'p05':float(np.quantile(r,.05)) if len(r) else None,'p95':float(np.quantile(r,.95)) if len(r) else None}

def portfolio(trades,frames):
 # At each open, process pending exits, then size entries using preceding close equity.
 # Intraday/close sale proceeds become available only next session.
 entries=defaultdict(list); exits=defaultdict(list); prices={}
 for t in trades:entries[t['entry_date']].append(t);exits[t['exit_date']].append(t)
 for iid,f in frames.items():
  for row in f.itertuples():prices[(iid[3:],row.date)]=row.close
 cash=100000.; positions={}; equity=100000.; peak=equity; mdd=0.; skipped=Counter(); curve=[]; taken=[]
 for d in days:
  if d<START or d>'2026-10-23':continue
  for t in exits[d]:
   if t['exit_phase']=='open' and t['ticker'] in positions:
    p=positions.pop(t['ticker']);cash+=p['shares']*t['exit_price']*.999
  prior_equity=equity
  for t in sorted(entries[d],key=lambda x:x['ticker']):
   if t['ticker'] in positions:skipped['duplicate']+=1;continue
   entry=t['entry_price']*1.001; risk=entry-t['stop']
   if risk<=0:skipped['open_at_or_below_stop']+=1;continue
   allocation=min(.10*prior_equity,cash,prior_equity*.01*entry/risk if risk else .10*prior_equity)
   if allocation<1:skipped['insufficient_cash']+=1;continue
   shares=allocation/entry;cash-=allocation;positions[t['ticker']]={'shares':shares,'last':t['entry_price']};taken.append(t)
  for t in exits[d]:
   if (t['exit_phase']!='open' or t['entry_date']==d) and t['ticker'] in positions:
    p=positions.pop(t['ticker']);cash+=p['shares']*t['exit_price']*.999
  for sym,p in positions.items():
   if (sym,d) in prices:p['last']=prices[(sym,d)]
  equity=cash+sum(p['shares']*p['last'] for p in positions.values());peak=max(peak,equity);mdd=min(mdd,equity/peak-1)
  curve.append({'date':d,'equity':equity,'cash':cash,'positions':len(positions)})
 return {'initial':100000,'final':equity,'return':equity/100000-1,'max_drawdown':mdd,'taken':len(taken),'skipped':dict(skipped),'open_positions':len(positions),'max_positions':max(x['positions'] for x in curve)},curve

frames,audit=load(); variants={}; alltrades={}
for name,w,mode in [('right_5',5,'right'),('right_10',10,'right'),('right_20',20,'right'),('left_10',10,'left')]:
 t,p,n=run(frames,w,mode);alltrades[name]=t; variants[name]={'pool':dict(p),'unresolved':n,'stats_10bps_side':stats(t),'stats_0bps':stats(t,0),'stats_20bps_side':stats(t,20)}
t=alltrades['right_10']; portfolio_stats,curve=portfolio(t,frames)
recs=list(csv.DictReader(open(ROOT/'doc/ibkrv52016/recommendations_20260427_20261023.csv',encoding='utf-8-sig')))
coverage=[x for x in t if '2026-04-27'<=x['signal_date']<='2026-09-22']
keys={(r['date'],r['ticker']) for r in recs}; same=[x for x in coverage if (x['signal_date'],x['ticker']) in keys]
result={'status':'PRICE_ONLY_EXPLORATORY_NOT_FULL_STRATEGY','snapshot_sha256':hashlib.sha256(SRC.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'analysis_window':[START,END],'symbols':len(frames),'audits':dict(audit),'variants':variants,'portfolio':portfolio_stats,'yearly':{y:stats([x for x in t if x['signal_date'][:4]==y]) for y in sorted(set(x['signal_date'][:4] for x in t))},'comparison':{'sample':len(coverage),'same_signal_date_ticker':len(same),'same_matches':same,'archive_range':['2026-04-27','2026-10-23'],'archive_record_count':len(recs)}}
(OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
for name,rows in [('trades.csv',t),('equity.csv',curve),('left_trades.csv',alltrades['left_10'])]:
 if rows:
  with (OUT/name).open('w',newline='') as f:
   wr=csv.DictWriter(f,fieldnames=rows[0].keys());wr.writeheader();wr.writerows(rows)
print(json.dumps(result,ensure_ascii=False,indent=2))
