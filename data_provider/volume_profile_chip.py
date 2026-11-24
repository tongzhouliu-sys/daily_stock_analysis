# -*- coding: utf-8 -*-
"""Volume-profile chip distribution estimate from daily OHLCV.

This is deliberately a *fallback* estimate, not exchange chip data. It is only
used when the official chip sources are unavailable (or absent) and the caller
explicitly opts in. The output is always marked with
``metadata["is_estimated"] = True`` so downstream report/context layers can
label it as an estimate instead of a real chip distribution.

Algorithm
---------
1. Take the latest ``VOLUME_PROFILE_WINDOW`` daily bars.
2. Build a price histogram by adding each bar's volume to the bin containing
   its typical price ``(high + low + close) / 3``.
3. ``profit_ratio`` is the share of volume at or below the current price.
4. ``avg_cost`` is the volume-weighted average typical price.
5. ``cost_90_*`` / ``cost_70_*`` are the narrowest contiguous bin ranges
   covering 90% / 70% of total volume; concentration uses the same
   ``(high - low) / (high + low)`` convention as the official sources.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from .realtime_types import ChipDistribution


VOLUME_PROFILE_WINDOW = 90
"""Number of trading days to include in the histogram."""

VOLUME_PROFILE_BINS = 30
"""Fixed number of price bins in the histogram."""

VOLUME_PROFILE_MIN_BARS = 20
"""Below this many usable bars the estimate is considered unreliable."""

VOLUME_PROFILE_SOURCE = "volume_profile_estimate"


def _to_records(bars: Any) -> list[dict[str, Any]]:
    """Return row mappings from a DataFrame, list of mappings, or None."""
    if bars is None:
        return []
    if hasattr(bars, "to_dict") and not isinstance(bars, (list, tuple, dict)):
        try:
            records = bars.to_dict("records")
        except TypeError:
            records = bars.to_dict()
        return [dict(row) for row in records] if records else []
    if isinstance(bars, dict):
        return [dict(bars)]
    if isinstance(bars, (list, tuple)):
        return [dict(row) for row in bars if isinstance(row, dict)]
    return []


def _as_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _percentile_bounds(
    volumes: Sequence[float],
    centers: Sequence[float],
    edges: Sequence[float],
    coverage: float,
) -> tuple[float, float]:
    """Narrowest contiguous bin window covering ``coverage`` of total volume."""
    total = sum(volumes)
    if total <= 0:
        return centers[0], centers[-1]
    target = total * coverage

    best_low = centers[0]
    best_high = centers[-1]
    best_width = float("inf")

    left = 0
    running = 0.0
    for right, volume in enumerate(volumes):
        running += volume
        while running >= target and left <= right:
            width = centers[right] - centers[left]
            if width < best_width:
                best_width = width
                best_low = edges[left]
                best_high = edges[right + 1]
            running -= volumes[left]
            left += 1

    return best_low, best_high


def _concentration(low: float, high: float) -> float:
    if low <= 0 or high <= 0:
        return 0.0
    return max(0.0, (high - low) / (high + low))


def build_volume_profile_chip(
    *,
    code: str,
    bars: Any,
    current_price: Optional[float] = None,
    window: int = VOLUME_PROFILE_WINDOW,
    bins: int = VOLUME_PROFILE_BINS,
) -> Optional[ChipDistribution]:
    """Estimate chip distribution from daily OHLCV bars.

    Returns ``None`` when there is not enough usable data. The returned object
    always carries ``source=volume_profile_estimate`` and
    ``metadata={"is_estimated": True, ...}``.
    """
    records = _to_records(bars)
    usable: list[tuple[float, float, float, float, str]] = []
    for row in records:
        close = _as_float(row.get("close"))
        volume = _as_float(row.get("volume"))
        if close is None or close <= 0 or volume is None or volume <= 0:
            continue
        high = _as_float(row.get("high"))
        low = _as_float(row.get("low"))
        high = high if high is not None and high > 0 else close
        low = low if low is not None and low > 0 else close
        if high < low:
            high, low = low, high
        typical = (high + low + close) / 3.0
        if typical <= 0:
            continue
        usable.append((typical, low, high, volume, str(row.get("date") or "")))

    usable = usable[-max(int(window), VOLUME_PROFILE_MIN_BARS):]
    if len(usable) < VOLUME_PROFILE_MIN_BARS:
        return None

    bin_count = max(int(bins), 1)
    price_low = min(item[1] for item in usable)
    price_high = max(item[2] for item in usable)
    if price_high <= price_low:
        price_high = price_low * 1.01 if price_low > 0 else price_low + 1.0

    width = (price_high - price_low) / bin_count
    edges = [price_low + width * index for index in range(bin_count + 1)]
    centers = [(edges[index] + edges[index + 1]) / 2.0 for index in range(bin_count)]
    volumes = [0.0 for _ in range(bin_count)]

    weighted_cost = 0.0
    total_volume = 0.0
    for typical, _low, _high, volume, _date in usable:
        index = int((typical - price_low) / width)
        index = min(max(index, 0), bin_count - 1)
        volumes[index] += volume
        weighted_cost += typical * volume
        total_volume += volume

    if total_volume <= 0 or weighted_cost <= 0:
        return None

    last_close = _as_float(usable[-1][0])
    reference_price = _as_float(current_price)
    if reference_price is None or reference_price <= 0:
        reference_price = last_close or 0.0

    profit_volume = sum(
        volume
        for volume, center in zip(volumes, centers)
        if reference_price > 0 and center <= reference_price
    )
    profit_ratio = min(max(profit_volume / total_volume, 0.0), 1.0)

    cost_90_low, cost_90_high = _percentile_bounds(volumes, centers, edges, 0.90)
    cost_70_low, cost_70_high = _percentile_bounds(volumes, centers, edges, 0.70)

    return ChipDistribution(
        code=code,
        date=usable[-1][4],
        source=VOLUME_PROFILE_SOURCE,
        profit_ratio=profit_ratio,
        avg_cost=weighted_cost / total_volume,
        cost_90_low=cost_90_low,
        cost_90_high=cost_90_high,
        concentration_90=_concentration(cost_90_low, cost_90_high),
        cost_70_low=cost_70_low,
        cost_70_high=cost_70_high,
        concentration_70=_concentration(cost_70_low, cost_70_high),
        metadata={
            "is_estimated": True,
            "estimation_method": "volume_profile",
            "window": len(usable),
            "bins": bin_count,
            "as_of": usable[-1][4] or None,
        },
    )
