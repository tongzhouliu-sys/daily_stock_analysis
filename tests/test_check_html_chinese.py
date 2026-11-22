# -*- coding: utf-8 -*-
"""Tests for the HTML Chinese prose checker (T78)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_html_chinese.py"
_SPEC = importlib.util.spec_from_file_location("check_html_chinese", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)


def _write(tmp_path: Path, html: str) -> Path:
    path = tmp_path / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


def test_detects_english_prose(tmp_path):
    path = _write(tmp_path, "<html><body><p>Favor buying on pullbacks.</p></body></html>")
    violations = _MODULE.check_html(path, set())
    assert violations == ["Favor buying on pullbacks."]


def test_allows_chinese_and_whitelisted_tokens(tmp_path):
    path = _write(
        tmp_path,
        "<html><body><p>核心结论：AAPL 的 RSI 为 58。</p><p>Apple</p></body></html>",
    )
    assert _MODULE.check_html(path, {"apple"}) == []


def test_ignores_code_blocks_and_urls(tmp_path):
    path = _write(
        tmp_path,
        "<html><body><pre>risk_level = medium</pre><p>链接 https://example.com/a b c</p></body></html>",
    )
    assert _MODULE.check_html(path, set()) == []
