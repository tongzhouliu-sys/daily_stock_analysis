# -*- coding: utf-8 -*-
"""Chinese text-variant generation for the HTML report (T78).

The HTML report is fixed to Chinese. Historical records may only contain English
or Korean model output, so this module produces a Chinese overlay for the known
natural-language fields (or a Chinese Markdown body for market reviews). The
result is persisted by :class:`src.services.history_service.HistoryService` and
reused on every later read.

The translator is injectable so tests and local tooling never need a network
call. The default implementation reuses the configured generation backend.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import get_config

logger = logging.getLogger(__name__)

TARGET_LANGUAGE = "zh"
VARIANT_KIND_SINGLE_STOCK = "single_stock_overlay"
VARIANT_KIND_MARKET_REVIEW = "market_review_markdown"

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_CHARS = 200_000
_MAX_BATCH_CHARS = 6_000

# Keys that are identifiers, enums, numbers or code-like and must never be
# translated. They are still displayed, but the surrounding localizers handle
# their labels.
_SKIP_FIELD_KEYS = {
    "code",
    "stock_code",
    "stock_name",
    "name",
    "canonical_id",
    "query_id",
    "report_language",
    "report_type",
    "model_used",
    "model",
    "backend",
    "provider",
    "action",
    "decision_type",
    "signal_type",
    "confidence_level",
    "trend_prediction",
    "operation_advice",
    "action_label",
    "data_sources",
    "source",
    "sources",
    "url",
    "href",
    "link",
    "risk_level",
    "success",
    "error_message",
    "search_performed",
    "news_result_count",
    "news_result_count_known",
    "news_evidence_present",
    "raw_response",
    "market_review_report",
    "news_content",
    "context_snapshot",
    "structured_payload",
    "created_at",
    "updated_at",
    "record_id",
    "id",
    "hash",
    "source_hash",
}

_SKIP_KEY_SUFFIXES = ("_code", "_id", "_url", "_href", "_at", "_hash")

# Small deterministic glossary for single-word structural values that the
# report renderer does not already localize.
_SINGLE_WORD_GLOSSARY = {
    "bullish": "看多",
    "bearish": "看空",
    "sideways": "震荡",
    "neutral": "中性",
    "strong bullish": "强烈看多",
    "strong bearish": "强烈看空",
    "buy": "买入",
    "hold": "持有",
    "sell": "卖出",
    "watch": "观望",
    "reduce": "减仓",
    "avoid": "回避",
    "alert": "警惕",
    "strong buy": "强烈买入",
    "strong sell": "强烈卖出",
    "uptrend": "上行趋势",
    "downtrend": "下行趋势",
    "healthy": "健康",
    "average": "一般",
    "caution": "警惕",
    "safe": "安全",
    "danger": "危险",
    "high": "高",
    "medium": "中",
    "low": "低",
    "none": "无",
    "yes": "是",
    "no": "否",
    "this week": "本周内",
    "today": "今日内",
    "immediately": "立即行动",
    "not urgent": "不急",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_URL_RE = re.compile(r"^https?://\S+$")
_CODE_LIKE_RE = re.compile(r"^[A-Za-z0-9_./:%+\- ]+$")


class ReportTranslationError(RuntimeError):
    """A Chinese variant could not be generated."""

    def __init__(self, message: str, *, error_code: str = "translation_failed") -> None:
        super().__init__(message)
        self.error_code = error_code


Translator = Callable[[Dict[str, str], int], Tuple[Dict[str, str], str, str]]
MarkdownTranslator = Callable[[str, int], Tuple[str, str, str]]


def _is_skipped_key(key: str) -> bool:
    lowered = (key or "").strip().lower()
    if not lowered:
        return True
    if lowered in _SKIP_FIELD_KEYS:
        return True
    return lowered.endswith(_SKIP_KEY_SUFFIXES)


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _needs_translation(text: Any) -> bool:
    """Heuristic: does this value contain English/Korean prose to translate?"""
    if not isinstance(text, str):
        return False
    value = text.strip()
    if len(value) < 2:
        return False
    if _has_cjk(value):
        # Mixed Chinese/English labels are handled by the renderer; do not
        # translate a value that is already predominantly Chinese.
        cjk_count = len(_CJK_RE.findall(value))
        if cjk_count >= max(2, len(value) // 8):
            return False
    if _URL_RE.match(value):
        return False
    if _HANGUL_RE.search(value):
        return True
    words = _ASCII_WORD_RE.findall(value)
    if not words:
        return False
    if value.lower() in _SINGLE_WORD_GLOSSARY:
        return True
    if len(words) == 1 and _CODE_LIKE_RE.match(value) and not value.endswith((".", "!", "?")):
        # Indicator / enum values such as RSI, MACD, AAPL.
        return False
    return True


def _walk_translatable(value: Any, path: str, out: Dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_skipped_key(str(key)):
                continue
            child_path = f"{path}.{key}" if path else str(key)
            _walk_translatable(item, child_path, out)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_translatable(item, f"{path}[{index}]", out)
        return
    if _needs_translation(value):
        out[path] = str(value).strip()


def collect_translatable_texts(raw_result: Dict[str, Any]) -> Dict[str, str]:
    """Return ``{path: text}`` for natural-language values needing Chinese."""
    overlay: Dict[str, str] = {}
    _walk_translatable(raw_result or {}, "", overlay)
    return overlay


def _get_path(payload: Any, path: str) -> Any:
    current = payload
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            current = current[int(part[1:-1])]
        else:
            current = current[part]
    return current


def _set_path(payload: Any, path: str, value: str) -> None:
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    current = payload
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if part.startswith("["):
            current = current[int(part[1:-1])]
        elif next_part.startswith("["):
            current = current[part]
        else:
            current = current[part]
    last = parts[-1]
    if last.startswith("["):
        current[int(last[1:-1])] = value
    else:
        current[last] = value


def apply_overlay(raw_result: Dict[str, Any], overlay: Dict[str, str]) -> Dict[str, Any]:
    """Return a deep-copied raw_result with translated fields applied."""
    result = copy.deepcopy(raw_result or {})
    for path, value in (overlay or {}).items():
        try:
            _set_path(result, path, value)
        except (KeyError, IndexError, TypeError):
            logger.warning("Skip invalid translation overlay path: %s", path)
    return result


def source_hash_for_record(raw_result: Dict[str, Any], market_markdown: Optional[str] = None) -> str:
    """Stable hash of the source content used by a variant."""
    payload = {
        "raw_result": raw_result or {},
        "market_markdown": market_markdown or "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_json_payload(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _default_translator(texts: Dict[str, str], timeout: int) -> Tuple[Dict[str, str], str, str]:
    """Translate ``{path: text}`` using the configured generation backend."""
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer()
    system_prompt = (
        "你是金融研报的简体中文译者。只翻译自然语言，不添加、删减或改写投资结论。"
        "保留代码、数字、百分比、URL、公司专名与技术指标缩写（如 RSI、MACD、MA5、ROE）。"
        "输出必须是 JSON，不得输出 Markdown 代码块或解释。"
    )
    payload = {"items": [{"path": path, "text": text} for path, text in texts.items()]}
    prompt = (
        "把下面 JSON 中每个 items[].text 翻译成简体中文，保持 path 不变，"
        "返回同样的 JSON 结构。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw, model, _usage = analyzer._call_litellm(
        prompt,
        {"temperature": 0.0, "max_tokens": 8192, "timeout": timeout},
        system_prompt=system_prompt,
    )
    data = _parse_json_payload(raw)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items")
    else:
        items = None
    if not isinstance(items, list):
        raise ReportTranslationError("translation response missing items", error_code="translation_invalid_response")
    translated: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        text = item.get("text")
        if path and isinstance(text, str):
            translated[path] = text.strip()
    missing = [path for path in texts if path not in translated]
    if missing:
        raise ReportTranslationError(
            f"translation response missing {len(missing)} fields",
            error_code="translation_incomplete",
        )
    return translated, "litellm", model or ""


def _default_markdown_translator(markdown: str, timeout: int) -> Tuple[str, str, str]:
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer()
    system_prompt = (
        "你是金融研报的简体中文译者。把用户提供的 Markdown 完整翻译成简体中文，"
        "保留原有标题层级、表格、列表、代码块、链接、数字与专名，不添加投资建议。"
        "只输出翻译后的 Markdown 正文。"
    )
    raw, model, _usage = analyzer._call_litellm(
        markdown,
        {"temperature": 0.0, "max_tokens": 16384, "timeout": timeout},
        system_prompt=system_prompt,
    )
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned:
        raise ReportTranslationError("empty markdown translation", error_code="translation_empty")
    return cleaned, "litellm", model or ""


class ReportTranslationService:
    """Generate Chinese overlays / market-review Markdown."""

    def __init__(
        self,
        *,
        translator: Optional[Translator] = None,
        markdown_translator: Optional[MarkdownTranslator] = None,
        timeout_seconds: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> None:
        self._translator = translator or _default_translator
        self._markdown_translator = markdown_translator or _default_markdown_translator
        config = get_config()
        self._timeout = int(timeout_seconds or getattr(config, "report_zh_variant_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._max_chars = int(max_chars or getattr(config, "report_zh_variant_max_chars", DEFAULT_MAX_CHARS))

    def translate_overlay(self, texts: Dict[str, str]) -> Tuple[Dict[str, str], str, str]:
        if not texts:
            return {}, "", ""
        total = sum(len(value) for value in texts.values())
        if total > self._max_chars:
            raise ReportTranslationError(
                f"text exceeds translation limit ({total} > {self._max_chars})",
                error_code="translation_too_large",
            )

        # Deterministic glossary first, then batch the rest.
        translated: Dict[str, str] = {}
        pending: Dict[str, str] = {}
        for path, text in texts.items():
            gloss = _SINGLE_WORD_GLOSSARY.get(text.strip().lower())
            if gloss:
                translated[path] = gloss
            else:
                pending[path] = text
        if not pending:
            return translated, "glossary", ""

        batches = _split_text_batches(pending, _MAX_BATCH_CHARS)
        backend = ""
        model = ""
        for batch in batches:
            batch_translated, backend, model = self._translator(batch, self._timeout)
            translated.update(batch_translated)
        untranslated = [
            path
            for path, text in translated.items()
            if not _has_cjk(text) and len(_ASCII_WORD_RE.findall(text)) >= 3
        ]
        if untranslated:
            raise ReportTranslationError(
                f"translation still contains English prose for {len(untranslated)} fields",
                error_code="translation_incomplete",
            )
        return translated, backend, model

    def translate_markdown(self, markdown: str) -> Tuple[str, str, str]:
        if not markdown.strip():
            return "", "", ""
        if len(markdown) > self._max_chars:
            raise ReportTranslationError(
                f"markdown exceeds translation limit ({len(markdown)} > {self._max_chars})",
                error_code="translation_too_large",
            )
        backend = ""
        model = ""
        chunks = _split_markdown_chunks(markdown, _MAX_BATCH_CHARS)
        translated_chunks: List[str] = []
        for chunk in chunks:
            translated, backend, model = self._markdown_translator(chunk, self._timeout)
            translated_chunks.append(translated)
        joined = "\n\n".join(part for part in translated_chunks if part.strip())
        if joined and not _has_cjk(joined) and len(_ASCII_WORD_RE.findall(joined)) >= 3:
            raise ReportTranslationError(
                "markdown translation still contains English prose",
                error_code="translation_incomplete",
            )
        return joined, backend, model


def _split_text_batches(texts: Dict[str, str], limit: int) -> List[Dict[str, str]]:
    batches: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    size = 0
    for path, text in texts.items():
        if current and size + len(text) > limit:
            batches.append(current)
            current = {}
            size = 0
        current[path] = text
        size += len(text)
    if current:
        batches.append(current)
    return batches


def _split_markdown_chunks(markdown: str, limit: int) -> List[str]:
    if len(markdown) <= limit:
        return [markdown]
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for line in markdown.splitlines(keepends=True):
        if current and size + len(line) > limit:
            chunks.append("".join(current))
            current = []
            size = 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks
