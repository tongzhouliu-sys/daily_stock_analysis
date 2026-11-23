# -*- coding: utf-8 -*-
"""Prediction-market API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PredictionMarketEventItem(BaseModel):
    id: int
    source: str
    market_id: str
    event_id: Optional[str] = None
    question: str
    outcome: Optional[str] = None
    implied_probability: Optional[float] = None
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    last_price: Optional[float] = None
    volume_24h: Optional[float] = None
    open_interest: Optional[float] = None
    liquidity_usd: Optional[float] = None
    status: str
    category: str
    tags: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    resolution: Optional[str] = None
    symbol: Optional[str] = None
    market: Optional[str] = None
    market_url: Optional[str] = None
    fetched_at: Optional[str] = None
    updated_at: Optional[str] = None


class PredictionMarketEventListResponse(BaseModel):
    items: List[PredictionMarketEventItem] = Field(default_factory=list)
    total: int


class PredictionMarketFetchRequest(BaseModel):
    dry_run: bool = False


class PredictionMarketFetchResponse(BaseModel):
    ok: bool
    fetched: int = 0
    saved: int = 0
    retention_deleted: Optional[int] = None
    skipped: Optional[bool] = None
    reason: Optional[str] = None
    sources: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, str]] = Field(default_factory=list)
    error: Optional[str] = None


class PredictionMarketProviderItem(BaseModel):
    name: str
    enabled: bool
    available: bool
    last_error: Optional[str] = None


class PredictionMarketProviderListResponse(BaseModel):
    providers: List[PredictionMarketProviderItem] = Field(default_factory=list)


class PredictionMarketOverviewResponse(BaseModel):
    enabled: bool
    total_contracts: int = 0
    by_category: Dict[str, int] = Field(default_factory=dict)
    by_source: Dict[str, int] = Field(default_factory=dict)
    top_events: List[Dict[str, Any]] = Field(default_factory=list)
    attention_concentration: float = 0.0
    attitude: Dict[str, Any] = Field(default_factory=dict)
    generated_at: Optional[str] = None
