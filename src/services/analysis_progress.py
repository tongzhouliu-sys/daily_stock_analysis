# -*- coding: utf-8 -*-
"""Single source of truth for analysis task stage boundaries.

The Web UI renders one five-step progress strip for a running analysis. Those
steps must come from the server: deriving them from a percentage in the client
would disagree with the pipeline as soon as a stage moves.

Only the *entry* progress values live here. Progress values emitted inside a
stage (for example 64/68/93-97 while generating) stay where they are; the stage
of any progress is resolved by ``progress_stage``.
"""

from __future__ import annotations

from typing import Optional

#: Numeric entry progress of each pipeline stage. Emitters must use these
#: instead of literals so the stage table and the pipeline cannot drift.
PROGRESS_MARKET_DATA = 10
PROGRESS_FUNDAMENTALS = 32
PROGRESS_NEWS = 46
PROGRESS_GENERATING = 58
PROGRESS_FINALIZING = 93

STAGE_QUEUED = "queued"
STAGE_MARKET_DATA = "market_data"
STAGE_FUNDAMENTALS = "fundamentals"
STAGE_NEWS = "news"
STAGE_GENERATING = "generating"
STAGE_FINALIZING = "finalizing"
STAGE_DONE = "done"

#: Ordered stage entries: (minimum progress, stage id).
_STAGE_ENTRIES = (
    (PROGRESS_MARKET_DATA, STAGE_MARKET_DATA),
    (PROGRESS_FUNDAMENTALS, STAGE_FUNDAMENTALS),
    (PROGRESS_NEWS, STAGE_NEWS),
    (PROGRESS_GENERATING, STAGE_GENERATING),
    (PROGRESS_FINALIZING, STAGE_FINALIZING),
)

#: User-facing stage order, used by the Web progress strip.
STAGE_ORDER = (
    STAGE_MARKET_DATA,
    STAGE_FUNDAMENTALS,
    STAGE_NEWS,
    STAGE_GENERATING,
    STAGE_FINALIZING,
)


def progress_stage(progress: int) -> str:
    """Return the stage id a progress value belongs to."""
    try:
        value = int(progress)
    except (TypeError, ValueError):
        value = 0
    if value >= 100:
        return STAGE_DONE
    if value < _STAGE_ENTRIES[0][0]:
        return STAGE_QUEUED
    stage = STAGE_QUEUED
    for threshold, candidate in _STAGE_ENTRIES:
        if value >= threshold:
            stage = candidate
        else:
            break
    return stage


def stage_index(stage: Optional[str]) -> int:
    """Return the zero-based index of ``stage`` in ``STAGE_ORDER`` (-1 if absent)."""
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage)
    return -1


__all__ = [
    "PROGRESS_MARKET_DATA",
    "PROGRESS_FUNDAMENTALS",
    "PROGRESS_NEWS",
    "PROGRESS_GENERATING",
    "PROGRESS_FINALIZING",
    "STAGE_QUEUED",
    "STAGE_MARKET_DATA",
    "STAGE_FUNDAMENTALS",
    "STAGE_NEWS",
    "STAGE_GENERATING",
    "STAGE_FINALIZING",
    "STAGE_DONE",
    "STAGE_ORDER",
    "progress_stage",
    "stage_index",
]
