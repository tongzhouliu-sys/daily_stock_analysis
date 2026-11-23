# -*- coding: utf-8 -*-
"""新闻状态运行时注入与定稿后处理测试。"""

from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock

from src.agent import news_evidence
from src.agent.llm_adapter import LLMResponse
from src.agent.runner import run_agent_loop
from src.agent.tools.registry import ToolRegistry
from src.services.news_confidence import reconcile_news_claims


@dataclass
class _StubResult:
    dashboard: Optional[dict[str, Any]] = None
    analysis_summary: str = ""
    operation_advice: str = "持有"
    trend_prediction: str = "震荡"
    raw_response: str = "原始响应不应被改写：新闻数据缺失"


def _dashboard(confidence_reason):
    return {
        "intelligence": {
            "risk_alerts": ["新闻数据块缺失，无法评估消息面风险。", "估值偏高。"],
        },
        "strategy_synthesis": {
            "supporting_skills": [
                {"skill_id": "trend", "reasoning": "筹码、新闻数据缺失，置信度下调。"},
            ],
            "opposing_skills": [
                {"skill_id": "risk", "reasoning": "新闻块缺失。"},
            ],
        },
        "phase_decision": {"confidence_reason": confidence_reason},
    }


def _texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)


def test_evidence_present_scrubs_risk_confidence_and_skill_reasoning():
    result = _StubResult(
        dashboard=_dashboard("新闻上下文缺失；技术面偏弱。"),
        analysis_summary="新闻数据缺失，暂无法判断。",
    )

    changed = reconcile_news_claims(
        result,
        evidence_present=True,
        news_result_count=20,
        language="zh",
    )

    assert changed
    assert result.dashboard["intelligence"]["risk_alerts"] == ["估值偏高。"]
    assert result.dashboard["phase_decision"]["confidence_reason"] == "技术面偏弱。"
    assert result.analysis_summary == ""
    assert result.raw_response == "原始响应不应被改写：新闻数据缺失"
    for text in _texts(result.dashboard):
        for marker in ("新闻数据块缺失", "新闻块缺失", "新闻上下文缺失", "新闻数据缺失"):
            assert marker not in text


def test_evidence_absent_keeps_missing_text_and_adds_confidence_hint():
    result = _StubResult(
        dashboard=_dashboard("技术面偏弱。"),
        analysis_summary="新闻数据缺失，暂无法判断。",
    )

    changed = reconcile_news_claims(
        result,
        evidence_present=False,
        news_result_count=0,
        language="zh",
    )

    assert changed
    assert "新闻数据块缺失" in result.dashboard["intelligence"]["risk_alerts"][0]
    assert "新闻数据缺失" in result.dashboard["strategy_synthesis"]["supporting_skills"][0]["reasoning"]
    assert result.analysis_summary == "新闻数据缺失，暂无法判断。"
    assert "新闻数据缺失或不足" in result.dashboard["phase_decision"]["confidence_reason"]


def test_runtime_news_section_reaches_the_final_llm_call():
    adapter = MagicMock()
    captured = {}

    def _call(messages, tools, **kwargs):
        captured["messages"] = [dict(message) for message in messages]
        return LLMResponse(content="final answer", tool_calls=[], usage={}, provider="stub")

    adapter.call_with_tools.side_effect = _call
    token = news_evidence.activate_news_evidence_scope("zh")
    try:
        news_evidence.record_news_evidence(
            3,
            [
                {"title": "标题1", "source": "测试源", "url": "https://example.com/1"},
            ],
        )
        result = run_agent_loop(
            messages=[{"role": "user", "content": "分析股票"}],
            tool_registry=ToolRegistry(),
            llm_adapter=adapter,
            max_steps=1,
        )
    finally:
        news_evidence.reset_news_evidence_scope(token)

    assert result.success
    assert "3 条新闻结果" in captured["messages"][-1]["content"]
    assert "新闻数据块缺失" in captured["messages"][-1]["content"]


def test_runtime_news_section_reports_zero_after_a_search_attempt():
    adapter = MagicMock()
    captured = {}

    def _call(messages, tools, **kwargs):
        captured["messages"] = [dict(message) for message in messages]
        return LLMResponse(content="final answer", tool_calls=[], usage={}, provider="stub")

    adapter.call_with_tools.side_effect = _call
    token = news_evidence.activate_news_evidence_scope("zh")
    try:
        news_evidence.record_news_evidence(0)
        run_agent_loop(
            messages=[{"role": "user", "content": "分析股票"}],
            tool_registry=ToolRegistry(),
            llm_adapter=adapter,
            max_steps=1,
        )
    finally:
        news_evidence.reset_news_evidence_scope(token)

    assert "结果为 0 条" in captured["messages"][-1]["content"]
