# -*- coding: utf-8 -*-
"""按真实新闻证据修正 Agent 报告的置信度理由。

Agent 在分析开始前拿到的 context pack 还没有预填新闻块，模型会据此在
`dashboard.phase_decision.confidence_reason` 里写下「新闻数据块缺失」。但 Agent
运行期间可能已经通过搜索工具拿到了新闻，并在报告正文中实际使用。分析结束后
本模块按本次真实的新闻证据修正这段理由：

- 有新闻证据：删除与「新闻缺失/不足」相矛盾的表述，必要时改写成已纳入新闻证据。
- 无新闻证据：确保理由明确提示新闻缺失或不足，而不是沉默。

只处理 Agent 路径的结果；经典路径的 Prompt 与输出保持不变。
"""

from __future__ import annotations

import re
from typing import Any, Optional

_ZH_SEPARATOR = "；"
_EN_SEPARATOR = " "
_KO_SEPARATOR = " "

_NEWS_MISSING_MARKERS = (
    "新闻数据块缺失",
    "新闻数据缺失",
    "新闻数据不足",
    "新闻数据块不可用",
    "新闻缺失",
    "新闻不足",
    "缺少新闻",
    "无新闻数据",
    "没有新闻数据",
    "未获取到新闻",
    "未获取新闻",
    "news data block is missing",
    "news block is missing",
    "news data is missing",
    "news data is insufficient",
    "news data is unavailable",
    "missing news data",
    "missing news",
    "no news data",
    "insufficient news",
    "뉴스 데이터가 누락",
    "뉴스 데이터 누락",
    "뉴스 데이터가 부족",
    "뉴스 데이터 부족",
    "뉴스 데이터가 없",
    "뉴스 데이터 없",
)

_SENTENCE_SPLIT_RE = re.compile(r"[；;。\n]+")


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


def _strip_news_missing_claims(reason: str, *, language: str) -> str:
    segments = [segment.strip() for segment in _SENTENCE_SPLIT_RE.split(reason)]
    kept = [segment for segment in segments if segment and not _contains_news_missing(segment)]
    return _separator(language).join(kept)


def _phase_decision(result: Any) -> Optional[dict]:
    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        return None
    phase_decision = dashboard.get("phase_decision")
    if isinstance(phase_decision, dict):
        return phase_decision
    return None


def reconcile_news_confidence_reason(
    result: Any,
    *,
    evidence_present: bool,
    news_result_count: Optional[int],
    language: str = "zh",
) -> bool:
    """按真实新闻证据修正 `phase_decision.confidence_reason`。

    返回是否修改了结果。没有 `phase_decision` 时保持原样，避免凭空生成模型
    没有给出的判断。
    """
    phase_decision = _phase_decision(result)
    if phase_decision is None:
        return False

    existing = _text(phase_decision.get("confidence_reason"))

    if evidence_present:
        if not _contains_news_missing(existing):
            return False
        cleaned = _strip_news_missing_claims(existing, language=language)
        phase_decision["confidence_reason"] = cleaned or _evidence_reason(
            news_result_count, language
        )
        return True

    if _contains_news_missing(existing):
        return False
    missing = _missing_reason(language)
    phase_decision["confidence_reason"] = (
        f"{existing}{_separator(language)}{missing}" if existing else missing
    )
    return True
