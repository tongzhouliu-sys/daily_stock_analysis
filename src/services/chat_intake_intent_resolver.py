"""v2: Intent inference for smart intake.

Extracts analysis background from user input using deterministic rules.
Covers: goal, horizon, position, risk_focus, research_focus, reading_style.

Each inference includes source, confidence, and evidence (text span).
P0: No LLM calls; pure rule-based inference.

T3: Intent inference service.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.agent_intake import BriefSource

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class InferredDimension:
    """Inferred dimension with source and evidence."""

    value: str
    source: BriefSource
    confidence: float = 1.0
    evidence: Optional[str] = None
    evidence_start: Optional[int] = None
    evidence_end: Optional[int] = None


@dataclass
class IntentInference:
    """Complete intent inference result."""

    goal: Optional[InferredDimension] = None
    horizon: Optional[InferredDimension] = None
    position: Optional[InferredDimension] = None
    risk_focus: Optional[InferredDimension] = None
    research_focus: List[InferredDimension] = field(default_factory=list)
    reading_style: Optional[InferredDimension] = None
    unknown_fields: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern definitions
# ─────────────────────────────────────────────────────────────────────────────


# Goal patterns
GOAL_PATTERNS = {
    "worth_watching": [
        (r"(?:值不值得|是否值得).*(?:关注|买)", "worth_watching"),
        (r"能不能.*买", "worth_watching"),
        (r"可以.*买", "worth_watching"),
        (r"有没有.*价值", "worth_watching"),
    ],
    "evaluate_holding": [
        (r"(?:已|已经).*(?:持仓|持有)", "evaluate_holding"),
        (r"我.*有.*仓位", "evaluate_holding"),
        (r"成本.*是", "evaluate_holding"),
    ],
    "understand_move": [
        (r"为什么.*(?:涨|跌)", "understand_move"),
        (r"(?:涨|跌).*原因", "understand_move"),
        (r"啥原因", "understand_move"),
    ],
    "learn_method": [
        (r"怎么分析", "learn_method"),
        (r"分析方法", "learn_method"),
        (r"如何分析", "learn_method"),
    ],
}

# Horizon patterns
HORIZON_PATTERNS = {
    "days": [
        (r"短线", "days"),
        (r"日内", "days"),
        (r"几天", "days"),
        (r"今天|明天", "days"),
    ],
    "weeks": [
        (r"短期", "weeks"),
        (r"几周", "weeks"),
        (r"1.*4.*周", "weeks"),
    ],
    "months": [
        (r"中期", "months"),
        (r"几个月", "months"),
        (r"1.*6.*月", "months"),
        (r"半年", "months"),
    ],
    "long": [
        (r"长期", "long"),
        (r"长线", "long"),
        (r"半年以上", "long"),
        (r"一年", "long"),
    ],
}

# Position patterns
POSITION_PATTERNS = {
    "not_held": [
        (r"没有.*持仓", "not_held"),
        (r"未.*持有", "not_held"),
        (r"空仓", "not_held"),
        (r"想.*买", "not_held"),
        (r"准备.*买", "not_held"),
    ],
    "held_protect_profit": [
        (r"持有.*盈利", "held_protect_profit"),
        (r"持仓.*赚", "held_protect_profit"),
        (r"浮盈", "held_protect_profit"),
    ],
    "held_control_drawdown": [
        (r"持有.*亏", "held_control_drawdown"),
        (r"持仓.*套", "held_control_drawdown"),
        (r"被套", "held_control_drawdown"),
        (r"浮亏", "held_control_drawdown"),
    ],
}

# Risk focus patterns
RISK_PATTERNS = {
    "downside": [
        (r"控制.*风险", "downside"),
        (r"下行.*风险", "downside"),
        (r"稳健", "downside"),
        (r"保守", "downside"),
    ],
    "balanced": [
        (r"均衡", "balanced"),
        (r"机会.*风险", "balanced"),
    ],
    "tolerant": [
        (r"激进", "tolerant"),
        (r"波动", "tolerant"),
        (r"高风险.*高收益", "tolerant"),
    ],
}

# Research focus patterns (multi-select)
RESEARCH_FOCUS_PATTERNS = {
    "trend_levels": [
        (r"趋势", "trend_levels"),
        (r"关键位置", "trend_levels"),
        (r"支撑.*压力", "trend_levels"),
    ],
    "business_valuation": [
        (r"基本面", "business_valuation"),
        (r"估值", "business_valuation"),
        (r"业绩", "business_valuation"),
    ],
    "news_events": [
        (r"消息", "news_events"),
        (r"新闻", "news_events"),
        (r"事件", "news_events"),
    ],
    "risks_counterpoints": [
        (r"风险", "risks_counterpoints"),
        (r"反例", "risks_counterpoints"),
    ],
    "comprehensive": [
        (r"综合分析", "comprehensive"),
    ],
}

# Reading style patterns
READING_STYLE_PATTERNS = {
    "highlights": [
        (r"简单说", "highlights"),
        (r"先看重点", "highlights"),
        (r"简要", "highlights"),
    ],
    "detailed": [
        (r"详细", "detailed"),
        (r"深入", "detailed"),
        (r"全面", "detailed"),
    ],
    "plain": [
        (r"通俗", "plain"),
        (r"易懂", "plain"),
        (r"小白", "plain"),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Main inference function
# ─────────────────────────────────────────────────────────────────────────────


def infer_intent(
    message: str,
    *,
    previous_brief: Optional[Dict[str, Any]] = None,
) -> IntentInference:
    """Infer analysis intent from user message.

    Args:
        message: User input message
        previous_brief: Previous session brief for inheritance

    Returns:
        IntentInference with inferred dimensions
    """
    if not message or not message.strip():
        return IntentInference(
            unknown_fields=["goal", "horizon", "position", "risk_focus", "reading_style"],
            assumptions=["未指定分析背景，按综合判断处理"],
        )

    text = message.strip()
    result = IntentInference()

    # Infer goal
    result.goal = _infer_from_patterns(text, GOAL_PATTERNS, "goal")

    # Infer horizon
    result.horizon = _infer_from_patterns(text, HORIZON_PATTERNS, "horizon")

    # Infer position
    result.position = _infer_from_patterns(text, POSITION_PATTERNS, "position")

    # Infer risk_focus
    result.risk_focus = _infer_from_patterns(text, RISK_PATTERNS, "risk_focus")

    # Infer research_focus (multi-select)
    result.research_focus = _infer_multi_from_patterns(text, RESEARCH_FOCUS_PATTERNS, "research_focus")

    # Infer reading_style
    result.reading_style = _infer_from_patterns(text, READING_STYLE_PATTERNS, "reading_style")

    # Build unknown_fields list
    result.unknown_fields = _build_unknown_fields(result)

    # Build assumptions
    result.assumptions = _build_assumptions(result)

    return result


def _infer_from_patterns(
    text: str,
    patterns: Dict[str, List[Tuple[str, str]]],
    dimension: str,
) -> Optional[InferredDimension]:
    """Infer single dimension from patterns."""
    for value, pattern_list in patterns.items():
        for pattern, _ in pattern_list:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return InferredDimension(
                    value=value,
                    source=BriefSource.EXPLICIT_MESSAGE,
                    confidence=1.0,
                    evidence=match.group(0),
                    evidence_start=match.start(),
                    evidence_end=match.end(),
                )
    return None


def _infer_multi_from_patterns(
    text: str,
    patterns: Dict[str, List[Tuple[str, str]]],
    dimension: str,
) -> List[InferredDimension]:
    """Infer multi-select dimension from patterns."""
    results = []
    for value, pattern_list in patterns.items():
        for pattern, _ in pattern_list:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                results.append(
                    InferredDimension(
                        value=value,
                        source=BriefSource.EXPLICIT_MESSAGE,
                        confidence=1.0,
                        evidence=match.group(0),
                        evidence_start=match.start(),
                        evidence_end=match.end(),
                    )
                )
                break  # Only match once per value
    return results


def _build_unknown_fields(inference: IntentInference) -> List[str]:
    """Build list of unknown fields."""
    unknown = []
    if inference.goal is None:
        unknown.append("goal")
    if inference.horizon is None:
        unknown.append("horizon")
    if inference.position is None:
        unknown.append("position")
    if inference.risk_focus is None:
        unknown.append("risk_focus")
    if inference.reading_style is None:
        unknown.append("reading_style")
    return unknown


def _build_assumptions(inference: IntentInference) -> List[str]:
    """Build assumptions list."""
    assumptions = []

    if inference.goal is None:
        assumptions.append("未指定目标，按综合判断处理")

    if inference.horizon is None:
        assumptions.append("未指定周期，按中期与短期结合的综合视角回答")

    if inference.position is None:
        assumptions.append("未指定持仓状态，不假设用户持仓")

    if inference.risk_focus is None:
        assumptions.append("未指定风险偏好，按中性风险呈现")

    if inference.reading_style is None:
        assumptions.append("使用默认阅读方式：先看重点")

    return assumptions


__all__ = [
    "InferredDimension",
    "IntentInference",
    "infer_intent",
]
