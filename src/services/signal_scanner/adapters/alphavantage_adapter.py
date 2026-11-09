"""Raw daily fallback with a private credential pool and persistent per-key budgets."""

import json
import os
from datetime import timedelta

import httpx

from src.services.signal_scanner.adapters.base import source_report
from src.services.signal_scanner.adapters.market import quality
from src.services.signal_scanner.core import DomainError, instant, now, require, stamp


class AlphaVantageAdapter:
    def __init__(self, cal, quota_path, *, keys=None, client=None, at=now):
        self.cal, self.path, self.client, self.at = cal, quota_path, client, at
        self.keys = keys if keys is not None else json.loads(os.environ.get("ALPHAVANTAGE_API_KEYS", "[]"))
        require(isinstance(self.keys, list) and len(self.keys) <= 10, "PRIVATE_CONFIG_INVALID", status=503)

    def fetch(self, symbol, start, end):
        from src.services.signal_scanner.services.daily_market import read_json, write_json

        require(self.keys, "CREDENTIAL_NOT_CONFIGURED", status=503)
        quota = read_json(self.path, {"next": 0, "keys": {}})
        current = self.at()
        if quota.get("blocked_until") and current < instant(quota["blocked_until"]):
            raise DomainError("SOURCE_FALLBACK_BUDGET_EXHAUSTED", status=503)
        shared_requests = [
            t
            for entry in quota["keys"].values()
            for t in entry.get("requests", [])
            if instant(t) > current - timedelta(days=1)
        ]
        # Live validation showed the provider also enforces a shared daily limit
        # on this connection. Rotating credentials must not multiply that quota.
        if len(shared_requests) >= 24:
            raise DomainError("SOURCE_FALLBACK_BUDGET_EXHAUSTED", status=503)
        response = None
        last_error = "SOURCE_FALLBACK_BUDGET_EXHAUSTED"
        starting_index = quota["next"]
        for offset in range(len(self.keys)):
            index = (starting_index + offset) % len(self.keys)
            entry = quota["keys"].setdefault(str(index), {"requests": []})
            entry["requests"] = [t for t in entry["requests"] if instant(t) > current - timedelta(days=1)]
            if len(entry["requests"]) >= 24:
                continue
            if entry.get("cooldown_until") and current < instant(entry["cooldown_until"]):
                continue
            entry["requests"].append(stamp(current))
            # Only numeric pool positions are persisted; never credential values or hashes.
            quota["next"] = (index + 1) % len(self.keys)
            write_json(self.path, quota)
            own = self.client is None
            client = self.client or httpx.Client(timeout=15, follow_redirects=False)
            try:
                response = client.get(
                    "https://www.alphavantage.co/query",
                    params={
                        "function": "TIME_SERIES_DAILY",
                        "symbol": symbol.replace(".", "-"),
                        "outputsize": "compact",
                        "datatype": "json",
                        "apikey": self.keys[index],
                    },
                )
            except httpx.RequestError:
                last_error = "SOURCE_UNREACHABLE"
                continue
            finally:
                if own:
                    client.close()
            if response.status_code in (401, 403, 429) or response.status_code >= 500:
                last_error = (
                    "SOURCE_RATE_LIMITED"
                    if response.status_code == 429
                    else "SOURCE_PERMISSION_DENIED"
                    if response.status_code in (401, 403)
                    else "SOURCE_UNAVAILABLE"
                )
                entry["cooldown_until"] = stamp(
                    current + timedelta(hours=24 if response.status_code == 429 else 1)
                )
                write_json(self.path, quota)
                continue
            require(response.status_code == 200, "SOURCE_REQUEST_REJECTED", status=503)
            try:
                data = response.json()
            except ValueError:
                raise DomainError("SOURCE_INVALID_RESPONSE", status=503) from None
            if not isinstance(data, dict):
                raise DomainError("SOURCE_INVALID_RESPONSE", status=503)
            if "Information" in data or "Note" in data:
                quota["blocked_until"] = stamp(current + timedelta(days=1))
                write_json(self.path, quota)
                raise DomainError("SOURCE_RATE_LIMITED", status=503)
            require("Error Message" not in data, "SOURCE_NO_COVERAGE", status=503)
            series = data.get("Time Series (Daily)")
            require(isinstance(series, dict) and series, "SOURCE_NO_COVERAGE", status=503)
            bars = []
            for day, value in sorted(series.items()):
                if not start <= day <= end:
                    continue
                try:
                    bar = {
                        "instrument_id": "US:" + symbol,
                        "market_date": day,
                        "session": "EOD",
                        "is_final": True,
                        **{
                            field: str(value[column])
                            for field, column in {
                                "open": "1. open",
                                "high": "2. high",
                                "low": "3. low",
                                "close": "4. close",
                                "volume": "5. volume",
                            }.items()
                        },
                        "provider_fields": {
                            "source_provider": "ALPHA_VANTAGE",
                            "corporate_action_coverage": "UNVERIFIED",
                        },
                    }
                except (KeyError, TypeError):
                    raise DomainError("SOURCE_INVALID_RESPONSE", status=503) from None
                require(not quality(bar, self.cal), "SOURCE_INVALID_ROWS", status=503)
                bars.append(bar)
            require(bars, "SOURCE_NO_COVERAGE", status=503)
            missing = sorted(set(self.cal.dates(start, end)) - {b["market_date"] for b in bars})
            require(not missing and bars[-1]["market_date"] == end, "SOURCE_HISTORY_INCOMPLETE", status=503)
            entry["successful_requests"] = entry.get("successful_requests", 0) + 1
            write_json(self.path, quota)
            return {
                "provider": "ALPHA_VANTAGE",
                "feed": "EOD_RAW",
                "bars": bars,
                "actions": [],
                "source_report": source_report(
                    "ALPHA_VANTAGE",
                    "EOD_DAILY",
                    True,
                    success=True,
                    freshness="OK",
                    quality="EOD_ACTIONS_UNVERIFIED",
                    count=len(bars),
                    latest_market_date=bars[-1]["market_date"],
                    rth_scope_verified=False,
                    corporate_action_coverage="UNVERIFIED",
                ),
            }
        raise DomainError(last_error, status=503)
