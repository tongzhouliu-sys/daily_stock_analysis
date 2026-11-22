# -*- coding: utf-8 -*-
"""Read-only prediction-market fetchers (Kalshi / Polymarket).

Normalizes external event contracts into the DSA field contract consumed by
``PredictionMarketService``. This module never trades, never writes to a venue and
only performs HTTP GETs against configured https endpoints.

Design goals:
- fail-open: one venue failing never blocks the other;
- tolerant parsing: venues change field names / price units over time;
- no credential leakage into logs or normalized payloads.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from src.config import Config, get_config

logger = logging.getLogger(__name__)

_ALLOWED_CATEGORIES = {"macro", "equity", "commodity", "crypto"}
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DISABLE_PROXIES = {"http": None, "https": None}
_TICKER_IN_PARENS_RE = re.compile(r"\(([A-Z]{1,5})\)")
_YES_LABELS = {"yes", "y", "true"}

# Best-effort platform category -> DSA category mapping.
_CATEGORY_KEYWORDS = {
    "macro": ("fed", "rate", "cpi", "inflation", "gdp", "unemployment", "jobs",
              "recession", "tariff", "treasury", "yield", "economy", "economic"),
    "commodity": ("oil", "brent", "wti", "gold", "gas", "crude", "copper", "silver"),
    "crypto": ("btc", "bitcoin", "eth", "ethereum", "crypto", "solana", "token"),
    "equity": ("earnings", "stock", "shares", "market cap", "revenue", "guidance",
               "ipo", "merger", "acquisition", "beat", "miss", "close above",
               "nvidia", "apple", "tesla", "microsoft", "amazon", "meta", "google"),
}


def _normalize_probability(value: Any) -> Optional[float]:
    """Return a probability in [0, 1] or ``None`` for invalid input."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    # Venues may quote in cents (0-100) or dollars (0-1).
    if number > 1.0:
        if number <= 100.0:
            number = number / 100.0
        else:
            return None
    if number < 0.0 or number > 1.0:
        return None
    return round(number, 6)


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _safe_text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _infer_category(question: str, raw_category: Any = None) -> str:
    text = f"{_safe_text(raw_category, 80)} {question}".lower()
    if "politic" in text or "election" in text or "president" in text or "senate" in text:
        return "politics"
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


def _infer_symbol(question: str, raw: Dict[str, Any]) -> Optional[str]:
    for key in ("symbol", "ticker", "asset", "stock", "underlying"):
        candidate = _safe_text(raw.get(key), 16).upper()
        if candidate and candidate.isalpha() and len(candidate) <= 5:
            return candidate
    match = _TICKER_IN_PARENS_RE.search(question)
    if match:
        return match.group(1)
    return None


def _infer_market(category: str, raw: Dict[str, Any]) -> str:
    explicit = _safe_text(raw.get("market"), 8).lower()
    if explicit in {"cn", "hk", "us", "global"}:
        return explicit
    return "global" if category == "macro" else "us"


class PredictionMarketFetcher:
    """Fetch and normalize prediction-market events from supported venues."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()

    # ------------------------------------------------------------------ HTTP
    def _get_json(self, url: str, *, headers: Optional[Dict[str, str]] = None) -> Any:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("prediction market endpoint must be an absolute https URL")
        if parsed.username or parsed.password:
            raise ValueError("prediction market endpoint must not contain credentials")
        timeout = max(1.0, min(float(self.config.prediction_market_fetch_timeout_sec), 30.0))
        request_headers = {"Accept": "application/json", "User-Agent": "daily-stock-analysis-pm/1.0"}
        request_headers.update(headers or {})
        response = requests.get(
            url,
            timeout=timeout,
            headers=request_headers,
            proxies=_DISABLE_PROXIES,
        )
        try:
            response.raise_for_status()
            content = response.content[:_MAX_RESPONSE_BYTES]
            return json.loads(content.decode("utf-8"))
        finally:
            response.close()

    # --------------------------------------------------------------- Kalshi
    def fetch_kalshi(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        base = (self.config.kalshi_api_base_url or "").strip().rstrip("/")
        if not base:
            return []
        max_items = max(1, int(limit or self.config.prediction_market_max_items_per_fetch))
        headers: Dict[str, str] = {}
        if self.config.kalshi_api_key:
            headers["Authorization"] = f"Bearer {self.config.kalshi_api_key}"
        payload = self._get_json(
            f"{base}/markets?status=open&limit={min(max_items, 200)}",
            headers=headers,
        )
        markets = payload.get("markets") if isinstance(payload, dict) else None
        if not isinstance(markets, list):
            return []
        return self._normalize_kalshi_markets(markets, max_items=max_items)

    def _normalize_kalshi_markets(self, markets: List[Any], *, max_items: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for raw in markets:
            if not isinstance(raw, dict):
                continue
            if len(items) >= max_items:
                break
            market_id = _safe_text(raw.get("ticker") or raw.get("market_id"), 255)
            question = _safe_text(raw.get("title") or raw.get("question") or raw.get("subtitle"), 500)
            if not market_id or not question:
                continue
            category = _infer_category(question, raw.get("category"))
            if category == "politics" and not self.config.prediction_market_politics_allowed:
                continue
            yes_bid = _normalize_probability(raw.get("yes_bid"))
            yes_ask = _normalize_probability(raw.get("yes_ask"))
            last_price = _normalize_probability(
                _first_not_none(raw.get("last_price"), raw.get("last_trade_price"))
            )
            implied = last_price
            if implied is None and yes_bid is not None and yes_ask is not None:
                implied = round((yes_bid + yes_ask) / 2.0, 6)
            if implied is None:
                implied = yes_ask if yes_ask is not None else yes_bid
            if implied is None:
                continue
            items.append(
                {
                    "source": "kalshi",
                    "market_id": market_id,
                    "event_id": _safe_text(raw.get("event_ticker"), 255) or None,
                    "question": question,
                    "outcome": "yes",
                    "implied_probability": implied,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "last_price": last_price,
                    "volume_24h": _safe_float(raw.get("volume_24h")),
                    "open_interest": _safe_float(raw.get("open_interest")),
                    "liquidity_usd": _safe_float(raw.get("liquidity")),
                    "status": self._normalize_status(raw.get("status")),
                    "category": category if (category in _ALLOWED_CATEGORIES or category == "politics") else "other",
                    "tags": json.dumps([category], ensure_ascii=False),
                    "open_time": _safe_text(raw.get("open_time"), 64) or None,
                    "close_time": _safe_text(raw.get("close_time"), 64) or None,
                    "resolution": _safe_text(raw.get("result"), 255) or None,
                    "symbol": _infer_symbol(question, raw),
                    "market": _infer_market(category, raw),
                    "market_url": f"https://kalshi.com/markets/{market_id}",
                }
            )
        return items

    # ------------------------------------------------------------ Polymarket
    def fetch_polymarket(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        base = (self.config.polymarket_api_base_url or "").strip().rstrip("/")
        if not base:
            return []
        max_items = max(1, int(limit or self.config.prediction_market_max_items_per_fetch))
        headers: Dict[str, str] = {}
        if self.config.polymarket_api_key:
            headers["Authorization"] = f"Bearer {self.config.polymarket_api_key}"
        payload = self._get_json(
            f"{base}/markets?active=true&closed=false&limit={min(max_items, 200)}"
            "&order=volume24hr&ascending=false",
            headers=headers,
        )
        markets = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(markets, list):
            return []
        return self._normalize_polymarket_markets(markets, max_items=max_items)

    def _normalize_polymarket_markets(self, markets: List[Any], *, max_items: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for raw in markets:
            if not isinstance(raw, dict):
                continue
            if len(items) >= max_items:
                break
            if raw.get("closed") is True:
                continue
            market_id = _safe_text(raw.get("id") or raw.get("conditionId") or raw.get("slug"), 255)
            question = _safe_text(raw.get("question") or raw.get("title"), 500)
            if not market_id or not question:
                continue
            category = _infer_category(question, raw.get("category") or raw.get("groupItemTitle"))
            if category == "politics" and not self.config.prediction_market_politics_allowed:
                continue
            outcomes = _parse_json_list(raw.get("outcomes"))
            prices = _parse_json_list(raw.get("outcomePrices") or raw.get("prices"))
            implied = self._polymarket_yes_probability(outcomes, prices)
            if implied is None:
                implied = _normalize_probability(
                    _first_not_none(raw.get("lastTradePrice"), raw.get("last_price"))
                )
            if implied is None:
                continue
            items.append(
                {
                    "source": "polymarket",
                    "market_id": market_id,
                    "event_id": _safe_text(raw.get("slug"), 255) or None,
                    "question": question,
                    "outcome": "yes",
                    "implied_probability": implied,
                    "yes_bid": _normalize_probability(raw.get("bestBid")),
                    "yes_ask": _normalize_probability(raw.get("bestAsk")),
                    "last_price": _normalize_probability(
                        _first_not_none(raw.get("lastTradePrice"), raw.get("last_price"))
                    ),
                    "volume_24h": _safe_float(raw.get("volume24hr") or raw.get("volume_24hr")),
                    "open_interest": _safe_float(raw.get("openInterest")),
                    "liquidity_usd": _safe_float(raw.get("liquidity") or raw.get("liquidityNum")),
                    "status": "open" if raw.get("active", True) else "closed",
                    "category": category if (category in _ALLOWED_CATEGORIES or category == "politics") else "other",
                    "tags": json.dumps([category], ensure_ascii=False),
                    "open_time": _safe_text(raw.get("startDate"), 64) or None,
                    "close_time": _safe_text(raw.get("endDate") or raw.get("end_date"), 64) or None,
                    "resolution": _safe_text(raw.get("resolution"), 255) or None,
                    "symbol": _infer_symbol(question, raw),
                    "market": _infer_market(category, raw),
                    "market_url": f"https://polymarket.com/event/{_safe_text(raw.get('slug'), 200)}"
                    if raw.get("slug")
                    else f"https://polymarket.com/market/{market_id}",
                }
            )
        return items

    @staticmethod
    def _polymarket_yes_probability(outcomes: List[Any], prices: List[Any]) -> Optional[float]:
        if not outcomes or not prices:
            return None
        for index, label in enumerate(outcomes):
            if _safe_text(label, 16).lower() in _YES_LABELS and index < len(prices):
                return _normalize_probability(prices[index])
        # Binary markets without an explicit "Yes" label default to the first outcome.
        if len(prices) >= 1 and len(outcomes) == 2:
            return _normalize_probability(prices[0])
        return None

    @staticmethod
    def _normalize_status(value: Any) -> str:
        text = _safe_text(value, 16).lower()
        if text in {"open", "active", "trading"}:
            return "open"
        if text in {"settled", "resolved", "finalized"}:
            return "settled"
        if text in {"closed", "inactive", "expired"}:
            return "closed"
        return "open"

    # ----------------------------------------------------------------- all
    def fetch_all(self, *, dry_run: bool = False) -> Dict[str, Any]:
        """Fail-open fetch across venues.

        ``dry_run`` is accepted for API symmetry; the fetcher never persists, so
        both modes return the same normalized list.
        """
        items: List[Dict[str, Any]] = []
        sources: Dict[str, Any] = {}
        errors: List[Dict[str, str]] = []
        for name, fetcher in (("kalshi", self.fetch_kalshi), ("polymarket", self.fetch_polymarket)):
            try:
                fetched = fetcher()
                sources[name] = {"ok": True, "count": len(fetched)}
                items.extend(fetched)
            except Exception as exc:  # fail-open per venue
                reason = self._sanitize_error(exc)
                sources[name] = {"ok": False, "count": 0, "error": reason}
                errors.append({"source": name, "error": reason})
                logger.warning("prediction market fetch failed source=%s: %s", name, reason)
        return {"items": items, "sources": sources, "errors": errors}

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        text = str(exc) or exc.__class__.__name__
        text = re.sub(r"(?i)(api[_-]?key|token|secret|authorization)=\S+", r"\1=[redacted]", text)
        return text[:300]
