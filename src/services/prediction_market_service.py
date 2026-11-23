# -*- coding: utf-8 -*-
"""Prediction-market service: fetch orchestration, local pool query and evidence text.

The service is read-only with respect to venues. It persists normalized events into
the local ``prediction_market_events`` pool and exposes low-sensitivity evidence
text for stock analysis, market review and the agent tool.

All failures are fail-open: missing config, venue errors or timeouts never raise to
the analysis caller.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from src.repositories.prediction_market_repo import PredictionMarketRepository

logger = logging.getLogger(__name__)

_DISCLAIMER_ZH = "（预测市场概率为群体定价，非投资建议，可能受流动性与合规限制影响。）"
_DISCLAIMER_EN = "(Prediction-market odds are crowd pricing, not investment advice; liquidity and regulatory limits apply.)"


class PredictionMarketService:
    """Fetch, persist and query prediction-market events (fail-open)."""

    _auto_fetch_lock = threading.Lock()
    _auto_fetch_in_progress = False
    _auto_fetch_last_run_at: Optional[datetime] = None

    def __init__(
        self,
        repository: Optional[PredictionMarketRepository] = None,
        config: Optional[Config] = None,
    ):
        self.repo = repository or PredictionMarketRepository()
        self.config = config or get_config()

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def reset_auto_fetch_state(cls) -> None:
        with cls._auto_fetch_lock:
            cls._auto_fetch_in_progress = False
            cls._auto_fetch_last_run_at = None

    def is_enabled(self) -> bool:
        return bool(getattr(self.config, "prediction_market_enabled", False))

    # --------------------------------------------------------------- fetch
    def fetch(self, *, dry_run: bool = False) -> Dict[str, Any]:
        """Fetch from all venues and optionally persist. Never raises."""
        if not self.is_enabled():
            return {"ok": False, "skipped": True, "reason": "disabled", "fetched": 0, "saved": 0}
        result: Dict[str, Any] = {"ok": True, "fetched": 0, "saved": 0, "sources": {}, "errors": []}
        try:
            from data_provider.prediction_market_fetcher import PredictionMarketFetcher

            fetcher = PredictionMarketFetcher(config=self.config)
            payload = fetcher.fetch_all(dry_run=dry_run)
            items = payload.get("items") or []
            result["sources"] = payload.get("sources") or {}
            result["errors"] = payload.get("errors") or []
            result["fetched"] = len(items)
            if not dry_run and items:
                result["saved"] = self.repo.upsert_events(items)
                result["retention_deleted"] = self.repo.apply_retention(
                    self.config.prediction_market_retention_days
                )
            return result
        except Exception as exc:  # fail-open
            reason = self._sanitize_error(exc)
            logger.warning("prediction market fetch failed (fail-open): %s", reason)
            result["ok"] = False
            result["error"] = reason
            return result

    def refresh_auto(self, *, force: bool = False) -> Dict[str, Any]:
        """Cooldown-guarded auto refresh used before reading the local pool."""
        if not self.is_enabled() or not getattr(self.config, "prediction_market_auto_fetch_enabled", False):
            return {"ok": True, "skipped": True, "reason": "disabled"}

        cls = type(self)
        cooldown_seconds = max(0, int(getattr(self.config, "prediction_market_refresh_cooldown_min", 60))) * 60
        with cls._auto_fetch_lock:
            if cls._auto_fetch_in_progress:
                return {"ok": True, "skipped": True, "reason": "in_progress"}
            if (
                not force
                and cls._auto_fetch_last_run_at is not None
                and (datetime.now() - cls._auto_fetch_last_run_at).total_seconds() < cooldown_seconds
            ):
                return {"ok": True, "skipped": True, "reason": "cooldown"}
            cls._auto_fetch_in_progress = True
        try:
            return self.fetch()
        finally:
            with cls._auto_fetch_lock:
                cls._auto_fetch_last_run_at = datetime.now()
                cls._auto_fetch_in_progress = False

    # --------------------------------------------------------------- query
    def query_events(
        self,
        *,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = "open",
        query: Optional[str] = None,
        limit: int = 20,
        days: Optional[int] = None,
        filter_noise: bool = False,
    ) -> List[Dict[str, Any]]:
        if not self.is_enabled():
            return []
        rows = self.repo.list_events(
            symbol=symbol,
            market=market,
            category=category,
            status=status,
            query=query,
            days=days,
            limit=limit,
        )
        items = [self._event_to_dict(row) for row in rows]
        if filter_noise:
            items = [item for item in items if not self._is_noise(item)]
        return items

    def _is_noise(self, item: Dict[str, Any]) -> bool:
        """Filter extreme-probability and low-volume events for evidence/tool output."""
        if not getattr(self.config, "prediction_market_filter_noise", True):
            return False
        probability = item.get("implied_probability")
        if probability is None or probability <= 0.0 or probability >= 1.0:
            return True
        min_volume = float(getattr(self.config, "prediction_market_min_volume_24h", 0.0) or 0.0)
        if min_volume > 0:
            volume = item.get("volume_24h")
            if volume is None or float(volume) < min_volume:
                return True
        return False

    def collect_events(
        self,
        *,
        symbol: Optional[str] = None,
        stock_name: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        """Collect deduplicated, noise-filtered events for stock evidence.

        Priority: symbol-specific → ``equity`` for the stock's market → ``global``
        macro. Crypto/commodity are excluded from single-stock context.
        """
        if not self.is_enabled():
            return []
        self.refresh_auto()
        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def _collect(**filters: Any) -> None:
            for item in self.query_events(status="open", limit=limit, filter_noise=True, **filters):
                key = f"{item.get('source')}:{item.get('market_id')}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                if len(collected) >= limit:
                    return

        if symbol:
            _collect(symbol=symbol)
        if len(collected) < limit and market:
            _collect(market=market, category="equity")
        if len(collected) < limit:
            _collect(market="global", category="macro")
        return collected

    # -------------------------------------------------------------- context
    def collect_evidence(
        self,
        *,
        symbol: Optional[str],
        stock_name: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 6,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Return ``(text_context, structured_events)`` for stock analysis."""
        events = self.collect_events(
            symbol=symbol, stock_name=stock_name, market=market, limit=limit
        )
        text = self._format_context(events, subject=stock_name or symbol)
        return text, events

    def build_evidence_context(
        self,
        *,
        symbol: Optional[str],
        stock_name: Optional[str] = None,
        market: Optional[str] = None,
        limit: int = 6,
    ) -> Optional[str]:
        """Return text evidence for stock analysis, or ``None`` when unavailable."""
        if not self.is_enabled():
            return None
        text, _events = self.collect_evidence(
            symbol=symbol, stock_name=stock_name, market=market, limit=limit
        )
        return text

    def build_market_context(self, *, market: Optional[str], limit: int = 6) -> Optional[str]:
        """Return macro / crypto / commodity / index evidence for market review."""
        if not self.is_enabled():
            return None
        self.refresh_auto()
        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()
        queries: List[Dict[str, Any]] = [
            {"market": "global", "category": "macro"},
            {"market": "global", "category": "crypto"},
            {"market": "global", "category": "commodity"},
        ]
        if market:
            queries.append({"market": market, "category": "equity"})
        for filters in queries:
            for item in self.query_events(status="open", limit=limit, filter_noise=True, **filters):
                key = f"{item.get('source')}:{item.get('market_id')}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                if len(collected) >= limit:
                    break
            if len(collected) >= limit:
                break
        return self._format_context(collected, subject=None)

    # ------------------------------------------------------------- overview
    def build_overview(
        self, *, market: Optional[str] = None, limit: int = 200, refresh: bool = True
    ) -> Dict[str, Any]:
        """Aggregate the local pool into a market-wide overview."""
        empty_attitude = {
            "label": "样本不足",
            "tilt": None,
            "risk_on": {"count": 0, "avg_probability": None},
            "risk_off": {"count": 0, "avg_probability": None},
        }
        base: Dict[str, Any] = {
            "enabled": self.is_enabled(),
            "total_contracts": 0,
            "by_category": {},
            "by_source": {},
            "top_events": [],
            "attention_concentration": 0.0,
            "attitude": empty_attitude,
            "generated_at": datetime.now().isoformat(),
        }
        if not self.is_enabled():
            return base
        if refresh:
            self.refresh_auto()
        items = self.query_events(status="open", market=market, limit=limit, filter_noise=True)
        base["total_contracts"] = len(items)
        if not items:
            return base

        by_category: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        for item in items:
            category = str(item.get("category") or "other")
            source = str(item.get("source") or "unknown")
            by_category[category] = by_category.get(category, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1
        base["by_category"] = dict(sorted(by_category.items(), key=lambda kv: kv[1], reverse=True))
        base["by_source"] = dict(sorted(by_source.items(), key=lambda kv: kv[1], reverse=True))

        ranked = sorted(items, key=lambda item: float(item.get("volume_24h") or 0.0), reverse=True)
        top_events = ranked[:5]
        base["top_events"] = [
            {
                "source": item.get("source"),
                "market_id": item.get("market_id"),
                "question": item.get("question"),
                "implied_probability": item.get("implied_probability"),
                "volume_24h": item.get("volume_24h"),
                "category": item.get("category"),
                "market_url": item.get("market_url"),
            }
            for item in top_events
        ]
        total_volume = sum(float(item.get("volume_24h") or 0.0) for item in items)
        top_volume = sum(float(item.get("volume_24h") or 0.0) for item in top_events)
        base["attention_concentration"] = round(top_volume / total_volume, 4) if total_volume > 0 else 0.0
        base["attitude"] = self._build_attitude(items)
        return base

    def build_overview_context(self, *, market: Optional[str] = None, limit: int = 200) -> Optional[str]:
        """Render the overview as a Markdown prompt block, or ``None`` when empty."""
        overview = self.build_overview(market=market, limit=limit)
        if not overview.get("enabled") or overview.get("total_contracts", 0) == 0:
            return None
        categories = " / ".join(
            f"{name} {count}" for name, count in (overview.get("by_category") or {}).items()
        )
        sources = " / ".join(
            f"{self._source_label(name)} {count}"
            for name, count in (overview.get("by_source") or {}).items()
        )
        lines = ["## 预测市场全景"]
        lines.append(f"- 活跃合约：{overview['total_contracts']} 个（{categories}；{sources}）")
        top_events = overview.get("top_events") or []
        if top_events:
            focus = "；".join(
                f"{index}) {event.get('question')}（{self._format_probability(event.get('implied_probability'))}）"
                for index, event in enumerate(top_events[:3], 1)
            )
            lines.append(f"- 关注焦点：{focus}")
        lines.append(
            f"- 关注集中度：前 5 个合约占 24h 成交额 {round(float(overview.get('attention_concentration') or 0.0) * 100)}%"
        )
        attitude = overview.get("attitude") or {}
        risk_on = attitude.get("risk_on") or {}
        risk_off = attitude.get("risk_off") or {}
        lines.append(
            "- 风险定价倾向："
            f"{attitude.get('label')}；"
            f"risk-on 平均 {self._format_probability(risk_on.get('avg_probability'))}"
            f"（{risk_on.get('count', 0)} 个） vs "
            f"risk-off 平均 {self._format_probability(risk_off.get('avg_probability'))}"
            f"（{risk_off.get('count', 0)} 个）"
        )
        lines.append("- 说明：基于本地预测市场事件池的启发式统计，非投资建议。")
        return "\n".join(lines)

    def _build_attitude(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        risk_on: List[float] = []
        risk_off: List[float] = []
        for item in items:
            probability = item.get("implied_probability")
            if not isinstance(probability, (int, float)):
                continue
            stance = self._classify_stance(str(item.get("question") or ""))
            if stance == "risk_on":
                risk_on.append(float(probability))
            elif stance == "risk_off":
                risk_off.append(float(probability))
        avg_on = round(sum(risk_on) / len(risk_on), 4) if risk_on else None
        avg_off = round(sum(risk_off) / len(risk_off), 4) if risk_off else None
        if len(risk_on) < 3 or len(risk_off) < 3:
            label = "样本不足"
            tilt = None
        else:
            tilt = round(avg_on - avg_off, 4)
            if tilt >= 0.15:
                label = "偏 risk-on"
            elif tilt <= -0.15:
                label = "偏 risk-off"
            else:
                label = "中性/分歧"
        return {
            "label": label,
            "tilt": tilt,
            "risk_on": {"count": len(risk_on), "avg_probability": avg_on},
            "risk_off": {"count": len(risk_off), "avg_probability": avg_off},
        }

    @staticmethod
    def _classify_stance(question: str) -> str:
        text = (question or "").lower()
        risk_off_phrases = (
            "recession", "rate hike", "hike rates", "hikes rates", "raise rates",
            "cpi above", "inflation above", "unemployment above", "hard landing",
            "stagflation", "default", "crash", "downturn",
        )
        risk_on_phrases = (
            "rate cut", "cuts rates", "cut rates", "cut interest rates",
            "soft landing", "all-time high", "record high", "beat earnings",
            "beats earnings", "exceed expectations", "upside",
        )
        for phrase in risk_off_phrases:
            if PredictionMarketService._phrase_hit(text, phrase):
                return "risk_off"
        for phrase in risk_on_phrases:
            if PredictionMarketService._phrase_hit(text, phrase):
                return "risk_on"
        return "neutral"

    @staticmethod
    def _phrase_hit(text: str, phrase: str) -> bool:
        if " " in phrase:
            return phrase in text
        return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None

    @staticmethod
    def _format_probability(value: Any) -> str:
        if isinstance(value, (int, float)):
            return f"{round(float(value) * 100)}%"
        return "N/A"

    @staticmethod
    def _source_label(source: str) -> str:
        return {"kalshi": "Kalshi", "polymarket": "Polymarket"}.get(str(source).lower(), str(source))

    def _format_context(self, items: List[Dict[str, Any]], *, subject: Optional[str]) -> Optional[str]:
        if not items:
            return None
        header = f"## 预测市场现状（{subject}）" if subject else "## 预测市场现状（宏观事件）"
        lines = [header]
        for idx, item in enumerate(items, 1):
            probability = item.get("implied_probability")
            probability_text = f"{round(probability * 100)}%" if isinstance(probability, (int, float)) else "N/A"
            volume = item.get("volume_24h")
            volume_text = f"24h成交额 {volume:,.0f}" if isinstance(volume, (int, float)) else "24h成交额 N/A"
            close_time = str(item.get("close_time") or "").strip()
            meta = " / ".join(part for part in (volume_text, close_time) if part)
            lines.append(f"{idx}. {item.get('question')} —— 隐含概率 {probability_text}" + (f"（{meta}）" if meta else ""))
            url = str(item.get("market_url") or "").strip()
            if url:
                lines.append(f"   来源：{url}")
        lines.append(_DISCLAIMER_ZH)
        return "\n".join(lines)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _event_to_dict(row: Any) -> Dict[str, Any]:
        return {
            "id": row.id,
            "source": row.source,
            "market_id": row.market_id,
            "event_id": row.event_id,
            "question": row.question,
            "outcome": row.outcome,
            "implied_probability": row.implied_probability,
            "yes_bid": row.yes_bid,
            "yes_ask": row.yes_ask,
            "last_price": row.last_price,
            "volume_24h": row.volume_24h,
            "open_interest": row.open_interest,
            "liquidity_usd": row.liquidity_usd,
            "status": row.status,
            "category": row.category,
            "tags": row.tags,
            "open_time": row.open_time,
            "close_time": row.close_time,
            "resolution": row.resolution,
            "symbol": row.symbol,
            "market": row.market,
            "market_url": row.market_url,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        text = str(exc) or exc.__class__.__name__
        return text[:300]
