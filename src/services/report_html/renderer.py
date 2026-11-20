# -*- coding: utf-8 -*-
"""飞书推送同源的报告 HTML 渲染器。

数据流（与飞书卡片共用同一份解析，保证信息一致）：

    Markdown -> format_feishu_markdown -> parse_report_document -> 本渲染器

安全策略：
- Jinja2 自动转义开启；正文行内片段先 html.escape 再按白名单还原少量标记；
- 只识别 ``**粗体**``、``*斜体*``、```代码``` 与 ``<font color=...>``；
- 其余标签一律当文本；URL 仅允许 http/https，禁止 javascript: 等协议；
- 输出零脚本、零外部资源（CSS 内联），可离线打开。

异常策略：渲染失败抛 :class:`ReportHtmlRenderError`，由调用方转成 500，
不做静默降级（避免用 fallback 掩盖契约漂移）。
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.formatters import format_feishu_markdown
from src.notification_sender.feishu_card import (
    parse_field_row,
    parse_report_document,
    parse_stock_summary,
    plan_field_grid,
    section_tone,
)
from src.report_language import normalize_report_language

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STYLES_PATH = Path(__file__).resolve().parent / "styles.css"

_ALLOWED_FONT_COLORS = {
    "red", "green", "grey", "gray", "orange", "blue", "yellow",
    "purple", "indigo", "turquoise", "wathet",
}

# <font color='grey'>💬 ...</font> 与 「💡 ...」 的引用识别
# html.escape(quote=True) 会把 ' 转成 &#x27;、" 转成 &quot;，正则需同时接受字面引号与实体。
_QUOTE_ENTITY = r"(?:&#x27;|&quot;|\'|\")"
_FONT_TAG_RE = re.compile(
    r"&lt;font\s+color\s*=\s*" + _QUOTE_ENTITY + r"?([a-z]+)" + _QUOTE_ENTITY
    + r"?\s*&gt;(.*?)&lt;/font&gt;"
)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+?)`")
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")
# 摘要指标：🟢N 🟡N 🔴N / ⚪N 🟠N 🔵N
_KPI_RE = re.compile(r"([🟢🟡🔴⚪🟠🔵])\s*(\d+)")

_CODE_FENCE_RE = re.compile(r"^```(\w*)\s*$")


class ReportHtmlRenderError(RuntimeError):
    """报告 HTML 渲染失败。"""


def _safe_inline(text: str) -> str:
    """转义后按白名单还原少量行内标记，返回可安全输出的 HTML 片段。"""
    if not text:
        return ""
    escaped = html.escape(str(text), quote=True)

    # <font color=...> 只允许白名单颜色，其余丢弃标签留文字。
    def repl_font(match: re.Match[str]) -> str:
        color = match.group(1).lower()
        inner = match.group(2)
        if color == "gray":
            color = "grey"
        if color in _ALLOWED_FONT_COLORS:
            return f'<span class="tone-{color}">{inner}</span>'
        return inner

    escaped = _FONT_TAG_RE.sub(repl_font, escaped)

    # 链接：仅把 http/https 文本渲染成安全链接，先占位避免后续标记误伤。
    links: List[str] = []

    def repl_url(match: re.Match[str]) -> str:
        url = match.group(0)
        token = f"\x00LINK{len(links)}\x00"
        links.append(url)
        return token

    escaped = _URL_RE.sub(repl_url, escaped)

    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)

    for index, url in enumerate(links):
        escaped = escaped.replace(
            f"\x00LINK{index}\x00",
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer nofollow">{html.escape(url)}</a>',
        )
    return escaped


def _extract_kpis(summary: str) -> List[str]:
    return [f"{emoji} {count}" for emoji, count in _KPI_RE.findall(summary or "")]


def _is_summary_line(text: str) -> bool:
    return text.lstrip().startswith("💡")


def _block_tone(title: str) -> str:
    tone = section_tone(title)
    return tone if tone in _ALLOWED_FONT_COLORS or tone in {"wathet"} else "wathet"


def _build_items(lines: List[str], *, summary_mode: bool) -> List[Dict[str, Any]]:
    """把章节正文行转换为 view model 条目。

    字段行（• 键：值 | 键：值）、个股摘要行、引用行、代码块、三级标题
    分别处理，其余按普通段落。字段网格复用 feishu_card 的对齐规则。
    """
    items: List[Dict[str, Any]] = []
    field_buffer: List[Any] = []
    paragraph: List[str] = []
    in_code = False
    code_lines: List[str] = []
    code_lang = ""

    def flush_fields() -> None:
        if not field_buffer:
            return
        grid = plan_field_grid(
            list(field_buffer),
            force_full=summary_mode and len(field_buffer) <= 1,
            short_limit=36 if summary_mode else 24,
            short_key_limit=26 if summary_mode else 12,
        )
        # render_inline 用 |safe 输出，这里必须先转义，避免字段值注入。
        items.append({
            "kind": "fields",
            "fields": [
                {"key": _safe_inline(f["key"]), "value": _safe_inline(f["value"]), "full": f["full"]}
                for f in grid
            ],
        })
        field_buffer.clear()

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
        if text:
            items.append({"kind": "paragraph", "text": _safe_inline(text)})
        paragraph.clear()

    for raw in lines:
        stripped = raw.strip()

        fence = _CODE_FENCE_RE.match(stripped)
        if fence or stripped.startswith("```"):
            if in_code:
                items.append({"kind": "code", "text": "\n".join(code_lines)})
                in_code = False
                code_lines = []
                code_lang = ""
            else:
                flush_fields()
                flush_paragraph()
                in_code = True
                code_lang = fence.group(1) if fence else ""
            continue
        if in_code:
            code_lines.append(raw.rstrip())
            continue

        if not stripped:
            flush_fields()
            flush_paragraph()
            continue

        # 三级标题（format_feishu_markdown 用「· **标题**」标记）
        heading = re.match(r"^·\s*\*\*(.+?)\*\*\s*$", stripped)
        if heading:
            flush_fields()
            flush_paragraph()
            title = heading.group(1).strip()
            tone = "red" if section_tone(title) == "red" else (
                "green" if section_tone(title) == "green" else ""
            )
            items.append({"kind": "heading3", "text": _safe_inline(title), "tone": tone})
            continue

        # 字段行优先识别（parse_field_row 要求保留行首「• 」）。
        cells = parse_field_row(stripped)
        if cells:
            flush_paragraph()
            field_buffer.extend(cells)
            continue

        # 其余列表项去掉装饰前缀后按正文处理。
        content = re.sub(r"^[•\-]\s*", "", stripped)

        if summary_mode:
            stock = parse_stock_summary(content)
            if stock:
                flush_paragraph()
                field_buffer.append((stock[0], stock[1]))
                continue

        # 引用 / 决策高亮
        if content.startswith("<font") or _is_summary_line(content):
            flush_fields()
            flush_paragraph()
            rendered = _safe_inline(content)
            kind = "note" if _is_summary_line(content) else "quote"
            items.append({"kind": kind, "text": rendered})
            continue

        paragraph.append(content)

    if in_code and code_lines:
        items.append({"kind": "code", "text": "\n".join(code_lines)})
    flush_fields()
    flush_paragraph()
    return items


def _merge_preamble(preamble: List[str]) -> List[Dict[str, Any]]:
    """标题与首个板块之间的元信息（市场状态等）作为引用呈现。"""
    items: List[Dict[str, Any]] = []
    for line in preamble:
        stripped = line.strip()
        if not stripped:
            continue
        items.append({"kind": "quote", "text": _safe_inline(stripped)})
    return items


def render_report_html(
    markdown: str,
    *,
    report_language: str = "zh",
    page_title: Optional[str] = None,
    generated_at: Optional[str] = None,
    include_disclaimer: bool = True,
) -> str:
    """把报告 Markdown 渲染为自包含、可直接离线打开的 HTML 字符串。

    Args:
        markdown: 与推送同源的 Markdown 报告正文。
        report_language: zh | en | ko，经 normalize_report_language 归一化。
        page_title: <title> 文本，缺省取文档标题或通用标题。
        generated_at: 页脚生成时间，渲染器内部不取当前时间，保证确定性。
        include_disclaimer: 是否与报告路由推送一致补齐 AI 生成声明。

    Returns:
        完整 HTML 文档字符串。

    Raises:
        ReportHtmlRenderError: 模板缺失或渲染失败。
    """
    lang = normalize_report_language(report_language)
    try:
        lark_text = format_feishu_markdown(markdown or "")
        doc = parse_report_document(lark_text, include_disclaimer=include_disclaimer)

        blocks: List[Dict[str, Any]] = []
        preamble_items = _merge_preamble(doc.preamble)
        if preamble_items:
            blocks.append({"title": "", "tone": "wathet", "items": preamble_items})

        for section in doc.sections:
            if not section.has_content():
                continue
            blocks.append({
                "title": section.title,
                "tone": _block_tone(section.title),
                "items": _build_items(
                    section.lines,
                    summary_mode=any(kw in section.title for kw in ("摘要", "Summary", "概览", "Overview", "今日结论")),
                ),
            })

        # 没有解析出任何板块时，整篇作为单段展示，保证信息不丢。
        if not blocks:
            lines = [line for line in lark_text.splitlines() if line.strip()]
            blocks.append({
                "title": "",
                "tone": "wathet",
                "items": _build_items(lines, summary_mode=False),
            })

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(default_for_string=True, default=True),
            keep_trailing_newline=True,
        )
        env.globals["render_item"] = env.get_template("_components.html.j2").module.render_item
        template = env.get_template("report.html.j2")
        styles = _STYLES_PATH.read_text(encoding="utf-8")
        return template.render(
            doc=doc,
            blocks=blocks,
            kpis=_extract_kpis(doc.summary),
            lang=lang,
            page_title=page_title or doc.title or "分析报告",
            generated_at=generated_at or "",
            styles=styles,
        )
    except ReportHtmlRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成显式异常，由接口层决定响应
        logger.exception("Render report HTML failed")
        raise ReportHtmlRenderError("生成 HTML 报告失败") from exc
