# -*- coding: utf-8 -*-
"""T10: 盘中未收盘的 K 线不参与量能评分。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.stock_analyzer import StockTrendAnalyzer, VolumeStatus


def _make_df(*, last_volume: float, base_volume: float = 1_000_000.0) -> pd.DataFrame:
    """30 天缓跌走势；前 5 日成交量固定，最后一日可调。"""
    rows = []
    price = 12.0
    for i in range(30):
        price = round(price - 0.05, 2)
        volume = base_volume
        if i >= 24:
            volume = base_volume
        rows.append(
            {
                "date": pd.Timestamp("2026-10-01") + pd.Timedelta(days=i),
                "open": price + 0.03,
                "high": price + 0.1,
                "low": price - 0.1,
                "close": price,
                "volume": volume,
                "amount": volume * price,
                "pct_chg": -0.4,
            }
        )
    rows[-1]["volume"] = last_volume
    return pd.DataFrame(rows)


@pytest.fixture
def analyzer() -> StockTrendAnalyzer:
    return StockTrendAnalyzer()


def test_partial_bar_keeps_volume_score_neutral(analyzer):
    df = _make_df(last_volume=300_000.0)  # 30% of the 5-day average

    result = analyzer.analyze(df.copy(), "600519", is_partial_bar=True)

    assert result.volume_status == VolumeStatus.NORMAL
    assert "不评分" in result.volume_trend
    assert "洗盘" not in "".join(result.signal_reasons)
    assert "洗盘" not in "".join(result.risk_factors)


def test_partial_bar_score_equals_neutral_volume(analyzer):
    """量能分应为中性 10 分：与“量能正常”的同一份数据得分一致。"""
    partial = analyzer.analyze(
        _make_df(last_volume=300_000.0), "600519", is_partial_bar=True
    )
    neutral = analyzer.analyze(_make_df(last_volume=1_000_000.0), "600519")
    assert neutral.volume_status == VolumeStatus.NORMAL
    assert partial.signal_score == neutral.signal_score


def test_full_day_bar_keeps_existing_behaviour(analyzer):
    result = analyzer.analyze(_make_df(last_volume=300_000.0), "600519")

    assert result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN
    assert result.volume_ratio_5d == pytest.approx(0.3, rel=1e-6)


def test_missing_five_day_average_is_not_shrink(analyzer):
    df = _make_df(last_volume=0.0, base_volume=0.0)

    result = analyzer.analyze(df, "600519")

    assert result.volume_status == VolumeStatus.NORMAL
    assert "缺失" in result.volume_trend
    assert "缩量" not in result.volume_trend
