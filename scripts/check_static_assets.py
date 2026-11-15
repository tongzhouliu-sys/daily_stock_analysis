#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static frontend sanity check for the desktop / server packaging pipeline.

Validates that ``index.html`` only references ``/assets/*.js`` and
``/assets/*.css`` files that actually exist on disk. A mismatch here is the
most common cause of the "Preparing backend..." / blank-page bug reported in
GitHub issues #1064, #1065, #1050: vite re-builds with a new content hash,
but the packaging step picks up a stale ``static/`` directory or copies the
files out of sync, so the browser receives a 404 (often as JSON) for the
main bundle and refuses to execute it.

Usage:
    python scripts/check_static_assets.py [<static_dir>]

Exits 0 when consistent, non-zero with a human-readable message otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# Match src="/assets/foo.js" or href="/assets/foo.css", with single or double
# quotes. Vite emits absolute paths by default (``base: '/'``).
_ASSET_PATTERN = re.compile(
    r"""(?:src|href)\s*=\s*["'](/assets/[^"']+)["']""",
    re.IGNORECASE,
)

# T30: 首屏不应整包加载的懒加载依赖。这些库只被懒加载页面使用，
# 一旦出现在入口的 modulepreload 列表里，首屏就会被它们的体积拖慢。
_FIRST_SCREEN_FORBIDDEN_MARKERS: Tuple[str, ...] = (
    "html2canvas",
    "jspdf",
    "docx",
    "recharts",
    # Markdown 渲染只在报告、问股等懒加载页面使用；它出现在首屏通常说明
    # 某个 CommonJS 代理模块被错误归入了首屏 runtime 块。
    "micromark",
)

_MODULEPRELOAD_PATTERN = re.compile(
    r"""<link[^>]*\brel\s*=\s*["']modulepreload["'][^>]*>""",
    re.IGNORECASE,
)
_HREF_PATTERN = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _parse_referenced_assets(index_html: str) -> List[str]:
    """Return the unique list of ``/assets/...`` paths referenced by ``index.html``."""
    seen: List[str] = []
    for match in _ASSET_PATTERN.finditer(index_html):
        ref = match.group(1)
        if ref not in seen:
            seen.append(ref)
    return seen


def parse_modulepreload_assets(index_html: str) -> List[str]:
    """Return the unique ``/assets/...`` paths the entry preloads."""
    seen: List[str] = []
    for tag in _MODULEPRELOAD_PATTERN.finditer(index_html):
        href_match = _HREF_PATTERN.search(tag.group(0))
        if not href_match:
            continue
        ref = href_match.group(1)
        if ref.startswith("/assets/") and ref not in seen:
            seen.append(ref)
    return seen


def check_preload_payloads(static_dir: Path, preload_refs: Sequence[str]) -> Dict[str, List[str]]:
    """Return ``{asset: [markers]}`` for preloaded files containing lazy-only libs.

    The marker list keeps the first screen from silently regressing: a vendor
    chunk mapping change (for example moving a dependency shared with an
    eagerly imported module into ``vendor-charts``) makes the entry preload the
    whole chart/export bundle again, and the only visible symptom is a slower
    first paint.
    """
    violations: Dict[str, List[str]] = {}
    for ref in preload_refs:
        candidate = static_dir / ref.lstrip("/")
        if not candidate.is_file():
            continue
        payload = candidate.read_bytes().lower()
        found = [marker for marker in _FIRST_SCREEN_FORBIDDEN_MARKERS if marker.encode() in payload]
        if found:
            violations[ref] = found
    return violations


def check_static_dir(static_dir: Path) -> Tuple[List[str], List[str]]:
    """
    Inspect ``static_dir`` and return ``(referenced, missing)``.

    ``referenced`` is the list of ``/assets/...`` paths declared in
    ``index.html``. ``missing`` is the subset that does not exist on disk.
    Raises ``FileNotFoundError`` if ``index.html`` itself is missing.
    """
    index_html_path = static_dir / "index.html"
    if not index_html_path.is_file():
        raise FileNotFoundError(f"index.html not found under {static_dir}")

    html = index_html_path.read_text(encoding="utf-8", errors="replace")
    referenced = _parse_referenced_assets(html)

    missing: List[str] = []
    for ref in referenced:
        # ref looks like "/assets/index-xxx.js"; strip the leading slash so
        # it resolves relative to ``static_dir``.
        candidate = static_dir / ref.lstrip("/")
        if not candidate.is_file():
            missing.append(ref)
    return referenced, missing


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        static_dir = Path(argv[1]).resolve()
    else:
        static_dir = (Path(__file__).resolve().parent.parent / "static").resolve()

    print(f"[check_static_assets] inspecting {static_dir}")

    try:
        referenced, missing = check_static_dir(static_dir)
    except FileNotFoundError as exc:
        print(f"[check_static_assets] ERROR: {exc}", file=sys.stderr)
        print(
            "[check_static_assets] Hint: build the frontend first via "
            "`cd apps/dsa-web && npm install && npm run build`.",
            file=sys.stderr,
        )
        return 2

    if not referenced:
        print(
            "[check_static_assets] WARNING: index.html does not reference any "
            "/assets/* files; this is unusual for a vite build.",
            file=sys.stderr,
        )
        return 0

    if missing:
        print(
            "[check_static_assets] ERROR: index.html references assets that "
            "are not present on disk:",
            file=sys.stderr,
        )
        for ref in missing:
            print(f"  - {ref}", file=sys.stderr)
        print(
            "[check_static_assets] This produces a blank page on first load "
            "(see GitHub #1064 / #1065). Re-run the frontend build and make "
            "sure the packaging step copies the freshly generated static/ "
            "directory.",
            file=sys.stderr,
        )
        return 1

    html = (static_dir / "index.html").read_text(encoding="utf-8", errors="replace")
    preload_refs = parse_modulepreload_assets(html)
    violations = check_preload_payloads(static_dir, preload_refs)
    if violations:
        print(
            "[check_static_assets] ERROR: first-screen modulepreload pulls in "
            "lazy-only bundles:",
            file=sys.stderr,
        )
        for ref, markers in sorted(violations.items()):
            print(f"  - {ref}: {', '.join(markers)}", file=sys.stderr)
        print(
            "[check_static_assets] Check apps/dsa-web/vite.config.ts "
            "manualChunks: a dependency shared with eagerly imported code must "
            "not be mapped into a lazy-only vendor chunk.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[check_static_assets] OK: {len(referenced)} asset reference(s) "
        f"resolved successfully, {len(preload_refs)} preload chunk(s) clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
