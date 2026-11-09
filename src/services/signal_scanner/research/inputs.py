import hashlib
import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from src.services.signal_scanner.core import digest, instant, reject_credentials, require, stamp
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.domain.features import StreamingFeatures, adjust


class ResearchCalendar(Calendar):
    @lru_cache(maxsize=50000)
    def dates(self, start, end):
        return super().dates(start, end)

    @lru_cache(maxsize=50000)
    def shift(self, day, n):
        return super().shift(day, n)

    @lru_cache(maxsize=50000)
    def distance(self, a, b):
        return super().distance(a, b)

    @lru_cache(maxsize=1000)
    def last_completed(self, at):
        return super().last_completed(at)


def read_snapshot(root, ref):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "INVALID_SNAPSHOT_REF", status=422)
    root = Path(root).resolve()
    path = (root / ref["path"]).resolve()
    require(not Path(ref["path"]).is_absolute() and path.is_relative_to(root), "INPUT_PATH_FORBIDDEN")
    require(path.is_file(), "MISSING_REFERENCED_INPUT")
    content = path.read_bytes()
    require(hashlib.sha256(content).hexdigest() == ref["sha256"], "ARTIFACT_CORRUPT")
    value = json.loads(content)
    reject_credentials(value)
    return value


def clock_for(day, m, cal):
    return stamp(instant(cal.session(day)["close_at"]) + timedelta(minutes=m["evaluation_clock"]["minutes"]))


def effective_actions(events, iid, clock, *, dividends=True):
    selected = []
    for e in events:
        if e.get("instrument_id") != iid or e["event_type"] not in (
            ("SPLIT", "DIVIDEND") if dividends else ("SPLIT",)
        ):
            continue
        if instant(e["known_at"]) > instant(clock):
            continue
        require(e.get("effective_at"), "CORPORATE_ACTION_TIME_UNVERIFIED")
        if instant(e["effective_at"]) <= instant(clock):
            selected.append(e)
    return selected


def active_members(snapshot, day, clock=None):
    return {
        x["instrument_id"]
        for x in snapshot["universe"]
        if x["from"] <= day <= x["to"]
        and (not clock or not x.get("known_at") or instant(x["known_at"]) <= instant(clock))
    }


def attributes(snapshot, iid, day, clock=None):
    base = next(i for i in snapshot["instruments"] if i["id"] == iid)
    rows = [
        x
        for x in base.get("history", [])
        if x["from"] <= day <= x["to"]
        and (not clock or not x.get("known_at") or instant(x["known_at"]) <= instant(clock))
    ]
    if rows:
        return {**base, **max(rows, key=lambda x: x.get("known_at", ""))}
    if snapshot.get("historical_classification_verified"):
        return {**base, "sector": "UNKNOWN", "sector_etf_id": None, "classification_status": "UNVERIFIED"}
    return base


def validate_snapshot(s, m, cal):
    require(s.get("schema_version") == 1 and s.get("data_kind") in ("SYNTHETIC", "REAL"), "INVALID_SNAPSHOT")
    require(s.get("session_kind") == "RTH_DAILY" and s.get("rth_evidence"), "RTH_UNVERIFIED")
    require(s.get("universe_version") == m["universe_version"], "UNIVERSE_VERSION_MISMATCH")
    require(s.get("calendar_version") == m["calendar_version"], "CALENDAR_VERSION_MISMATCH")
    ids = [i["id"] for i in s["instruments"]]
    require(len(ids) == len(set(ids)) and bool(ids), "DUPLICATE_INSTRUMENT")
    require(s.get("universe") and all(x["instrument_id"] in ids for x in s["universe"]), "INVALID_UNIVERSE")
    require(m["benchmark_ref"]["market"] in ids, "MISSING_MARKET_BENCHMARK")
    require(s.get("price_return_kind") == m["price_basis"]["return_kind"], "BASIS_MISMATCH")
    require(s.get("adjustment_epoch") == m["price_basis"]["adjustment_epoch"], "BASIS_MISMATCH")
    pit = m["research_mode"] == "POINT_IN_TIME"
    if pit:
        require(
            s.get("historical_membership_verified") and s.get("historical_classification_verified"),
            "POINT_IN_TIME_HISTORY_UNVERIFIED",
        )
    if pit:
        for row in s["universe"]:
            require(row.get("known_at"), "POINT_IN_TIME_MEMBERSHIP_UNVERIFIED")
            instant(row["known_at"])
    for inst in s["instruments"]:
        require(
            inst.get("asset_type") in ("COMMON", "ADR", "ETF") and inst.get("currency") == "USD",
            "UNSUPPORTED_INSTRUMENT",
        )
        if pit:
            require(inst.get("history"), "POINT_IN_TIME_CLASSIFICATION_UNVERIFIED")
            for row in inst["history"]:
                require(row.get("known_at"), "POINT_IN_TIME_CLASSIFICATION_UNVERIFIED")
                instant(row["known_at"])
    for iid, bars in s["bars"].items():
        require(iid in ids, "UNKNOWN_INSTRUMENT")
        seen = set()
        for b in bars:
            require(
                cal.session(b["market_date"]) and isinstance(b.get("revision"), int) and b["revision"] > 0,
                "INVALID_BAR_REVISION",
            )
            key = (b["market_date"], b["revision"])
            require(key not in seen, "DUPLICATE_BAR_REVISION")
            seen.add(key)
            require(b.get("session_kind") == "RTH_DAILY", "RTH_UNVERIFIED")
            if pit:
                require(
                    b.get("available_at") and b.get("available_at_kind") in ("MEASURED", "PROVIDER"),
                    "POINT_IN_TIME_HISTORY_UNVERIFIED",
                )
            if b.get("available_at"):
                instant(b["available_at"])
            require(b.get("bar_end_at") == cal.session(b["market_date"])["close_at"], "INVALID_BAR_END")
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    v = Decimal(str(b[field]))
                except Exception:
                    require(False, "INVALID_OHLCV")
                require(v.is_finite() and v >= 0 and (field == "volume" or v > 0), "INVALID_OHLCV")
            require(
                Decimal(str(b["low"])) <= min(Decimal(str(b["open"])), Decimal(str(b["close"])))
                and Decimal(str(b["high"])) >= max(Decimal(str(b["open"])), Decimal(str(b["close"]))),
                "INVALID_OHLCV",
            )
    for e in s.get("events", []):
        require(e.get("known_at") and e.get("event_id") and e.get("revision"), "INVALID_EVENT_REVISION")
        instant(e["known_at"])
    return s


class InputView:
    def __init__(self, s, m, cal):
        self.s, self.m, self.cal = s, m, cal
        self.index = {}
        self.visibility = {}
        self.prefixes = {}
        self.events_cache = {}
        for rows in s["bars"].values():
            for b in rows:
                self.visibility[id(b)] = (
                    instant(b["available_at"]).timestamp() if b.get("available_at") else None
                )
        for iid, bars in s["bars"].items():
            grouped = defaultdict(list)
            for b in bars:
                grouped[b["market_date"]].append(b)
            self.index[iid] = dict(sorted(grouped.items()))

    def events(self, clock):
        if clock in self.events_cache:
            return self.events_cache[clock]
        latest = {}
        for e in self.s.get("events", []):
            if instant(e["known_at"]) <= instant(clock):
                if e["event_id"] not in latest or e["revision"] > latest[e["event_id"]]["revision"]:
                    latest[e["event_id"]] = e
        result = sorted(
            (e for e in latest.values() if e.get("status") != "CANCELLED"), key=lambda x: x["event_id"]
        )
        self.events_cache[clock] = result
        return result

    def bars(self, iid, day, clock):
        out = []
        at = instant(clock).timestamp()
        revised = self.m["research_mode"] == "REVISED_HISTORY"
        for d, rows in self.index.get(iid, {}).items():
            if d > day:
                break
            if len(rows) == 1:
                row = rows[0]
                available = self.visibility[id(row)]
                if (available is not None and available <= at) or (
                    available is None and revised and clock_for(d, self.m, self.cal) <= clock
                ):
                    out.append(row)
            else:
                visible = [
                    b
                    for b in rows
                    if (self.visibility[id(b)] is not None and self.visibility[id(b)] <= at)
                    or (
                        self.visibility[id(b)] is None and revised and clock_for(d, self.m, self.cal) <= clock
                    )
                ]
                if visible:
                    out.append(max(visible, key=lambda b: b["revision"]))
        return out

    def prefix_hash(self, iid, bars):
        previous, chain = self.prefixes.get(iid, ([], digest([])))
        if len(bars) == len(previous) + 1 and bars[:-1] == previous:
            chain = digest([chain, bars[-1]])
        else:
            chain = digest([])
            for b in bars:
                chain = digest([chain, b])
        self.prefixes[iid] = (bars, chain)
        return chain

    def quality(self, bars, day):
        if not bars or bars[-1]["market_date"] != day:
            return "DATA_UNAVAILABLE_AT_CLOCK"
        if any(not b.get("is_final") or b.get("quality_status") != "OK" for b in bars):
            return "LATEST_REVISION_INVALID"
        if [b["market_date"] for b in bars] != self.cal.dates(bars[0]["market_date"], day):
            return "MISSING_INTERMEDIATE_SESSION"
        return None


class FeatureCursor:
    def __init__(self, cal, iid):
        self.cal, self.iid = cal, iid
        self.stream = StreamingFeatures(cal, iid)
        self.previous = []
        self.action_hash = None

    def feature(self, bars, actions, clock):
        action_hash = digest(actions)
        changed = (
            self.action_hash != action_hash
            or len(bars) != len(self.previous) + 1
            or bars[:-1] != self.previous
        )
        if changed:
            self.stream = StreamingFeatures(self.cal, self.iid, action_hash)
            adjusted = adjust(bars, actions, clock)
        else:
            # No new action/revision: past adjusted units and recursive state stay valid.
            adjusted = adjust([bars[-1]], actions, clock)
        for b in adjusted:
            f = self.stream.append(b)
        self.previous = bars
        self.action_hash = action_hash
        f["evaluated_at"] = clock
        return f, changed
