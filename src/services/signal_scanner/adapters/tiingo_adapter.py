"""Tiingo EOD raw bars. EOD volume is never silently asserted to be verified RTH."""

import os
import re
from decimal import Decimal

import httpx

from src.services.signal_scanner.adapters.base import MarketDataAdapter, get_response, source_report
from src.services.signal_scanner.adapters.market import quality
from src.services.signal_scanner.core import DomainError, dec, digest, now, require, text_decimal


class TiingoAdapter(MarketDataAdapter):
    def __init__(self, cal, key=None, client=None):
        self.cal = cal
        self.key = key if key is not None else os.environ.get("TIINGO_API_KEY")
        self.client = client

    def fetch(self, symbol, start, end):
        require(self.key, "CREDENTIAL_NOT_CONFIGURED", status=503)
        require(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol), "VALIDATION_ERROR", status=422)
        require(start <= end <= self.cal.last_completed(now()), "OUT_OF_RANGE", status=422)
        # A dividend on the first requested day needs the preceding raw close.
        previous = self.cal.cal.date_to_session(start, direction="previous").strftime("%Y-%m-%d")
        first_session = self.cal.cal.sessions[0].strftime("%Y-%m-%d")
        request_start = self.cal.shift(previous, -1) if previous == start and previous > first_session else previous
        own = self.client is None
        client = self.client or httpx.Client(timeout=15, follow_redirects=False)
        try:
            response = get_response(
                client,
                f"https://api.tiingo.com/tiingo/daily/{symbol.lower().replace('.', '-')}/prices",
                headers={"Authorization": "Token " + self.key},
                params=dict(startDate=request_start, endDate=end, resampleFreq="daily", format="json"),
            )
            try:
                rows = response.json(parse_float=Decimal)
            except ValueError:
                raise DomainError("SOURCE_INVALID_RESPONSE", status=503) from None
        finally:
            if own:
                client.close()
        require(isinstance(rows, list) and rows, "SOURCE_NO_COVERAGE", status=503)
        bars, actions, errors = [], [], []
        iid = "US:" + symbol
        seen = set()
        required = {"date", "open", "high", "low", "close", "volume", "divCash", "splitFactor"}
        for row in sorted(rows, key=lambda x: x.get("date", "") if isinstance(x, dict) else ""):
            if not isinstance(row, dict) or not required <= set(row):
                errors.append(dict(reason="MISSING_FIELDS"))
                continue
            day = row["date"][:10]
            if not request_start <= day <= end:
                continue
            require(day not in seen, "SOURCE_DUPLICATE_SESSION", status=503)
            seen.add(day)
            b = dict(
                instrument_id=iid,
                market_date=day,
                session="EOD",
                is_final=True,
                **{k: str(row[k]) for k in ("open", "high", "low", "close", "volume")},
                provider_fields={
                    k: str(row[k])
                    for k in (
                        "adjOpen",
                        "adjHigh",
                        "adjLow",
                        "adjClose",
                        "adjVolume",
                        "divCash",
                        "splitFactor",
                    )
                    if k in row
                },
            )
            row_errors = quality(b, self.cal)
            try:
                split, cash = dec(str(row["splitFactor"])), dec(str(row["divCash"]), nonnegative=True)
                require(split > 0, "SOURCE_ACTION_INVALID", status=503)
            except DomainError:
                row_errors.append("CORPORATE_ACTION_INVALID")
                split, cash = Decimal(1), Decimal(0)
            if row_errors:
                errors.append(dict(market_date=day, reasons=row_errors))
            if self.cal.session(day):
                effective = self.cal.session(day)["open_at"]
                if split != 1 and day >= start:
                    actions.append(
                        dict(
                            event_id=f"tiingo:{iid}:SPLIT:{day}",
                            instrument_id=iid,
                            event_type="SPLIT",
                            effective_at=effective,
                            status="PUBLISHED",
                            payload_json=dict(ratio=text_decimal(split), source_field="splitFactor"),
                        )
                    )
                if cash > 0 and day >= start:
                    payload = dict(
                        amount=text_decimal(cash),
                        currency="USD",
                        ex_date=day,
                        pay_date=None,
                        source_field="divCash",
                    )
                    if bars and not quality(bars[-1], self.cal):
                        previous = dec(bars[-1]["close"]) / split
                        factor = (previous - cash) / previous
                        if 0 < factor <= 1:
                            payload.update(
                                price_factor=text_decimal(factor),
                                factor_method="PREVIOUS_RAW_CLOSE_AND_EX_CASH",
                                previous_market_date=bars[-1]["market_date"],
                            )
                    actions.append(
                        dict(
                            event_id=f"tiingo:{iid}:DIVIDEND:{day}",
                            instrument_id=iid,
                            event_type="DIVIDEND",
                            effective_at=effective,
                            status="PUBLISHED",
                            payload_json=payload,
                        )
                    )
            bars.append(b)
        warmup = [b for b in bars if b["market_date"] < start]
        bars = [b for b in bars if b["market_date"] >= start]
        expected = self.cal.dates(start, end)
        missing = sorted(set(expected) - {b["market_date"] for b in bars})
        last = max((b["market_date"] for b in bars), default=None)
        healthy = not errors and not missing and last == end
        report = source_report(
            "TIINGO",
            "EOD_DAILY",
            True,
            success=True,
            reason=None if healthy else "SOURCE_HISTORY_INCOMPLETE",
            freshness="OK" if last == end else "STALE",
            quality="ROWS_VALID_SCOPE_UNVERIFIED" if healthy else "DEGRADED",
            symbol=symbol,
            count=len(bars),
            first_market_date=bars[0]["market_date"] if bars else None,
            latest_market_date=last,
            data_as_of=self.cal.session(last)["close_at"] if last and self.cal.session(last) else None,
            available_at=None,
            available_at_kind="UNKNOWN",
            missing_sessions=missing,
            invalid_rows=errors,
            session_scope="EOD_PROVIDER_DECLARED",
            rth_scope_verified=False,
            volume_basis="TIINGO_EOD_PROVIDER_DECLARED",
            pagination="API_DATE_RANGE_NOT_PAGINATED",
            public_docs="https://www.tiingo.com/documentation/end-of-day",
        )
        report["status"] = "SUCCEEDED" if healthy else "PARTIAL"
        return dict(
            provider="TIINGO",
            feed="EOD_RAW",
            coverage="TIINGO_EOD_PROVIDER_DECLARED",
            instruments=[
                dict(
                    id=iid,
                    ticker=symbol,
                    name=symbol + " · Tiingo EOD",
                    type="ETF" if symbol == "SPY" else "COMMON",
                    currency="USD",
                    venue="US_EQUITIES",
                    roles=["TRADE", "MARKET"] if symbol == "SPY" else ["TRADE"],
                    profile=dict(
                        source="TIINGO_EOD",
                        rth_scope_verified=False,
                        concentration_bucket="BROAD_MARKET" if symbol == "SPY" else None,
                    ),
                )
            ],
            bars=bars,
            warmup_bars=warmup,
            actions=actions,
            source_report=report,
            raw_response_hash=digest(rows),
        )
