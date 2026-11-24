# -*- coding: utf-8 -*-
"""历史报告 HTML 接口测试。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1.endpoints import history as history_endpoint
from src.services.report_html import ReportHtmlRenderError


class _FakeHistoryService:
    def __init__(self, result, markdown="# 中钨高新 000657 分析报告", *, missing=False):
        self.result = result
        self.markdown = markdown
        self.missing = missing
        self.zh_calls = 0
        self.ensure_calls = []

    def resolve_and_get_detail(self, record_id):
        return self.result

    def get_markdown_report(self, record_id, *, report_language=None):
        return self.markdown

    def get_markdown_report_zh(self, record_id):
        self.zh_calls += 1
        if self.missing:
            from src.services.history_service import ReportChineseVariantMissing

            raise ReportChineseVariantMissing(str(record_id), reason="missing")
        return self.markdown

    def ensure_chinese_variant(self, record_id, *, force=False):
        self.ensure_calls.append(bool(force))
        return {"record_id": record_id, "status": "ready", "variant_kind": "single_stock_overlay"}


def _patch_service(monkeypatch, result, markdown="# 中钨高新 000657 分析报告", *, missing=False):
    service = _FakeHistoryService(result, markdown, missing=missing)
    monkeypatch.setattr(history_endpoint, "HistoryService", lambda _db: service)
    monkeypatch.setattr(
        history_endpoint,
        "get_config",
        lambda: SimpleNamespace(report_language="zh", markdown_to_image_max_chars=15000),
    )
    return service


def _result(**overrides):
    base = {
        "id": 17,
        "report_type": "detailed",
        "raw_result": {"code": "000657", "name": "中钨高新"},
        "context_snapshot": {},
    }
    base.update(overrides)
    return base


def test_report_html_returns_html_with_security_headers(monkeypatch):
    _patch_service(monkeypatch, _result(), markdown="# 中钨高新 000657 分析报告\n\n## 核心结论\n\n维持观望。")
    response = history_endpoint.get_history_report_html("17", download=0, db_manager=object())
    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert b"<!doctype html>" in response.body
    assert "中钨高新" in response.body.decode("utf-8")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )
    assert "content-disposition" not in response.headers


def test_report_html_download_filename_is_sanitized(monkeypatch):
    _patch_service(monkeypatch, _result(id="17/../../etc/passwd"))
    response = history_endpoint.get_history_report_html("17", download=1, db_manager=object())
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "/" not in disposition
    assert ".." not in disposition
    assert disposition.endswith('.html"')


def test_report_html_forces_chinese_report_generation(monkeypatch):
    service = _patch_service(monkeypatch, _result(raw_result={"report_language": "en"}))
    captured = {}

    def fake_render(markdown, **kwargs):
        captured.update(kwargs)
        return "<!doctype html><html lang='zh'></html>"

    monkeypatch.setattr(history_endpoint, "render_report_html", fake_render)
    history_endpoint.get_history_report_html("17", db_manager=object())
    assert service.zh_calls == 1
    assert "report_language" not in captured


def test_report_html_409_when_chinese_variant_missing(monkeypatch):
    _patch_service(monkeypatch, _result(raw_result={"report_language": "en"}), missing=True)
    with pytest.raises(HTTPException) as exc:
        history_endpoint.get_history_report_html("17", db_manager=object())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "zh_variant_missing"
    assert exc.value.detail["record_id"] == "17"


def test_ensure_report_zh_variant_endpoint(monkeypatch):
    service = _patch_service(monkeypatch, _result(raw_result={"report_language": "en"}))
    payload = history_endpoint.ensure_history_report_zh_variant("17", force=1, db_manager=object())
    assert payload["status"] == "ready"
    assert service.ensure_calls == [True]


def test_report_html_uses_chinese_frame_for_english_markdown(monkeypatch):
    _patch_service(
        monkeypatch,
        _result(raw_result={"report_language": "en"}),
        markdown="# AAPL Analysis\n\n## Core Conclusion\n\n- Hold.\n",
    )
    response = history_endpoint.get_history_report_html("17", db_manager=object())
    body = response.body.decode("utf-8")
    assert '<html lang="zh">' in body
    assert "内容由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。" in body


def test_report_html_market_review(monkeypatch):
    _patch_service(
        monkeypatch,
        _result(report_type="market_review", context_snapshot={"market_review_payload": {}}),
        markdown="# 大盘复盘\n\n## 指数表现\n\n上证指数上涨。",
    )
    response = history_endpoint.get_history_report_html("17", db_manager=object())
    assert response.status_code == 200
    assert b"\xe5\xa4\xa7\xe7\x9b\x98\xe5\xa4\x8d\xe7\x9b\x98" in response.body  # 大盘复盘


def test_report_html_404_when_record_missing(monkeypatch):
    service = _FakeHistoryService(None)
    monkeypatch.setattr(history_endpoint, "HistoryService", lambda _db: service)
    with pytest.raises(HTTPException) as exc:
        history_endpoint.get_history_report_html("999", db_manager=object())
    assert exc.value.status_code == 404


def test_report_html_413_when_too_large(monkeypatch):
    _patch_service(monkeypatch, _result(), markdown="x" * (history_endpoint._REPORT_HTML_MAX_CHARS + 1))
    with pytest.raises(HTTPException) as exc:
        history_endpoint.get_history_report_html("17", db_manager=object())
    assert exc.value.status_code == 413
    assert exc.value.detail["error"] == "report_html_too_large"


def test_report_html_500_does_not_leak_error_detail(monkeypatch):
    _patch_service(monkeypatch, _result())

    def boom(*args, **kwargs):
        raise ReportHtmlRenderError("secret template path /internal/details")

    monkeypatch.setattr(history_endpoint, "render_report_html", boom)
    with pytest.raises(HTTPException) as exc:
        history_endpoint.get_history_report_html("17", db_manager=object())
    assert exc.value.status_code == 500
    assert exc.value.detail == {"error": "generation_failed", "message": "生成 HTML 报告失败"}
    assert "secret" not in str(exc.value.detail)


def test_report_html_requires_session_when_auth_enabled():
    """鉴权开启时，未带会话访问 report-html 必须返回 401。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import Request, Response
    from api.middlewares.auth import AuthMiddleware

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/history/17/report-html",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": "",
    }
    middleware = AuthMiddleware(app=MagicMock())
    with patch("api.middlewares.auth.is_auth_enabled", return_value=True):
        response = asyncio.run(
            middleware.dispatch(Request(scope), AsyncMock(return_value=Response(status_code=200)))
        )
    assert response.status_code == 401
