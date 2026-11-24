# -*- coding: utf-8 -*-
"""Unit tests for the daily-OHLCV volume-profile chip estimate."""

from __future__ import annotations

import pytest

from data_provider.volume_profile_chip import (
    VOLUME_PROFILE_BINS,
    VOLUME_PROFILE_SOURCE,
    build_volume_profile_chip,
)


def _bars() -> list[dict]:
    rows = []
    for index in range(30):
        price = (10.0, 20.0, 30.0)[index % 3]
        rows.append(
            {
                "date": f"2026-05-{index + 1:02d}",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000.0,
            }
        )
    return rows


def test_volume_profile_profit_ratio_and_average_cost() -> None:
    chip = build_volume_profile_chip(code="NVDA", bars=_bars(), current_price=25.0)

    assert chip is not None
    assert chip.source == VOLUME_PROFILE_SOURCE
    assert chip.profit_ratio == pytest.approx(2 / 3, abs=0.01)
    assert chip.avg_cost == pytest.approx(20.0, abs=0.01)


def test_volume_profile_90_percent_interval_is_narrowest_covering_range() -> None:
    chip = build_volume_profile_chip(code="NVDA", bars=_bars(), current_price=25.0)

    assert chip is not None
    # 90% of equal volume across three price levels must cover 10 -> 30.
    assert chip.cost_90_low == pytest.approx(10.0, abs=0.01)
    assert chip.cost_90_high == pytest.approx(30.0, abs=0.01)
    assert chip.concentration_90 == pytest.approx(0.5, abs=0.01)
    assert chip.cost_90_low <= chip.cost_70_low <= chip.avg_cost
    assert chip.avg_cost <= chip.cost_70_high <= chip.cost_90_high


def test_volume_profile_falls_back_to_last_close_as_reference_price() -> None:
    chip = build_volume_profile_chip(code="NVDA", bars=_bars(), current_price=None)

    assert chip is not None
    # Last row is price 30, so every cost level is profitable.
    assert chip.profit_ratio == pytest.approx(1.0, abs=0.01)


def test_volume_profile_marks_estimate_metadata() -> None:
    chip = build_volume_profile_chip(code="NVDA", bars=_bars(), current_price=25.0)

    assert chip is not None
    assert chip.metadata["is_estimated"] is True
    assert chip.metadata["estimation_method"] == "volume_profile"
    assert chip.metadata["bins"] == VOLUME_PROFILE_BINS
    assert chip.to_dict()["is_estimated"] is True


def test_volume_profile_returns_none_when_sample_too_short() -> None:
    assert build_volume_profile_chip(
        code="NVDA", bars=_bars()[:10], current_price=25.0
    ) is None


def test_volume_profile_returns_none_without_usable_volume() -> None:
    rows = [dict(row, volume=0) for row in _bars()]
    assert build_volume_profile_chip(
        code="NVDA", bars=rows, current_price=25.0
    ) is None
