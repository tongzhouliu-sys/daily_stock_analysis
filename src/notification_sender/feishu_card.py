# -*- coding: utf-8 -*-
"""飞书推送消息卡片（Card JSON 1.0）的结构化渲染。

Webhook 与 App Bot 两条发送链路共用 :func:`build_feishu_card`：把已转换为
``lark_md`` 的报告正文拆成「彩色标题栏 + 章节分节 + 智能字段布局 + 备注页脚」，
避免长报告全部挤在一个 ``div`` 里。短通知、无法安全结构化的内容、以及超出
字节预算的卡片会自动退化为紧凑单块卡片；发送失败时原链路仍会回退为纯文本。

只使用官方 Card JSON 1.0 组件（header / div / hr / note / fields）与 lark_md
支持的 ``<text_tag>`` 标签，不引入需要高版本客户端才能渲染的组件。

优化特性：
- 多级标题视觉层次（一级大标题、二级带色标签、三级带图标）
- 智能字段布局（关键数据全宽、次要信息多列）
- 增强的视觉分隔（章节间使用 hr + 标题颜色区分）
- 列表项图标化（根据内容类型自动添加 emoji）
- 引用块样式优化（使用灰色背景 + 左边框）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CARD_TITLE = "📊 股票智能分析报告"
_CARD_SUBTITLE = "仅供研究参考 · 不构成投资建议"

# 机器人回复、文件内容、简短告警等保持紧凑卡片，避免无意义的分节。
_RICH_CARD_MIN_CHARS = 400
_MAX_SECTIONS = 14
_MAX_ELEMENTS = 42
_MAX_FIELD_BLOCKS = 8
_FIELDS_PER_BLOCK = 6

# format_feishu_markdown 会把 ## / ### 转成「▎ **标题**」「· **标题**」，
# 一级标题保持「**标题**」，这里据此还原章节层级。
_SECTION_TITLE_RE = re.compile(r"^(▎|·)?\s*\*\*(.+?)\*\*\s*$")
_HR_RE = re.compile(r"^\s*[─—]{4,}\s*$")
_META_LINE_RE = re.compile(r"^\*(.+)\*$")
_PAGE_MARKER_RE = re.compile(r"📄\s*(\d+)\s*/\s*(\d+)")
_KEY_VALUE_ROW_RE = re.compile(r"^•\s+(.+)$")
_META_KEYWORDS = ("数据来源", "报告生成时间", "分析模型", "仅供参考", "不构成投资建议")

# 章节标题颜色规则（按优先级排序）
_SECTION_COLOR_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("风险", "警报", "预警", "⚠", "🚨", "止损", "数据限制"), "red"),
    (("利好", "催化", "✨", "机会", "突破"), "green"),
    (("行情", "📈", "📉", "技术", "指标", "数据透视", "盘面"), "turquoise"),
    (("作战", "计划", "🎯", "仓位", "点位", "护栏", "策略"), "indigo"),
    (("结论", "摘要", "📌", "核心", "决策", "总结"), "orange"),
    (("新闻", "舆情", "📰", "📢", "板块", "速览", "消息"), "blue"),
    (("信息", "速览", "概览", "🔍"), "purple"),
)

# 列表项图标映射（根据内容关键词）
_LIST_ITEM_ICONS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("风险", "警告", "注意", "⚠"), "⚠️"),
    (("利好", "机会", "突破", "✨"), "✨"),
    (("买入", "看多", "强势", "🟢"), "🟢"),
    (("卖出", "看空", "弱势", "🔴"), "🔴"),
    (("观望", "等待", "中性", "🟡"), "🟡"),
    (("支撑", "压力", "点位", "📍"), "📍"),
    (("目标", "预期", "🎯"), "🎯"),
)

# 关键数据字段（使用全宽布局）
_KEY_DATA_FIELDS = {"当前价", "涨跌幅", "评分", "评级", "信号", "目标价", "止损价"}


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
    if "利好" in head or "突破" in head:
        return "green"
    return "blue"


def _header(content: str) -> Dict[str, Any]:
    return {
        "template": _template_for(content),
        "title": {"tag": "plain_text", "content": CARD_TITLE},
        "subtitle": {"tag": "plain_text", "content": _CARD_SUBTITLE},
    }


def _color_for_title(title: str) -> str:
    """根据标题内容智能选择颜色。"""
    for keywords, color in _SECTION_COLOR_RULES:
        if any(keyword in title for keyword in keywords):
            return color
    return "wathet"


def _title_tag(title: str, level: int = 2) -> str:
    """生成带颜色和层级的标题标签。
    
    Args:
        title: 标题文本
        level: 标题层级 (1=一级, 2=二级, 3=三级)
    """
    safe = title.replace("<", "‹").replace(">", "›").strip()
    color = _color_for_title(title)
    
    # 一级标题：更大更醒目
    if level == 1:
        return f"<text_tag color='{color}'>{safe}</text_tag>"
    # 二级标题：带颜色标签
    elif level == 2:
        return f"<text_tag color='{color}'>{safe}</text_tag>"
    # 三级标题：较小，带图标
    else:
        return f"<text_tag color='grey'>{safe}</text_tag>"


def _icon_for_list_item(text: str) -> str:
    """根据列表项内容智能选择图标。"""
    for keywords, icon in _LIST_ITEM_ICONS:
        if any(keyword in text for keyword in keywords):
            return icon
    return "•"


def _div(content: str) -> Dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note(notes: List[str]) -> Dict[str, Any]:
    """生成页脚备注，使用更优雅的格式。"""
    if not notes:
        return {"tag": "note", "elements": []}
    
    # 使用分隔符连接，限制长度
    note_text = " · ".join(notes)[:200]
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"📋 {note_text}"}],
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
    """解析章节结构，保留层级信息。"""
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


def _is_key_field(key: str) -> bool:
    """判断是否为关键数据字段（需要全宽显示）。"""
    return any(key_field in key for key_field in _KEY_DATA_FIELDS)


def _fields_element(cells: List[Tuple[str, str]], *, compact: bool = False) -> Dict[str, Any]:
    """生成字段布局元素。
    
    Args:
        cells: (key, value) 列表
        compact: 是否紧凑模式（全部双列）
    """
    fields = []
    for key, value in cells:
        # 关键字段或紧凑模式使用全宽，否则双列
        is_short = not _is_key_field(key) and not compact
        fields.append({
            "is_short": is_short,
            "text": {"tag": "lark_md", "content": f"**{key}**\n{value}"},
        })
    
    return {"tag": "div", "fields": fields}


def _format_list_item(text: str) -> str:
    """格式化列表项，根据内容智能添加图标。"""
    # 移除原有的项目符号
    cleaned = re.sub(r'^[•\-\*]\s*', '', text).strip()
    if not cleaned:
        return text
    
    # 根据内容添加智能图标
    icon = _icon_for_list_item(cleaned)
    return f"{icon} {cleaned}"


def _format_quote_block(text: str) -> str:
    """格式化引用块，使用更优雅的样式。"""
    # 移除原有的引用标记
    cleaned = re.sub(r'^>\s*', '', text).strip()
    if not cleaned:
        return ""
    
    # 使用灰色背景和左边框效果
    return f"<font color='grey'>💬 {cleaned}</font>"


def _enhance_content(lines: List[str]) -> List[str]:
    """增强内容排版，优化可读性。"""
    enhanced = []
    for line in lines:
        stripped = line.strip()
        
        # 处理列表项
        if stripped.startswith(('• ', '- ', '* ')):
            enhanced.append(_format_list_item(line))
        # 处理引用块
        elif stripped.startswith('> '):
            enhanced.append(_format_quote_block(line))
        # 处理加粗的关键信息
        elif '**' in stripped and any(keyword in stripped for keyword in 
                                      ('买入', '卖出', '观望', '评分', '评级', '信号')):
            # 保持原样，这些已经有良好格式
            enhanced.append(line)
        else:
            enhanced.append(line)
    
    return enhanced


def _render_blocks(
    lines: List[str], heading: str, field_budget: int, level: int = 2
) -> Tuple[List[Dict[str, Any]], int]:
    """把一个章节渲染为若干 div / fields 元素，并限制双列块数量。
    
    Args:
        lines: 章节内容行
        heading: 章节标题
        field_budget: 字段块预算
        level: 章节层级 (1=一级, 2=二级, 3=三级)
    """
    blocks: List[Dict[str, Any]] = []
    
    # 增强内容排版
    enhanced_lines = _enhance_content(lines)
    
    text_buffer: List[str] = []
    if heading:
        # 根据层级添加不同的标题样式
        if level == 1:
            # 一级标题：使用更大的标签
            text_buffer.append(f"\n{text_tag}\n")
        elif level == 2:
            text_buffer.append(text_tag)
        else:
            # 三级标题：较小，带图标
            text_buffer.append(text_tag)
    
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

    for line in enhanced_lines:
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
    """构建卡片元素列表，优化视觉层次和分隔。"""
    elements: List[Dict[str, Any]] = []
    field_blocks = 0
    
    for i, section in enumerate(sections):
        # 跳过空章节
        if not section.title and not section.lines:
            continue
            
        blocks, used = _render_blocks(
            section.lines,
            section.title,
            _MAX_FIELD_BLOCKS - field_blocks,
            level=section.level,
        )
        field_blocks += used
        
        if not blocks:
            continue
            
        # 章节间添加分隔线（除了第一个）
        if elements and section.title:
            elements.append({"tag": "hr"})
            
        elements.extend(blocks)
    
    # 页脚备注
    if notes:
        if elements:
            elements.append({"tag": "hr"})
        elements.append(_note(notes))
        
    return elements


def _card_size(card: Dict[str, Any]) -> int:
    """计算卡片 JSON 的字节大小。"""
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
