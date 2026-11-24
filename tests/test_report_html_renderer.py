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
def test_render_structure(name):
    html = render_report_html(_sample(name))
    assert html.startswith("<!doctype html>")
    assert '<html lang="zh">' in html
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


def test_link_query_string_is_escaped_once():
    from src.services.report_html.renderer import _safe_inline

    html = _safe_inline("https://example.org/a?x=1&b=2")
    assert 'href="https://example.org/a?x=1&amp;b=2"' in html
    assert "&amp;amp;" not in html


def test_url_placeholder_injection_is_plain_text():
    from src.services.report_html.renderer import _safe_inline

    assert "LINK0" in _safe_inline("\x00LINK0\x00")
    assert "<a " not in _safe_inline("\x00LINK0\x00")


def test_deterministic():
    markdown = _sample("single_stock_report")
    assert render_report_html(markdown) == render_report_html(markdown)


def test_empty_and_short_content():
    for value in ["", "短文本"]:
        html = render_report_html(value)
        assert html.startswith("<!doctype html>")
        assert "<main" in html


def test_language_heading_lang():
    english_markdown = "# AAPL Analysis\n\n## Core Conclusion\n\n- Hold.\n"
    html = render_report_html(english_markdown)
    assert '<html lang="zh">' in html
    assert "内容由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。" in html


def test_renderer_does_not_accept_report_language():
    with pytest.raises(TypeError):
        render_report_html("任意内容", report_language="en")  # type: ignore[call-arg]


def test_render_error_is_explicit(monkeypatch):
    import src.services.report_html.renderer as renderer

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(renderer, "format_feishu_markdown", boom)
    with pytest.raises(ReportHtmlRenderError):
        render_report_html("任意内容")
