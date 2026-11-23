# -*- coding: utf-8 -*-
"""Agent 运行期实际消费的新闻证据。

empty-news disclosure 必须反映「本次分析真正用到的新闻证据」。Agent 模式下情报
由 Agent 自己调用搜索工具取得，因此计数只能来自这些工具的真实返回，不能用分析
结束后补打的一次 `search_stock_news()` 代替：那次补查与 Agent 消费的证据无关，
两个方向都会失真——Agent 明明用了新闻却因补查失败而被标成「未纳入新闻面证据」，
或 Agent 没拿到新闻却因补查有结果而被错误地不提示。

除计数外，本模块还保留本次工具返回的新闻条目（标题/摘要/来源），供分析结束后
回填 context pack 的新闻块、质量分与 `analysis_history.news_content`。没有条目时
只保留计数，行为与历史版本一致。

用法：pipeline 在 `executor.run()` 前后开启并读取作用域；搜索工具在返回结果时
记录本次真正交给 Agent 的条数。工具在 ThreadPoolExecutor 中执行，
`src/agent/runner.py` 通过 `contextvars.copy_context()` 提交任务，因此 ContextVar
中的**可变**累加器在工作线程与父线程之间是同一个对象，工具线程里的累加对
pipeline 可见。请勿把它换成保存不可变值的 ContextVar，那样父线程读不到。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SUMMARY_HEADER = "【本次分析检索到的新闻情报】"
_SNIPPET_LIMIT = 200


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_item(item: Any) -> Optional[Dict[str, str]]:
    """把工具返回的新闻结果收敛成可持久化的纯文本字段。"""
    if isinstance(item, Mapping):
        getter = item.get
    else:
        getter = lambda name: getattr(item, name, None)  # noqa: E731 - small local adapter

    title = _text(getter("title"))
    if not title:
        return None
    return {
        "title": title,
        "snippet": _text(getter("snippet")),
        "url": _text(getter("url")),
        "source": _text(getter("source")),
        "published_date": _text(getter("published_date")),
        "dimension": _text(getter("dimension")),
    }


def _normalize_items(items: Optional[Iterable[Any]]) -> List[Dict[str, str]]:
    if not items:
        return []
    normalized: List[Dict[str, str]] = []
    for item in items:
        value = _normalize_item(item)
        if value is not None:
            normalized.append(value)
    return normalized


def _dedupe_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result: List[Dict[str, str]] = []
    for item in items:
        key = (
            item.get("title", ""),
            item.get("source", ""),
            item.get("published_date", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def format_news_evidence_summary(
    items: Iterable[Any],
    *,
    max_items: int = 20,
) -> str:
    """把 Agent 本次实际拿到的新闻条目格式化成可持久化的摘要文本。"""
    normalized = _dedupe_items(_normalize_items(items))[: max(0, int(max_items))]
    if not normalized:
        return ""

    lines = [_SUMMARY_HEADER]
    for index, item in enumerate(normalized, 1):
        date_text = f" [{item['published_date']}]" if item["published_date"] else ""
        source_text = f"[{item['source']}] " if item["source"] else ""
        lines.append(f"{index}. {source_text}{item['title']}{date_text}")
        snippet = item["snippet"]
        if snippet:
            lines.append(f"   {snippet[:_SNIPPET_LIMIT]}")
        if item["url"]:
            lines.append(f"   来源：{item['url']}")
    return "\n".join(lines)


class NewsEvidenceAccumulator:
    """线程安全地累计 Agent 本次分析实际消费的新闻条数与条目。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._items: List[Dict[str, str]] = []

    def record(self, count: int, items: Optional[Iterable[Any]] = None) -> None:
        """记录一次搜索工具真实返回的条数与条目；零命中也必须记录（记 0）。"""
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 0
        if value < 0:
            value = 0
        normalized = _normalize_items(items)
        with self._lock:
            self._total += value
            self._items.extend(normalized)

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    @property
    def items(self) -> List[Dict[str, str]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def sources(self, *, limit: int = 3) -> List[str]:
        """返回本次新闻条目里去重后的来源站点，供输入数据块登记来源。"""
        seen: List[str] = []
        for item in self.items:
            source = item.get("source", "")
            if source and source not in seen:
                seen.append(source)
                if len(seen) >= max(1, int(limit)):
                    break
        return seen

    def source_label(self) -> str:
        """返回可直接写入 context pack 的新闻来源标签。"""
        sources = self.sources()
        if sources:
            return "agent:search_tools (" + ", ".join(sources) + ")"
        return "agent:search_tools"

    def summary(self, *, max_items: int = 20) -> str:
        """返回本次 Agent 消费的新闻摘要；没有条目时返回空串。"""
        return format_news_evidence_summary(self.items, max_items=max_items)

    def resolve(self, *, search_available: bool) -> Optional[int]:
        """收敛成 `news_result_count` 的三态语义。

        - 搜索渠道不可用：`None`，即未执行检索，披露「未配置搜索渠道」。
        - 渠道可用：从 `0` 起步，Agent 调用工具拿到多少算多少。

        渠道可用但 Agent 一次都没搜的情况刻意归入 `0` 而不是 `None`：此时报告
        确实没有新闻证据，但「未配置搜索渠道」是与事实相反的解释，而「未获取到
        可用的新闻面数据」在这两种子情形下都成立。
        """
        if not search_available:
            return None
        return self.total


_CURRENT_ACCUMULATOR: ContextVar[Optional[NewsEvidenceAccumulator]] = ContextVar(
    "news_evidence_accumulator",
    default=None,
)


def activate_news_evidence_scope() -> Token:
    """开启一次 Agent 运行的证据作用域，返回重置令牌。"""
    return _CURRENT_ACCUMULATOR.set(NewsEvidenceAccumulator())


def get_current_news_evidence() -> Optional[NewsEvidenceAccumulator]:
    return _CURRENT_ACCUMULATOR.get()


def reset_news_evidence_scope(token: Optional[Token]) -> None:
    if token is None:
        return
    try:
        _CURRENT_ACCUMULATOR.reset(token)
    except Exception as exc:  # pragma: no cover - 防御性 fail-open
        logger.warning("news evidence scope reset failed: %s", exc)


def record_news_evidence(
    count: int,
    items: Optional[Iterable[Any]] = None,
) -> None:
    """供 Agent 搜索工具调用。

    没有活动作用域时静默忽略：非 Agent 路径自己直接维护计数，工具也可能在
    Agent 分析之外被调用（例如报告页的后续资讯检索），那些都不应影响本次分析的
    披露判定。
    """
    accumulator = _CURRENT_ACCUMULATOR.get()
    if accumulator is None:
        return
    accumulator.record(count, items)
