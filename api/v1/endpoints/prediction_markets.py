# -*- coding: utf-8 -*-
"""Prediction-market API endpoints (read-only event pool)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.prediction_markets import (
    PredictionMarketEventItem,
    PredictionMarketEventListResponse,
    PredictionMarketFetchRequest,
    PredictionMarketFetchResponse,
    PredictionMarketProviderItem,
    PredictionMarketProviderListResponse,
)
from src.config import get_config
from src.services.prediction_market_service import PredictionMarketService
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized_error = sanitize_diagnostic_text(str(exc), max_length=300) or "internal prediction market error"
    logger.error("%s: %s", message, sanitized_error)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal prediction market service error"},
    )


@router.get(
    "/events",
    response_model=PredictionMarketEventListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List persisted prediction-market events",
)
def list_events(
    symbol: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query("open"),
    query: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PredictionMarketEventListResponse:
    try:
        items = PredictionMarketService().query_events(
            symbol=symbol,
            market=market,
            category=category,
            status=status,
            query=query,
            days=days,
            limit=limit,
        )
        return PredictionMarketEventListResponse(
            items=[PredictionMarketEventItem(**item) for item in items],
            total=len(items),
        )
    except Exception as exc:
        raise _internal_error("List prediction market events failed", exc)


@router.post(
    "/fetch",
    response_model=PredictionMarketFetchResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Fetch prediction-market events with fail-open semantics",
)
def fetch_events(request: PredictionMarketFetchRequest = PredictionMarketFetchRequest()) -> PredictionMarketFetchResponse:
    try:
        return PredictionMarketFetchResponse(**PredictionMarketService().fetch(dry_run=request.dry_run))
    except Exception as exc:
        raise _internal_error("Fetch prediction market events failed", exc)


@router.get(
    "/providers",
    response_model=PredictionMarketProviderListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List configured prediction-market providers",
)
def list_providers() -> PredictionMarketProviderListResponse:
    try:
        config = get_config()
        enabled = bool(getattr(config, "prediction_market_enabled", False))
        providers = [
            PredictionMarketProviderItem(
                name="kalshi",
                enabled=enabled,
                available=bool((getattr(config, "kalshi_api_base_url", "") or "").strip()),
            ),
            PredictionMarketProviderItem(
                name="polymarket",
                enabled=enabled,
                available=bool((getattr(config, "polymarket_api_base_url", "") or "").strip()),
            ),
        ]
        return PredictionMarketProviderListResponse(providers=providers)
    except Exception as exc:
        raise _internal_error("List prediction market providers failed", exc)
