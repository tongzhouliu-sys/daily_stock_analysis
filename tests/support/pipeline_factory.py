"""Build a ``StockAnalysisPipeline`` shell for unit tests.

``StockAnalysisPipeline.__init__`` wires a database, data fetchers, the LLM
analyzer, notification senders and several optional services. Unit tests only
exercise one method at a time, so constructing the real object would touch the
network and the local database.

Historically every test file called ``StockAnalysisPipeline.__new__`` itself and
then assigned whatever attributes it happened to need. That spread the implicit
"which attributes must exist" contract across 17 files and forced defensive
``getattr(self, ...)`` defaults into production code.

Use :func:`make_pipeline` instead. It provides a deterministic baseline for the
attributes ``__init__`` normally sets, and accepts keyword overrides for the
collaborators a test cares about. When production code gains a new attribute
that a test path reads, add a default here — not a new ``getattr(self, ...)`` in
``src/core/pipeline.py``.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

from src.core.pipeline import StockAnalysisPipeline

_DEFAULT_ATTRIBUTES: dict[str, Any] = {
    "config": None,  # replaced with a MagicMock below
    "max_workers": 1,
    "source_message": None,
    "query_id": None,
    "trace_id": None,
    "query_source": "system",
    "save_context_snapshot": False,
    "progress_callback": None,
    "analysis_skills": None,
    "analysis_phase": "auto",
    "portfolio_context": None,
    "daily_market_context_enabled": False,
    "daily_market_context_allow_generate": True,
}

_COLLABORATORS: tuple[str, ...] = (
    "db",
    "fetcher_manager",
    "trend_analyzer",
    "analyzer",
    "notifier",
)

# ``__init__`` leaves these unset and the pipeline builds them lazily on first
# use (see ``_get_concept_rankings_for_market`` and
# ``_build_market_structure_context``). Keep them ``None`` so tests observe the
# same lazy construction path production does; a blanket MagicMock would short
# circuit it and silently change behaviour.
_LAZY_SERVICES: tuple[str, ...] = (
    "market_structure_service",
    "market_hotspot_service",
    "search_service",
    "social_sentiment_service",
)


def make_pipeline(**overrides: Any) -> StockAnalysisPipeline:
    """Return a pipeline with every ``__init__`` attribute present but inert.

    Args:
        **overrides: Attributes to set after the defaults. Values replace the
            defaults verbatim, so pass ``MagicMock()``, ``SimpleNamespace`` or a
            real collaborator depending on what the test needs.
    """
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    for name, value in _DEFAULT_ATTRIBUTES.items():
        setattr(pipeline, name, value)
    pipeline.config = MagicMock()
    for name in _COLLABORATORS:
        setattr(pipeline, name, MagicMock())
    for name in _LAZY_SERVICES:
        setattr(pipeline, name, None)
    pipeline._single_stock_notify_lock = threading.Lock()
    pipeline._daily_market_context_service_lock = threading.Lock()
    pipeline._concept_rankings_cache_lock = threading.Lock()
    pipeline._concept_rankings_cache = {}
    pipeline._last_local_report_path = None
    pipeline._last_local_report_error = None
    for name, value in overrides.items():
        setattr(pipeline, name, value)
    return pipeline
