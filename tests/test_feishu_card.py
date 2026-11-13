# -*- coding: utf-8 -*-
"""飞书结构化消息卡片与排版转换的回归测试。"""

import json
from types import SimpleNamespace
from unittest import mock

from src.formatters import (
    chunk_content_by_max_bytes,
    format_feishu_markdown,
    strip_hidden_markdown_metadata,
)
from src.notification_sender.feishu_card import CARD_TITLE, build_feishu_card
from src.notification_sender.feishu_sender import FeishuSender

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
    assert "共分析 1 只股票" in card["header"]["subtitle"]["content"]
    assert "🟡观望:1" in card["header"]["subtitle"]["content"]

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


def test_card_over_budget_falls_back_to_compact_card_without_losing_text():
    formatted = _formatted_report()
    card = build_feishu_card(formatted, max_bytes=1000)
    text = _card_text(card)

    assert len(card["elements"]) >= 1
    assert not any("fields" in element for element in card["elements"])
    for token in ("贵州茅台", "空仓者", "腾讯财经", "数据来源"):
        assert token in text, token


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


def test_wide_market_snapshot_table_becomes_aligned_fields():
    formatted = format_feishu_markdown(
        "## 行情快照\n\n"
        "| 收盘 | 昨收 | 开盘 | 最高 | 最低 | 涨跌幅 | 涨跌额 | 振幅 | 成交量 | 成交额 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 251.30 | 246.80 | 247.50 | 253.60 | 245.20 | +1.82% | +4.50 | 3.40% | 28.6万手 | 71.2亿 |\n\n"
        + "盘面描述" * 120
    )
    card = build_feishu_card(formatted, max_bytes=20000)
    fields = [
        item
        for element in card["elements"]
        for item in element.get("fields", [])
    ]
    labels = [item["text"]["content"].split("\n")[0] for item in fields]

    assert "**收盘**" in labels
    assert "**成交额**" in labels
    assert "指数" not in labels
    assert all(item["is_short"] for item in fields if item["text"]["content"].startswith("**收盘**"))


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
    card = mock_post.call_args.kwargs["json"]["card"]
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
