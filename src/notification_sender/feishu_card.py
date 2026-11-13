# -*- coding: utf-8 -*-
"""飞书推送消息卡片（Card JSON 1.0）的结构化渲染。

Webhook 与 App Bot 两条发送链路共用 :func:`build_feishu_card`。输入是已经过
``format_feishu_markdown`` 转换的 lark_md 正文，渲染时按以下层次组织：

- 文档一级标题 -> 卡片标题栏（不再使用固定的通用标题）
- 开头摘要行 -> 卡片副标题（例如「共分析 3 只股票 · 🟢1 🟡2 🔴0」）
- ``##`` 板块 -> 彩色 ``<text_tag>`` 标签，板块之间只保留一条 ``hr``
- ``###`` 小节 -> 加粗小标题，靠组件间距分节，不再逐节画横线
- ``• 键：值`` 紧凑表格行 -> ``fields`` 双列/全宽自适应布局
- 脚注元信息 -> ``note`` 页脚，自动去重并补齐免责声明

只使用官方 Card JSON 1.0 组件（header / div / hr / note / fields）与 lark_md
支持的 ``<text_tag>``、``<font>`` 标签，不引入需要高版本客户端才能渲染的组件。

降级路径按信息密度逐级收敛，避免长报告退化成「一整块文字」：

1. 富卡片（分节 + 语义色 + 字段布局）
2. 轻卡片（按 ``##`` 板块合并为少量 div，保留分节）
3. 紧凑卡片（标题栏 + 单个 div，完整正文）

任何降级路径都不会丢失正文信息；卡片被飞书拒绝时发送层仍会回退为纯文本。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CARD_TITLE = "📊 股票智能分析报告"
CARD_SUBTITLE = "仅供研究参考 · 不构成投资建议"

# 机器人回复、文件内容、简短告警等保持紧凑卡片，避免无意义的分节。
_RICH_CARD_MIN_CHARS = 200
_MAX_ELEMENTS = 60
_MAX_FIELD_BLOCKS = 24
_FIELDS_PER_BLOCK = 12
_MAX_TITLE_CHARS = 52
_MAX_SUBTITLE_CHARS = 56

# format_feishu_markdown 会把 ## / ### 转成「▎ **标题**」「· **标题**」，
# 一级标题保持「**标题**」，这里据此还原章节层级。
_SECTION_TITLE_RE = re.compile(r"^(▎|·)?\s*\*\*(.+?)\*\*\s*$")
_HR_RE = re.compile(r"^\s*[─—]{4,}\s*$")
_META_LINE_RE = re.compile(r"^(?!\*\*)\*(.+?)\*$")
_PAGE_MARKER_RE = re.compile(r"📄\s*(\d+)\s*/\s*(\d+)")
_KEY_VALUE_ROW_RE = re.compile(r"^•\s+(.+)$")
_GREY_QUOTE_RE = re.compile(r"^<font color=['\"]grey['\"]>\s*💬\s*(.*?)</font>\s*$")
_STOCK_SUMMARY_RE = re.compile(
    r"^(?P<emoji>[🟢🟡🔴⚪🟠🔵])\s*\*\*(?P<name>.+?)\*\*\s*[:：]\s*(?P<meta>.+)$"
)
_STOCK_SUMMARY_PLAIN_RE = re.compile(r"^\*\*(?P<name>.+?)\*\*\s*[:：]\s*(?P<meta>.+)$")
_EMOJI_PREFIX_RE = re.compile(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_INLINE_MARKUP_RE = re.compile(r"</?[^>]+>")

_META_KEYWORDS = (
    "数据来源",
    "报告生成时间",
    "分析模型",
    "仅供参考",
    "不构成投资建议",
    "Data source",
    "Generated at",
    "Model",
)

_SUMMARY_SECTION_KEYWORDS = ("摘要", "Summary", "概览", "Overview", "今日结论")
_SUMMARY_LINE_KEYWORDS = ("决策", "结论", "建议", "操作", "Decision", "Action")

# 章节标题颜色规则（按优先级排序）
_SECTION_COLOR_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("🔴", "🚨", "⚠", "风险", "警报", "预警", "止损", "数据限制"), "red"),
    (("🟢", "✨", "利好", "催化", "机会", "突破"), "green"),
    (("🟡",), "yellow"),
    (("⚪",), "grey"),
    (("📈", "📉", "行情", "技术", "指标", "数据透视", "盘面"), "turquoise"),
    (("🎯", "作战", "计划", "仓位", "点位", "护栏", "策略"), "indigo"),
    (("📌", "结论", "摘要", "核心", "决策", "总结"), "orange"),
    (("📰", "舆情", "📢", "板块", "速览", "消息"), "blue"),
    (("🔍", "信息", "概览"), "purple"),
)


@dataclass
class _Section:
    level: int
    title: str
    lines: List[str] = field(default_factory=list)
    divider_before: bool = False

    def has_content(self) -> bool:
        return bool(self.title) or any(line.strip() for line in self.lines)


def _config() -> Dict[str, Any]:
    return {"wide_screen_mode": True, "enable_forward": True}


def _clamp(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clean_inline(text: str) -> str:
    """去掉 lark_md 行内标记，用于标题栏 / 副标题等纯文本字段。"""
    text = _INLINE_MARKUP_RE.sub("", text or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    return text.strip()


def _template_for(title: str, content: str = "") -> str:
    """按报告类型选择标题主题色，只读取标题和开头，避免正文误伤。"""
    head = f"{title}\n{content[:300]}"
    if "大盘复盘" in head or "市场复盘" in head or "复盘" in head:
        return "indigo"
    if "风险预警" in head or "异常告警" in head or "风险" in title:
        return "orange"
    if "利好" in head or "突破" in head:
        return "green"
    return "blue"


def _header(title: str, subtitle: str, content: str) -> Dict[str, Any]:
    return {
        "template": _template_for(title, content),
        "title": {"tag": "plain_text", "content": _clamp(title or CARD_TITLE, _MAX_TITLE_CHARS)},
        "subtitle": {
            "tag": "plain_text",
            "content": _clamp(subtitle or CARD_SUBTITLE, _MAX_SUBTITLE_CHARS),
        },
    }


def _color_for_title(title: str) -> str:
    """根据标题内容智能选择颜色。"""
    for keywords, color in _SECTION_COLOR_RULES:
        if any(keyword in title for keyword in keywords):
            return color
    return "wathet"


def _title_tag(title: str) -> str:
    """生成带语义色的章节标签，作为卡片内的主标题。"""
    safe = (title or "").replace("<", "‹").replace(">", "›").strip()
    return f"<text_tag color='{_color_for_title(title)}'>{safe}</text_tag>"


def _div(content: str) -> Dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _append_hr(elements: List[Dict[str, Any]]) -> None:
    """插入分隔线，并避免连续或开头出现空的 hr。"""
    if not elements:
        return
    if elements[-1].get("tag") == "hr":
        return
    elements.append({"tag": "hr"})


def _note(notes: List[str]) -> Dict[str, Any]:
    """生成页脚备注：去重、补齐免责声明，并避免重复图标。"""
    deduped: List[str] = []
    seen: set[str] = set()
    for note in notes:
        cleaned = _clean_inline(note)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)

    if not any(CARD_SUBTITLE in note for note in deduped):
        deduped.append(CARD_SUBTITLE)

    text = " · ".join(deduped)
    if not _EMOJI_PREFIX_RE.match(text):
        text = f"📋 {text}"
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": text}],
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
    """解析章节结构，保留 ``##`` / ``###`` 层级和分隔线边界。"""
    sections: List[_Section] = []
    current = _Section(0, "")

    def start_section(level: int, title: str) -> None:
        nonlocal current
        if current.has_content():
            sections.append(current)
            current = _Section(level, title)
        else:
            # 分隔线可能出现在标题前，此时把边界传递给真正的标题。
            current = _Section(level, title, divider_before=current.divider_before)

    for line in lines:
        match = _SECTION_TITLE_RE.match(line)
        if match:
            prefix, title = match.group(1), match.group(2).strip()
            level = 1 if prefix is None else (2 if prefix == "▎" else 3)
            start_section(level, title)
            continue
        # 文本分隔线改由卡片的 hr 组件承担。
        if _HR_RE.match(line):
            if current.has_content():
                sections.append(current)
                current = _Section(0, "", divider_before=True)
            else:
                current.divider_before = True
            continue
        current.lines.append(line)

    if current.has_content():
        sections.append(current)
    return sections


def _split_document(sections: List[_Section]) -> Tuple[str, List[str], List[_Section]]:
    """把首个一级标题提升为卡片标题，其余内容作为正文。"""
    if sections and sections[0].level == 1 and sections[0].title:
        return sections[0].title, sections[0].lines, sections[1:]
    return "", [], sections


def _clean_summary_text(text: str) -> str:
    text = _clean_inline(text)
    text = text.replace("｜", " | ")
    return re.sub(r"\s+", " ", text).strip()


def _extract_summary(preamble: List[str]) -> Tuple[str, List[str]]:
    """从开头引用行提取「共分析 N 只股票 / 买入·观望·卖出」摘要。"""
    summary = ""
    kept: List[str] = []
    for line in preamble:
        stripped = line.strip()
        match = _GREY_QUOTE_RE.match(stripped)
        inner = match.group(1) if match else ""
        if not inner and stripped.startswith(">"):
            inner = stripped[1:].strip()
        if inner and (
            "只股票" in inner
            or "共分析" in inner
            or "stocks" in inner.lower()
            or all(token in inner for token in ("🟢", "🟡", "🔴"))
        ):
            summary = _clean_summary_text(inner)
            continue
        kept.append(line)
    return summary, kept


def _render_preamble(preamble: List[str]) -> str:
    """渲染标题与首个板块之间的元信息（市场状态等）。"""
    rendered: List[str] = []
    for line in preamble:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("市场状态") or stripped.lower().startswith("market status"):
            rendered.append(f"<font color='grey'>{_clean_inline(stripped)}</font>")
            continue
        rendered.append(_format_quote(stripped))
    text = "\n".join(rendered)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _format_quote(line: str) -> str:
    """引用块升级：决策类引用改为高亮提示，其余保持灰色小字。"""
    match = _GREY_QUOTE_RE.match(line.strip())
    if not match:
        return line
    inner = match.group(1).strip()
    if not inner:
        return ""
    if any(keyword in inner for keyword in _SUMMARY_LINE_KEYWORDS):
        return f"💡 {inner}"
    return f"<font color='grey'>💬 {inner}</font>"


def _parse_stock_summary(line: str) -> Optional[Tuple[str, str]]:
    """解析「⚪ **贵州茅台(600519.SH)**: 观望 | 评分 58 | 震荡」摘要行。"""
    match = _STOCK_SUMMARY_RE.match(line.strip()) or _STOCK_SUMMARY_PLAIN_RE.match(line.strip())
    if not match:
        return None
    name = _clean_inline(match.group("name")).strip()
    meta = match.group("meta").strip()
    if not name or not meta or len(meta) > 80:
        return None
    emoji = match.groupdict().get("emoji") or ""
    label = f"{emoji} {name}".strip()
    return label, meta


def _parse_field_cells(line: str) -> Optional[List[Tuple[str, str]]]:
    """把「• 键：值 | 键：值」的紧凑表格行解析成字段。

    两列表格在 ``format_feishu_markdown`` 中已经转换为「• 键：值」，
    因此这里允许 1 个字段；3~12 列的行情/点位表格仍按多字段展开，
    让十列的市场快照也能落成双列网格，而不是一条超长文本。
    """
    match = _KEY_VALUE_ROW_RE.match(line.strip())
    if not match:
        return None
    parts = [part.strip() for part in match.group(1).split(" | ")]
    if not 1 <= len(parts) <= _FIELDS_PER_BLOCK:
        return None
    cells: List[Tuple[str, str]] = []
    for part in parts:
        if "：" not in part:
            return None
        key, value = part.split("：", 1)
        key = _clean_inline(key).strip()
        value = value.strip()
        if not key or not value or len(key) > 16 or len(value) > 120:
            return None
        cells.append((key, value))
    return cells


def _is_short_field(
    key: str, value: str, *, limit: int = 28, key_limit: int = 12
) -> bool:
    """短字段双列展示，长文本全宽展示，避免半屏挤压。"""
    return len(value) <= limit and len(key) <= key_limit and "\n" not in value


def _fields_element(
    cells: List[Tuple[str, str]],
    *,
    force_full: bool = False,
    short_limit: int = 28,
    short_key_limit: int = 12,
) -> Dict[str, Any]:
    short_flags = [
        (not force_full)
        and _is_short_field(key, value, limit=short_limit, key_limit=short_key_limit)
        for key, value in cells
    ]
    # 同一组字段保持对齐：只要有一项需要全宽，整组都按全宽展示。
    if short_flags and not all(short_flags):
        short_flags = [False] * len(short_flags)
    # 奇数个双列字段会留下半行空白，把最后一个也拉成全宽以保持对齐。
    elif short_flags and len(short_flags) % 2 == 1:
        short_flags[-1] = False

    fields = []
    for (key, value), is_short in zip(cells, short_flags):
        fields.append({
            "is_short": is_short,
            "text": {"tag": "lark_md", "content": f"**{key}**\n{value}"},
        })
    return {"tag": "div", "fields": fields}


def _section_heading(section: _Section) -> str:
    if not section.title:
        return ""
    if section.level in (1, 2):
        return _title_tag(section.title)
    # 三级标题默认用加粗保持层级；风险 / 利好类小节保留语义色标签。
    if _color_for_title(section.title) in ("red", "green"):
        return _title_tag(section.title)
    return f"**{section.title}**"


def _render_blocks(
    section: _Section,
    field_budget: int,
    *,
    summary_mode: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """把一个章节渲染为若干 div / fields 元素，并限制字段块数量。"""
    blocks: List[Dict[str, Any]] = []
    text_buffer: List[str] = []
    field_buffer: List[Tuple[str, str]] = []
    used_blocks = 0

    heading = _section_heading(section)
    if heading:
        text_buffer.append(heading)
        text_buffer.append("")

    summary_cells = 0
    if summary_mode:
        summary_cells = sum(1 for line in section.lines if _parse_stock_summary(line))
    force_full_summary = summary_mode and summary_cells <= 1

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
            blocks.append(
                _fields_element(
                    field_buffer,
                    force_full=force_full_summary,
                    short_limit=36 if summary_mode else 24,
                    short_key_limit=26 if summary_mode else 12,
                )
            )
            used_blocks += 1
        field_buffer = []

    for line in section.lines:
        stripped = line.strip()
        if not stripped:
            # 摘要列表允许跨空行合并成网格；正文空行仍作为字段/段落边界。
            if summary_mode:
                if not field_buffer:
                    text_buffer.append("")
                continue
            flush_fields()
            text_buffer.append("")
            continue

        if summary_mode:
            summary = _parse_stock_summary(stripped)
            if summary:
                flush_text()
                field_buffer.append(summary)
                continue

        cells = _parse_field_cells(stripped) if used_blocks < field_budget else None
        if cells:
            flush_text()
            field_buffer.extend(cells)
            while len(field_buffer) >= _FIELDS_PER_BLOCK and used_blocks < field_budget:
                blocks.append(
                    _fields_element(
                        field_buffer[:_FIELDS_PER_BLOCK],
                        force_full=force_full_summary,
                        short_limit=36 if summary_mode else 24,
                        short_key_limit=26 if summary_mode else 12,
                    )
                )
                field_buffer = field_buffer[_FIELDS_PER_BLOCK:]
                used_blocks += 1
            continue

        flush_fields()
        text_buffer.append(_format_quote(line))

    flush_fields()
    flush_text()
    return blocks, used_blocks


def _is_summary_section(title: str) -> bool:
    return any(keyword in title for keyword in _SUMMARY_SECTION_KEYWORDS)


def _build_elements(
    sections: List[_Section],
    preamble: List[str],
    notes: List[str],
) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    field_blocks = 0

    preamble_text = _render_preamble(preamble)
    if preamble_text:
        elements.append(_div(preamble_text))

    for section in sections:
        if not section.has_content():
            continue
        blocks, used = _render_blocks(
            section,
            _MAX_FIELD_BLOCKS - field_blocks,
            summary_mode=_is_summary_section(section.title),
        )
        if not blocks:
            continue
        field_blocks += used
        if section.level == 2 or section.divider_before:
            _append_hr(elements)
        elements.extend(blocks)

    _append_hr(elements)
    elements.append(_note(notes))
    return elements


def _group_sections(sections: List[_Section]) -> List[List[_Section]]:
    groups: List[List[_Section]] = []
    current: List[_Section] = []
    for section in sections:
        if not section.has_content():
            continue
        if current and (section.level == 2 or section.divider_before):
            groups.append(current)
            current = []
        current.append(section)
    if current:
        groups.append(current)
    return groups


def _build_lite_elements(
    sections: List[_Section],
    preamble: List[str],
    notes: List[str],
) -> List[Dict[str, Any]]:
    """轻卡片：按板块合并为少量 div，保留分节但去掉字段/标签开销。"""
    elements: List[Dict[str, Any]] = []
    preamble_text = _render_preamble(preamble)
    if preamble_text:
        elements.append(_div(preamble_text))

    for group in _group_sections(sections):
        lines: List[str] = []
        for section in group:
            heading = _section_heading(section)
            if heading:
                lines.append(heading)
            for raw in section.lines:
                stripped = raw.strip()
                if stripped:
                    lines.append(_format_quote(stripped))
            if lines and lines[-1] != "":
                lines.append("")
        content = re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())
        if content:
            if elements:
                _append_hr(elements)
            elements.append(_div(content))

    _append_hr(elements)
    elements.append(_note(notes))
    return elements


def _strip_title_line(content: str, title: str) -> str:
    """紧凑卡片中移除已经提升到标题栏的 H1，避免重复。"""
    if not title:
        return content
    lines = content.splitlines()
    for index, line in enumerate(lines):
        match = _SECTION_TITLE_RE.match(line)
        if match and match.group(2).strip() == title:
            del lines[index]
            return "\n".join(lines).strip()
    return content


def _strip_summary_line(content: str, summary: str) -> str:
    """紧凑卡片中移除已经提升到副标题的摘要行，避免重复。"""
    if not summary:
        return content
    lines = content.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = _GREY_QUOTE_RE.match(stripped)
        inner = match.group(1) if match else (stripped[1:] if stripped.startswith(">") else "")
        if inner and _clean_summary_text(inner) == summary:
            del lines[index]
            return "\n".join(lines).strip()
    return content


def _card_size(card: Dict[str, Any]) -> int:
    """计算卡片 JSON 的字节大小。"""
    try:
        return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _fits(card: Dict[str, Any], max_bytes: Optional[int]) -> bool:
    return not max_bytes or max_bytes <= 0 or _card_size(card) <= max_bytes


def build_feishu_card(content: str, *, max_bytes: Optional[int] = None) -> Dict[str, Any]:
    """构建飞书 interactive 卡片的 body（不含 ``msg_type`` 外层）。

    Args:
        content: 已经过 ``format_feishu_markdown`` 转换的 lark_md 正文。
        max_bytes: 卡片 JSON 的字节预算；超限时按富卡片 -> 轻卡片 -> 紧凑卡片
            逐级降级，避免被飞书拒绝。

    Returns:
        Card JSON 1.0 字典。任何情况下都不会丢弃正文信息。
    """
    body_lines, notes = _extract_footer(content.splitlines())
    sections = _parse_sections(body_lines)
    title, preamble, body = _split_document(sections)
    summary, preamble = _extract_summary(preamble)
    header = _header(title, summary, content)

    if len(content) >= _RICH_CARD_MIN_CHARS and body:
        elements = _build_elements(body, preamble, notes)
        if 0 < len(elements) <= _MAX_ELEMENTS:
            card = {"config": _config(), "header": header, "elements": elements}
            if _fits(card, max_bytes):
                return card

        lite_elements = _build_lite_elements(body, preamble, notes)
        if 0 < len(lite_elements) <= _MAX_ELEMENTS:
            card = {"config": _config(), "header": header, "elements": lite_elements}
            if _fits(card, max_bytes):
                return card

    compact_body = _strip_summary_line(_strip_title_line(content, title), summary)
    compact_elements = [_div(compact_body)] if compact_body.strip() else [_note([])]
    return {
        "config": _config(),
        "header": header,
        "elements": compact_elements,
    }
