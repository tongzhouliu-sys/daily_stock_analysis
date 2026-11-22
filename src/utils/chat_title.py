# -*- coding: utf-8 -*-
"""Deterministic, low-cost titles for Chat sessions.

The first user message is a poor sidebar title: it is often a long, polite
sentence that gets truncated mid-word.  This module derives a compact title
without calling an LLM (no extra cost or latency), while custom titles set by
the user always win.

The title is composed from a detected *target* (stock / index name or code) and
a *label* (strategy skill or ordinary topic):

- ``<target> · <skill>`` for strategy questions ("贵州茅台 · 缠论"),
- ``<target> · <topic>`` for topic questions ("英伟达 · 财报"),
- ``<a> vs <b>`` for two-way comparisons ("茅台 vs 五粮液"),
- the cleaned first clause when nothing above matches a short, readable
  question ("茅台现在适合买入").
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

MAX_TITLE_LENGTH = 24
EMPTY_TITLE = "新对话"

# ---------------------------------------------------------------------------
# Leading noise
# ---------------------------------------------------------------------------

# Politeness only.  Analysis verbs are stripped in a separate pass because they
# are also a useful signal for target detection and must survive the first pass.
_POLITE_PREFIXES = (
    "麻烦你", "麻烦您", "麻烦", "请你", "请您", "请帮忙", "请帮我", "请问", "请",
    "帮我", "帮忙", "劳驾", "我想问一下", "我想问", "我想", "想问一下", "想问",
    "问一下", "问下", "可不可以", "能不能", "能否", "可以帮我", "可以", "给我",
    "please", "can you", "could you",
)

# Leading analysis verbs, removed only when falling back to the raw question.
# Longest variants win automatically (the strip loop always takes the longest
# matching prefix), so "分析一下" is consumed before "分析".
_LEADING_VERBS = (
    "分析一下", "分析下", "分析", "看看", "看下", "看一下", "了解一下", "了解",
    "解读一下", "解读", "研究一下", "研究", "评估一下", "评估", "判断一下",
    "判断", "对比一下", "对比", "介绍一下", "介绍", "推荐一下", "推荐",
    "查一下", "查查", "聊聊", "说说", "跟踪",
)

_TRAILING_PUNCTUATION = "。！？!?.,，;；:：、 \t\r\n"
_QUESTION_TAILS = ("吗", "呢", "啊", "吧", "呀", "哈")

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

# Longest keywords first so "均线金叉" wins over "金叉" and "波浪理论" over
# "波浪".  Labels mirror the built-in strategy display names in strategies/*.yaml.
_SKILL_KEYWORDS = (
    ("一阳夹三阴", "一阳夹三阴"),
    ("均线金叉", "均线金叉"),
    ("缩量回踩", "缩量回踩"),
    ("放量突破", "放量突破"),
    ("底部放量", "底部放量"),
    ("波浪理论", "波浪理论"),
    ("箱体震荡", "箱体震荡"),
    ("情绪周期", "情绪周期"),
    ("热点题材", "热点题材"),
    ("事件驱动", "事件驱动"),
    ("预期重估", "预期重估"),
    ("成长质量", "成长质量"),
    ("龙头策略", "龙头策略"),
    ("缠论", "缠论"),
    ("趋势分析", "趋势"),
    ("价值投资", "价值投资"),
    ("风险评估", "风险评估"),
    ("基本面", "基本面"),
    ("技术面", "技术面"),
    ("金叉", "金叉"),
    ("箱体", "箱体"),
    ("热点", "热点"),
    ("放量", "放量"),
    ("缩量", "缩量"),
    ("趋势", "趋势"),
    ("情绪", "情绪"),
    ("量化", "量化"),
    ("均线", "均线"),
    ("波浪", "波浪"),
)

# Ordinary questions rarely name a strategy, but they still have a clear topic.
# Longest/most specific first; report variants all fold into "财报".
_TOPIC_KEYWORDS = (
    ("操作建议", "操作建议"),
    ("基本面", "基本面"),
    ("技术面", "技术面"),
    ("三季报", "财报"),
    ("半年报", "财报"),
    ("季报", "财报"),
    ("年报", "财报"),
    ("中报", "财报"),
    ("财报", "财报"),
    ("业绩", "业绩"),
    ("走势", "走势"),
    ("趋势", "趋势"),
    ("估值", "估值"),
    ("回撤", "回撤"),
    ("风险", "风险"),
    ("止损", "止损"),
    ("止盈", "止盈"),
    ("建仓", "建仓"),
    ("加仓", "加仓"),
    ("减仓", "减仓"),
    ("清仓", "清仓"),
    ("买入", "买入"),
    ("卖出", "卖出"),
    ("持有", "持有"),
    ("对比", "对比"),
    ("复盘", "复盘"),
    ("利好", "利好"),
    ("利空", "利空"),
    ("板块", "板块"),
    ("次新", "次新"),
    ("抄底", "抄底"),
    ("追高", "追高"),
    ("解套", "解套"),
    ("分红", "分红"),
    ("回购", "回购"),
    ("目标价", "目标价"),
)

# Topics that read fine inline in a short yes/no question ("茅台现在适合买入")
# and therefore do not force the "target · topic" rewrite on short questions.
_SIMPLE_ACTION_TOPICS = frozenset({
    "买入", "卖出", "建仓", "加仓", "减仓", "清仓", "持有",
    "抄底", "追高", "止盈", "止损", "解套", "上车",
})

# Words that terminate a leading stock name (or a target right after a verb).
# They cover topic/strategy keywords plus time / action / question markers.
# Single-character risky markers ("能", "会", "该", "的") are only used after an
# explicit verb, never when scanning a bare leading name.
_ALL_BOUNDARY_WORDS = tuple(sorted(set(
    tuple(keyword for keyword, _ in _SKILL_KEYWORDS)
    + tuple(keyword for keyword, _ in _TOPIC_KEYWORDS)
    + (
        "现在", "最近", "未来", "近期", "目前", "当前", "接下来", "后面",
        "适合", "是否", "可以", "能不能", "要不要", "该不该",
        "哪个", "哪只", "哪一", "什么", "怎么", "怎样", "怎么样", "如何",
        "何时", "什么时候", "为啥", "为什么", "值得", "更值得", "划算",
        "会涨", "会跌", "会不会", "还能", "还能不能",
        "的", "能", "会", "该",
        "吗", "呢", "啊", "吧", "呀", "、",
        # price-movement descriptions end a name too
        "跌了这么多", "涨了这么多", "跌了", "涨了", "跌", "涨",
        # two-way comparison connectors also terminate a name
        "二选一", "对比", "比较", "和", "与", "跟", "vs", "VS",
    )
), key=len, reverse=True))

# Markers that are unsafe for bare leading-name scanning: they frequently occur
# inside real names (中国能源) or are too generic ("的").
_LEADING_NAME_UNSAFE = frozenset({"的", "能", "会", "该"})

# Words that are never a stock name and therefore invalidate a comparison side.
_NON_STOCK_WORDS = frozenset(
    {label for _, label in _SKILL_KEYWORDS}
    | {label for _, label in _TOPIC_KEYWORDS}
    | {
        "股票", "基金", "债券", "房价", "房子", "黄金", "原油", "外汇",
        "比特币", "币圈", "理财", "存款", "保险",
    }
)

_CLAUSE_SEPARATORS = "，,；;。!?！？、"

_VERB_PATTERN = r"(?:分析|看看|看下|看一下|研究|判断|评估|解读|跟踪|聊聊|说说|查查)"
_VERB_BOUNDARY = (
    "(?=" + "|".join(re.escape(w) for w in _ALL_BOUNDARY_WORDS)
    + r"|[，。！？!?；;]|$)"
)
_AFTER_VERB_RE = re.compile(
    _VERB_PATTERN + r"(?:一下|下)?\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9]{1,9}?)"
    + _VERB_BOUNDARY
)
_BEFORE_ACTION_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,6}?)"
    r"(?:跌了这么多|涨了这么多|跌了|涨了|已经|目前|现在|最近|未来|近期)?"
    r"(?:还能不能|能不能|还能|适合|能|可以|该|要不要|是否)"
    r"(?:买入|卖出|建仓|加仓|减仓|持有|清仓|抄底|追高|上车|入手|止盈|止损)"
)
_STOCK_CODE_RE = re.compile(
    r"(?<![0-9A-Za-z])((?:SH|SZ|BJ|HK|US)?\d{5,6})(?![0-9])",
    re.IGNORECASE,
)

# Two-way comparison connectors.  Strong ones unambiguously mean "compare";
# weak ones ("和", "与", "跟", "、") also require short, stock-like names.
_STRONG_COMPARE_CONNECTORS = ("二选一", "对比", "比较", " vs ", "vs", "VS")
_WEAK_COMPARE_CONNECTORS = ("和", "与", "跟", "、")


def _strip_prefixes(text: str, prefixes) -> str:
    """Strip the longest matching leading prefix, repeatedly."""
    while True:
        lowered = text.lower()
        match = None
        for prefix in prefixes:
            if lowered.startswith(prefix) and (match is None or len(prefix) > len(match)):
                match = prefix
        if match is None:
            return text
        text = text[len(match):].lstrip("，,、:： ")


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


def _leading_name(text: str) -> Optional[str]:
    """Extract a leading stock name (optionally with a market prefix)."""
    text = text.strip()
    market = ""
    match = re.match(r"^(美股|港股|A股)", text)
    if match:
        market = match.group(1)
        text = text[match.end():]

    cut: Optional[int] = None
    for word in _ALL_BOUNDARY_WORDS:
        if word in _LEADING_NAME_UNSAFE:
            continue
        position = text.find(word)
        if position >= 2 and (cut is None or position < cut):
            cut = position

    if cut is None:
        run = re.match(r"[\u4e00-\u9fa5]{2,6}", text)
        if not run:
            return None
        name = run.group(0)
    else:
        name = text[:cut]

    name = name.rstrip("公司")
    if not (2 <= len(name) <= 6) or not re.fullmatch(r"[\u4e00-\u9fa5]{2,6}", name):
        return None
    return market + name


def _detect_target(text: str) -> Optional[str]:
    code = _STOCK_CODE_RE.search(text)
    if code:
        return code.group(1).upper()

    candidates: list[tuple[int, str]] = []
    after = _AFTER_VERB_RE.search(text)
    if after:
        target = re.sub(r"^(?:一下|下|这只|那个|这个)", "", after.group(1))
        target = target.rstrip("公司")
        if 2 <= len(target) <= 10:
            candidates.append((after.start(1), target))

    before = _BEFORE_ACTION_RE.search(text)
    if before:
        name = before.group(1).rstrip("公司")
        # Reject fragments that grabbed a modal/description word instead of a
        # name (e.g. "还能不" out of "还能不能追高"); `_leading_name` below then
        # recovers the real name from the start of the text.
        if not any(word in name for word in _ALL_BOUNDARY_WORDS if len(word) >= 2):
            candidates.append((before.start(1), name))

    if candidates:
        return min(candidates, key=lambda item: item[0])[1]

    return _leading_name(text)


def _detect_compare(text: str) -> Optional[Tuple[str, str]]:
    connectors = tuple((c, True) for c in _STRONG_COMPARE_CONNECTORS) + tuple(
        (c, False) for c in _WEAK_COMPARE_CONNECTORS
    )
    for connector, strong in connectors:
        index = text.find(connector)
        while index > 0:
            left = _leading_name(text[:index])
            right = _leading_name(text[index + len(connector):])
            if left and right and left != right:
                if left in _NON_STOCK_WORDS or right in _NON_STOCK_WORDS:
                    index = text.find(connector, index + 1)
                    continue
                if strong or (len(left) <= 4 and len(right) <= 4):
                    return left, right
            index = text.find(connector, index + 1)
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

    cleaned = _strip_question_tail(_strip_prefixes(text, _POLITE_PREFIXES)) or text
    no_verb = _strip_prefixes(cleaned, _LEADING_VERBS)

    skill = _detect_skill(cleaned)
    target = _detect_target(cleaned)
    rambling = len(cleaned) > 18 or any(sep in cleaned for sep in _CLAUSE_SEPARATORS)
    topic = _detect_topic(cleaned) if rambling else None
    if topic is None and target is not None:
        # A noun topic right after a target reads better as "target · topic"
        # even on a short question ("苹果公司财报什么时候出" -> "苹果 · 财报"),
        # but simple action topics keep their readable short form.
        detected = _detect_topic(cleaned)
        if detected and detected not in _SIMPLE_ACTION_TOPICS:
            topic = detected
    label = skill or topic

    compare = _detect_compare(no_verb)
    if compare:
        title = f"{compare[0]} vs {compare[1]}"
    elif label and target:
        title = f"{target} · {label}"
    elif target:
        # A target was found but no clear label: keep the question and just drop
        # the leading verb ("帮我分析一下贵州茅台" -> "贵州茅台").
        title = no_verb
    elif label:
        title = label
    else:
        title = no_verb or cleaned

    if len(title) > MAX_TITLE_LENGTH and not (label and target):
        title = _first_clause(title)

    return _compact(title) or EMPTY_TITLE
