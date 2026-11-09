import csv
import io

import httpx

from src.services.signal_scanner.adapters.base import get_response, source_report
from src.services.signal_scanner.core import DomainError, dec, require, text_decimal


class FredVixAdapter:
    def __init__(self, cal, client=None):
        self.cal, self.client = cal, client

    def fetch(self, start, end):
        own = self.client is None
        client = self.client or httpx.Client(timeout=15, follow_redirects=False)
        try:
            response = get_response(
                client,
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params=dict(id="VIXCLS", cosd=start, coed=end),
            )
            reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
            require(
                reader.fieldnames and "VIXCLS" in reader.fieldnames, "SOURCE_INVALID_RESPONSE", status=503
            )
            rows = list(reader)
        finally:
            if own:
                client.close()
        events, missing, invalid = [], [], []
        for row in rows:
            day = row.get("observation_date") or row.get("DATE") or ""
            if not start <= day <= end:
                continue
            try:
                session = self.cal.session(day)
            except ValueError:
                session = None
            if not session:
                continue
            value = row.get("VIXCLS")
            if value in (None, "", "."):
                missing.append(day)
                continue
            try:
                number = dec(value)
                require(0 < number < 200, "SOURCE_INVALID_VALUE", status=503)
            except DomainError:
                invalid.append(day)
                continue
            events.append(
                dict(
                    event_id="fred:VIXCLS:" + day,
                    instrument_id=None,
                    event_type="VIX_VALUE",
                    scheduled_date=day,
                    status="PUBLISHED",
                    payload_json=dict(
                        market_date=day,
                        value=text_decimal(number),
                        series_id="VIXCLS",
                        as_of=day,
                        as_of_kind="OBSERVATION_DATE",
                        available_at=None,
                        available_at_kind="UNKNOWN",
                    ),
                )
            )
        require(events, "SOURCE_NO_COVERAGE", status=503)
        events.sort(key=lambda e: e["payload_json"]["market_date"])
        last = events[-1]["payload_json"]
        report = source_report(
            "FRED",
            "VIX_HISTORY",
            True,
            success=True,
            freshness="OK" if last["market_date"] == end else "STALE",
            quality="DEGRADED" if invalid else "VALID_WITH_GAPS" if missing else "OK",
            reason="SOURCE_DATA_STALE"
            if last["market_date"] != end
            else "SOURCE_INVALID_VALUE"
            if invalid
            else None,
            series_id="VIXCLS",
            count=len(events),
            first_market_date=events[0]["payload_json"]["market_date"],
            latest_market_date=last["market_date"],
            latest_value=last["value"],
            as_of=last["market_date"],
            as_of_kind="OBSERVATION_DATE",
            data_as_of=None,
            missing_sessions=missing,
            invalid_rows=invalid,
            access_mode="PUBLIC_CSV_NO_KEY",
            public_docs="https://fred.stlouisfed.org/series/VIXCLS",
        )
        if invalid or last["market_date"] != end:
            report["status"] = "PARTIAL"
        return dict(events=events, source_report=report)
