# -*- coding: utf-8 -*-
"""
===================================
Markdown 转图片工具模块
===================================

将 Markdown 转为 PNG 图片（用于不支持 Markdown 的通知渠道）。
支持 playwright、markdown-to-file (m2f) 与 wkhtmltoimage (imgkit)。
默认 auto：按 playwright -> markdown-to-file -> wkhtmltoimage 顺序降级，
任一引擎可用即可出图，因此不再强制要求安装 wkhtmltoimage。

Security note: imgkit passes HTML to wkhtmltoimage via stdin, not argv, so
command injection from content is not applicable. Output is rasterized to PNG
(no script execution). Input is from system-generated reports, not raw user
input. Risk is considered low for the current use case.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from src.share_image import (
    ShareImageBranding,
    build_share_image_html,
    share_image_branding_from_config,
)

logger = logging.getLogger(__name__)


def _share_image_branding(config: object) -> ShareImageBranding:
    return share_image_branding_from_config(config)


def _resolve_playwright_command() -> Optional[str]:
    """Resolve a global or repository-local Playwright CLI executable."""
    command = shutil.which("playwright")
    if command:
        return command

    repository_root = Path(__file__).resolve().parent.parent
    executable_name = "playwright.cmd" if os.name == "nt" else "playwright"
    local_command = repository_root / "apps" / "dsa-web" / "node_modules" / ".bin" / executable_name
    if local_command.is_file():
        return str(local_command)
    return None


def _run_playwright_screenshot(
    playwright_command: str,
    html_url: str,
    png_path: str,
    channel: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run the Playwright screenshot CLI, optionally against a browser channel."""
    args = [
        playwright_command,
        "screenshot",
        "--browser",
        "chromium",
        "--viewport-size",
        "1080,720",
    ]
    if channel:
        args.extend(["--channel", channel])
    args.extend(["--full-page", html_url, png_path])
    return subprocess.run(args, capture_output=True, timeout=60, check=False)


def _is_missing_playwright_browser(stderr: str) -> bool:
    """Detect the Playwright CLI error shown when bundled browsers are absent."""
    return "Executable doesn't exist" in stderr or "playwright install" in stderr


def _markdown_to_image_playwright(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """Convert a share-poster HTML document to PNG with Playwright Chromium."""
    playwright_command = _resolve_playwright_command()
    if playwright_command is None:
        logger.warning(
            "Playwright CLI not found. Install Web dependencies with: "
            "cd apps/dsa-web && npm ci. Fallback to text."
        )
        return None

    temp_dir = tempfile.mkdtemp()
    html_path = Path(temp_dir) / "report.html"
    png_path = Path(temp_dir) / "report.png"
    try:
        html_path.write_text(
            build_share_image_html(
                markdown_text,
                structured_payload=structured_payload,
                branding=branding,
            ),
            encoding="utf-8",
        )
        html_url = html_path.resolve().as_uri()
        result = _run_playwright_screenshot(playwright_command, html_url, str(png_path))
        if result.returncode == 0 and png_path.exists():
            return png_path.read_bytes()

        stderr = result.stderr.decode("utf-8", errors="replace")
        if _is_missing_playwright_browser(stderr):
            logger.info(
                "Playwright bundled Chromium is unavailable, retrying with the "
                "system Google Chrome channel."
            )
            result = _run_playwright_screenshot(
                playwright_command,
                html_url,
                str(png_path),
                channel="chrome",
            )
            if result.returncode == 0 and png_path.exists():
                return png_path.read_bytes()
            stderr = result.stderr.decode("utf-8", errors="replace")

        logger.error("Playwright image conversion failed: %s", stderr[:500])
        return None
    except Exception as exc:
        logger.error("Playwright image conversion error: %s", exc)
        return None
    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception as exc:
            logger.debug("Failed to remove temp dir %s: %s", temp_dir, exc)


def _markdown_to_image_m2f(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """Convert Markdown to PNG via markdown-to-file (m2f) CLI. Better emoji support (Issue #455)."""
    m2f_command = shutil.which("m2f")
    if m2f_command is None:
        logger.warning(
            "m2f (markdown-to-file) not found in PATH. "
            "Install with: npm i -g markdown-to-file. Fallback to text."
        )
        return None

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        md_path = os.path.join(temp_dir, "report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            # m2f keeps raw HTML in Markdown input, allowing both engines to
            # share the same deterministic poster layout and embedded QR codes.
            f.write(
                build_share_image_html(
                    markdown_text,
                    structured_payload=structured_payload,
                    branding=branding,
                )
            )

        result = subprocess.run(
            [m2f_command, md_path, "png", f"outputDirectory={temp_dir}"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        png_path = os.path.join(temp_dir, "report.png")
        if result.returncode != 0 or not os.path.isfile(png_path):
            logger.warning(
                "m2f conversion failed: returncode=%s, stderr=%s",
                result.returncode,
                (result.stderr or b"").decode("utf-8", errors="replace")[:200],
            )
            return None

        with open(png_path, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired:
        logger.warning("m2f conversion timed out (60s)")
        return None
    except Exception as e:
        logger.warning("markdown_to_image (m2f) failed: %s", e)
        return None
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except OSError as e:
                logger.debug("Failed to remove temp dir %s: %s", temp_dir, e)


def _markdown_to_image_wkhtml(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """Convert Markdown to PNG via imgkit/wkhtmltoimage."""
    try:
        import imgkit
    except ImportError:
        logger.debug("imgkit not installed, markdown_to_image unavailable")
        return None

    try:
        html = build_share_image_html(
            markdown_text,
            structured_payload=structured_payload,
            branding=branding,
        )
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": 1080,
            "disable-smart-width": "",
            "quality": 95,
            "quiet": "",
        }
        out = imgkit.from_string(html, False, options=options)
        if out and isinstance(out, bytes) and len(out) > 0:
            return out
        logger.warning("imgkit.from_string returned empty or invalid result")
        return None
    except OSError as e:
        if "wkhtmltoimage" in str(e).lower() or "wkhtmltopdf" in str(e).lower():
            logger.debug("wkhtmltopdf/wkhtmltoimage not found: %s", e)
        else:
            logger.warning("imgkit/wkhtmltoimage error: %s", e)
        return None
    except Exception as e:
        logger.warning("markdown_to_image conversion failed: %s", e)
        return None


def _markdown_to_image_auto(
    markdown_text: str,
    structured_payload: Optional[Mapping[str, Any]] = None,
    branding: Optional[ShareImageBranding] = None,
) -> Optional[bytes]:
    """Render with the first available engine so no single tool is mandatory."""
    engines = (
        ("playwright", _markdown_to_image_playwright),
        ("markdown-to-file", _markdown_to_image_m2f),
        ("wkhtmltoimage", _markdown_to_image_wkhtml),
    )
    for engine, renderer in engines:
        result = renderer(markdown_text, structured_payload, branding)
        if result:
            logger.debug("Markdown rendered to image with engine: %s", engine)
            return result
        logger.info("md2img engine '%s' is unavailable, trying next engine", engine)
    logger.warning(
        "No md2img engine could render the report. Install one of: "
        "Playwright (cd apps/dsa-web && npm ci), markdown-to-file "
        "(npm i -g markdown-to-file), or wkhtmltopdf."
    )
    return None


def markdown_to_image(
    markdown_text: str,
    max_chars: int = 15000,
    structured_payload: Optional[Mapping[str, Any]] = None,
) -> Optional[bytes]:
    """
    Convert Markdown to PNG image bytes.

    Engine is read from config.md2img_engine: auto (default, tries
    playwright -> markdown-to-file -> wkhtmltoimage), or an explicit engine
    name to pin a single renderer.

    When conversion fails or dependencies unavailable, returns None so caller
    can fall back to text sending.

    Args:
        markdown_text: Raw Markdown content.
        max_chars: Skip conversion and return None if content exceeds this length
            (avoids huge images). Default 15000.
        structured_payload: Optional stock-analysis or market-review JSON. Exact
            structured fields take precedence over Markdown extraction.

    Returns:
        PNG bytes, or None if conversion fails or dependencies unavailable.
    """
    if len(markdown_text) > max_chars:
        logger.warning(
            "Markdown content (%d chars) exceeds max_chars (%d), skipping image conversion",
            len(markdown_text),
            max_chars,
        )
        return None

    try:
        from src.config import get_config

        config = get_config()
        engine = getattr(config, "md2img_engine", "auto")
        branding = _share_image_branding(config)
    except Exception:
        engine = "auto"
        branding = share_image_branding_from_config(object())

    if engine == "markdown-to-file":
        return _markdown_to_image_m2f(markdown_text, structured_payload, branding)
    if engine == "playwright":
        return _markdown_to_image_playwright(markdown_text, structured_payload, branding)
    if engine == "wkhtmltoimage":
        return _markdown_to_image_wkhtml(markdown_text, structured_payload, branding)
    return _markdown_to_image_auto(markdown_text, structured_payload, branding)
