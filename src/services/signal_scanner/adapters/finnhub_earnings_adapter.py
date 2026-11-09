"""One bounded date-range request; calendar rows do not prove publication timestamps."""

import os
from datetime import date
from decimal import Decimal

import httpx

from src.services.signal_scanner.adapters.base import get_response, source_report
from src.services.signal_scanner.core import DomainError, dec, now, require, text_decimal


class FinnhubEarningsAdapter:
    def __init__(self, key=None, client=None):
        self.key = key if key is not None else os.environ.get("FINNHUB_API_KEY")
        self.client = client

    def fetch(self, symbols, start, end):
        require(self.key, "CREDENTIAL_NOT_CONFIGURED", status=503)
        require(0 < len(symbols) <= 8, "OUT_OF_RANGE", "当前只开放不超过8个验证标的", 422)
        own = self.client is None
        client = self.client or httpx.Client(timeout=15, follow_redirects=False)
        try:
            response = get_response(
                client,
                "https://finnhub.io/api/v1/calendar/earnings",
                headers={"X-Finnhub-Token": self.key},
                params={"from": start, "to": end},
            )
            try:
                data = response.json(parse_float=Decimal)
            except ValueError:
                raise DomainError("SOURCE_INVALID_RESPONSE", status=503) from None
        finally:
            if own:
                client.close()
        require(isinstance(data, dict), "SOURCE_INVALID_RESPONSE", status=503)
        if data.get("error"):
            raise DomainError("SOURCE_PERMISSION_DENIED", status=503)
        require(isinstance(data.get("earningsCalendar"), list), "SOURCE_INVALID_RESPONSE", status=503)
        events, invalid = [], []
        covered = set()
        identities = set()
        for item in data["earningsCalendar"]:
            symbol = item.get("symbol")
            if symbol not in symbols:
                continue
            day = item.get("date")
            try:
                date.fromisoformat(day)
            except (ValueError, TypeError):
                invalid.append(dict(symbol=symbol, reason="INVALID_DATE"))
                continue
            if not start <= day <= end:
                continue
            hour = {"bmo": "BMO", "amc": "AMC"}.get(str(item.get("hour", "")).lower(), "UNKNOWN")
            payload = dict(
                symbol=symbol,
                hour=hour,
                provider_hour=item.get("hour"),
                quarter=item.get("quarter"),
                year=item.get("year"),
                publication_time_verified=False,
                coverage="EVENT_OBSERVED",
            )
            for field in ("epsActual", "epsEstimate", "revenueActual", "revenueEstimate"):
                raw = item.get(field)
                if raw is None:
                    payload[field] = None
                else:
                    try:
                        payload[field] = text_decimal(dec(str(raw)))
                    except DomainError:
                        payload[field] = None
                        invalid.append(dict(symbol=symbol, reason="INVALID_FINANCIAL_FIELD", field=field))
            payload["actual_results_observed"] = (
                payload.get("epsActual") is not None or payload.get("revenueActual") is not None
            )
            ident = f"{item['year']}:Q{item['quarter']}" if item.get("year") and item.get("quarter") else day
            eid = f"finnhub:earnings:US:{symbol}:{ident}"
            require(eid not in identities, "SOURCE_DUPLICATE_EVENT", status=503)
            identities.add(eid)
            covered.add(symbol)
            events.append(
                dict(
                    event_id=eid,
                    instrument_id="US:" + symbol,
                    event_type="EARNINGS",
                    scheduled_date=day,
                    scheduled_at=None,
                    published_at=None,
                    status="OVERDUE_UNVERIFIED" if day < now().date().isoformat() else "SCHEDULED",
                    payload_json=payload,
                )
            )
        coverage = {symbol: "EVENT_OBSERVED" if symbol in covered else "NO_COVERAGE" for symbol in symbols}
        report = source_report(
            "FINNHUB",
            "EARNINGS_CALENDAR",
            True,
            success=True,
            freshness="OK",
            quality="DEGRADED"
            if invalid
            else "PARTIAL_COVERAGE"
            if len(covered) < len(symbols)
            else "OBSERVED_EVENTS_ONLY",
            reason="NO_COVERAGE"
            if not covered
            else "PARTIAL_COVERAGE"
            if len(covered) < len(symbols)
            else None,
            count=len(events),
            symbols=symbols,
            coverage_by_symbol=coverage,
            request_count=1,
            from_date=start,
            to_date=end,
            invalid_rows=invalid,
            complete_coverage_verified=False,
            publication_time_verified=False,
            public_docs="https://finnhub.io/docs/api/earnings-calendar",
        )
        if not covered or invalid:
            report["status"] = "PARTIAL"
        return dict(events=events, source_report=report)
