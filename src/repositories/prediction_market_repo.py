# -*- coding: utf-8 -*-
"""Repository helpers for persisted prediction-market events."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, desc, func, select

from src.storage import DatabaseManager, PredictionMarketEvent, utc_naive_now

# Fields refreshed when an event already exists. Identity / create-time fields are
# intentionally excluded so a re-fetch never rewrites provenance.
_MUTABLE_FIELDS = (
    "event_id",
    "question",
    "outcome",
    "implied_probability",
    "yes_bid",
    "yes_ask",
    "last_price",
    "volume_24h",
    "open_interest",
    "liquidity_usd",
    "status",
    "category",
    "tags",
    "open_time",
    "close_time",
    "resolution",
    "symbol",
    "market",
    "market_url",
    "updated_at",
)


class PredictionMarketRepository:
    """DB access layer for normalized prediction-market events."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_events(self, items: Iterable[Dict[str, Any]], *, now=None) -> int:
        """Insert or refresh events keyed by ``(source, market_id)``.

        Returns the number of inserted rows (refreshes are not counted as inserts).
        """
        saved = 0
        fetched_at = now or utc_naive_now()
        with self.db.get_session() as session:
            for fields in items:
                if not isinstance(fields, dict):
                    continue
                source = str(fields.get("source") or "").strip()
                market_id = str(fields.get("market_id") or "").strip()
                question = str(fields.get("question") or "").strip()
                if not source or not market_id or not question:
                    continue
                existing = session.execute(
                    select(PredictionMarketEvent)
                    .where(
                        and_(
                            PredictionMarketEvent.source == source,
                            PredictionMarketEvent.market_id == market_id,
                        )
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    for key in _MUTABLE_FIELDS:
                        if key in fields and fields[key] is not None:
                            setattr(existing, key, fields[key])
                    existing.fetched_at = fetched_at
                    continue
                row_fields = dict(fields)
                row_fields["source"] = source
                row_fields["market_id"] = market_id
                row_fields["question"] = question
                row_fields.setdefault("status", "open")
                row_fields.setdefault("category", "other")
                row_fields["fetched_at"] = fetched_at
                row_fields.setdefault("updated_at", fetched_at)
                session.add(PredictionMarketEvent(**row_fields))
                saved += 1
            session.commit()
        return saved

    def list_events(
        self,
        *,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        query: Optional[str] = None,
        days: Optional[int] = None,
        limit: int = 20,
    ) -> List[PredictionMarketEvent]:
        conditions = []
        if symbol:
            conditions.append(func.lower(PredictionMarketEvent.symbol) == str(symbol).strip().lower())
        if market:
            conditions.append(PredictionMarketEvent.market == market)
        if category:
            conditions.append(PredictionMarketEvent.category == category)
        if status:
            conditions.append(PredictionMarketEvent.status == status)
        if query:
            pattern = f"%{str(query).strip()}%"
            conditions.append(PredictionMarketEvent.question.like(pattern))
        if days is not None:
            cutoff = utc_naive_now() - timedelta(days=max(1, int(days)))
            conditions.append(PredictionMarketEvent.fetched_at >= cutoff)
        where_clause = and_(*conditions) if conditions else True
        safe_limit = max(1, min(int(limit), 100))
        with self.db.get_session() as session:
            rows = session.execute(
                select(PredictionMarketEvent)
                .where(where_clause)
                .order_by(
                    desc(func.coalesce(PredictionMarketEvent.volume_24h, 0.0)),
                    desc(PredictionMarketEvent.id),
                )
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def apply_retention(self, retention_days: int) -> int:
        cutoff = utc_naive_now() - timedelta(days=max(1, int(retention_days)))
        with self.db.get_session() as session:
            result = session.execute(
                delete(PredictionMarketEvent).where(PredictionMarketEvent.fetched_at < cutoff)
            )
            session.commit()
            return int(result.rowcount or 0)
