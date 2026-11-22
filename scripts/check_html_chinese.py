#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail when an HTML report still contains English prose (T78).

Usage:
    python scripts/check_html_chinese.py report.html
    python scripts/check_html_chinese.py --allow "Apple" report.html

Whitelisted tokens (tickers, indicators, numbers, URLs) are ignored. A text node
with three or more consecutive ASCII words is treated as untranslated prose.
"""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_URL_RE = re.compile(r"https?://\S+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&'.-]*")
_SKIP_TAGS = {"style", "script", "pre", "code"}
_INDICATOR_RE = re.compile(
    r"^(RSI|MACD|MA\d+|EMA\d+|ROE|ROA|TTM|PE|PB|EPS|ETF|IPO|AI|LLM|URL|HTTP|HTTPS|API|JSON|HTML|CSS|USD|CNY|HKD|KRW|JPY|AAPL|TSLA|NVDA|MSFT|AMZN|GOOGL|META|SPX|QQQ)$",
    re.IGNORECASE,
)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.texts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = (data or "").strip()
        if text:
            self.texts.append(text)


def _english_prose(text: str, allow: set[str]) -> str | None:
    if _CJK_RE.search(text):
        return None
    cleaned = _URL_RE.sub(" ", text)
    words = [word for word in _WORD_RE.findall(cleaned) if word.lower() not in allow]
    consecutive = 0
    for word in words:
        if _INDICATOR_RE.match(word) or len(word) == 1:
            consecutive = 0
            continue
        consecutive += 1
        if consecutive >= 3:
            return text
    return None


def check_html(path: Path, allow: set[str]) -> list[str]:
    parser = _TextCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for text in parser.texts:
        hit = _english_prose(text, allow)
        if hit:
            violations.append(hit)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that an HTML report is fully Chinese")
    parser.add_argument("path", type=Path, help="HTML file to check")
    parser.add_argument("--allow", action="append", default=[], help="Extra whitelisted token (repeatable)")
    args = parser.parse_args(argv)

    allow = {token.strip().lower() for token in args.allow if token.strip()}
    violations = check_html(args.path, allow)
    if not violations:
        print(f"[ok] {args.path}: no English prose detected")
        return 0
    print(f"[fail] {args.path}: {len(violations)} English text node(s)")
    for text in violations[:20]:
        print(f"  - {text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
