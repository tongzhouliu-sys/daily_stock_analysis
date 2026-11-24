# -*- coding: utf-8 -*-
"""飞书推送消息卡片（Card JSON 1.0）的结构化渲染。

Webhook 与 App Bot 两条发送链路共用 :func:`build_feishu_card`。输入是已经过
``format_feishu_markdown`` 转换的 lark_md 正文，渲染时按以下层次组织：

- 文档一级标题 -> 卡片标题栏（不再使用固定的通用标题）
- 开头摘要行 -> 卡片副标题（例如「共分析 3 只股票 · 🟢1 🟡2 🔴0」）
- ``##`` 板块 -> 彩色 ``<text_tag>`` 标签，板块之间只保留一条 ``hr``
- ``###`` 小节 -> 加粗小标题，靠组件间距分节，不再逐节画横线
- 多标摘要 -> 原生 ``table``（标的 / 动作 / 评分 / 趋势）
- 个股数值 -> 按分类拆成小版块（灰色分类标题 + 行内「指标 数值」双列）
- 其余 ``• 键：值`` 紧凑表格行 -> ``fields`` 双列/全宽自适应布局
- 脚注元信息 -> ``note`` 页脚，自动去重并补齐免责声明

只使用官方 Card JSON 1.0 组件（header / div / hr / note / fields / table）与
lark_md 支持的 ``<text_tag>``、``<font>`` 标签。原生 ``table`` 需要飞书
7.4+；低版本客户端对表格显示升级提示，其余组件不受影响。

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
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.report_language import REPORT_AI_DISCLAIMER, is_report_disclaimer_line

CARD_TITLE = "📊 股票智能分析报告"
CARD_SUBTITLE = "仅供研究参考"

# 机器人回复、文件内容、简短告警等保持紧凑卡片，避免无意义的分节。
_RICH_CARD_MIN_CHARS = 200
# 表格会显著压缩字段块数量；放宽上限让 3~5 只股票的富卡片先过结构检查，
# 最终是否可发仍由 max_bytes 的真实 JSON 体积决定。
_MAX_ELEMENTS = 80
_MAX_FIELD_BLOCKS = 24
_FIELDS_PER_BLOCK = 12
# 飞书卡片单卡最多 5 个 table 组件，超出的表格会被客户端降级成文本。
_MAX_TABLES = 5
_TABLE_PAGE_SIZE = 10
_MAX_METRIC_VALUE_CHARS = 24
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
    "RHCLOUD",
    "Data source",
    "Generated at",
    "Model",
)

_SUMMARY_SECTION_KEYWORDS = ("摘要", "Summary", "概览", "Overview", "今日结论")
_SUMMARY_LINE_KEYWORDS = ("决策", "结论", "建议", "操作", "Decision", "Action")

# 数值表格：只收「短、含数字、无句读」的键值行，长策略/描述仍走正文。
_DIGIT_RE = re.compile(r"\d")
# 句读判定不含 ASCII `.`/`,`：它们是小数点和千分位，不能把 1453.00 判成句子。
_SENTENCE_PUNCT_RE = re.compile(r"[，。；！？;!?]")
# 这些键描述对象本身，不是指标；放进数值表会把名称/代码误当行情。
_METRIC_SKIP_KEYS = frozenset(
    {
        "股票",
        "名称",
        "代码",
        "标的",
        "指数",
        "信号",
        "来源",
        "数据来源",
        "报告生成时间",
        "分析模型",
        "模型",
    }
)
# 章节标题 -> 板块短标签；未命中时用去掉编号前缀后的完整标题，不再截断。
_METRIC_CATEGORY_MAP = {
    "当日行情": "行情",
    "行情快照": "行情",
    "盘面速览": "行情",
    "指数表现": "指数",
    "数据透视": "技术",
    "技术分析": "技术",
    "盘中决策护栏": "盘中",
    "盘中护栏": "盘中",
    "操作点位": "点位",
    "关键点位": "点位",
    "仓位建议": "仓位",
    "信号归因分析": "归因",
    "检查清单": "清单",
}
# 表格分页时第一页先展示最可执行的数据。
_METRIC_CATEGORY_PRIORITY = {
    "点位": 0,
    "行情": 1,
    "指数": 1,
    "技术": 2,
    "盘中": 3,
    "仓位": 4,
    "归因": 5,
    "清单": 6,
}
_METRIC_EMOJI_RE = re.compile(
    r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F\u200D\s·▎]+"
)
# 「一、」「二、」「1.」这类章节编号前缀，用于大盘复盘章节名归一。
_ENUM_PREFIX_RE = re.compile(r"^[一二三四五六七八九十\d]+[、．.）\)]\s*")

# 章节标题颜色规则（按优先级排序）
_SECTION_COLOR_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("🔴", "🚨", "⚠", "风险", "警报", "预警", "止损", "数据限制"), "red"),
    (("🟢", "✨", "利好", "催化", "机会", "突破"), "green"),
    (("🟡",), "yellow"),
    (("🟠",), "orange"),
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


_COUNT_TOKEN_RE = re.compile(r"([🟢🟡🔴])\s*([^\s:：]*)\s*[:：]\s*(\d+)")
_COUNT_TAG_COLORS = {"🟢": "green", "🟡": "yellow", "🔴": "red"}


def _split_count_tags(summary: str) -> Tuple[str, List[Dict[str, Any]]]:
    """把「🟢买入:0 🟡观望:1 🔴卖出:0」拆成标题栏彩色后缀标签。

    只有整段都是计数时提升为标签，避免误伤「买入 0 只，观望 1 只」这类句子。
    """
    if not summary:
        return "", []
    parts = [part.strip() for part in re.split(r"[|｜]", summary) if part.strip()]
    kept: List[str] = []
    tags: List[Dict[str, Any]] = []
    for part in parts:
        tokens = list(_COUNT_TOKEN_RE.finditer(part))
        if not tokens or _COUNT_TOKEN_RE.sub("", part).strip():
            kept.append(part)
            continue
        for token in tokens:
            emoji, label, count = token.groups()
            tags.append(
                {
                    "tag": "text_tag",
                    "text": {
                        "tag": "plain_text",
                        "content": f"{emoji} {label} {count}".strip(),
                    },
                    "color": _COUNT_TAG_COLORS[emoji],
                }
            )
    return " · ".join(kept), tags[:3]


def _header(title: str, subtitle: str, content: str) -> Dict[str, Any]:
    summary_text, tags = _split_count_tags(subtitle)
    header: Dict[str, Any] = {
        "template": _template_for(title, content),
        "title": {"tag": "plain_text", "content": _clamp(title or CARD_TITLE, _MAX_TITLE_CHARS)},
        "subtitle": {
            "tag": "plain_text",
            "content": _clamp(summary_text or subtitle or CARD_SUBTITLE, _MAX_SUBTITLE_CHARS),
        },
    }
    if tags:
        header["text_tag_list"] = tags
    return header


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


def _dedupe_notes(notes: List[str], *, include_disclaimer: bool = False) -> List[str]:
    """脚注去重与统一声明补齐；卡片页脚与 HTML 报告共用同一口径。"""
    deduped: List[str] = []
    seen: set[str] = set()
    for note in notes:
        cleaned = _clean_inline(note)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)

    if include_disclaimer and not any(REPORT_AI_DISCLAIMER in note for note in deduped):
        deduped.append(REPORT_AI_DISCLAIMER)
    return deduped


def _note(notes: List[str], *, include_disclaimer: bool = False) -> Optional[Dict[str, Any]]:
    """生成页脚备注：去重、按需补齐统一声明，并避免重复图标。"""
    deduped = _dedupe_notes(notes, include_disclaimer=include_disclaimer)

    if not deduped:
        return None

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
            meta_text = meta.group(1).strip().strip("*")
            if is_report_disclaimer_line(meta_text):
                # 旧声明直接丢弃；统一声明保留并归一化，避免重复。
                if REPORT_AI_DISCLAIMER in meta_text:
                    notes.append(REPORT_AI_DISCLAIMER)
                continue
            notes.append(meta_text)
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


def plan_field_grid(
    cells: List[Tuple[str, str]],
    *,
    force_full: bool = False,
    short_limit: int = 28,
    short_key_limit: int = 12,
) -> List[Dict[str, Any]]:
    """规划字段网格的短/全宽布局；卡片 fields 与 HTML 报告共用同一规则。

    同一组字段保持对齐：只要有一项需要全宽，整组都按全宽展示；奇数个
    双列字段把最后一项拉成全宽，避免半行空白。
    """
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
    return [
        {"key": key, "value": value, "full": not is_short}
        for (key, value), is_short in zip(cells, short_flags)
    ]


def _fields_element(
    cells: List[Tuple[str, str]],
    *,
    force_full: bool = False,
    short_limit: int = 28,
    short_key_limit: int = 12,
    inline: bool = False,
) -> Dict[str, Any]:
    grid = plan_field_grid(
        cells,
        force_full=force_full,
        short_limit=short_limit,
        short_key_limit=short_key_limit,
    )
    fields = [
        {
            "is_short": not item["full"],
            "text": {
                "tag": "lark_md",
                # inline 用于数值型字段：标签与数值同行，数字不换行。
                "content": (
                    f"**{item['key']}**：{item['value']}"
                    if inline
                    else f"**{item['key']}**\n{item['value']}"
                ),
            },
        }
        for item in grid
    ]
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


# ----------------------------------------------------------------------
# 原生表格（Card JSON 1.0，飞书 7.4+）
#
# 表格只放在卡片根节点（飞书限制：不能嵌套、单卡最多 5 个）。数字类内容
# 收进表格后，组件数量与半屏空白都明显减少，比 fields 网格更适合行情、
# 点位这类高密度数据。
# ----------------------------------------------------------------------


def _table_element(
    columns: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    *,
    page_size: int = _TABLE_PAGE_SIZE,
    freeze_first_column: bool = False,
    row_height: str = "middle",
) -> Dict[str, Any]:
    return {
        "tag": "table",
        "page_size": max(1, min(int(page_size or 1), _TABLE_PAGE_SIZE)),
        "row_height": row_height,
        "freeze_first_column": bool(freeze_first_column),
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def _is_short_text_cell(key: str, value: str) -> bool:
    """短文本单元可安全进表格；长句/句读文本继续留在正文。"""
    cleaned_key = _clean_inline(key).strip()
    cleaned_value = _clean_inline(value).strip()
    if not cleaned_key or not cleaned_value:
        return False
    if cleaned_key in _METRIC_SKIP_KEYS:
        return False
    if len(cleaned_value) > _MAX_METRIC_VALUE_CHARS:
        return False
    if _SENTENCE_PUNCT_RE.search(cleaned_value):
        return False
    return True


def _is_metric_cell(key: str, value: str) -> bool:
    """判断一个键值单元是否适合进数值表。

    只收长度可控、含数字、不带句读的短值；长策略、观察条件、来源说明
    继续留在正文/字段里，避免表格被大段文字撑开。
    """
    if not _is_short_text_cell(key, value):
        return False
    return bool(_DIGIT_RE.search(_clean_inline(value)))


def _metric_label(text: str) -> str:
    """去掉标题/键名前的 emoji，表格里保持对齐和窄列宽。"""
    return _METRIC_EMOJI_RE.sub("", _clean_inline(text)).strip()


def _metric_category(title: str) -> str:
    text = _metric_label(title)
    text = _ENUM_PREFIX_RE.sub("", text).strip()
    if text in _METRIC_CATEGORY_MAP:
        return _METRIC_CATEGORY_MAP[text]
    # 未命中（如「盘面总览」「指数结构」）时用清洗后的完整标题，
    # 不再截断成「二、指…」这类省略号。
    return text


def _summary_table(summary_rows: List[Tuple[str, str]]) -> Dict[str, Any]:
    """多标的摘要表：标的 / 动作 / 评分 / 趋势。

    动作用彩色选项标签、评分用分级色 + ``/100`` 居中，把「结论」做成
    一眼可辨的视觉锚点；标的去掉行首信号 emoji，避免与动作颜色重复。
    """
    rows: List[Dict[str, Any]] = []
    for label, meta in summary_rows:
        parts = [part.strip() for part in meta.split("|") if part.strip()]
        action = parts[0] if parts else ""
        score = ""
        trend = ""
        for part in parts[1:]:
            score_match = re.search(r"评分\s*([\d.]+)", part)
            if score_match:
                score = score_match.group(1)
            elif part:
                trend = part

        name = _METRIC_EMOJI_RE.sub("", _clean_inline(label)).strip()
        action_text = _clean_inline(action)
        score_cell = ""
        if score:
            score_cell = (
                f"<font color='{_signal_score_color(score)}'>"
                f"**{_signal_number_text(score)}**</font>/100"
            )
        rows.append(
            {
                "name": name,
                "action": (
                    [{"text": action_text, "color": _signal_action_color(action_text)}]
                    if action_text
                    else ""
                ),
                "score": score_cell,
                "trend": _clean_inline(trend),
            }
        )
    columns = [
        {"name": "name", "display_name": "标的", "data_type": "text", "width": "40%"},
        {
            "name": "action",
            "display_name": "动作",
            "data_type": "options",
            "width": "20%",
            "horizontal_align": "center",
        },
        {
            "name": "score",
            "display_name": "评分",
            "data_type": "lark_md",
            "width": "18%",
            "horizontal_align": "center",
        },
        {"name": "trend", "display_name": "趋势", "data_type": "text", "width": "22%"},
    ]
    return _table_element(columns, rows)


def _metric_block_elements(rows: List[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    """个股关键数据按分类拆成小版块：灰色分类标题 + 行内「指标 数值」双列。

    比「分类 / 指标 / 数值」三列表更贴合阅读习惯：每类数据自成一个小块，
    指标与数值同行，数字不换行。
    """
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for category, key, value in rows:
        grouped.setdefault(category, []).append((key, value))

    elements: List[Dict[str, Any]] = []
    for category, cells in grouped.items():
        if not cells:
            continue
        elements.append(_div(f"<font color='grey'>**{category}**</font>"))
        elements.append(_fields_element(cells, inline=True))
    return elements


def _collect_metric_rows(
    sections: List[_Section],
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[_Section, List[str]]]]:
    """从一组小节里收集数值行，并返回去掉数值行后的正文。

    同一行里的数值单元进表格，短文本单元（如「行情来源：腾讯财经」）一并
    进表；长策略/观察条件等保留在正文，保证信息不丢。同名指标只保留首次
    出现，避免「当前价」在行情与技术两处重复占行。表格行按「点位 -> 行情
    -> 技术 -> 盘中」排序，第一页先给最可执行的数据。
    """
    rows: List[Tuple[str, str, str]] = []
    plans: List[Tuple[_Section, List[str]]] = []
    seen_keys: set[str] = set()

    for section in sections:
        category = _metric_category(section.title)
        section_metrics: List[Tuple[str, str]] = []
        remaining: List[str] = []

        for line in section.lines:
            stripped = line.strip()
            if not stripped:
                remaining.append(line)
                continue
            cells = _parse_field_cells(stripped)
            if not cells:
                remaining.append(line)
                continue

            # 行里含「指数 / 名称 / 代码 / 标的」等标识列时，整行保留在正文，
            # 不要把标识名和它的数值拆到两个地方（否则读者看不出数字属于谁）。
            if any(_clean_inline(key).strip() in _METRIC_SKIP_KEYS for key, _ in cells):
                remaining.append(line)
                continue

            numeric: List[Tuple[str, str]] = []
            table_cells: List[Tuple[str, str]] = []
            has_numeric = False
            long_text: List[Tuple[str, str]] = []
            for key, value in cells:
                if _is_metric_cell(key, value):
                    numeric.append((_metric_label(key), value))
                    table_cells.append((_metric_label(key), value))
                    has_numeric = True
                elif _is_short_text_cell(key, value):
                    table_cells.append((_metric_label(key), value))
                else:
                    long_text.append((key, value))

            # 只有「数值占多数」的行才整行进表。否则像「指数：上证指数 |
            # 涨跌幅：+0.8% | 量能：放量」这种行会把标识列（指数名）留在
            # 正文、只剩涨跌幅进表，读者反而看不懂表格在描述谁。
            if not has_numeric or len(numeric) * 2 < len(cells):
                remaining.append(line)
                continue

            # 与数字同行的短文本（来源、窗口、动作）按原顺序一起进表，
            # 避免留下只有「行情来源」一行的孤立小节。
            section_metrics.extend(table_cells)
            if long_text:
                rebuilt = " | ".join(f"{key}：{value}" for key, value in long_text)
                remaining.append(f"• {rebuilt}")

        deduped: List[Tuple[str, str]] = []
        for key, value in section_metrics:
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append((key, value))
        if deduped:
            rows.extend((category, key, value) for key, value in deduped)

        plans.append((section, remaining))

    rows.sort(key=lambda row: _METRIC_CATEGORY_PRIORITY.get(row[0], 9))
    return rows, plans


def _build_group_elements(
    group: List[_Section],
    *,
    field_budget: int,
    table_budget: int,
    summary_mode: bool = False,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """渲染一个板块组（摘要 / 单只股票），返回元素、字段块数和表格数。"""
    elements: List[Dict[str, Any]] = []
    used_fields = 0
    used_tables = 0
    head, rest = group[0], group[1:]

    if summary_mode:
        summary_rows: List[Tuple[str, str]] = []
        other_lines: List[str] = []
        for line in head.lines:
            parsed = _parse_stock_summary(line)
            if parsed:
                summary_rows.append(parsed)
            else:
                other_lines.append(line)

        if len(summary_rows) >= 1 and table_budget > 0:
            if head.title:
                elements.append(_div(_section_heading(head)))
            elements.append(_summary_table(summary_rows))
            used_tables += 1
            if any(line.strip() for line in other_lines):
                blocks, used = _render_blocks(
                    _Section(0, "", other_lines),
                    field_budget - used_fields,
                )
                elements.extend(blocks)
                used_fields += used
        else:
            # 单只股票时一句摘要用双列字段更紧凑，不占用表格名额。
            blocks, used = _render_blocks(head, field_budget, summary_mode=True)
            elements.extend(blocks)
            used_fields += used

        for section in rest:
            if not section.has_content():
                continue
            blocks, used = _render_blocks(section, field_budget - used_fields)
            elements.extend(blocks)
            used_fields += used
        return elements, used_fields, used_tables

    # 个股组：head 是股票标题，数值行来自其后的行情/技术/点位小节；
    # 单节文档（如仅一段行情快照）则 head 自身就带数值行。
    rows, plans = _collect_metric_rows([head] + rest)
    if len(rows) >= 2:
        head_section, head_remaining = plans[0]
        blocks, used = _render_blocks(
            _Section(
                head_section.level,
                head_section.title,
                head_remaining,
                head_section.divider_before,
            ),
            field_budget,
        )
        elements.extend(blocks)
        used_fields += used
        elements.extend(_metric_block_elements(rows))
        for section, remaining in plans[1:]:
            if not any(line.strip() for line in remaining):
                continue
            rebuilt_section = _Section(
                section.level,
                section.title,
                remaining,
                section.divider_before,
            )
            blocks, used = _render_blocks(
                rebuilt_section,
                field_budget - used_fields,
            )
            elements.extend(blocks)
            used_fields += used
        return elements, used_fields, used_tables

    blocks, used = _render_blocks(head, field_budget)
    elements.extend(blocks)
    used_fields += used
    for section in rest:
        if not section.has_content():
            continue
        blocks, used = _render_blocks(section, field_budget - used_fields)
        elements.extend(blocks)
        used_fields += used

    return elements, used_fields, used_tables


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
            # 宽数据行（如大盘「指数结构」的指数表）以首列为行标识：自成一块、
            # 行内「标签：值」展示，避免标识名与数值被换行拆开；窄表仍走原网格。
            if (
                len(cells) >= 5
                and _clean_inline(cells[0][0]).strip() in _METRIC_SKIP_KEYS
            ):
                flush_fields()
                if used_blocks >= field_budget:
                    restored = " | ".join(f"{key}：{value}" for key, value in cells)
                    text_buffer.append(f"• {restored}")
                else:
                    for start in range(0, len(cells), _FIELDS_PER_BLOCK):
                        blocks.append(
                            _fields_element(
                                cells[start:start + _FIELDS_PER_BLOCK],
                                short_limit=24,
                                inline=True,
                            )
                        )
                        used_blocks += 1
                continue
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


# ----------------------------------------------------------------------
# 结构化信号区
#
# 通知链路已经把个股 / 大盘的结构化结果（action_label、sentiment_score、
# battle_plan.sniper_points、market_light 等）算好放进 render_context，
# 卡片优先用它渲染「动作徽章 + 评分分级 + 关键点位」；结构化数据缺失时
# 返回空列表，卡片继续走原有文本解析，行为完全不变。
#
# 阈值与 src/share_image.py::_tone_for_score 保持同一口径。
# ----------------------------------------------------------------------

_SIGNAL_SCORE_POSITIVE = 60
_SIGNAL_SCORE_NEGATIVE = 40
_MARKET_STATUS_COLORS = {"green": "green", "yellow": "orange", "red": "red"}


def _as_mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lark_text(value: Any) -> str:
    """转义会破坏 lark_md 的尖括号，避免结构化字段注入标签。"""
    return str(value or "").replace("<", "‹").replace(">", "›")


def _signal_text(value: Any, limit: int = 24) -> str:
    if value is None:
        return ""
    return _clamp(_lark_text(_clean_inline(str(value))), limit)


def _signal_number_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _clamp(_lark_text(_clean_inline(str(value))), 18)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _signal_sniper_text(value: Any) -> str:
    """把点位字段（标量 / 字典 / 列表）压成卡片单元格里的短文本。"""
    if isinstance(value, Mapping):
        for key in ("value", "price", "text", "level", "target"):
            nested = value.get(key)
            if nested not in (None, ""):
                return _signal_sniper_text(nested)
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_signal_sniper_text(item) for item in value[:2]]
        return "–".join(part for part in parts if part)
    return _signal_number_text(value)


def _signal_action_color(action: str) -> str:
    """动作 -> 标签色：买绿 / 卖红 / 观望持有橙 / 未知灰。

    信号区徽章与摘要表动作列共用同一色板，保证两处结论颜色一致。
    """
    normalized = (action or "").lower()
    if any(term in normalized for term in ("买", "加仓", "buy", "add", "long")):
        return "green"
    if any(term in normalized for term in ("卖", "减仓", "回避", "sell", "reduce", "avoid", "short")):
        return "red"
    if any(term in normalized for term in ("观望", "持有", "中性", "watch", "hold", "neutral")):
        return "orange"
    return "grey"


def _signal_score_color(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "grey"
    if value > _SIGNAL_SCORE_POSITIVE:
        return "green"
    if value < _SIGNAL_SCORE_NEGATIVE:
        return "red"
    return "orange"


def _columns_element(cells: List[Tuple[str, str]]) -> Dict[str, Any]:
    """把若干「标签 + 值」渲染成 Card JSON 1.0 的 column_set 并排卡。

    标签灰色小字、值由调用方决定强调样式，两者同行展示，数字不换行。
    """
    columns = [
        {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "vertical_align": "center",
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"<font color='grey'>{label}</font>：{value}",
                    },
                }
            ],
        }
        for label, value in cells
    ]
    return {"tag": "column_set", "flex_mode": "none", "columns": columns}


def _extract_stock_signal(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    dashboard = _as_mapping(payload.get("dashboard"))
    battle = _as_mapping(dashboard.get("battle_plan"))
    sniper = _as_mapping(battle.get("sniper_points"))

    action = _signal_text(
        payload.get("action_label") or payload.get("operation_advice"), 16
    )
    score = payload.get("sentiment_score")
    trend = _signal_text(payload.get("trend_prediction"), 16)

    points: List[Tuple[str, str]] = []
    for key, label in (
        ("ideal_buy", "🎯 理想买"),
        ("stop_loss", "🛑 止损"),
        ("take_profit", "🎯 目标"),
    ):
        value = _signal_sniper_text(sniper.get(key))
        if value:
            points.append((label, value))

    if not action and score in (None, "") and not trend and not points:
        return None
    return {"action": action, "score": score, "trend": trend, "points": points}


def _extract_market_signal(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    light = _as_mapping(payload.get("market_light"))
    if not light:
        return None
    label = _signal_text(light.get("label"), 12)
    score = light.get("score")
    temperature = _signal_text(light.get("temperature_label"), 12)
    if not label and score in (None, "") and not temperature:
        return None
    return {
        "status": str(light.get("status") or ""),
        "label": label,
        "score": score,
        "temperature": temperature,
    }


def _render_stock_signal_zone(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    head: List[Tuple[str, str]] = []
    if signal.get("action"):
        color = _signal_action_color(signal["action"])
        head.append(("操作", f"<text_tag color='{color}'>{signal['action']}</text_tag>"))
    if signal.get("score") not in (None, ""):
        color = _signal_score_color(signal["score"])
        head.append(
            (
                "评分",
                f"<font color='{color}'>**{_signal_number_text(signal['score'])}**</font>/100",
            )
        )
    if signal.get("trend"):
        head.append(("趋势", f"**{signal['trend']}**"))
    if head:
        elements.append(_columns_element(head))
    if signal.get("points"):
        elements.append(
            _columns_element([(label, f"**{value}**") for label, value in signal["points"]])
        )
    return elements


def _render_market_signal_zone(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    color = _MARKET_STATUS_COLORS.get(signal.get("status", ""), "grey")
    cells: List[Tuple[str, str]] = []
    if signal.get("label"):
        cells.append(("市场情绪", f"<text_tag color='{color}'>{signal['label']}</text_tag>"))
    if signal.get("score") not in (None, ""):
        cells.append(
            (
                "情绪分",
                f"<font color='{color}'>**{_signal_number_text(signal['score'])}**</font>/100",
            )
        )
    if signal.get("temperature"):
        cells.append(("温度", f"**{signal['temperature']}**"))
    return [_columns_element(cells)] if cells else []


def _build_signal_zone(render_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 render_context.structured_payload 渲染信号区；不可用时返回空列表。"""
    if not isinstance(render_context, Mapping):
        return []
    payload = render_context.get("structured_payload")
    if not isinstance(payload, Mapping):
        return []
    try:
        if payload.get("kind") == "market_review" or "market_light" in payload:
            market_signal = _extract_market_signal(payload)
            return _render_market_signal_zone(market_signal) if market_signal else []
        stock_signal = _extract_stock_signal(payload)
        return _render_stock_signal_zone(stock_signal) if stock_signal else []
    except Exception:  # 结构化渲染异常时静默回退到文本卡片
        return []


def _build_elements(
    sections: List[_Section],
    preamble: List[str],
    notes: List[str],
    *,
    include_disclaimer: bool = False,
    render_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    field_blocks = 0
    table_blocks = 0

    signal_elements = _build_signal_zone(render_context)
    if signal_elements:
        elements.extend(signal_elements)
        _append_hr(elements)

    preamble_text = _render_preamble(preamble)
    if preamble_text:
        elements.append(_div(preamble_text))

    for group in _group_sections(sections):
        if not group:
            continue
        head = group[0]
        group_elements, used_fields, used_tables = _build_group_elements(
            group,
            field_budget=_MAX_FIELD_BLOCKS - field_blocks,
            table_budget=_MAX_TABLES - table_blocks,
            summary_mode=_is_summary_section(head.title),
        )
        if not group_elements:
            continue
        field_blocks += used_fields
        table_blocks += used_tables
        if head.level == 2 or head.divider_before:
            _append_hr(elements)
        elements.extend(group_elements)

    note = _note(notes, include_disclaimer=include_disclaimer)
    if note is not None:
        _append_hr(elements)
        elements.append(note)
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
    *,
    include_disclaimer: bool = False,
    render_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """轻卡片：按板块合并为少量 div，保留分节但去掉字段/标签开销。"""
    elements: List[Dict[str, Any]] = []
    signal_elements = _build_signal_zone(render_context)
    if signal_elements:
        elements.extend(signal_elements)
        _append_hr(elements)
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

    note = _note(notes, include_disclaimer=include_disclaimer)
    if note is not None:
        _append_hr(elements)
        elements.append(note)
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


def build_feishu_card(
    content: str,
    *,
    max_bytes: Optional[int] = None,
    include_disclaimer: bool = False,
    render_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建飞书 interactive 卡片的 body（不含 ``msg_type`` 外层）。

    Args:
        content: 已经过 ``format_feishu_markdown`` 转换的 lark_md 正文。
        max_bytes: 卡片 JSON 的字节预算；超限时按富卡片 -> 轻卡片 -> 紧凑卡片
            逐级降级，避免被飞书拒绝。紧凑卡片也会校验大小，超限则返回 None
            由发送层处理。
        include_disclaimer: 仅分析报告推送为 True，页脚补统一 AI 生成声明；
            其他飞书消息不补声明。
        render_context: 可选渲染上下文（``route_type`` / ``report_type`` /
            ``results`` / ``structured_payload``）。有结构化数据时富卡片顶部
            渲染「信号区」；缺省 None 时行为与旧版逐字节一致。

    Returns:
        Card JSON 1.0 字典。任何情况下都不会丢弃正文信息。紧凑卡片超限时返回 None。
    """
    body_lines, notes = _extract_footer(content.splitlines())
    sections = _parse_sections(body_lines)
    title, preamble, body = _split_document(sections)
    summary, preamble = _extract_summary(preamble)
    header = _header(title, summary, content)

    if len(content) >= _RICH_CARD_MIN_CHARS and body:
        elements = _build_elements(
            body,
            preamble,
            notes,
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )
        if 0 < len(elements) <= _MAX_ELEMENTS:
            card = {"config": _config(), "header": header, "elements": elements}
            if _fits(card, max_bytes):
                return card

        lite_elements = _build_lite_elements(
            body,
            preamble,
            notes,
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )
        if 0 < len(lite_elements) <= _MAX_ELEMENTS:
            card = {"config": _config(), "header": header, "elements": lite_elements}
            if _fits(card, max_bytes):
                return card

    # 紧凑卡片：用已剥离页脚的正文，避免声明同时出现在正文与 note。
    compact_source = "\n".join(body_lines)
    compact_body = _strip_summary_line(_strip_title_line(compact_source, title), summary)
    signal_elements = _build_signal_zone(render_context)

    def _compact_card(include_signal: bool) -> Dict[str, Any]:
        compact_elements: List[Dict[str, Any]] = []
        if include_signal:
            compact_elements.extend(signal_elements)
        if compact_body.strip():
            compact_elements.append(_div(compact_body))
        note = _note(notes, include_disclaimer=include_disclaimer)
        if note is not None:
            compact_elements.append(note)
        if not compact_elements:
            compact_elements = [_div("")]
        return {"config": _config(), "header": header, "elements": compact_elements}

    # 优先带信号区；超预算时退回纯紧凑卡片，最后才由发送层走纯文本兜底。
    attempts = (True, False) if signal_elements else (False,)
    for include_signal in attempts:
        compact_card = _compact_card(include_signal)
        if _fits(compact_card, max_bytes):
            return compact_card
    return None


# ----------------------------------------------------------------------
# 公开只读 API：HTML 报告渲染（src/services/report_html）与飞书卡片
# 共用同一份解析，保证两处输出的信息一致。以下入口只读不写，
# 不改变 build_feishu_card 的任何行为。
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ReportDocument:
    """飞书推送内容的中间模型：标题 / 摘要 / 章节结构 / 脚注。

    输入必须是已经过 ``format_feishu_markdown`` 转换的 lark_md 正文，
    与 ``build_feishu_card`` 的输入完全一致。
    """

    title: str
    summary: str
    theme: str
    preamble: List[str]
    sections: List[_Section]
    notes: List[str]


def parse_report_document(
    content: str,
    *,
    include_disclaimer: bool = False,
) -> ReportDocument:
    """按 ``build_feishu_card`` 同一顺序解析文档结构，返回只读模型。

    Args:
        content: 已经过 ``format_feishu_markdown`` 转换的 lark_md 正文。
        include_disclaimer: 与报告路由推送保持一致时传 True，
            脚注会按需补齐统一 AI 生成声明。

    Returns:
        ReportDocument。解析逻辑全部复用卡片内部函数，不复制实现。
    """
    body_lines, notes = _extract_footer((content or "").splitlines())
    sections = _parse_sections(body_lines)
    title, preamble, body = _split_document(sections)
    summary, preamble = _extract_summary(preamble)
    deduped_notes = _dedupe_notes(notes, include_disclaimer=include_disclaimer)
    return ReportDocument(
        title=title,
        summary=summary,
        theme=_template_for(title, content or ""),
        preamble=preamble,
        sections=body,
        notes=deduped_notes,
    )


def section_tone(title: str) -> str:
    """章节标题的语义色；卡片 ``text_tag`` 与 HTML 报告共用。"""
    return _color_for_title(title)


def parse_field_row(line: str) -> Optional[List[Tuple[str, str]]]:
    """解析「• 键：值 | 键：值」紧凑行；解析不了返回 None。"""
    return _parse_field_cells(line)


def parse_stock_summary(line: str) -> Optional[Tuple[str, str]]:
    """解析「⚪ **名称(代码)**: 摘要」的个股摘要行。"""
    return _parse_stock_summary(line)


def is_summary_section(title: str) -> bool:
    """判断章节是否为「摘要 / Summary / 概览」类板块。"""
    return _is_summary_section(title)


def format_quote_line(line: str) -> str:
    """引用行的分类结果：灰色引用 / 💡 决策高亮 / 原文不变。"""
    return _format_quote(line)
