# -*- coding: utf-8 -*-
"""Pipeline artifact metadata for unsupported chip instruments."""

from __future__ import annotations

from tests.support import make_pipeline


def _legacy_artifacts(pipeline, *, branch_code: str = "SPX", chip_not_supported: bool = True):
    return pipeline._build_legacy_analysis_artifacts(
        code=branch_code,
        stock_name=branch_code,
        market="us",
        phase=None,
        context={"code": branch_code},
        enhanced_context={},
        realtime_quote=None,
        trend_result=None,
        chip_data=None,
        fundamental_context=None,
        news_context=None,
        news_result_count=None,
        query_id="q-chip",
        chip_not_supported=chip_not_supported,
    )


def _agent_artifacts(pipeline, *, branch_code: str = "SPX", chip_not_supported: bool = True):
    return pipeline._build_agent_analysis_artifacts(
        code=branch_code,
        stock_name=branch_code,
        market="us",
        phase=None,
        initial_context={},
        fundamental_context=None,
        query_id="q-chip",
        chip_not_supported=chip_not_supported,
    )


def test_legacy_artifacts_mark_chip_not_supported() -> None:
    artifacts = _legacy_artifacts(make_pipeline())

    assert artifacts.chip_data is None
    assert artifacts.metadata["chip_not_supported"] is True


def test_legacy_artifacts_omit_flag_when_supported() -> None:
    artifacts = _legacy_artifacts(
        make_pipeline(), branch_code="600519", chip_not_supported=False
    )

    assert "chip_not_supported" not in artifacts.metadata


def test_agent_artifacts_mark_chip_not_supported() -> None:
    artifacts = _agent_artifacts(make_pipeline())

    assert artifacts.chip_data is None
    assert artifacts.metadata["chip_not_supported"] is True


def test_agent_artifacts_omit_flag_when_supported() -> None:
    artifacts = _agent_artifacts(
        make_pipeline(), branch_code="600519", chip_not_supported=False
    )

    assert "chip_not_supported" not in artifacts.metadata
