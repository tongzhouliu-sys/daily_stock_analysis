# -*- coding: utf-8 -*-
"""按真实新闻证据修正 Agent 报告里的新闻状态表述。

Agent 在分析开始前拿到的 context pack 还没有预填新闻块，模型会据此在
风险项、置信度理由、技能推理里写下「新闻数据块缺失 / 新闻块缺失 /
新闻上下文缺失」。但 Agent 运行期间可能已经通过搜索工具拿到了新闻，并在报告
正文中实际使用。分析结束后本模块按本次真实的新闻证据修正这些文字：

- 有新闻证据：删除与「新闻缺失/不足」相矛盾的句子或列表项；必要时把置信度
  理由改写成已纳入新闻证据。
- 无新闻证据：确保置信度理由明确提示新闻缺失或不足，而不是沉默。

只处理 Agent 路径的结果；经典路径的 Prompt 与输出保持不变。
"""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from typing import Any, Iterator, Optional, Tuple

_ZH_SEPARATOR = "；"
_EN_SEPARATOR = " "
_KO_SEPARATOR = " "

_NEWS_MISSING_MARKERS = (
    "新闻数据块缺失",
    "新闻数据块不可用",
    "新闻数据块不足",
    "新闻块缺失",
    "新闻块不可用",
    "新闻上下文缺失",
    "新闻上下文不足",
    "新闻数据缺失",
    "新闻数据不足",
    "新闻数据不可用",
    "新闻缺失",
    "新闻不足",
    "缺少新闻",
    "无新闻数据",
    "没有新闻数据",
    "未获取到新闻",
    "未获取新闻",
    "新闻获取失败",
    "news data block is missing",
    "news data block missing",
    "news block is missing",
    "news block missing",
    "news context is missing",
    "news context missing",
    "news data is missing",
    "news data missing",
    "news data is insufficient",
    "news data is unavailable",
    "missing news data",
    "missing news",
    "no news data",
    "insufficient news",
    "뉴스 데이터 블록 누락",
    "뉴스 데이터가 누락",
    "뉴스 데이터 누락",
    "뉴스 데이터가 부족",
    "뉴스 데이터 부족",
    "뉴스 블록 누락",
    "뉴스 컨텍스트 누락",
    "뉴스 데이터가 없",
    "뉴스 데이터 없",
)

_SENTENCE_PATTERN = re.compile(r"([；;。\n])")


def _separator(language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized == "zh":
        return _ZH_SEPARATOR
    if normalized == "ko":
        return _KO_SEPARATOR
    return _EN_SEPARATOR


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _contains_news_missing(text: str) -> bool:
    lowered = _text(text).lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _NEWS_MISSING_MARKERS)


def _evidence_reason(news_result_count: Optional[int], language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized == "zh":
        if news_result_count and news_result_count > 0:
            return f"本次分析已纳入 {news_result_count} 条新闻证据。"
        return "本次分析已纳入新闻证据。"
    if normalized == "ko":
        if news_result_count and news_result_count > 0:
            return f"이번 분석에는 뉴스 근거 {news_result_count}건이 반영되었습니다."
        return "이번 분석에는 뉴스 근거가 반영되었습니다."
    if news_result_count and news_result_count > 0:
        return f"This analysis incorporates {news_result_count} news items."
    return "This analysis incorporates news-based evidence."


def _missing_reason(language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized == "zh":
        return "新闻数据缺失或不足。"
    if normalized == "ko":
        return "뉴스 데이터가 부족하거나 누락되었습니다."
    return "News data is missing or insufficient."


def _strip_news_missing_sentences(text: str, *, language: str) -> str:
    """删除包含新闻缺失表述的句子，保留原有标点与其余内容。"""
    parts = _SENTENCE_PATTERN.split(text)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        segment = parts[index]
        delimiter = parts[index + 1] if index + 1 < len(parts) else ""
        if _contains_news_missing(segment):
            continue
        if segment.strip():
            kept.append(segment.strip() + delimiter)
    cleaned = "".join(kept).strip()
    # 只清理被删句留下的前导分隔符，保留正文末尾标点。
    return cleaned.lstrip("；;。 \n\t").rstrip(" \n\t")


_NON_REPORT_TEXT_FIELDS = {
    "dashboard",
    "raw_response",
    "error_message",
    "diagnostic_context_snapshot",
    "analysis_context_pack_overview",
    "market_snapshot",
    "fundamental_context",
    "market_structure_context",
    "market_phase_summary",
}


def _result_text_attributes(result: Any) -> Iterator[Tuple[str, str]]:
    """Yield report-visible string attributes on the result object."""
    if not is_dataclass(result):
        return
    for result_field in fields(result):
        if result_field.name in _NON_REPORT_TEXT_FIELDS:
            continue
        value = getattr(result, result_field.name, None)
        if isinstance(value, str):
            yield result_field.name, value


def _scrub_text_fields(value: Any, *, language: str) -> Any:
    """递归清理风险项、置信度理由、技能推理等文字字段。

    列表里的字符串若整项都只剩缺失表述，直接丢弃该项；字典中的字符串保留，
    由调用方决定空值是否需要兜底。
    """
    if isinstance(value, str):
        return _strip_news_missing_sentences(value, language=language)
    if isinstance(value, list):
        cleaned: list[Any] = []
        for item in value:
            new_item = _scrub_text_fields(item, language=language)
            if isinstance(item, str) and not _text(new_item):
                continue
            cleaned.append(new_item)
        return cleaned
    if isinstance(value, dict):
        return {
            key: _scrub_text_fields(item, language=language)
            for key, item in value.items()
        }
    return value


def _phase_decision(result: Any) -> Optional[dict]:
    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        return None
    phase_decision = dashboard.get("phase_decision")
    if isinstance(phase_decision, dict):
        return phase_decision
    return None


def reconcile_news_claims(
    result: Any,
    *,
    evidence_present: bool,
    news_result_count: Optional[int],
    language: str = "zh",
) -> bool:
    """按真实新闻证据修正报告中的新闻状态文字。

    返回是否修改了结果。无新闻证据时只补置信度理由，不清理任何「缺失」提示。
    """
    phase_decision = _phase_decision(result)

    if not evidence_present:
        # 只有「检索发起但零命中」（明确 0）时才补缺失提示；未配置搜索渠道
        # （None）由报告层单独披露，不能改写模型已有的阶段化理由。
        if phase_decision is None or news_result_count != 0:
            return False
        existing = _text(phase_decision.get("confidence_reason"))
        if _contains_news_missing(existing):
            return False
        missing = _missing_reason(language)
        phase_decision["confidence_reason"] = (
            f"{existing}{_separator(language)}{missing}" if existing else missing
        )
        return True

    changed = False
    dashboard = getattr(result, "dashboard", None)
    if isinstance(dashboard, dict):
        cleaned = _scrub_text_fields(dashboard, language=language)
        if cleaned != dashboard:
            dashboard.clear()
            dashboard.update(cleaned)
            changed = True

    # 模型有时把技能/风控判断同时写进 AnalysisResult 顶层字段。
    for attribute, current in list(_result_text_attributes(result)):
        cleaned = _strip_news_missing_sentences(current, language=language)
        if cleaned != current:
            setattr(result, attribute, cleaned)
            changed = True

    if phase_decision is not None and not _text(phase_decision.get("confidence_reason")):
        phase_decision["confidence_reason"] = _evidence_reason(
            news_result_count, language
        )
        changed = True

    return changed


def reconcile_news_confidence_reason(
    result: Any,
    *,
    evidence_present: bool,
    news_result_count: Optional[int],
    language: str = "zh",
) -> bool:
    """Compatibility alias for the previous confidence-reason-only entrypoint."""
    return reconcile_news_claims(
        result,
        evidence_present=evidence_present,
        news_result_count=news_result_count,
        language=language,
    )
