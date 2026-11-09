from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal

from src.services.signal_scanner.core import digest, text_decimal

ALGORITHM = "features-1.0.0"


def moving(values, n, *, wilder=False):
    out = [None] * len(values)
    first = next((i for i, v in enumerate(values) if v is not None), len(values))
    if first + n > len(values):
        return out
    pos = first + n - 1
    if any(v is None for v in values[first : pos + 1]):
        return out
    out[pos] = sum(values[first : pos + 1]) / n
    alpha = 1 / n if wilder else 2 / (n + 1)
    for i in range(pos + 1, len(values)):
        if values[i] is not None and out[i - 1] is not None:
            out[i] = (1 - alpha) * out[i - 1] + alpha * values[i]
    return out


def sma(values, n, i):
    return (
        sum(values[i - n + 1 : i + 1]) / n if i >= n - 1 and None not in values[i - n + 1 : i + 1] else None
    )


def adjust(bars, actions=(), cutoff=None):
    """Anchor current units; volume changes for splits only. No forward-known actions."""
    result = []
    for b in bars:
        factor, volume_factor = Decimal(1), Decimal(1)
        for e in actions:
            if cutoff and e["known_at"] > cutoff:
                continue
            effective = (e.get("effective_at") or e.get("scheduled_date") or "")[:10]
            if b["market_date"] < effective <= bars[-1]["market_date"]:
                payload = e["payload_json"]
                if e["event_type"] == "SPLIT":
                    ratio = Decimal(str(payload["ratio"]))
                    if ratio <= 0:
                        raise ValueError("Invalid split")
                    factor /= ratio
                    volume_factor *= ratio
                elif e["event_type"] == "DIVIDEND":
                    # Supplied, verified factor avoids silently inventing an action price basis.
                    if "price_factor" not in payload:
                        raise ValueError("Dividend price factor unverified")
                    f = Decimal(str(payload["price_factor"]))
                    if not 0 < f <= 1:
                        raise ValueError("Invalid dividend factor")
                    factor *= f
        result.append(
            {
                **b,
                **{k: float(Decimal(str(b[k])) * factor) for k in ("open", "high", "low", "close")},
                "volume": float(Decimal(str(b["volume"])) * volume_factor),
                "raw_close": str(b["close"]),
                "price_factor": text_decimal(factor),
            }
        )
    return result


def build_features(bars, cal, instrument_id="", epoch="none"):
    if not bars:
        return []
    c, h, l, o, v = ([float(b[k]) for b in bars] for k in ("close", "high", "low", "open", "volume"))
    n = len(c)
    ema12, ema26 = moving(c, 12), moving(c, 26)
    macd = [a - b if a is not None and b is not None else None for a, b in zip(ema12, ema26)]
    signal = moving(macd, 9)
    tr, plus, minus, gain, loss = [[None] * n for _ in range(5)]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        plus[i], minus[i] = (up if up > down and up > 0 else 0), (down if down > up and down > 0 else 0)
        gain[i], loss[i] = max(0, c[i] - c[i - 1]), max(0, c[i - 1] - c[i])
    atr, pdm, mdm, ag, al = [moving(x, 14, wilder=True) for x in (tr, plus, minus, gain, loss)]
    di_plus, di_minus, dx, rsi = [[None] * n for _ in range(4)]
    for i in range(14, n):
        di_plus[i] = 100 * pdm[i] / atr[i] if atr[i] else 0
        di_minus[i] = 100 * mdm[i] / atr[i] if atr[i] else 0
        denominator = di_plus[i] + di_minus[i]
        dx[i] = 100 * abs(di_plus[i] - di_minus[i]) / denominator if denominator else 0
        rsi[i] = 50 if not ag[i] and not al[i] else 100 if not al[i] else 100 - 100 / (1 + ag[i] / al[i])
    adx = moving(dx, 14, wilder=True)
    weeks = defaultdict(list)
    weekly = {}
    for i, b in enumerate(bars):
        key = cal.session(b["market_date"])["week_last_date"]
        weeks[key].append(i)
        if b["market_date"] == key:
            idx = weeks[key]
            expected = cal.dates(bars[idx[0]]["market_date"], key)
            first_session = cal.session(bars[idx[0]]["market_date"])
            from datetime import date

            complete_start = (
                date.fromisoformat(first_session["previous_date"]).isocalendar()[:2]
                != date.fromisoformat(bars[idx[0]]["market_date"]).isocalendar()[:2]
            )
            if complete_start and [bars[j]["market_date"] for j in idx] == expected:
                weekly[key] = c[i]
    input_hash = digest(bars)
    result = []
    for i, b in enumerate(bars):
        completed = [val for day, val in weekly.items() if day <= b["market_date"]]
        maweek = sum(completed[-20:]) / 20 if len(completed) >= 20 else None
        pastweek = sum(completed[-24:-4]) / 20 if len(completed) >= 24 else None
        weekdir = (
            None
            if maweek is None or pastweek is None
            else "BULLISH"
            if completed[-1] > maweek and maweek > pastweek
            else "BEARISH"
            if completed[-1] < maweek and maweek < pastweek
            else "NEUTRAL"
        )
        previous20 = sum(v[i - 20 : i]) / 20 if i >= 20 else None
        f = dict(
            instrument_id=instrument_id,
            source_market_date=b["market_date"],
            count=i + 1,
            bar_end_at=b.get("bar_end_at", cal.session(b["market_date"])["close_at"]),
            input_hash=input_hash,
            algorithm_version=ALGORITHM,
            series_origin=bars[0]["market_date"],
            price_basis="ADJUSTED",
            adjustment_epoch=epoch,
            price_factor=b.get("price_factor", "1"),
            open=o[i],
            high=h[i],
            low=l[i],
            close=c[i],
            volume=v[i],
            prev_close=c[i - 1] if i else None,
            prev_volume=v[i - 1] if i else None,
            ma10=sma(c, 10, i),
            ma20=sma(c, 20, i),
            ma50=sma(c, 50, i),
            ma50_prev5=sma(c, 50, i - 5),
            ma10_prev=sma(c, 10, i - 1),
            ma50_prev=sma(c, 50, i - 1),
            atr=atr[i],
            atr_prev=atr[i - 1] if i else None,
            atr_median60=statistics.median(atr[i - 60 : i])
            if i >= 74 and None not in atr[i - 60 : i]
            else None,
            adx=adx[i],
            adx_prev=adx[i - 1] if i else None,
            di_plus=di_plus[i],
            di_minus=di_minus[i],
            rsi=rsi[i],
            rsi_prev=rsi[i - 1] if i else None,
            macd=macd[i],
            macd_prev=macd[i - 1] if i else None,
            macd_signal=signal[i],
            macd_signal_prev=signal[i - 1] if i else None,
            rv=v[i] / previous20 if previous20 and previous20 > 0 else None,
            volume_mean20=previous20,
            b20=max(h[i - 20 : i]) if i >= 20 else None,
            b60=max(h[i - 60 : i]) if i >= 60 else None,
            body=abs(c[i] - o[i]) / (h[i] - l[i]) if h[i] > l[i] else None,
            clv=(c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else None,
            return20=c[i] / c[i - 20] - 1 if i >= 20 else None,
            weekly=weekdir,
            close3=c[max(0, i - 2) : i + 1],
            volume3=v[max(0, i - 2) : i + 1],
        )
        f["missing_fields"] = [k for k in ("ma50", "atr", "adx", "rv", "weekly") if f[k] is None]
        result.append(f)
    return result


def distances(price, anchor, atr, *, reason=None):
    if reason:
        return dict(distance_to_entry_pct=None, distance_to_entry_atr=None, reason_code=reason)
    if price is None or anchor is None or Decimal(str(price)) <= 0 or Decimal(str(anchor)) <= 0:
        return dict(
            distance_to_entry_pct=None, distance_to_entry_atr=None, reason_code="INVALID_ANCHOR_OR_PRICE"
        )
    p, a = Decimal(str(price)), Decimal(str(anchor))
    return dict(
        distance_to_entry_pct=text_decimal(100 * (p - a) / a),
        distance_to_entry_atr=text_decimal((p - a) / Decimal(str(atr)))
        if atr is not None and Decimal(str(atr)) > 0
        else None,
        reason_code=None if atr is not None and Decimal(str(atr)) > 0 else "ATR_UNAVAILABLE",
        unit="PERCENT_POINTS",
    )


class _MovingState:
    def __init__(self, n, wilder=False):
        self.n, self.alpha = n, 1 / n if wilder else 2 / (n + 1)
        self.seed, self.value = [], None

    def push(self, value):
        if value is None:
            return None
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.n:
                self.value = sum(self.seed) / self.n
        else:
            self.value = (1 - self.alpha) * self.value + self.alpha * value
        return self.value


class StreamingFeatures:
    """Incremental implementation of the same formulas and initialization above.

    On revised/action-adjusted prefixes the caller must reset and replay the prefix.
    append never accepts a future row or computes a feature for a later session.
    """
    def __init__(self, cal, instrument_id="", epoch="none"):
        self.cal, self.iid, self.epoch = cal, instrument_id, epoch
        self.bars, self.cs, self.hs, self.vs, self.fs, self.atrs = [], [], [], [], [], []
        self.weekly, self.week_days = {}, defaultdict(list)
        self.ema12, self.ema26, self.signal = _MovingState(12), _MovingState(26), _MovingState(9)
        self.wilders = [_MovingState(14, True) for _ in range(5)]
        self.adx_state = _MovingState(14, True)
        self.chain = digest([])

    def append(self, b):
        from datetime import date
        i=len(self.bars)
        if i and b["market_date"] != self.cal.shift(self.bars[-1]["market_date"],1):
            raise ValueError("MISSING_INTERMEDIATE_SESSION")
        c,h,l,o,v=(float(b[k]) for k in ("close","high","low","open","volume"))
        prev=self.bars[-1] if i else None
        previous20=sum(self.vs[-20:])/20 if i>=20 else None
        b20=max(self.hs[-20:]) if i>=20 else None
        b60=max(self.hs[-60:]) if i>=60 else None
        a12,a26=self.ema12.push(c),self.ema26.push(c)
        macd=a12-a26 if a12 is not None and a26 is not None else None
        signal=self.signal.push(macd)
        atr=pdm=mdm=ag=al=None
        if prev:
            pc,ph,pl=(float(prev[k]) for k in ("close","high","low"))
            up,down=h-ph,pl-l
            inputs=[max(h-l,abs(h-pc),abs(l-pc)),
                    up if up>down and up>0 else 0,down if down>up and down>0 else 0,
                    max(0,c-pc),max(0,pc-c)]
            atr,pdm,mdm,ag,al=[state.push(x) for state,x in zip(self.wilders,inputs)]
        dp=dm=dx=rsi=None
        if i>=14:
            dp=100*pdm/atr if atr else 0
            dm=100*mdm/atr if atr else 0
            dx=100*abs(dp-dm)/(dp+dm) if dp+dm else 0
            rsi=50 if not ag and not al else 100 if not al else 100-100/(1+ag/al)
        adx=self.adx_state.push(dx)
        session=self.cal.session(b["market_date"])
        wk=session["week_last_date"]
        self.week_days[wk].append(b["market_date"])
        if b["market_date"]==wk:
            ds=self.week_days[wk]
            complete=(date.fromisoformat(self.cal.session(ds[0])["previous_date"]).isocalendar()[:2]
                      !=date.fromisoformat(ds[0]).isocalendar()[:2])
            if complete and ds==self.cal.dates(ds[0],wk):self.weekly[wk]=c
        wc=list(self.weekly.values())
        maweek=sum(wc[-20:])/20 if len(wc)>=20 else None
        pastweek=sum(wc[-24:-4])/20 if len(wc)>=24 else None
        weekdir=None if maweek is None or pastweek is None else (
            "BULLISH" if wc[-1]>maweek and maweek>pastweek else
            "BEARISH" if wc[-1]<maweek and maweek<pastweek else "NEUTRAL")
        self.bars.append(b);self.cs.append(c);self.hs.append(h);self.vs.append(v)
        self.chain=digest([self.chain,b])
        pf=self.fs[-1] if self.fs else {}
        f=dict(instrument_id=self.iid,source_market_date=b["market_date"],count=i+1,
               bar_end_at=b.get("bar_end_at",session["close_at"]),input_hash=self.chain,
               algorithm_version=ALGORITHM,series_origin=self.bars[0]["market_date"],
               price_basis="ADJUSTED",adjustment_epoch=self.epoch,price_factor=b.get("price_factor","1"),
               open=o,high=h,low=l,close=c,volume=v,
               prev_close=float(prev["close"]) if prev else None,
               prev_volume=float(prev["volume"]) if prev else None,
               ma10=sma(self.cs,10,i),ma20=sma(self.cs,20,i),ma50=sma(self.cs,50,i),
               ma50_prev5=sma(self.cs,50,i-5),ma10_prev=pf.get("ma10"),ma50_prev=pf.get("ma50"),
               atr=atr,atr_prev=self.atrs[-1] if self.atrs else None,
               atr_median60=statistics.median(self.atrs[-60:])
               if i>=74 and None not in self.atrs[-60:] else None,
               adx=adx,adx_prev=pf.get("adx"),di_plus=dp,di_minus=dm,rsi=rsi,rsi_prev=pf.get("rsi"),
               macd=macd,macd_prev=pf.get("macd"),macd_signal=signal,macd_signal_prev=pf.get("macd_signal"),
               rv=v/previous20 if previous20 and previous20>0 else None,volume_mean20=previous20,
               b20=b20,b60=b60,body=abs(c-o)/(h-l) if h>l else None,clv=(c-l)/(h-l) if h>l else None,
               return20=c/self.cs[i-20]-1 if i>=20 else None,weekly=weekdir,
               close3=self.cs[-3:],volume3=self.vs[-3:])
        f["missing_fields"]=[k for k in ("ma50","atr","adx","rv","weekly") if f[k] is None]
        self.atrs.append(atr);self.fs.append(f)
        return f
