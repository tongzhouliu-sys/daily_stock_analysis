# -*- coding: utf-8 -*-
"""Repository for analysis-linked prediction-market evidence (sidecar)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select

from src.storage import AnalysisPredictionMarket, DatabaseManager


class PredictionMarketEvidenceRepository:
    """Persist and read structured prediction-market evidence for an analysis."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def replace_for_history(self, analysis_history_id: int, events: Iterable[Dict[str, Any]]) -> int:
        """Replace all sidecar rows for one analysis history record."""
        if not analysis_history_id:
            return 0
        history_id = int(analysis_history_id)
        rows = [event for event in (events or []) if isinstance(event, dict)]
        with self.db.get_session() as session:
            session.execute(
                delete(AnalysisPredictionMarket).where(
                    AnalysisPredictionMarket.analysis_history_id == history_id
                )
            )
            saved = 0
            for event in rows:
                source = str(event.get("source") or "").strip()
                market_id = str(event.get("market_id") or "").strip()
                question = str(event.get("question") or "").strip()
                if not source or not market_id or not question:
                    continue
                session.add(
                    AnalysisPredictionMarket(
                        analysis_history_id=history_id,
                        source=source[:16],
                        market_id=market_id[:255],
                        question=question,
                        implied_probability=self._safe_float(event.get("implied_probability")),
                        volume_24h=self._safe_float(event.get("volume_24h")),
                        close_time=str(event.get("close_time") or "")[:64] or None,
                        category=str(event.get("category") or "")[:32] or None,
                        symbol=str(event.get("symbol") or "")[:32] or None,
                        market=str(event.get("market") or "")[:16] or None,
                        market_url=str(event.get("market_url") or "") or None,
                    )
                )
                saved += 1
            session.commit()
        return saved

    def list_for_history(self, analysis_history_id: int) -> List[Dict[str, Any]]:
        if not analysis_history_id:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(AnalysisPredictionMarket)
                .where(AnalysisPredictionMarket.analysis_history_id == int(analysis_history_id))
                .order_by(AnalysisPredictionMarket.id.asc())
            ).scalars().all()
        return [self._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row: Any) -> Dict[str, Any]:
        return {
            "source": row.source,
            "market_id": row.market_id,
            "question": row.question,
            "implied_probability": row.implied_probability,
            "volume_24h": row.volume_24h,
            "close_time": row.close_time,
            "category": row.category,
            "symbol": row.symbol,
            "market": row.market,
            "market_url": row.market_url,
        }

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
