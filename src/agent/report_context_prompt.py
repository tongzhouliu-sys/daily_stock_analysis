# -*- coding: utf-8 -*-
"""Prompt rendering for a server-validated historical stock report.

The rendered block is historical reference material.  It deliberately carries
its own source id and generation time so the model can distinguish the saved
report from live quotes or later turns, and it never includes raw context
snapshots, diagnostics or provider configuration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.market_structure_prompt import format_market_structure_prompt_section
from src.report_language import normalize_report_language

_STRATEGY_FIELDS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")


def format_report_context_prompt_section(
    context: Any,
    report_language: str = "zh",
) -> str:
    """Render one validated report projection for single- and multi-agent prompts."""
    if not isinstance(context, dict):
        return ""
    try:
        record_id = int(context.get("record_id"))
    except (TypeError, ValueError):
        return ""
    if record_id <= 0:
        return ""

    language = normalize_report_language(report_language)
    lines = _header(language)
    lines.append(_field(language, "record", str(record_id)))
    if context.get("created_at"):
        lines.append(_field(language, "created_at", str(context["created_at"])))
    if context.get("report_type"):
        lines.append(_field(language, "report_type", str(context["report_type"])))
    identity = str(context.get("stock_code") or "").strip()
    if identity:
        name = str(context.get("stock_name") or "").strip()
        lines.append(_field(language, "stock", f"{name} ({identity})" if name else identity))

    for key in (
        "analysis_summary",
        "key_points",
        "operation_advice",
        "action_label",
        "trend_prediction",
        "risk_warning",
        "buy_reason",
    ):
        value = context.get(key)
        if value:
            lines.append(_field(language, key, str(value)))

    strategy = context.get("strategy")
    if isinstance(strategy, dict):
        for key in _STRATEGY_FIELDS:
            value = strategy.get(key)
            if value is not None and str(value).strip():
                lines.append(_field(language, key, str(value)))

    price = context.get("price")
    if price is not None:
        lines.append(_field(language, "price", str(price)))
    change_pct = context.get("change_pct")
    if change_pct is not None:
        lines.append(_field(language, "change_pct", f"{change_pct}%"))

    truncated = [
        str(item) for item in context.get("truncated_fields") or []
        if isinstance(item, str) and item.strip()
    ]
    if truncated:
        lines.append(_field(language, "truncated", ", ".join(truncated)))

    market_structure = format_market_structure_prompt_section(
        context.get("market_structure"),
        language,
    ).strip()
    if market_structure:
        lines.append(market_structure)

    lines.append(_guardrail(language))
    return "\n".join(lines).strip() + "\n"


def _header(language: str) -> List[str]:
    if language == "en":
        return [
            "## Historical Analysis Report Selected by the User",
            "The following is saved historical material, not a live quote. "
            "Use it as the report the user is asking about.",
        ]
    if language == "ko":
        return [
            "## 사용자가 선택한 과거 분석 보고서",
            "아래 내용은 실시간 시세가 아닌 저장된 과거 자료입니다. "
            "사용자가 질문하는 보고서로 사용하십시오.",
        ]
    return [
        "## 用户指定的历史分析报告",
        "以下为已保存的历史资料，不是实时行情；请把它作为用户正在追问的那份报告。",
    ]


_LABELS = {
    "zh": {
        "record": "报告记录 ID",
        "created_at": "报告生成时间",
        "report_type": "报告类型",
        "stock": "证券",
        "analysis_summary": "分析摘要",
        "key_points": "主要结论",
        "operation_advice": "操作建议",
        "action_label": "操作标签",
        "trend_prediction": "趋势判断",
        "risk_warning": "风险提示",
        "buy_reason": "买入理由",
        "ideal_buy": "理想买入点",
        "secondary_buy": "次优买入点",
        "stop_loss": "止损位",
        "take_profit": "目标位",
        "price": "报告当时价格",
        "change_pct": "报告当时涨跌幅",
        "truncated": "已按上下文预算截取",
    },
    "en": {
        "record": "Report record ID",
        "created_at": "Report generated at",
        "report_type": "Report type",
        "stock": "Security",
        "analysis_summary": "Analysis summary",
        "key_points": "Key conclusions",
        "operation_advice": "Operation advice",
        "action_label": "Action label",
        "trend_prediction": "Trend view",
        "risk_warning": "Risk warning",
        "buy_reason": "Buy rationale",
        "ideal_buy": "Ideal entry",
        "secondary_buy": "Secondary entry",
        "stop_loss": "Stop loss",
        "take_profit": "Target",
        "price": "Price at report time",
        "change_pct": "Change at report time",
        "truncated": "Truncated to context budget",
    },
    "ko": {
        "record": "보고서 레코드 ID",
        "created_at": "보고서 생성 시각",
        "report_type": "보고서 유형",
        "stock": "종목",
        "analysis_summary": "분석 요약",
        "key_points": "주요 결론",
        "operation_advice": "운영 조언",
        "action_label": "행동 라벨",
        "trend_prediction": "추세 판단",
        "risk_warning": "위험 경고",
        "buy_reason": "매수 근거",
        "ideal_buy": "이상적 매수점",
        "secondary_buy": "차선 매수점",
        "stop_loss": "손절가",
        "take_profit": "목표가",
        "price": "보고서 당시 가격",
        "change_pct": "보고서 당시 등락률",
        "truncated": "컨텍스트 예산에 맞게 잘림",
    },
}


def _field(language: str, key: str, value: str) -> str:
    label = _LABELS.get(language, _LABELS["zh"]).get(key, key)
    return f"- {label}: {value}"


def _guardrail(language: str) -> str:
    if language == "en":
        return (
            "- Guardrail: keep the report's date and source separate from current market data; "
            "do not silently replace missing fields with live or inferred values."
        )
    if language == "ko":
        return (
            "- 제약: 보고서의 날짜와 출처를 현재 시장 데이터와 구분하고, "
            "누락된 필드를 실시간 값이나 추측으로 대체하지 마십시오."
        )
    return "- 约束：区分报告日期、来源与当前行情；缺失字段保持缺失，不要用实时值或推测补齐。"
