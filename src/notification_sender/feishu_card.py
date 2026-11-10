# -*- coding: utf-8 -*-
"""飞书推送消息卡片（Card JSON 1.0）的结构化渲染。

Webhook 与 App Bot 两条发送链路共用 :func:`build_feishu_card`：把已转换为
``lark_md`` 的报告正文拆成「彩色标题栏 + 章节分节 + 双列字段 + 备注页脚」，
避免长报告全部挤在一个 ``div`` 里。短通知、无法安全结构化的内容、以及超出
字节预算的卡片会自动退化为紧凑单块卡片；发送失败时原链路仍会回退为纯文本。

只使用官方 Card JSON 1.0 组件（header / div / hr / note / fields）与 lark_md
支持的 ``<text_tag>`` 标签，不引入需要高版本客户端才能渲染的组件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CARD_TITLE = "股票智能分析报告"
_CARD_SUBTITLE = "仅供研究参考 · 不构成投资建议"

# 机器人回复、文件内容、简短告警等保持紧凑卡片，避免无意义的分节。
_RICH_CARD_MIN_CHARS = 400
_MAX_SECTIONS = 14
_MAX_ELEMENTS = 42
_MAX_FIELD_BLOCKS = 6
_FIELDS_PER_BLOCK = 6

# format_feishu_markdown 会把 ## / ### 转成「▎ **标题**」「· **标题**」，
# 一级标题保持「**标题**」，这里据此还原章节层级。
_SECTION_TITLE_RE = re.compile(r"^(▎|·)?\s*\*\*(.+?)\*\*\s*$")
_HR_RE = re.compile(r"^\s*[─—]{4,}\s*$")
_META_LINE_RE = re.compile(r"^\*(.+)\*$")
_PAGE_MARKER_RE = re.compile(r"📄\s*(\d+)\s*/\s*(\d+)")
_KEY_VALUE_ROW_RE = re.compile(r"^•\s+(.+)$")
_META_KEYWORDS = ("数据来源", "报告生成时间", "分析模型", "仅供参考", "不构成投资建议")

_SECTION_COLOR_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("风险", "警报", "预警", "⚠", "🚨", "止损", "数据限制"), "red"),
    (("利好", "催化", "✨", "机会"), "green"),
    (("行情", "📈", "📉", "技术", "指标", "数据透视"), "turquoise"),
    (("作战", "计划", "🎯", "仓位", "点位", "护栏"), "indigo"),
    (("结论", "摘要", "📌", "核心", "决策"), "orange"),
    (("新闻", "舆情", "📰", "📢", "板块", "速览"), "blue"),
)


@dataclass
class _Section:
    level: int
    title: str
    lines: List[str] = field(default_factory=list)


def _config() -> Dict[str, Any]:
    return {"wide_screen_mode": True, "enable_forward": True}


def _template_for(content: str) -> str:
    """按报告类型选择标题主题色，只读取开头，避免正文里的风险字样误伤。"""
    head = content[:300]
    if "大盘复盘" in head or "市场复盘" in head:
        return "indigo"
    if "风险预警" in head or "异常告警" in head:
        return "orange"
    return "blue"


def _header(content: str) -> Dict[str, Any]:
    return {
        "template": _template_for(content),
        "title": {"tag": "plain_text", "content": CARD_TITLE},
        "subtitle": {"tag": "plain_text", "content": _CARD_SUBTITLE},
    }


def _color_for_title(title: str) -> str:
    for keywords, color in _SECTION_COLOR_RULES:
        if any(keyword in title for keyword in keywords):
            return color
    return "wathet"


def _title_tag(title: str) -> str:
    safe = title.replace("<", "‹").replace(">", "›").strip()
    return f"<text_tag color='{_color_for_title(title)}'>{safe}</text_tag>"


def _div(content: str) -> Dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note(notes: List[str]) -> Dict[str, Any]:
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": " · ".join(notes)[:180]}],
    }


def _compact_card(content: str, header: Dict[str, Any]) -> Dict[str, Any]:
    """最保守的卡片形态：完整正文放在单个 div，不丢任何信息。"""
    return {
        "config": _config(),
        "header": header,
        "elements": [_div(content)],
    }


def _extract_footer(lines: List[str]) -> Tuple[List[str], List[str]]:
    """把斜体元信息行与分页标记从正文移到页脚备注。"""
    body: List[str] = []
    notes: List[str] = []
    for line in lines:
        stripped = line.strip()
        page = _PAGE_MARKER_RE.search(stripped)
        if page and stripped.startswith("📄"):
            notes.append(f"第 {page.group(1)}/{page.group(2)} 页")
            continue
        meta = _META_LINE_RE.match(stripped)
        if meta and any(keyword in meta.group(1) for keyword in _META_KEYWORDS):
            notes.append(meta.group(1).strip().strip("*"))
            continue
        body.append(line)
    return body, notes


def _parse_sections(lines: List[str]) -> List[_Section]:
    sections: List[_Section] = [_Section(0, "")]
    for line in lines:
        match = _SECTION_TITLE_RE.match(line)
        if match:
            prefix, title = match.group(1), match.group(2).strip()
            level = 1 if prefix is None else (2 if prefix == "▎" else 3)
            if len(sections) >= _MAX_SECTIONS:
                sections[-1].lines.append(line)
                continue
            sections.append(_Section(level, title))
            continue
        # 文本分隔线改由卡片的 hr 组件承担。
        if _HR_RE.match(line):
            if len(sections) < _MAX_SECTIONS:
                sections.append(_Section(0, ""))
            else:
                sections[-1].lines.append(line)
            continue
        sections[-1].lines.append(line)
    return sections


def _parse_field_cells(line: str) -> Optional[List[Tuple[str, str]]]:
    """把「• 键：值 | 键：值」的紧凑表格行解析成双列字段。"""
    match = _KEY_VALUE_ROW_RE.match(line)
    if not match:
        return None
    parts = [part.strip() for part in match.group(1).split(" | ")]
    if not 2 <= len(parts) <= 4:
        return None
    cells: List[Tuple[str, str]] = []
    for part in parts:
        if "：" not in part:
            return None
        key, value = part.split("：", 1)
        key, value = key.strip().strip("*"), value.strip()
        if not key or not value or len(key) > 14 or len(value) > 80:
            return None
        cells.append((key, value))
    return cells


def _fields_element(cells: List[Tuple[str, str]]) -> Dict[str, Any]:
    return {
        "tag": "div",
        "fields": [
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**{key}**\n{value}"},
            }
            for key, value in cells
        ],
    }


def _render_blocks(
    lines: List[str], heading: str, field_budget: int
) -> Tuple[List[Dict[str, Any]], int]:
    """把一个章节渲染为若干 div / fields 元素，并限制双列块数量。"""
    blocks: List[Dict[str, Any]] = []
    text_buffer: List[str] = [heading] if heading else []
    field_buffer: List[Tuple[str, str]] = []
    used_blocks = 0

    def flush_text() -> None:
        nonlocal text_buffer
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(text_buffer).strip("\n"))
        if text.strip():
            blocks.append(_div(text))
        text_buffer = []

    def flush_fields() -> None:
        nonlocal field_buffer, used_blocks
        if not field_buffer:
            return
        if used_blocks >= field_budget:
            # 预算用尽时把字段还原成普通文本，保证信息不丢。
            restored = " | ".join(f"{key}：{value}" for key, value in field_buffer)
            text_buffer.append(f"• {restored}")
        else:
            blocks.append(_fields_element(field_buffer))
            used_blocks += 1
        field_buffer = []

    for line in lines:
        cells = _parse_field_cells(line) if used_blocks < field_budget else None
        if cells:
            flush_text()
            field_buffer.extend(cells)
            while len(field_buffer) >= _FIELDS_PER_BLOCK and used_blocks < field_budget:
                blocks.append(_fields_element(field_buffer[:_FIELDS_PER_BLOCK]))
                field_buffer = field_buffer[_FIELDS_PER_BLOCK:]
                used_blocks += 1
            continue
        flush_fields()
        text_buffer.append(line)

    flush_fields()
    flush_text()
    return blocks, used_blocks


def _build_elements(sections: List[_Section], notes: List[str]) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    field_blocks = 0
    for section in sections:
        blocks, used = _render_blocks(
            section.lines,
            _title_tag(section.title) if section.title else "",
            _MAX_FIELD_BLOCKS - field_blocks,
        )
        field_blocks += used
        if not blocks:
            continue
        if elements:
            elements.append({"tag": "hr"})
        elements.extend(blocks)
    if notes:
        if elements:
            elements.append({"tag": "hr"})
        elements.append(_note(notes))
    return elements


def _card_size(card: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def build_feishu_card(content: str, *, max_bytes: Optional[int] = None) -> Dict[str, Any]:
    """构建飞书 interactive 卡片的 body（不含 ``msg_type`` 外层）。

    Args:
        content: 已经过 ``format_feishu_markdown`` 转换的 lark_md 正文。
        max_bytes: 卡片 JSON 的字节预算；超限时退化为紧凑卡片，避免被飞书拒绝。

    Returns:
        Card JSON 1.0 字典。任何情况下都不会丢弃正文信息。
    """
    header = _header(content)
    card: Optional[Dict[str, Any]] = None

    if len(content) >= _RICH_CARD_MIN_CHARS:
        body, notes = _extract_footer(content.splitlines())
        sections = _parse_sections(body)
        if any(section.title for section in sections):
            elements = _build_elements(sections, notes)
            if 0 < len(elements) <= _MAX_ELEMENTS:
                card = {"config": _config(), "header": header, "elements": elements}

    if card is None:
        card = _compact_card(content, header)

    if max_bytes and max_bytes > 0 and _card_size(card) > max_bytes:
        # 结构化元素会增加 JSON 体积；退化为原始正文可保证仍在原预算内。
        card = _compact_card(content, header)
    return card
