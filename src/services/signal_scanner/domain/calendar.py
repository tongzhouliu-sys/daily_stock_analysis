from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from src.services.signal_scanner.core import instant, stamp

NY = ZoneInfo("America/New_York")
SG = ZoneInfo("Asia/Singapore")


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XNYS", start="1990-01-01", end="2035-12-31")


class Calendar:
    version = f"XNYS-exchange_calendars-{xcals.__version__}"

    def __init__(self):
        self.cal = calendar()

    @lru_cache(maxsize=30000)
    def session(self, d: str):
        key = pd.Timestamp(d)
        if key not in self.cal.sessions:
            return None
        o, c = self.cal.session_open(key).to_pydatetime(), self.cal.session_close(key).to_pydatetime()
        week = key.isocalendar()[:2]
        later = self.cal.sessions[self.cal.sessions >= key][:6]
        last = max(x for x in later if x.isocalendar()[:2] == week)
        return dict(
            market_date=d,
            open_at=stamp(o),
            close_at=stamp(c),
            previous_date=self.cal.previous_session(key).strftime("%Y-%m-%d"),
            next_date=self.cal.next_session(key).strftime("%Y-%m-%d"),
            week_last_date=last.strftime("%Y-%m-%d"),
            is_early_close=(c.astimezone(NY).hour < 16),
            settlement_date=None,
            calendar_version=self.version,
        )

    def dates(self, start: str, end: str):
        return [x.strftime("%Y-%m-%d") for x in self.cal.sessions_in_range(start, end)]

    def shift(self, d: str, n: int):
        index = self.cal.sessions.get_loc(pd.Timestamp(d))
        return self.cal.sessions[index + n].strftime("%Y-%m-%d")

    def distance(self, a: str, b: str):
        return int(self.cal.sessions.get_loc(pd.Timestamp(b)) - self.cal.sessions.get_loc(pd.Timestamp(a)))

    def last_completed(self, at: datetime):
        d = at.astimezone(NY).date()
        key = self.cal.date_to_session(str(d), direction="previous")
        if self.cal.session_close(key).to_pydatetime() > at:
            key = self.cal.previous_session(key)
        return key.strftime("%Y-%m-%d")

    def action_date(self, at: datetime):
        key = self.cal.date_to_session(str(at.astimezone(NY).date()), direction="next")
        if self.cal.session_close(key).to_pydatetime() <= at:
            key = self.cal.next_session(key)
        return key.strftime("%Y-%m-%d")

    def risk_date(self, at: datetime):
        key = self.cal.date_to_session(str(at.astimezone(NY).date()), direction="next")
        if self.cal.session_close(key).to_pydatetime() < at:
            key = self.cal.next_session(key)
        return key.strftime("%Y-%m-%d")

    def first_full_after(self, published_at: str):
        at = instant(published_at)
        key = self.cal.date_to_session(str(at.astimezone(NY).date()), direction="next")
        if self.cal.session_open(key).to_pydatetime() <= at:
            key = self.cal.next_session(key)
        return key.strftime("%Y-%m-%d")

    def context(self, at: datetime, events=(), timezone="Asia/Singapore", extended=False, coverage=None):
        zone = ZoneInfo(timezone)
        day = at.astimezone(NY).date().isoformat()
        s = self.session(day)
        status, phase = "CLOSED", None
        if s:
            o, c = instant(s["open_at"]), instant(s["close_at"])
            if o <= at < c:
                status = "RTH"
                phase = (
                    "OPENING_30M"
                    if at < min(o + timedelta(minutes=30), c)
                    else "POWER_HOUR"
                    if at >= max(o, c - timedelta(minutes=60))
                    else "REGULAR"
                )
            elif extended:
                local = at.astimezone(NY)
                status = (
                    "PRE"
                    if local.hour >= 4 and at < o
                    else "POST"
                    if at >= c and local.hour < 20
                    else "CLOSED"
                )
            else:
                status = "UNKNOWN"
        previous = self.last_completed(at)
        action = self.action_date(at)
        nxt = self.session(action)
        next_open = (
            nxt["open_at"] if instant(nxt["open_at"]) > at else self.session(self.shift(action, 1))["open_at"]
        )

        def times(value):
            dt = instant(value)
            return dict(
                utc=stamp(dt),
                et=dt.astimezone(NY).isoformat(),
                display=dt.astimezone(zone).isoformat(),
                market_timezone="America/New_York",
                display_timezone=timezone,
            )

        latest = {}
        for e in events:
            if instant(e["known_at"]) <= at and (
                e["event_id"] not in latest or e["revision"] > latest[e["event_id"]]["revision"]
            ):
                latest[e["event_id"]] = e
        future = [
            e
            for e in latest.values()
            if e.get("scheduled_at") and instant(e["scheduled_at"]) > at and e["status"] != "CANCELLED"
        ]
        first = min((e["scheduled_at"] for e in future), default=None)
        selected = [{**e, "time": times(e["scheduled_at"])} for e in future if e["scheduled_at"] == first]
        return dict(
            evaluated_at=stamp(at),
            data_as_of=stamp(at),
            market="US_EQUITIES",
            calendar_version=self.version,
            schedule_version=self.version + ("-EXTENDED-EXPLICIT" if extended else "-RTH"),
            venue_scope=["XNYS"],
            session_status=status,
            rth_phase=phase,
            current_market_date=day if status == "RTH" else None,
            source_market_date=previous,
            action_market_date=action,
            risk_market_date=self.risk_date(at),
            previous_rth_close=times(self.session(previous)["close_at"]),
            next_rth_open=times(next_open),
            next_rth_close=times(nxt["close_at"]),
            next_events=sorted(selected, key=lambda e: (e["event_type"], e["event_id"])),
            undated_events=[
                e
                for e in latest.values()
                if not e.get("scheduled_at")
                and e["status"] != "CANCELLED"
                and e["event_type"] in ("EARNINGS", "CPI", "FOMC", "EMPLOYMENT", "MARKET_HALT")
            ],
            event_coverage=coverage or dict(status="UNKNOWN", reason="EVENT_SOURCE_NOT_CONFIGURED"),
            source_coverage="UNVERIFIED",
            quote_status="DISABLED",
            dst_notice=dst_notice(at, timezone),
        )


def dst_notice(at: datetime, timezone="Asia/Singapore"):
    zone = ZoneInfo(timezone)
    # Include a transition earlier today so the morning digest can emit its DAY phase.
    start = (at - timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    previous = start.astimezone(NY).utcoffset()
    for h in range(24 * 370):
        point = start + timedelta(hours=h + 1)
        offset = point.astimezone(NY).utcoffset()
        if offset != previous:
            diff = (point.astimezone(zone).date() - at.astimezone(zone).date()).days
            if 0 <= diff <= 3:
                phase = "DAY" if diff == 0 else "PRE"
                return dict(
                    transition_at=stamp(point),
                    phase=phase,
                    key=f"owner/{timezone}/{stamp(point)}/{phase}",
                    timezone=timezone,
                )
            if diff > 3:
                return None
        previous = offset
    return None
