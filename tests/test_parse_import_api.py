# -*- coding: utf-8 -*-
"""JSON text size guard for POST /api/v1/stocks/parse-import."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.stocks import MAX_IMPORT_TEXT_BYTES


def _client() -> TestClient:
    return TestClient(create_app())


def test_parse_import_accepts_text_within_limit() -> None:
    with _client() as client:
        response = client.post("/api/v1/stocks/parse-import", json={"text": "600519,000001"})

    assert response.status_code == 200
    assert "600519" in response.json()["codes"]


def test_parse_import_rejects_text_over_limit() -> None:
    oversized = "6" * (MAX_IMPORT_TEXT_BYTES + 1)
    with _client() as client:
        response = client.post("/api/v1/stocks/parse-import", json={"text": oversized})

    assert response.status_code == 400
    assert response.json()["error"] == "text_too_large"


def test_parse_import_rejects_declared_oversized_body() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/stocks/parse-import",
            content=b'{"text": "' + b"6" * (MAX_IMPORT_TEXT_BYTES * 2) + b'"}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "text_too_large"
