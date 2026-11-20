# -*- coding: utf-8 -*-
"""飞书卡片文档解析入口测试。

覆盖两件事：
1. ``build_feishu_card()`` 对既有样本的输出与基线快照逐字节一致
   （H1 只新增公开入口、抽取共用函数，不改卡片行为）；
2. ``parse_report_document()`` 与 ``build_feishu_card()`` 解析出同一份结构。
"""

import json
from pathlib import Path

import pytest

from src.formatters import format_feishu_markdown
from src.notification_sender.feishu_card import (
    build_feishu_card,
    parse_report_document,
    plan_field_grid,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notification_reports"
SAMPLES = ["single_stock_report", "aggregate_report", "market_review_report"]


def _sample(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def _card_text(card) -> str:
    parts = []
    for element in card.get("elements", []):
        text = element.get("text", {})
        if text.get("tag") == "lark_md":
            parts.append(text.get("content", ""))
        for field in element.get("fields", []):
            parts.append(field.get("text", {}).get("content", ""))
    return "\n".join(parts)


@pytest.mark.parametrize("name", SAMPLES)
def test_feishu_card_output_matches_baseline_snapshot(name):
    """卡片输出与基线快照逐字节一致，证明 H1 未改变飞书行为。"""
    baseline = json.loads(
        (FIXTURES / f"{name}.feishu_card.json").read_text(encoding="utf-8")
    )
    current = build_feishu_card(format_feishu_markdown(_sample(name)))
    assert json.dumps(current, ensure_ascii=False, sort_keys=True) == json.dumps(
        baseline, ensure_ascii=False, sort_keys=True
    )


@pytest.mark.parametrize("name", SAMPLES)
def test_parse_report_document_matches_card_structure(name):
    lark = format_feishu_markdown(_sample(name))
    card = build_feishu_card(lark)
    doc = parse_report_document(lark, include_disclaimer=True)

    # 卡片标题栏必须与解析出的文档标题一致（无标题时回退到通用标题）。
    header_title = card["header"]["title"]["content"]
    assert doc.title in header_title or header_title in (doc.title or "")

    # 解析出的二级章节标题必须出现在卡片正文里（富卡片与紧凑卡片都成立）。
    card_text = _card_text(card)
    for section in doc.sections:
        if section.level == 2 and section.title:
            assert section.title in card_text, section.title


def test_plan_field_grid_alignment_rules():
    """短字段双列、长字段整组全宽、奇数末项拉满。"""
    short = [("A", "1"), ("B", "2")]
    assert [item["full"] for item in plan_field_grid(short)] == [False, False]

    mixed = [("A", "1"), ("B", "很长的值" * 20)]
    assert [item["full"] for item in plan_field_grid(mixed)] == [True, True]

    odd = [("A", "1"), ("B", "2"), ("C", "3")]
    assert [item["full"] for item in plan_field_grid(odd)] == [False, False, True]
