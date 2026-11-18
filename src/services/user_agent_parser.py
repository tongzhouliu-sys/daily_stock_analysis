# -*- coding: utf-8 -*-
"""User-Agent parser for client identification."""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple


def parse_user_agent(
    ua: Optional[str],
    sec_ch_ua: Optional[str] = None,
    sec_ch_ua_mobile: Optional[str] = None,
    sec_ch_ua_platform: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Parse User-Agent and Client Hints to extract client info.

    Returns dict with: client_type, browser, browser_version, os, device_type, user_agent
    """
    result = {
        "client_type": "unknown",
        "browser": None,
        "browser_version": None,
        "os": None,
        "device_type": "unknown",
        "user_agent": None,
    }

    if not ua:
        return result

    # Clean UA: remove control chars, truncate to 512
    ua_clean = re.sub(r'[\x00-\x1f\x7f]', '', ua)
    result["user_agent"] = ua_clean[:512]

    # Use Client Hints if available
    if sec_ch_ua_platform:
        platform = sec_ch_ua_platform.strip('"').lower()
        if "android" in platform:
            result["os"] = "Android"
        elif "ios" in platform:
            result["os"] = "iOS"
        elif "macos" in platform:
            result["os"] = "macOS"
        elif "windows" in platform:
            result["os"] = "Windows"
        elif "linux" in platform:
            result["os"] = "Linux"
        elif "chromeos" in platform:
            result["os"] = "ChromeOS"

    # Determine device type from Client Hints
    if sec_ch_ua_mobile == "?1":
        result["device_type"] = "mobile"
    elif sec_ch_ua_mobile == "?0":
        result["device_type"] = "desktop"

    # Detect client type
    ua_lower = ua.lower()

    # Desktop app (Electron)
    if "electron/" in ua_lower:
        result["client_type"] = "desktop"
        # Extract Electron version
        match = re.search(r'electron/(\d+)', ua_lower)
        if match:
            result["browser"] = "Electron"
            result["browser_version"] = match.group(1)
        # Still detect OS before returning
        if not result["os"]:
            os_patterns = [
                (r'windows nt', 'Windows'),
                (r'mac os x', 'macOS'),
                (r'iphone|ipod', 'iOS'),
                (r'ipad', 'iPadOS'),
                (r'android', 'Android'),
                (r'cros', 'ChromeOS'),
                (r'linux', 'Linux'),
            ]
            for pattern, name in os_patterns:
                if re.search(pattern, ua_lower):
                    result["os"] = name
                    break
        if result["device_type"] == "unknown":
            result["device_type"] = "desktop"
        return result

    # Script/bot
    script_patterns = [
        (r'curl/(\d+)', 'curl'),
        (r'wget/(\d+)', 'wget'),
        (r'python-requests/(\d+)', 'python-requests'),
        (r'python-httpx/(\d+)', 'python-httpx'),
        (r'aiohttp/(\d+)', 'aiohttp'),
        (r'postmanruntime/(\d+)', 'postman'),
        (r'go-http-client/(\d+)', 'go-http-client'),
        (r'okhttp/(\d+)', 'okhttp'),
    ]
    for pattern, name in script_patterns:
        match = re.search(pattern, ua_lower)
        if match:
            result["client_type"] = "script"
            result["browser"] = name
            result["browser_version"] = match.group(1)
            return result

    # Browser detection (order matters)
    browser_patterns = [
        (r'micromessenger/(\d+)', '微信'),
        (r'(?:lark|feishu)/(\d+)', '飞书'),
        (r'dingtalk/(\d+)', '钉钉'),
        (r'edg/(\d+)', 'Edge'),
        (r'opr/(\d+)', 'Opera'),
        (r'samsungbrowser/(\d+)', 'Samsung'),
        (r'(?:chrome|crios)/(\d+)', 'Chrome'),
        (r'(?:firefox|fxios)/(\d+)', 'Firefox'),
        (r'version/(\d+).*safari/', 'Safari'),
    ]

    for pattern, name in browser_patterns:
        match = re.search(pattern, ua_lower)
        if match:
            result["client_type"] = "browser"
            result["browser"] = name
            result["browser_version"] = match.group(1)
            break

    # OS detection if not set by Client Hints
    if not result["os"]:
        # Order matters: check iOS/iPadOS before macOS since iPhone UA
        # contains "Mac OS X" but should be classified as iOS.
        os_patterns = [
            (r'iphone|ipod', 'iOS'),
            (r'ipad', 'iPadOS'),
            (r'windows nt', 'Windows'),
            (r'android', 'Android'),
            (r'cros', 'ChromeOS'),
            (r'mac os x', 'macOS'),
            (r'linux', 'Linux'),
        ]
        for pattern, name in os_patterns:
            if re.search(pattern, ua_lower):
                result["os"] = name
                break

    # Device type detection
    if result["device_type"] == "unknown":
        if "ipad" in ua_lower or ("android" in ua_lower and "mobile" not in ua_lower):
            result["device_type"] = "tablet"
        elif "iphone" in ua_lower or ("android" in ua_lower and "mobile" in ua_lower):
            result["device_type"] = "mobile"
        elif result["os"] in ["Windows", "macOS", "Linux", "ChromeOS", "iPadOS"]:
            result["device_type"] = "desktop"

    return result
