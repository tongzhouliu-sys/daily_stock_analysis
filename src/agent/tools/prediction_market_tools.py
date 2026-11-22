# -*- coding: utf-8 -*-
"""Prediction-market tools for the stock analysis agent.

Exposes a single read-only tool that lets the agent search local prediction-market
event probabilities (Kalshi / Polymarket). The tool never trades, never writes and
never exposes venue credentials.
"""

import logging

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

logger = logging.getLogger(__name__)

_PREDICTION_MARKET_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read"],
    permissions=["prediction_market:read"],
)


def _handle_search_prediction_markets(
    query: str = None,
    symbol: str = None,
    market: str = None,
    category: str = None,
    status: str = "open",
    limit: int = 5,
) -> dict:
    """Search normalized prediction-market events from the local pool."""
    try:
        from src.services.prediction_market_service import PredictionMarketService

        service = PredictionMarketService()
        if not service.is_enabled():
            return {
                "status": "disabled",
                "items": [],
                "reason": "prediction market is not enabled",
            }
        service.refresh_auto()
        items = service.query_events(
            symbol=symbol,
            market=market,
            category=category,
            status=status,
            query=query,
            limit=limit,
            filter_noise=True,
        )
        return {
            "status": "ok",
            "count": len(items),
            "items": [
                {
                    "source": item.get("source"),
                    "question": item.get("question"),
                    "implied_probability": item.get("implied_probability"),
                    "volume_24h": item.get("volume_24h"),
                    "close_time": item.get("close_time"),
                    "category": item.get("category"),
                    "symbol": item.get("symbol"),
                    "market_url": item.get("market_url"),
                }
                for item in items
            ],
            "disclaimer": "Prediction-market odds are crowd pricing, not investment advice.",
        }
    except Exception as exc:  # fail-open, never expose credentials/stack
        logger.warning("search_prediction_markets failed (fail-open): %s", exc.__class__.__name__)
        return {
            "status": "unavailable",
            "items": [],
            "reason": "prediction market service unavailable",
        }


search_prediction_markets_tool = ToolDefinition(
    name="search_prediction_markets",
    description="Search prediction-market event probabilities (Kalshi/Polymarket) for macro, "
                "equity, commodity or crypto events, e.g. Fed rate decisions, earnings beats, "
                "or price thresholds. Returns implied probability, 24h volume, close time and "
                "source URL. Read-only; odds are crowd pricing, not investment advice.",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Event keyword, e.g. 'fed rate', 'nvda earnings' (optional)",
            required=False,
        ),
        ToolParameter(
            name="symbol",
            type="string",
            description="Optional stock code, e.g. 'NVDA'",
            required=False,
        ),
        ToolParameter(
            name="market",
            type="string",
            description="Optional market scope: 'us', 'cn', 'hk' or 'global'; omit to search all",
            required=False,
            enum=["cn", "hk", "us", "global"],
        ),
        ToolParameter(
            name="category",
            type="string",
            description="Event category: macro / equity / commodity / crypto",
            required=False,
            enum=["macro", "equity", "commodity", "crypto"],
        ),
        ToolParameter(
            name="status",
            type="string",
            description="Contract status filter (default: open)",
            required=False,
            default="open",
            enum=["open", "closed", "settled"],
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Maximum number of events to return (default: 5)",
            required=False,
            default=5,
        ),
    ],
    handler=_handle_search_prediction_markets,
    category="market",
    policy=_PREDICTION_MARKET_READ_POLICY,
)


ALL_PREDICTION_MARKET_TOOLS = [
    search_prediction_markets_tool,
]
