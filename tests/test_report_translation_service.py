# -*- coding: utf-8 -*-
"""Tests for Chinese report variant generation (T78)."""

from __future__ import annotations

import json

import pytest

from src.services.report_translation_service import (
    ReportTranslationError,
    ReportTranslationService,
    apply_overlay,
    collect_translatable_texts,
    source_hash_for_record,
)


def _raw_result() -> dict:
    return {
        "code": "AAPL",
        "name": "Apple",
        "analysis_summary": "Momentum remains constructive.",
        "risk_warning": "A close below support invalidates the setup.",
        "dashboard": {
            "intelligence": {
                "risk_alerts": ["Valuation is stretched."],
                "sentiment_summary": "Institutions are net buyers.",
            },
            "core_conclusion": {
                "one_sentence": "Favor buying on pullbacks.",
                "position_advice": {"no_position": "Open a starter position."},
            },
            "battle_plan": {
                "action_checklist": ["Volume confirms."],
                "position_strategy": {"entry_plan": "Scale in on weakness."},
            },
        },
        "market_snapshot": {"close": 190.5, "volume": 1000},
        "data_sources": ["efinance"],
    }


def test_collect_skips_identifiers_numbers_and_chinese():
    texts = collect_translatable_texts(_raw_result())
    assert texts["analysis_summary"] == "Momentum remains constructive."
    assert texts["dashboard.intelligence.risk_alerts[0]"] == "Valuation is stretched."
    assert texts["dashboard.core_conclusion.position_advice.no_position"] == "Open a starter position."
    assert "code" not in texts
    assert "name" not in texts
    assert "dashboard.battle_plan.action_checklist[0]" in texts
    assert all("market_snapshot" not in path for path in texts)


def test_apply_overlay_is_deep_copy_and_ignores_bad_paths():
    raw = _raw_result()
    overlay = {
        "analysis_summary": "动能仍然积极。",
        "dashboard.intelligence.risk_alerts[0]": "估值偏高。",
        "dashboard.missing.field": "忽略",
    }
    translated = apply_overlay(raw, overlay)
    assert translated["analysis_summary"] == "动能仍然积极。"
    assert translated["dashboard"]["intelligence"]["risk_alerts"][0] == "估值偏高。"
    # 原对象不被修改
    assert raw["analysis_summary"] == "Momentum remains constructive."


def test_translate_overlay_uses_glossary_and_injected_translator():
    calls = []

    def translator(batch, timeout):
        calls.append((batch, timeout))
        return {path: f"中文:{text}" for path, text in batch.items()}, "fake", "fake-model"

    service = ReportTranslationService(translator=translator, timeout_seconds=7)
    translated, backend, model = service.translate_overlay({
        "a": "Bullish",
        "b": "Favor buying on pullbacks.",
    })
    assert translated["a"] == "看多"
    assert translated["b"] == "中文:Favor buying on pullbacks."
    assert backend == "fake"
    assert model == "fake-model"
    assert calls and calls[0][1] == 7


def test_translate_markdown_chunks_and_returns_text():
    calls = []

    def markdown_translator(text, timeout):
        calls.append((text, timeout))
        return f"译:{text}", "fake", "fake-model"

    service = ReportTranslationService(markdown_translator=markdown_translator, max_chars=200000)
    translated, backend, model = service.translate_markdown("# 标题\n\nBody text.")
    assert translated.startswith("译:# 标题")
    assert backend == "fake"
    assert model == "fake-model"
    assert calls


def test_translate_rejects_oversized_payload():
    service = ReportTranslationService(max_chars=10)
    with pytest.raises(ReportTranslationError) as exc:
        service.translate_overlay({"a": "x" * 100})
    assert exc.value.error_code == "translation_too_large"


def test_translate_rejects_still_english_output():
    service = ReportTranslationService(translator=lambda batch, timeout: (dict(batch), "fake", "fake"))
    with pytest.raises(ReportTranslationError) as exc:
        service.translate_overlay({"a": "Favor buying on pullbacks."})
    assert exc.value.error_code == "translation_incomplete"


def test_source_hash_changes_with_content():
    raw = _raw_result()
    first = source_hash_for_record(raw)
    raw["analysis_summary"] = "Changed."
    assert source_hash_for_record(raw) != first


def test_collect_ignores_already_chinese_text():
    texts = collect_translatable_texts({
        "analysis_summary": "基本面稳健，短期震荡，关注成交量变化。",
        "risk_warning": "跌破支撑位则失效。",
    })
    assert texts == {}


def test_collect_detects_korean_text():
    texts = collect_translatable_texts({
        "analysis_summary": "모멘텀이 여전히 긍정적입니다.",
    })
    assert texts["analysis_summary"] == "모멘텀이 여전히 긍정적입니다."


def test_collect_detects_ordinary_single_word_but_skips_tickers():
    texts = collect_translatable_texts({
        "dashboard.battle_plan.action_checklist": ["Excellent", "AAPL", "RSI"],
    })
    assert texts == {"dashboard.battle_plan.action_checklist[0]": "Excellent"}


def test_markdown_detection_catches_headings_tables_and_short_sentences():
    from src.services.report_translation_service import markdown_needs_translation

    assert markdown_needs_translation("# Market Review\n\n| Index | Change |\n| --- | --- |")
    assert markdown_needs_translation("Stocks rallied.")
    assert not markdown_needs_translation("# 大盘复盘\n\n| 指数 | 涨跌 |\n| --- | --- |")
    assert not markdown_needs_translation("核心结论：回调买入。")


def test_parse_json_payload_accepts_fenced_json():
    from src.services.report_translation_service import _parse_json_payload

    parsed = _parse_json_payload('```json\n{"items": []}\n```')
    assert parsed == {"items": []}
    assert json.dumps(parsed) == '{"items": []}'
