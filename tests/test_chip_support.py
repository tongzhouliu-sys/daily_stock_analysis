# -*- coding: utf-8 -*-
"""Capability gate tests for chip distribution support."""

from __future__ import annotations

import pytest

from data_provider.base import (
    chip_distribution_unsupported_reason,
    is_chip_distribution_supported,
    is_chip_estimate_supported,
)


@pytest.mark.parametrize(
    ("code", "expected_reason"),
    (
        ("NVDA", "non_cn_market"),
        ("AAPL", "non_cn_market"),
        ("SPY", "non_cn_market"),
        ("SOXL", "non_cn_market"),
        ("SPX", "non_cn_market"),
        ("NDX", "non_cn_market"),
        ("^GSPC", "non_cn_market"),
        ("hk00700", "non_cn_market"),
        ("00700", "non_cn_market"),
        ("00700.HK", "non_cn_market"),
        ("HSI.HK", "non_cn_market"),
        ("hkHSI", "non_cn_market"),
        ("510300", "etf"),
        ("159915", "etf"),
        ("sh000016", "cn_index"),
        ("sz399006", "cn_index"),
        ("399006", "cn_index"),
        ("000300.SH", "cn_index"),
        ("399001.SZ", "cn_index"),
        ("csi930955", "cn_index"),
        ("930955.CSI", "cn_index"),
    ),
)
def test_chip_distribution_is_unsupported_for_non_cn_and_index(
    code: str, expected_reason: str
) -> None:
    assert chip_distribution_unsupported_reason(code) == expected_reason
    assert is_chip_distribution_supported(code) is False


@pytest.mark.parametrize("code", ("600519", "000001", "300750", "001205"))
def test_chip_distribution_is_supported_for_a_share_stocks(code: str) -> None:
    assert chip_distribution_unsupported_reason(code) is None
    assert is_chip_distribution_supported(code) is True


def test_index_asset_type_overrides_code_shape() -> None:
    assert is_chip_distribution_supported("600519", asset_type="index") is False
    assert chip_distribution_unsupported_reason("600519", asset_type="index") == "index_target"


def test_estimate_gate_excludes_indices_and_leveraged_etfs() -> None:
    assert is_chip_estimate_supported("NVDA") is True
    assert is_chip_estimate_supported("SPY") is True
    assert is_chip_estimate_supported("600519") is True
    assert is_chip_estimate_supported("SOXL") is False
    assert is_chip_estimate_supported("SPX") is False
    assert is_chip_estimate_supported("sh000016") is False
    # Broad ETFs are not indices/leveraged, so an estimate is allowed when the
    # switch is on (official chip sources still do not cover them).
    assert is_chip_estimate_supported("510300") is True
    assert is_chip_estimate_supported("600519", asset_type="index") is False
