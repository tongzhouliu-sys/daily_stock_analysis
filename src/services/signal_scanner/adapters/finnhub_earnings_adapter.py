"""One bounded date-range request; calendar rows do not prove publication timestamps.

FINNHUB_API_KEY 支持英文逗号分隔的多个 Key；鉴权失败、配额用尽或限流时切换下一个 Key。
"""

import os
from datetime import date
from decimal import Decimal

import httpx

from data_provider.api_key_pool import ApiKeyPool, KeyFailureError, parse_api_keys
from src.services.signal_scanner.adapters.base import get_response, source_report
from src.services.signal_scanner.core import DomainError, dec, now, require, text_decimal

EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
# 鉴权失败、权限不足或限流只说明当前 Key 不可用，换下一个 Key 仍可能成功
KEY_FAILURE_CODES = frozenset({"SOURCE_PERMISSION_DENIED", "SOURCE_RATE_LIMITED"})


class FinnhubEarningsAdapter:
    def __init__(self, key=None, client=None):
        value = key if key is not None else os.environ.get("FINNHUB_API_KEY")
        self._key_pool = ApiKeyPool(parse_api_keys(value))
        self.client = client

    @property
    def key(self):
        """主 Key，供既有「是否已配置凭据」判断使用。"""
        return self._key_pool.primary

    @property
    def keys(self):
        return self._key_pool.keys

    def _calendar(self, client, api_key, start, end):
        """单 Key 请求日历；Key 级失败包装成 KeyFailureError 交给 Key 池切换。"""
        try:
            response = get_response(
                client,
                EARNINGS_URL,
                headers={"X-Finnhub-Token": api_key},
                params={"from": start, "to": end},
            )
        except DomainError as error:
            if error.code not in KEY_FAILURE_CODES:
                raise
            raise KeyFailureError(error.code, error=error) from None
        try:
            data = response.json(parse_float=Decimal)
        except ValueError:
            raise DomainError("SOURCE_INVALID_RESPONSE", status=503) from None
        if isinstance(data, dict) and data.get("error"):
            raise KeyFailureError(
                "SOURCE_PERMISSION_DENIED", error=DomainError("SOURCE_PERMISSION_DENIED", status=503)
            )
        return data

    def fetch(self, symbols, start, end):
        require(self._key_pool.is_configured, "CREDENTIAL_NOT_CONFIGURED", status=503)
        require(0 < len(symbols) <= 8, "OUT_OF_RANGE", "当前只开放不超过8个验证标的", 422)
        own = self.client is None
        client = self.client or httpx.Client(timeout=15, follow_redirects=False)
        try:
            data = self._key_pool.call(lambda api_key: self._calendar(client, api_key, start, end))
        except KeyFailureError as error:
            # 所有 Key 都失败时保留原有的领域错误语义
            raise (error.error or DomainError(error.reason or "SOURCE_PERMISSION_DENIED", status=503)) from None
        finally:
            if own:
                client.close()
        require(isinstance(data, dict), "SOURCE_INVALID_RESPONSE", status=503)
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
