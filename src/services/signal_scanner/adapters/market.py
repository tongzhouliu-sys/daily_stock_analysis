from __future__ import annotations

import csv
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import httpx

from src.services.signal_scanner.core import DomainError, dec, digest, instant, now, reject_credentials, require, stamp, uid
from src.services.signal_scanner.storage.db import insert, one, update, audit


def capability_enabled(config, name):
    entry = next((c for c in config.get("capabilities", []) if c["capability_id"] == name), None)
    return bool(
        entry and entry["enabled"] and entry["support"] == "SUPPORTED" and entry["entitlement"] == "GRANTED"
    )


def input_path(runtime: Path, filename: str):
    root = (runtime / "imports").resolve()
    path = (root / filename).resolve()
    require(path.is_relative_to(root) and path.is_file(), "DEPENDENCY_UNAVAILABLE", "导入文件不存在", 503)
    require(path.stat().st_size <= 50 * 1024 * 1024, "OUT_OF_RANGE", "行情导入文件上限50MB", 413)
    return path


def load_bundle(runtime, filename):
    path = input_path(runtime, filename)
    if path.suffix == ".csv":
        metadata = json.loads(input_path(runtime, path.stem + ".meta.json").read_text())
        return {**metadata, "bars": list(csv.DictReader(path.open(encoding="utf-8-sig")))}
    return json.loads(path.read_text())


def register_instruments(cx, items):
    for i in items:
        require(
            set(i) >= {"id", "ticker", "name", "type", "currency", "venue"},
            "VALIDATION_ERROR",
            "缺少证券主数据",
            422,
        )
        require(i["type"] in ("COMMON", "ADR", "ETF") and i["currency"] == "USD", "OUT_OF_RANGE", status=422)
        require(dec(i.get("price_tick", "0.01")) > 0, "OUT_OF_RANGE", status=422)
        old = one(cx, "SELECT * FROM instruments WHERE id=?", (i["id"],))
        if old:
            require(
                old["currency"] == i["currency"] and old["asset_type"] == i["type"],
                "REVISION_CONFLICT",
                "已有证券身份不允许重用",
            )
            profile = {**old["profile_json"], **i.get("profile", {})}
            fields = dict(
                current_ticker=i["ticker"],
                display_name=i["name"],
                primary_venue=i["venue"],
                sector_code=i.get("sector", old["sector_code"]),
                price_tick=i.get("price_tick", old["price_tick"]),
                profile_json=profile,
                reference_roles_json=i.get("roles", old["reference_roles_json"]),
            )
            if any(old[k] != v for k, v in fields.items()):
                aliases = list(old["aliases_json"])
                if old["current_ticker"] != i["ticker"]:
                    aliases.append(
                        dict(
                            ticker=old["current_ticker"],
                            venue=old["primary_venue"],
                            **{"from": None, "to": i.get("effective_from")},
                            known_at=stamp(),
                        )
                    )
                update(
                    cx,
                    "instruments",
                    "id",
                    i["id"],
                    {
                        **fields,
                        "aliases_json": aliases,
                        "metadata_revision": old["metadata_revision"] + 1,
                        "row_version": old["row_version"] + 1,
                    },
                )
                audit(
                    cx,
                    "INSTRUMENT_METADATA_CHANGED",
                    i["id"],
                    "来源主数据更新；内部身份不变",
                    old_ticker=old["current_ticker"],
                    new_ticker=i["ticker"],
                )
            continue
        insert(
            cx,
            "instruments",
            dict(
                id=i["id"],
                current_ticker=i["ticker"],
                display_name=i["name"],
                primary_venue=i["venue"],
                asset_type=i["type"],
                currency=i["currency"],
                sector_code=i.get("sector"),
                price_tick=i.get("price_tick", "0.01"),
                reference_roles_json=i.get("roles", ["TRADE"]),
                profile_json=i.get("profile", {}),
                aliases_json=i.get("aliases", []),
            ),
        )


def quality(bar, cal):
    errors = []
    try:
        values = {k: dec(str(bar[k])) for k in ("open", "high", "low", "close", "volume")}
        if min(values[k] for k in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
            errors.append("INVALID_PRICE_OR_VOLUME")
        if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(
            values["open"], values["close"]
        ):
            errors.append("INVALID_OHLC")
        session = cal.session(bar["market_date"])
        if not session:
            errors.append("NON_SESSION")
        if session and (
            bar.get("bar_start_at", session["open_at"]) != session["open_at"]
            or bar.get("bar_end_at", session["close_at"]) != session["close_at"]
        ):
            errors.append("NOT_RTH")
        if bar.get("is_final", True) not in (True, "true", "True", "1", 1):
            errors.append("NOT_FINAL")
        if session and instant(session["close_at"]) > now():
            errors.append("UNFINISHED_SESSION")
    except (KeyError, ValueError, DomainError):
        errors.append("INVALID_FIELDS")
    return errors


def ingest(cx, bundle, cal, artifacts):
    reject_credentials(bundle)
    require(
        set(bundle) >= {"provider", "feed", "coverage", "instruments", "bars"},
        "VALIDATION_ERROR",
        "缺少来源/口径声明",
        422,
    )
    register_instruments(cx, bundle["instruments"])
    rawref = artifacts.save(bundle, "raw")
    counts = dict(inserted=0, duplicates=0, quarantined=0, invalid_records=[])
    for b in bundle["bars"]:
        if not one(cx, "SELECT id FROM instruments WHERE id=?", (b.get("instrument_id"),)) or not set(b) >= {
            "market_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            counts["invalid_records"].append(
                dict(instrument_id=b.get("instrument_id"), reason="IDENTITY_OR_FIELDS_INVALID")
            )
            counts["quarantined"] += 1
            continue
        errors = quality(b, cal)
        try:
            session = cal.session(b["market_date"])
        except (ValueError, TypeError):
            session = None
        if not session:
            counts["invalid_records"].append(dict(instrument_id=b.get("instrument_id"), reason="NON_SESSION"))
            counts["quarantined"] += 1
            continue
        semantic = {
            k: b.get(k)
            for k in ("instrument_id", "market_date", "open", "high", "low", "close", "volume", "is_final")
        }
        if "provider_fields" in b:
            semantic["provider_fields"] = b["provider_fields"]
        if "session" in b:
            semantic["session"] = b["session"]
        h = digest(semantic)
        base = (b["instrument_id"], b["market_date"], bundle["provider"], bundle["feed"])
        old = one(
            cx,
            "SELECT * FROM bar_revisions WHERE instrument_id=? AND market_date=? AND provider=? AND feed=? ORDER BY revision DESC LIMIT 1",
            base,
        )
        if old and old["content_hash"] == h:
            counts["duplicates"] += 1
            continue
        available = b.get("available_at")
        if available:
            try:
                if not instant(session["close_at"]) <= instant(available) <= now():
                    errors.append("INVALID_AVAILABILITY")
                    available = None
            except DomainError:
                errors.append("INVALID_AVAILABILITY")
                available = None
        insert(
            cx,
            "bar_revisions",
            dict(
                id=uid("bar"),
                instrument_id=b["instrument_id"],
                market_date=b["market_date"],
                session=b.get("session", "RTH"),
                bar_start_at=session["open_at"],
                bar_end_at=session["close_at"],
                **{k: str(b[k]) for k in ("open", "high", "low", "close", "volume")},
                provider=bundle["provider"],
                feed=bundle["feed"],
                coverage=bundle["coverage"],
                revision=(old["revision"] + 1) if old else 1,
                available_at=stamp(instant(available)) if available else None,
                available_at_kind="PROVIDER" if available else "UNKNOWN",
                ingested_at=stamp(),
                is_final=b.get("is_final", True) in (True, "true", "True", "1", 1),
                quality_status="QUARANTINED" if errors else "OK",
                content_hash=h,
                raw_ref=rawref,
                quality_json={
                    "errors": errors,
                    **({"provider_fields": b["provider_fields"]} if "provider_fields" in b else {}),
                },
            ),
        )
        counts["inserted"] += 1
        counts["quarantined"] += bool(errors)
    for supplied in bundle.get("settlement_sessions", []):
        s = cal.session(supplied["market_date"])
        require(
            s and supplied.get("source") and supplied.get("verified_at"),
            "VALIDATION_ERROR",
            "结算日须有来源与核验时点",
            422,
        )
        from datetime import date

        settlement = date.fromisoformat(supplied["settlement_date"])
        require(settlement.isoformat() > s["market_date"], "OUT_OF_RANGE", status=422)
        version = cal.version + ":settlement:" + digest(supplied)[:12]
        if not one(
            cx,
            "SELECT market_date FROM market_sessions WHERE calendar_version=? AND market_date=?",
            (version, s["market_date"]),
        ):
            insert(
                cx,
                "market_sessions",
                {
                    **s,
                    "calendar_version": version,
                    "market": "US_EQUITIES",
                    "settlement_date": settlement.isoformat(),
                    "source": supplied["source"],
                    "verified_at": supplied["verified_at"],
                },
            )
    return counts


class AlpacaRTH:
    """Only the data.alpaca.markets historical-data endpoint; no trading API."""

    def __init__(self, feed="sip"):
        self.feed = feed

    def fetch(self, instruments, start, end, cal):
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        require(key and secret, "DEPENDENCY_UNAVAILABLE", "缺少行情凭据", 503)
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        minutes = defaultdict(list)
        token = None
        pages = 0
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            while True:
                params = dict(
                    symbols=",".join(i["current_ticker"] for i in instruments),
                    timeframe="1Min",
                    start=start,
                    end=end,
                    adjustment="raw",
                    feed=self.feed,
                    limit=10000,
                    sort="asc",
                )
                if token:
                    params["page_token"] = token
                response = None
                for attempt in range(3):
                    try:
                        response = client.get(
                            "https://data.alpaca.markets/v2/stocks/bars", params=params, headers=headers
                        )
                        if response.status_code in (401, 403):
                            raise DomainError("SOURCE_PERMISSION_DENIED", status=503)
                        if response.status_code < 500 and response.status_code != 429:
                            break
                    except httpx.RequestError:
                        if attempt == 2:
                            raise DomainError("SOURCE_UNREACHABLE", status=503) from None
                    if attempt < 2:
                        delay = response.headers.get("Retry-After", "1") if response else "1"
                        time.sleep(min(15, float(delay) if delay.isdigit() else 1))
                require(
                    response is not None and response.status_code == 200, "SOURCE_REQUEST_FAILED", status=503
                )
                data = response.json()
                for symbol, items in data.get("bars", {}).items():
                    minutes[symbol].extend(items)
                token = data.get("next_page_token")
                pages += 1
                require(pages <= 200, "SOURCE_BUDGET_EXCEEDED", "历史导入需分段，未完成不发布", 503)
                if not token:
                    break
        bars = []
        mapping = {i["current_ticker"]: i["id"] for i in instruments}
        from src.services.signal_scanner.domain.calendar import NY

        for symbol, items in minutes.items():
            byday = defaultdict(list)
            for m in items:
                at = instant(m["t"])
                day = at.astimezone(NY).date().isoformat()
                s = cal.session(day)
                if s and instant(s["open_at"]) <= at < instant(s["close_at"]):
                    byday[day].append(m)
            for day, ms in sorted(byday.items()):
                ms.sort(key=lambda m: m["t"])
                s = cal.session(day)
                # Missing minutes cannot silently be treated as verified full-session coverage.
                expected = int((instant(s["close_at"]) - instant(s["open_at"])).total_seconds() / 60)
                if len({m["t"] for m in ms}) != expected:
                    continue
                if instant(s["close_at"]) > now():
                    continue
                bars.append(
                    dict(
                        instrument_id=mapping[symbol],
                        market_date=day,
                        open=str(ms[0]["o"]),
                        high=str(max(m["h"] for m in ms)),
                        low=str(min(m["l"] for m in ms)),
                        close=str(ms[-1]["c"]),
                        volume=str(sum(m["v"] for m in ms)),
                        available_at=stamp(),
                        is_final=True,
                    )
                )
        return dict(
            provider="ALPACA",
            feed=self.feed,
            coverage="CONSOLIDATED" if self.feed == "sip" else "SINGLE_VENUE",
            instruments=[
                dict(
                    id=i["id"],
                    ticker=i["current_ticker"],
                    name=i["display_name"],
                    venue=i["primary_venue"],
                    type=i["asset_type"],
                    currency=i["currency"],
                )
                for i in instruments
            ],
            bars=bars,
        )
