# -*- coding: utf-8 -*-
"""飞书结构化消息卡片与排版转换的回归测试。"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from src.formatters import (
    chunk_content_by_max_bytes,
    format_feishu_markdown,
    strip_hidden_markdown_metadata,
)
from src.notification_sender.feishu_card import CARD_TITLE, build_feishu_card
from src.notification_sender.feishu_sender import FeishuSender
from src.report_language import REPORT_AI_DISCLAIMER

REPORT = """# 🎯 2026-11-09 决策仪表盘

> 共分析 **1** 只股票 | 🟢买入:0 🟡观望:1 🔴卖出:0
市场状态：A股 · 盘前

## 📊 分析结果摘要

⚪ **贵州茅台(600519.SH)**: 观望 | 评分 58 | 震荡

---

## ⚪ 贵州茅台 (600519.SH)

### 📰 重要信息速览

**💭 舆情情绪**: 机构看好板块重估，但短期市场普跌导致防御心理占据主导。
**📊 业绩预期**: 2026年前三季度营收增长1.8%，业绩表现平稳但增速放缓。

**🚨 风险警报**:
- 风险点1：2026-11-06 大盘普跌，市场情绪极度低迷，买盘退潮。
- 风险点2：RSI指标进入超买区，短期存在技术性回调压力。

### 📌 核心结论

**⚪ 观望** | 震荡

> **一句话决策**: 均线多头但大盘极差且短期超买，建议等待回踩MA10支撑确认。

| 持仓情况 | 操作建议 |
|---------|---------|
| 🆕 **空仓者** | 暂不追高，等待股价缩量回调至MA10附近观察支撑。 |
| 💼 **持仓者** | 继续持有，以MA20作为中期防守位，跌破则减仓。 |

### 📈 当日行情

| 当前价 | 量比 | 换手率 | 行情来源 |
|-------|------|--------|----------|
| 1453.00 | 0.65 | 0.34% | 腾讯财经 |

### 🛡️ 盘中决策护栏

**观察条件**:
- 观察开盘是否受大盘拖累低开跌破1450元
- 观察开盘后量比是否恢复至正常区间

*📋 数据来源：腾讯行情、AkShare筹码*
*报告生成时间：2026-11-09 08:45:46*
"""


def _formatted_report() -> str:
    return format_feishu_markdown(strip_hidden_markdown_metadata(REPORT))


def _card_text(card: dict) -> str:
    """收集卡片内所有可见文本，用于校验正文信息没有丢失。"""
    parts = []
    for element in card["elements"]:
        text = element.get("text")
        if isinstance(text, dict):
            parts.append(text.get("content", ""))
        for item in element.get("fields", []):
            parts.append(item.get("text", {}).get("content", ""))
        for row in element.get("rows", []):
            parts.extend(str(value) for value in row.values())
        for note_element in element.get("elements", []):
            parts.append(note_element.get("content", ""))
    return "\n".join(parts)


def _config(**overrides):
    defaults = dict(
        feishu_webhook_url=None,
        feishu_webhook_secret=None,
        feishu_webhook_keyword=None,
        feishu_max_bytes=20000,
        feishu_send_as_file=False,
        webhook_verify_ssl=True,
        feishu_app_id="",
        feishu_app_secret="",
        feishu_chat_id="",
        feishu_receive_id_type="chat_id",
        feishu_domain="feishu",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_short_message_keeps_compact_card_and_full_content():
    card = build_feishu_card("股票日报\nhello")

    assert card["header"]["title"]["content"] == CARD_TITLE
    assert card["header"]["template"] == "blue"
    assert card["header"]["subtitle"]["content"]
    assert len(card["elements"]) == 1
    assert card["elements"][0]["text"]["content"] == "股票日报\nhello"


def test_report_title_and_summary_move_into_header():
    card = build_feishu_card(_formatted_report(), max_bytes=20000)

    assert card["header"]["title"]["content"] == "🎯 2026-11-09 决策仪表盘"
    # 计数提升为标题栏彩色后缀标签，副标题只留标的数量。
    assert card["header"]["subtitle"]["content"] == "共分析 1 只股票"
    tags = card["header"]["text_tag_list"]
    assert [tag["text"]["content"] for tag in tags] == [
        "🟢 买入 0",
        "🟡 观望 1",
        "🔴 卖出 0",
    ]

    body = _card_text(card)
    assert "🎯 2026-11-09 决策仪表盘" not in body
    assert "共分析" not in body


def test_long_report_is_split_into_sections_with_fields_and_note():
    formatted = _formatted_report()
    card = build_feishu_card(formatted, max_bytes=20000)
    tags = [element["tag"] for element in card["elements"]]

    assert tags.count("hr") >= 2
    assert "note" in tags
    assert any("fields" in element for element in card["elements"])
    assert any(
        "<text_tag" in element.get("text", {}).get("content", "")
        for element in card["elements"]
    )

    text = _card_text(card)
    for token in (
        "市场状态",
        "贵州茅台",
        "空仓者",
        "腾讯财经",
        "数据来源",
        "报告生成时间",
    ):
        assert token in text, token
    assert "| --- |" not in text


def test_headings_use_semantic_colors():
    formatted = format_feishu_markdown(
        "## 🚨 风险警报\n\n" + "风险说明" * 200 + "\n\n### ✨ 利好催化\n\n" + "机会说明" * 200
    )
    card = build_feishu_card(formatted, max_bytes=20000)
    headings = " ".join(
        element["text"]["content"]
        for element in card["elements"]
        if "text_tag" in element.get("text", {}).get("content", "")
    )

    assert "<text_tag color='red'>" in headings
    assert "<text_tag color='green'>" in headings


def test_header_template_follows_report_type():
    review = "# 🎯 大盘复盘\n\n" + "盘面描述" * 300
    assert build_feishu_card(review)["header"]["template"] == "indigo"
    assert build_feishu_card(_formatted_report())["header"]["template"] == "blue"


def test_card_over_budget_returns_none_for_pagination():
    """紧凑卡片超限时返回 None，由发送层处理分页或纯文本兜底。"""
    formatted = _formatted_report()
    card = build_feishu_card(formatted, max_bytes=1000)

    # 当所有卡片类型都超限时，返回 None
    assert card is None, "超限时应返回 None，由发送层处理"


def test_page_marker_and_footer_metadata_move_to_note():
    content = _formatted_report() + "\n\n📄 2/3"
    card = build_feishu_card(content, max_bytes=20000)
    note = next(element for element in card["elements"] if element["tag"] == "note")
    note_text = " ".join(item["content"] for item in note["elements"])

    assert "第 2/3 页" in note_text
    assert "数据来源" in note_text
    assert note_text.startswith("📋 数据来源")
    assert "📋 📋" not in note_text
    assert "📄 2/3" not in _card_text(card)


def test_position_table_becomes_named_fields_without_duplicate_headers():
    card = build_feishu_card(_formatted_report(), max_bytes=20000)
    fields_text = " ".join(
        item["text"]["content"]
        for element in card["elements"]
        for item in element.get("fields", [])
    )

    assert "**🆕 空仓者**" in fields_text
    assert "**💼 持仓者**" in fields_text
    assert "持仓情况" not in fields_text
    assert "操作建议" not in fields_text


def test_quote_decision_is_highlighted_instead_of_grey():
    card = build_feishu_card(_formatted_report(), max_bytes=20000)
    text = _card_text(card)
    decision_lines = [
        element["text"]["content"]
        for element in card["elements"]
        if "一句话决策" in element.get("text", {}).get("content", "")
    ]

    assert decision_lines
    assert all("<font color='grey'>" not in line for line in decision_lines)
    assert "💡 **一句话决策**" in text


def test_formatter_converts_two_column_tables_to_row_keys():
    result = format_feishu_markdown(
        "| 指标 | 数值 |\n| --- | --- |\n| 当前价 | 1453.00 |\n| 量比 | 0.65 |"
    )

    assert "• 当前价：1453.00" in result
    assert "• 量比：0.65" in result
    assert "指标：" not in result


def test_formatter_keeps_heading_hierarchy_and_grey_quotes():
    result = format_feishu_markdown("# 一级\n\n\n\n## 二级\n### 三级\n> 引用")

    assert "**一级**" in result
    assert "▎ **二级**" in result
    assert "· **三级**" in result
    assert "<font color='grey'>💬 引用</font>" in result
    assert "\n\n\n" not in result


def test_wide_market_snapshot_becomes_per_category_blocks():
    formatted = format_feishu_markdown(
        "## 行情快照\n\n"
        "| 收盘 | 昨收 | 开盘 | 最高 | 最低 | 涨跌幅 | 涨跌额 | 振幅 | 成交量 | 成交额 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 251.30 | 246.80 | 247.50 | 253.60 | 245.20 | +1.82% | +4.50 | 3.40% | 28.6万手 | 71.2亿 |\n\n"
        + "盘面描述" * 120
    )
    card = build_feishu_card(formatted, max_bytes=20000)
    tables = [element for element in card["elements"] if element["tag"] == "table"]

    # 十列快照不再单独占一张表，数值按分类拆成小版块，指标与数值同行。
    assert tables == []
    text = _card_text(card)
    assert "<font color='grey'>**行情**</font>" in text
    fields = " ".join(
        item["text"]["content"]
        for element in card["elements"]
        for item in element.get("fields", [])
    )
    assert "**收盘**：251.30" in fields
    assert "**成交额**：71.2亿" in fields


def test_chunking_can_split_on_formatted_level_two_headings():
    content = "▎ **第一节**\n" + "A" * 100 + "\n▎ **第二节**\n" + "B" * 100
    chunks = chunk_content_by_max_bytes(content, 160)

    assert len(chunks) > 1
    assert "▎ **第一节**" in chunks[0]
    assert "▎ **第二节**" in "".join(chunks)


@mock.patch("src.notification_sender.feishu_sender.requests.post")
def test_webhook_sends_structured_card(mock_post):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"code": 0}
    mock_post.return_value = response
    sender = FeishuSender(_config(feishu_webhook_url="https://feishu.example/hook"))

    assert sender.send_to_feishu(REPORT) is True
    card = json.loads(mock_post.call_args.kwargs["data"])["card"]
    assert len(card["elements"]) > 3
    assert "<text_tag" in _card_text(card)


@mock.patch.object(FeishuSender, "_app_send_raw", return_value=True)
@mock.patch.object(FeishuSender, "_ensure_app_client", return_value=object())
def test_app_bot_sends_structured_card(_mock_client, mock_raw):
    sender = FeishuSender(
        _config(feishu_app_id="cli_app", feishu_app_secret="secret", feishu_chat_id="oc_chat")
    )

    assert sender.send_to_feishu(REPORT) is True
    assert mock_raw.call_args[0][1] == "interactive"
    card = json.loads(mock_raw.call_args[0][2])
    assert any(element["tag"] == "hr" for element in card["elements"])


class UnifiedDisclaimerCardTest(unittest.TestCase):
    def test_rich_card_ends_with_unified_disclaimer(self):
        card = build_feishu_card(
            _formatted_report(), max_bytes=20000, include_disclaimer=True
        )
        note = next(e for e in card["elements"] if e["tag"] == "note")
        note_text = note["elements"][0]["content"]
        self.assertTrue(note_text.endswith(REPORT_AI_DISCLAIMER))

    def test_compact_card_also_keeps_disclaimer(self):
        card = build_feishu_card("股票日报\nhello", include_disclaimer=True)
        self.assertEqual(card["elements"][-1]["tag"], "note")
        note_text = card["elements"][-1]["elements"][0]["content"]
        self.assertIn(REPORT_AI_DISCLAIMER, note_text)
        self.assertNotIn(REPORT_AI_DISCLAIMER, card["elements"][0]["text"]["content"])

    def test_non_report_card_has_no_disclaimer(self):
        card = build_feishu_card("股票日报\nhello")
        self.assertEqual(len(card["elements"]), 1)
        self.assertNotIn(REPORT_AI_DISCLAIMER, _card_text(card))

    def test_legacy_disclaimer_is_replaced_in_note(self):
        content = _formatted_report() + "\n*仅供研究参考 · 不构成投资建议*"
        card = build_feishu_card(content, max_bytes=20000, include_disclaimer=True)
        text = _card_text(card)
        self.assertNotIn("仅供研究参考 · 不构成投资建议", text)
        self.assertIn(REPORT_AI_DISCLAIMER, text)


# ---------------------------------------------------------------------------
# 结构化信号区
# ---------------------------------------------------------------------------

SINGLE_STOCK_CONTEXT = {
    "route_type": "report",
    "report_type": "single",
    "structured_payload": {
        "name": "贵州茅台",
        "code": "600519.SH",
        "action_label": "观望",
        "sentiment_score": 58,
        "trend_prediction": "震荡",
        "dashboard": {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "1450.00",
                    "stop_loss": "1420.00",
                    "take_profit": "1520.00",
                }
            }
        },
    },
}


def _column_text(card):
    """收集卡片中所有 column_set 单元格的文本。"""
    parts = []
    for element in card["elements"]:
        if element.get("tag") != "column_set":
            continue
        for column in element.get("columns", []):
            for sub in column.get("elements", []):
                parts.append(sub.get("text", {}).get("content", ""))
    return "\n".join(parts)


def test_signal_zone_renders_action_score_and_points():
    card = build_feishu_card(
        _formatted_report(), max_bytes=20000, render_context=SINGLE_STOCK_CONTEXT
    )

    assert card["elements"][0]["tag"] == "column_set"
    zone = _column_text(card)
    assert "<font color='grey'>操作</font>" in zone
    assert "<text_tag color='orange'>观望</text_tag>" in zone
    assert "<font color='grey'>评分</font>" in zone
    assert "<font color='orange'>**58**</font>/100" in zone
    assert "<font color='grey'>🎯 理想买</font>" in zone and "**1450**" in zone
    assert "<font color='grey'>🛑 止损</font>" in zone and "**1420**" in zone
    assert "<font color='grey'>🎯 目标</font>" in zone and "**1520**" in zone


def test_signal_zone_absent_without_render_context():
    card = build_feishu_card(_formatted_report(), max_bytes=20000)

    assert all(element.get("tag") != "column_set" for element in card["elements"])


def test_signal_zone_hidden_when_structured_payload_empty():
    card = build_feishu_card(
        _formatted_report(),
        max_bytes=20000,
        render_context={"route_type": "report", "structured_payload": None},
    )

    assert all(element.get("tag") != "column_set" for element in card["elements"])


def test_signal_zone_skips_missing_fields():
    context = {"structured_payload": {"action_label": "买入", "sentiment_score": 72}}
    card = build_feishu_card(
        _formatted_report(), max_bytes=20000, render_context=context
    )
    zone = _column_text(card)

    assert "<text_tag color='green'>买入</text_tag>" in zone
    assert "<font color='green'>**72**</font>/100" in zone
    # 没有 sniper_points 时不渲染点位行。
    assert "理想买" not in zone


def test_signal_score_and_action_color_thresholds():
    from src.notification_sender.feishu_card import (
        _signal_action_color,
        _signal_score_color,
    )

    assert _signal_action_color("买入") == "green"
    assert _signal_action_color("卖出") == "red"
    assert _signal_action_color("观望") == "orange"
    assert _signal_action_color("持有") == "orange"
    assert _signal_score_color(72) == "green"
    assert _signal_score_color(58) == "orange"
    assert _signal_score_color(30) == "red"


def test_market_signal_zone_renders_market_light():
    market_report = "# 大盘复盘\n\n## 指数表现\n\n" + "盘面描述" * 120
    context = {
        "structured_payload": {
            "kind": "market_review",
            "market_light": {
                "status": "green",
                "label": "可进攻",
                "score": 72,
                "temperature_label": "偏热",
            },
        }
    }
    card = build_feishu_card(
        format_feishu_markdown(market_report),
        max_bytes=20000,
        render_context=context,
    )
    zone = _column_text(card)

    assert "市场情绪" in zone
    assert "<text_tag color='green'>可进攻</text_tag>" in zone
    assert "情绪分" in zone
    assert "<font color='green'>**72**</font>/100" in zone
    assert "温度" in zone


@mock.patch("src.notification_sender.feishu_sender.requests.post")
def test_webhook_forwards_render_context_to_signal_zone(mock_post):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"code": 0}
    mock_post.return_value = response
    sender = FeishuSender(_config(feishu_webhook_url="https://feishu.example/hook"))

    assert sender.send_to_feishu(REPORT, render_context=SINGLE_STOCK_CONTEXT) is True
    card = json.loads(mock_post.call_args.kwargs["data"])["card"]
    assert any(element.get("tag") == "column_set" for element in card["elements"])


@mock.patch.object(FeishuSender, "_app_send_raw", return_value=True)
@mock.patch.object(FeishuSender, "_ensure_app_client", return_value=object())
def test_app_bot_forwards_render_context_to_signal_zone(_mock_client, mock_raw):
    sender = FeishuSender(
        _config(feishu_app_id="cli_app", feishu_app_secret="secret", feishu_chat_id="oc_chat")
    )

    assert sender.send_to_feishu(REPORT, render_context=SINGLE_STOCK_CONTEXT) is True
    card = json.loads(mock_raw.call_args[0][2])
    assert any(element.get("tag") == "column_set" for element in card["elements"])


def test_chunked_send_only_first_chunk_gets_render_context():
    """分片发送时信号区只作用于首片，续页不重复渲染。"""
    sender = FeishuSender(_config(feishu_max_bytes=20000))
    captured = []

    def _fake_send(chunk, **kwargs):
        captured.append(kwargs.get("render_context"))
        return True

    long_content = "▎ **区块**\n" + ("内容" * 300)
    with mock.patch.object(
        FeishuSender, "_send_feishu_message", side_effect=_fake_send
    ), mock.patch("src.notification_sender.feishu_sender.time.sleep"):
        ok = sender._send_feishu_chunked(
            long_content, 200, render_context=SINGLE_STOCK_CONTEXT
        )

    assert ok is True
    assert len(captured) >= 2
    assert captured[0] == SINGLE_STOCK_CONTEXT
    assert all(context is None for context in captured[1:])


# ---------------------------------------------------------------------------
# 原生表格：多标的摘要 + 个股关键数据
# ---------------------------------------------------------------------------

MULTI_STOCK_REPORT = """# 🎯 2026-11-09 决策仪表盘

> 共分析 **2** 只股票 | 🟢买入:1 🟡观望:1 🔴卖出:0

## 📊 分析结果摘要

🟢 **五粮液(000858.SZ)**: 买入 | 评分 72 | 震荡
⚪ **贵州茅台(600519.SH)**: 观望 | 评分 58 | 震荡

---

## 🟢 五粮液 (000858.SZ)

### 📈 当日行情

| 当前价 | 量比 | 换手率 | 行情来源 |
| --- | --- | --- | --- |
| 1453.00 | 0.65 | 0.34% | 腾讯财经 |

### 📊 数据透视

| 当前价 | MA5 | MA10 |
| --- | --- | --- |
| 1453.00 | 1448.20 | 1432.70 |

### 📌 核心结论

| 持仓情况 | 操作建议 |
| --- | --- |
| 🆕 **空仓者** | 暂不追高，等待股价缩量回调至MA10附近观察支撑，企稳后再考虑首仓。 |

---

## ⚪ 贵州茅台 (600519.SH)

### 📈 当日行情

| 收盘 | 涨跌幅 | 成交量 |
| --- | --- | --- |
| 1453.00 | -0.86% | 3.42万手 |
"""


def _tables(card):
    return [element for element in card["elements"] if element["tag"] == "table"]


def test_multi_stock_summary_becomes_native_table():
    card = build_feishu_card(format_feishu_markdown(MULTI_STOCK_REPORT), max_bytes=20000)
    tables = _tables(card)
    summary = tables[0]

    assert [column["display_name"] for column in summary["columns"]] == [
        "标的",
        "动作",
        "评分",
        "趋势",
    ]
    assert summary["page_size"] == 10
    assert summary["row_height"] == "middle"
    assert summary["header_style"]["background_style"] == "grey"
    assert [column["data_type"] for column in summary["columns"]] == [
        "text",
        "options",
        "lark_md",
        "text",
    ]
    assert len(summary["rows"]) == 2
    assert summary["rows"][0] == {
        "name": "五粮液(000858.SZ)",
        "action": [{"text": "买入", "color": "green"}],
        "score": "<font color='green'>**72**</font>/100",
        "trend": "震荡",
    }
    assert summary["rows"][1]["name"] == "贵州茅台(600519.SH)"
    assert summary["rows"][1]["action"] == [{"text": "观望", "color": "orange"}]
    assert summary["rows"][1]["score"] == "<font color='orange'>**58**</font>/100"


def test_numeric_sections_become_per_category_blocks():
    card = build_feishu_card(format_feishu_markdown(MULTI_STOCK_REPORT), max_bytes=20000)
    tables = _tables(card)

    # 只有摘要表是原生 table，个股数值改按分类拆成小版块。
    assert len(tables) == 1
    assert [column["display_name"] for column in tables[0]["columns"]] == [
        "标的",
        "动作",
        "评分",
        "趋势",
    ]

    text = _card_text(card)
    assert "<font color='grey'>**行情**</font>" in text
    assert "<font color='grey'>**技术**</font>" in text

    fields = " ".join(
        item["text"]["content"]
        for element in card["elements"]
        for item in element.get("fields", [])
    )
    # 指标与数值同行，数字不换行
    assert "**当前价**：1453.00" in fields
    assert "**MA5**：1448.20" in fields
    # 同名指标只保留首次出现
    assert fields.count("**当前价**") == 1
    assert "**行情来源**：腾讯财经" in fields


def test_long_text_rows_stay_in_fields_not_tables():
    card = build_feishu_card(format_feishu_markdown(MULTI_STOCK_REPORT), max_bytes=20000)
    table_values = "\n".join(
        str(value)
        for table in _tables(card)
        for row in table["rows"]
        for value in row.values()
    )
    fields_text = " ".join(
        item["text"]["content"]
        for element in card["elements"]
        for item in element.get("fields", [])
    )

    assert "暂不追高" not in table_values
    assert "**🆕 空仓者**" in fields_text


def test_plain_text_columns_keep_no_lark_markup():
    """text 列保持纯文本；动作(options)/评分(lark_md)列才携带语义标记。"""
    card = build_feishu_card(format_feishu_markdown(MULTI_STOCK_REPORT), max_bytes=20000)
    for table in _tables(card):
        type_by_name = {column["name"]: column["data_type"] for column in table["columns"]}
        for row in table["rows"]:
            for name, value in row.items():
                if type_by_name.get(name) == "text":
                    assert "**" not in str(value)
                    assert "<font" not in str(value)
                    assert "<text_tag" not in str(value)


def test_market_review_section_titles_are_not_truncated():
    report = """# A股大盘复盘

> 今日A股震荡。

### 一、盘面总览

两市成交额放大，情绪修复。

### 二、指数结构

| 指数 | 最新 | 涨跌幅 |
|------|------|--------|
| 上证指数 | 3200.00 | ↑ +0.50% |
| 深证成指 | 10450.00 | ↑ +0.80% |
"""
    card = build_feishu_card(format_feishu_markdown(report), max_bytes=30000)
    text = _card_text(card)

    assert "一、盘面总览" in text
    assert "二、指数结构" in text
    # 不再把章节标题截断成「二、指…」这类省略号
    assert "二、指…" not in text
    assert "…" not in "".join(
        line for line in text.splitlines() if "、盘面" in line or "、指数" in line
    )
    # 指数名和它的数值留在同一小节，不再拆开
    assert "上证指数" in text
    assert "3200.00" in text


def test_many_stocks_use_metric_blocks_not_extra_tables():
    stocks = []
    for index in range(6):
        stocks.append(
            f"## 🟢 标的{index} (60000{index}.SH)\n\n"
            "### 📈 当日行情\n\n"
            "| 收盘 | 涨跌幅 | 成交量 |\n| --- | --- | --- |\n| 10.00 | +1.20% | 3.4万手 |\n"
        )
    content = (
        "# 🎯 决策仪表盘\n\n"
        "> 共分析 **6** 只股票 | 🟢买入:6 🟡观望:0 🔴卖出:0\n\n"
        "## 📊 分析结果摘要\n\n"
        + "\n".join(
            f"🟢 **标的{index}(60000{index}.SH)**: 买入 | 评分 7{index} | 震荡"
            for index in range(6)
        )
        + "\n\n"
        + "\n".join(stocks)
    )
    card = build_feishu_card(format_feishu_markdown(content), max_bytes=20000)

    # 只有一张摘要表；六只股票的数值都拆成小版块，不再各占一张表。
    assert len(_tables(card)) == 1
    assert "**行情**" in _card_text(card)


def test_single_stock_summary_renders_one_row_table():
    card = build_feishu_card(_formatted_report(), max_bytes=20000)
    summary_tables = [
        table
        for table in _tables(card)
        if [column["display_name"] for column in table["columns"]]
        == ["标的", "动作", "评分", "趋势"]
    ]

    assert len(summary_tables) == 1
    assert len(summary_tables[0]["rows"]) == 1
    assert summary_tables[0]["rows"][0]["name"] == "贵州茅台(600519.SH)"
    assert summary_tables[0]["rows"][0]["action"] == [{"text": "观望", "color": "orange"}]
