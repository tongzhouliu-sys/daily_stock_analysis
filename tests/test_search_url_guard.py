# -*- coding: utf-8 -*-
"""SSRF guard for fetch_url_content()."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from src import search_service


def _addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def test_rejects_non_http_schemes() -> None:
    assert search_service.fetch_url_content("file:///etc/passwd") == ""
    assert search_service.fetch_url_content("ftp://example.com/a") == ""


def test_rejects_loopback_and_metadata_addresses() -> None:
    assert search_service.fetch_url_content("http://127.0.0.1:8000/") == ""
    assert search_service.fetch_url_content("http://169.254.169.254/latest/meta-data/") == ""


def test_rejects_dns_that_resolves_to_private_address() -> None:
    with patch.object(search_service.socket, "getaddrinfo", return_value=_addrinfo("10.0.0.5")):
        assert search_service.fetch_url_content("http://internal.example/") == ""


def test_rejects_credentials_in_url() -> None:
    with patch.object(search_service.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert search_service.fetch_url_content("http://user:pass@example.com/") == ""


def test_allows_public_url_and_parses_article() -> None:
    article = MagicMock()
    article.text = "hello world"
    with (
        patch.object(search_service.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")),
        patch.object(search_service, "Article", return_value=article) as article_cls,
    ):
        result = search_service.fetch_url_content("https://example.com/news")

    assert result == "hello world"
    article.download.assert_called_once()
    article.parse.assert_called_once()
    article_cls.assert_called_once()
