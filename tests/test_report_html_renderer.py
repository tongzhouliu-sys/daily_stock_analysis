# -*- coding: utf-8 -*-
"""报告 HTML 渲染器测试：结构、与飞书一致性、安全、确定性。"""

from pathlib import Path

import pytest

from src.formatters import format_feishu_markdown
from src.notification_sender.feishu_card import build_feishu_card, parse_report_document
from src.services.report_html import ReportHtmlRenderError, render_report_html

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "notification_reports"
SAMPLES = ["single_stock_report", "aggregate_report", "market_review_report"]


def _sample(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", SAMPLES)
@pytest.mark.parametrize("lang", ["zh", "en", "ko"])
def test_render_structure(name, lang):
    html = render_report_html(_sample(name), report_language=lang)
    assert html.startswith("<!doctype html>")
    assert f'<html lang="{lang}">' in html
    assert "<title>" in html and "</title>" in html
    assert "<style>" in html and "</style>" in html
    assert '<main class="page">' in html
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()


@pytest.mark.parametrize("name", SAMPLES)
def test_html_matches_feishu_card_info(name):
    """核心一致性：飞书卡片里的章节标题与字段键值都能在 HTML 中找到。"""
    markdown = _sample(name)
    html = render_report_html(markdown)

    lark = format_feishu_markdown(markdown)
    card = build_feishu_card(lark)
    assert card is not None

    doc = parse_report_document(lark, include_disclaimer=True)
    # 章节标题（标题栏 + 正文 text_tag）必须在 HTML 中。
    assert doc.title.replace("&", "&amp;") in html or doc.title in html
    for section in doc.sections:
        if section.title:
            assert section.title in html, f"section {section.title!r} missing"

    # 富卡片里的字段键和值出现在 HTML 的 .field 结构中。
    field_keys = []
    for element in card["elements"]:
        for field in element.get("fields", []):
            content = field["text"]["content"]
            first_line = content.split("\n", 1)[0].strip("*\n")
            field_keys.append(first_line)
    for key in field_keys:
        assert key in html, f"field key {key!r} missing from HTML"

    # 免责声明一致。
    assert "不构成投资建议" in html or "investment" in html.lower()


def test_xss_is_escaped():
    malicious = (
        "# 测试\n\n"
        "## 注入\n\n"
        "<script>alert(1)</script>\n\n"
        '<img src=x onerror="alert(2)">\n\n'
        "<font color=\"red\" onmouseover=\"alert(3)\">文本</font>\n\n"
        "[链接](javascript:alert(4))\n"
    )
    html = render_report_html(malicious)
    lowered = html.lower()
    assert "<script" not in lowered
    assert "<img" not in lowered          # 被转义为 &lt;img，不构成真实标签
    assert "<font" not in lowered          # font 只还原为受控 span
    assert "javascript:" not in lowered.split("href=")[0].split("<style>")[0]  # 正文未渲染为链接
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_safe_external_links_only():
    html = render_report_html("# 标题\n\n正文 https://example.org/path 结束。\n")
    assert 'href="https://example.org/path"' in html
    assert 'rel="noopener noreferrer nofollow"' in html
    assert 'target="_blank"' in html


def test_deterministic():
    markdown = _sample("single_stock_report")
    assert render_report_html(markdown) == render_report_html(markdown)


def test_empty_and_short_content():
    for value in ["", "短文本"]:
        html = render_report_html(value)
        assert html.startswith("<!doctype html>")
        assert "<main" in html


def test_language_heading_lang():
    assert '<html lang="zh">' in render_report_html(_sample("single_stock_report"), report_language="zh")
    assert '<html lang="en">' in render_report_html(_sample("single_stock_report"), report_language="en")
    assert '<html lang="ko">' in render_report_html(_sample("single_stock_report"), report_language="ko")


def test_render_error_is_explicit(monkeypatch):
    import src.services.report_html.renderer as renderer

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(renderer, "format_feishu_markdown", boom)
    with pytest.raises(ReportHtmlRenderError):
        render_report_html("任意内容")
