# -*- coding: utf-8 -*-
"""Deterministic, low-cost titles for Chat sessions.

The first user message is a poor sidebar title: it is often a long, polite
sentence that gets truncated mid-word.  This module derives a compact title
without calling an LLM (no extra cost or latency), while custom titles set by
the user always win.
"""

from __future__ import annotations

import re
from typing import Optional

MAX_TITLE_LENGTH = 24
EMPTY_TITLE = "新对话"

_POLITE_PREFIXES = (
    "麻烦你",
    "麻烦您",
    "麻烦",
    "请你",
    "请您",
    "请帮忙",
    "请帮我",
    "请问",
    "请",
    "帮我",
    "帮忙",
    "劳驾",
    "我想问一下",
    "我想问",
    "我想",
    "想问一下",
    "想问",
    "问一下",
    "问下",
    "可不可以",
    "能不能",
    "能否",
    "可以帮我",
    "可以",
    "给我",
    "please",
    "can you",
    "could you",
)

_TRAILING_PUNCTUATION = "。！？!?.,，;；:：、 \t\r\n"
_QUESTION_TAILS = ("吗", "呢", "啊", "吧", "呀", "哈")

# Longest keywords first so "波浪理论" wins over "波浪".
_SKILL_KEYWORDS = (
    ("波浪理论", "波浪理论"),
    ("缠论", "缠论"),
    ("趋势分析", "趋势"),
    ("箱体震荡", "箱体"),
    ("情绪周期", "情绪周期"),
    ("价值投资", "价值投资"),
    ("风险评估", "风险评估"),
    ("基本面", "基本面"),
    ("技术面", "技术面"),
    ("箱体", "箱体"),
    ("趋势", "趋势"),
    ("情绪", "情绪"),
    ("量化", "量化"),
)

# Ordinary questions rarely name a strategy, but they still have a clear topic.
# Longest/most specific first so "基本面" wins over "面" and "操作建议" over "建议".
_TOPIC_KEYWORDS = (
    ("操作建议", "操作建议"),
    ("基本面", "基本面"),
    ("走势", "走势"),
    ("趋势", "趋势"),
    ("估值", "估值"),
    ("财报", "财报"),
    ("回撤", "回撤"),
    ("风险", "风险"),
    ("止损", "止损"),
    ("建仓", "建仓"),
    ("加仓", "加仓"),
    ("减仓", "减仓"),
    ("买入", "买入"),
    ("卖出", "卖出"),
    ("持有", "持有"),
    ("对比", "对比"),
    ("复盘", "复盘"),
)
_CLAUSE_SEPARATORS = "，,；;。!?！？"

_VERB_PATTERN = r"(?:分析|看看|看下|看一下|研究|判断|评估|解读|跟踪|聊聊|说说|查查)"
_TARGET_BOUNDARY = r"(?=的|现在|最近|未来|近期|走势|趋势|行情|能|是否|适合|可以|该|要不要|吗|呢|[，。！？!?；;]|$)"
_AFTER_VERB_RE = re.compile(
    _VERB_PATTERN + r"(?:一下|下)?\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9]{1,9}?)"
    + _TARGET_BOUNDARY
)
_BEFORE_ACTION_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,10}?)(?:现在|最近|未来|近期)?"
    r"(?:适合|能|可以|该|要不要|是否)"
    r"(?:买入|卖出|建仓|加仓|减仓|持有|抄底|追高|上车|入手)"
)
_STOCK_CODE_RE = re.compile(
    r"(?<![0-9A-Za-z])((?:SH|SZ|BJ|HK|US)?\d{5,6})(?![0-9])",
    re.IGNORECASE,
)


def _strip_polite_prefix(text: str) -> str:
    changed = True
    while changed:
        changed = False
        lowered = text.lower()
        for prefix in _POLITE_PREFIXES:
            if lowered.startswith(prefix):
                text = text[len(prefix):].lstrip("，,、:： ")
                changed = True
                break
    return text


def _strip_question_tail(text: str) -> str:
    text = text.rstrip(_TRAILING_PUNCTUATION)
    changed = True
    while changed and len(text) > 2:
        changed = False
        for tail in _QUESTION_TAILS:
            if text.endswith(tail):
                text = text[: -len(tail)].rstrip(_TRAILING_PUNCTUATION)
                changed = True
                break
    return text


def _first_clause(text: str) -> str:
    match = re.search(r"[，。！？!?；;\n]", text)
    return text[: match.start()] if match else text


def _detect_skill(text: str) -> Optional[str]:
    for keyword, label in _SKILL_KEYWORDS:
        if keyword in text:
            return label
    return None


def _detect_topic(text: str) -> Optional[str]:
    # Prefer the topic the user actually raised first, not the one listed first
    # in the keyword table (e.g. "建仓吗，顺便看看风险" is about 建仓).
    best: Optional[tuple[int, str]] = None
    for keyword, label in _TOPIC_KEYWORDS:
        position = text.find(keyword)
        if position >= 0 and (best is None or position < best[0]):
            best = (position, label)
    return best[1] if best else None


def _detect_target(text: str) -> Optional[str]:
    code = _STOCK_CODE_RE.search(text)
    if code:
        return code.group(1).upper()

    # A trailing "看看风险" must not override the stock named at the start.
    candidates: list[tuple[int, str]] = []
    after = _AFTER_VERB_RE.search(text)
    if after:
        target = re.sub(r"^(?:一下|下|这只|那个|这个)", "", after.group(1))
        if 2 <= len(target) <= 10:
            candidates.append((after.start(1), target))

    before = _BEFORE_ACTION_RE.search(text)
    if before:
        candidates.append((before.start(1), before.group(1)))

    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    return None


def _compact(text: str, limit: int = MAX_TITLE_LENGTH) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ，,、:：")
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 1)]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ，,、:：;；") + "…"


def summarize_chat_title(message: Optional[str]) -> str:
    """Return a concise, stable sidebar title derived from the first question."""

    if not message:
        return EMPTY_TITLE
    text = re.sub(r"\s+", " ", message).strip()
    text = text.lstrip("@").replace("**", "").replace("`", "")
    if not text:
        return EMPTY_TITLE

    cleaned = _strip_question_tail(_strip_polite_prefix(text)) or text
    label = _detect_skill(cleaned)
    # Short one-clause questions already read like a title after polite noise is
    # stripped; only condense the rambling ones into "<target> · <topic>".
    is_long_question = len(cleaned) > 18 or any(sep in cleaned for sep in _CLAUSE_SEPARATORS)
    if not label and is_long_question:
        label = _detect_topic(cleaned)
    target = _detect_target(cleaned)

    if label and target:
        title = f"{target} · {label}"
    else:
        title = cleaned

    if len(title) > MAX_TITLE_LENGTH and not (label and target):
        title = _first_clause(title)

    return _compact(title) or EMPTY_TITLE
