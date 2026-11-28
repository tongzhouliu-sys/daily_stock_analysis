# -*- coding: utf-8 -*-
"""v2 smart intake: server-side object resolution and preference inference."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from src.agent.intake_questions import build_smart_intake, classify_question_intent
from src.agent.object_resolution import resolve_intake_object
from src.services.agent_chat_intake_service import AgentChatIntakeService


@pytest.fixture(scope="module")
def index():
    from src.services.agent_chat_session_service import _stock_lookup

    return _stock_lookup()


def _resolve(index, message: str) -> Dict[str, Any]:
    registry, lookup = index
    intent = classify_question_intent(message)
    return resolve_intake_object(
        message, registry, lookup,
        concept_question=intent["question_type"] == "concept",
    )


class TestObjectResolution:
    def test_explicit_code_is_resolved_locally(self, index):
        resolution = _resolve(index, "分析 600519")
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_name"] == "贵州茅台"
        assert resolution["identity"]["market"] == "CN"

    def test_prose_mention_is_resolved(self, index):
        resolution = _resolve(index, "我持有茅台被套了，长期怎么看")
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_code"] == "600519"

    def test_ambiguous_name_returns_candidates(self, index):
        resolution = _resolve(index, "帮我看看中芯国际")
        assert resolution["status"] == "ambiguous"
        assert resolution["candidates"]
        assert all("canonical_id" in candidate for candidate in resolution["candidates"])

    def test_missing_object_stops_before_guessing(self, index):
        resolution = _resolve(index, "换一只帮我看看")
        assert resolution["status"] == "missing"
        assert resolution["candidates"] == []

    def test_concept_question_needs_no_object(self, index):
        resolution = _resolve(index, "什么是市盈率")
        assert resolution["status"] == "not_required"
        assert resolution["reason"] == "concept_question"

    def test_market_question_needs_no_object(self, index):
        resolution = _resolve(index, "今天大盘怎么样")
        assert resolution["status"] == "not_required"
        assert resolution["scope"] == "market"

    def test_lowercase_ascii_alias_is_not_a_substring_hit(self, index):
        # P1: "aapl" must not bind the AAPJ name "AAP" via substring scan.
        resolution = _resolve(index, "aapl")
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_code"] == "AAPL"

    def test_name_plus_code_does_not_go_ambiguous(self, index):
        # P7: a correct "APPLE（AAPL）" pair must not fork into AAPJ / AAPL.
        resolution = _resolve(index, "APPLE（AAPL）怎么样")
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_code"] == "AAPL"

    @pytest.mark.parametrize("message,expected_code", [
        ("aapl", "AAPL"),
        ("AAPL", "AAPL"),
        ("分析 aapl", "AAPL"),
        ("tsla", "TSLA"),
        ("nvda怎么样", "NVDA"),
    ])
    def test_lowercase_tickers_resolve(self, index, message, expected_code):
        resolution = _resolve(index, message)
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_code"] == expected_code

    @pytest.mark.parametrize("message", ["分析 123456", "分析 999999"])
    def test_unknown_code_does_not_auto_start(self, index, message):
        # P3: a code-shaped token with no index row must not auto-start.
        resolution = _resolve(index, message)
        assert resolution["status"] == "ambiguous"
        assert resolution["reason"] == "unknown_code"
        assert len(resolution["candidates"]) == 1
        assert resolution["candidates"][0]["stock_name"] in (None, "")
        assert resolution["recommended"] is None

    @pytest.mark.parametrize("message", ["700", "分析 700"])
    def test_hk_short_code_becomes_confirm(self, index, message):
        # P5: a bare 1-4 digit HK short code resolves to its padded stock.
        resolution = _resolve(index, message)
        assert resolution["status"] == "ambiguous"
        assert resolution["reason"] == "short_code"
        assert resolution["candidates"][0]["stock_code"] == "HK00700"
        assert resolution["recommended"]["stock_code"] == "HK00700"

    def test_short_code_only_when_whole_message_is_numeric(self, index):
        # Digits embedded in prose must not trigger the short-code path.
        resolution = _resolve(index, "茅台跌了 700 块怎么办")
        assert resolution["status"] == "resolved"
        assert resolution["identity"]["stock_code"] == "600519"

    @pytest.mark.parametrize("message", [
        "最近行情怎么样", "半导体板块还能追吗", "推荐几只股票", "我想买点白酒股",
    ])
    def test_non_stock_questions_do_not_require_object(self, index, message):
        # P6: market/sector/stock-picking questions do not force stock choice.
        resolution = _resolve(index, message)
        assert resolution["status"] == "not_required"

    def test_ambiguous_object_still_asks(self, index):
        resolution = _resolve(index, "帮我看看中芯国际")
        assert resolution["status"] == "ambiguous"
        assert resolution["reason"] == "ambiguous_object"

    # R3.7: every stock picker writes "名称（代码）" into the composer; that
    # mention must resolve back to the picked stock, alone or before a question.
    # A stratified fixed sample keeps this fast (one resolution is ~0.7s).
    @pytest.mark.parametrize("mention,expected_canonical", [
        ("贵州茅台（600519）", "sh600519"),
        ("平安银行（000001）", "sz000001"),
        ("腾讯控股（00700）", "hk00700"),
        ("阿里巴巴-W（09988）", "hk09988"),
        ("苹果（AAPL）", "AAPL"),
        ("英伟达（NVDA）", "NVDA"),
        ("特斯拉（TSLA）", "TSLA"),
        ("沪深300（sh000300）", "sh000300"),
        ("上证指数（sh000001）", "sh000001"),
        ("宁德时代（300750）", "sz300750"),
        ("EVOA", "EVOA"),
    ])
    def test_picker_mention_resolves_back(self, index, mention, expected_canonical):
        for message in (mention, f"{mention} 现在怎么样"):
            resolution = _resolve(index, message)
            if resolution["status"] == "resolved":
                assert resolution["identity"]["canonical_id"] == expected_canonical
            else:
                assert any(
                    candidate["canonical_id"] == expected_canonical
                    for candidate in resolution["candidates"]
                ), resolution


class TestPreferenceInference:
    def test_inferred_values_carry_evidence(self):
        intent = classify_question_intent("我持有茅台被套了，长期怎么看")
        inferred = intent["inferred"]
        assert inferred["position"][0] == "held_control_drawdown"
        assert "被套" in inferred["position"][1]
        assert inferred["horizon"] == ("long", "长期")

    @pytest.mark.parametrize("message,dimension", [
        ("我想看看有没有机会", None),
        ("公司连续亏损还能买吗", None),
        ("最近波动大怎么办", None),
        ("今天为什么跌", None),
        ("机器学习概念股有哪些", None),
        ("一年内新高还能追吗", None),
    ])
    def test_known_false_positives_stay_unknown(self, message, dimension):
        inferred = classify_question_intent(message)["inferred"]
        if dimension is None:
            assert "position" not in inferred
            assert "horizon" not in inferred or "今天" not in message
            assert "risk_focus" not in inferred
            assert "goal" not in inferred or "连续亏损" not in message
        else:  # pragma: no cover - keeps parametrization readable
            assert dimension in inferred

    def test_high_precision_rules_still_fire(self):
        assert classify_question_intent("这个位置值得关注吗")["inferred"]["goal"][0] == "worth_watching"
        assert classify_question_intent("我想短线做T")["inferred"]["horizon"][0] == "days"
        assert classify_question_intent("只做保守配置")["inferred"]["risk_focus"][0] == "downside"


class TestSmartIntakeBuild:
    def test_resolved_question_auto_starts_with_prefilled_answers(self, index):
        message = "我持有茅台被套了，长期怎么看"
        resolution = _resolve(index, message)
        intent = classify_question_intent(message)
        built = build_smart_intake(message, resolution, intent["inferred"])

        assert built["plan"]["auto_start"] is True
        assert built["plan"]["blocking_reason"] is None
        assert all(not question.required for question in built["questions"])
        answered = {answer.question_id: answer for answer in built["answers"]}
        assert answered["position"].option_ids == ["held_control_drawdown"]
        assert answered["position"].source == "explicit_message"
        assert built["brief"].object.stock_code == "600519"
        assert built["brief"].sources["position"] == "explicit_message"
        assert "被套" in built["brief"].evidence["position"]
        assert "position" not in built["brief"].unknown_fields

    def test_ambiguous_question_asks_one_blocking_object_question(self, index):
        message = "帮我看看中芯国际"
        resolution = _resolve(index, message)
        built = build_smart_intake(message, resolution, {})

        assert built["plan"]["auto_start"] is False
        assert built["plan"]["blocking_reason"] == "ambiguous"
        blocking = [question for question in built["questions"] if question.required]
        assert [question.id for question in blocking] == ["object"]
        assert blocking[0].recommended_option_id in {
            option.id for option in blocking[0].options
        }
        assert blocking[0].allow_free_text is True


class _FakeDB:
    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}

    def read_chat_session_snapshot(self, session_id: str) -> Dict[str, Any]:
        return {"session_generation": "gen-1", "session_state_version": 0}

    def create_intake_draft(self, **kwargs) -> Dict[str, Any]:
        row = {
            "intake_id": kwargs["intake_id"],
            "schema_version": kwargs.get("schema_version", 2),
            "session_id": kwargs["session_id"],
            "session_generation": kwargs["session_generation"],
            "base_state_version": kwargs.get("base_state_version", 0),
            "revision": 1,
            "original_message": kwargs["original_message"],
            "questions_json": kwargs["questions_json"],
            "answers_json": kwargs.get("answers_json", "[]"),
            "status": kwargs.get("status", "pending"),
            "created_at": None,
            "updated_at": None,
        }
        self.rows[row["intake_id"]] = row
        return row

    def get_intake_draft(self, intake_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(intake_id)


class TestCreateIntakeV2:
    def test_auto_start_draft_is_created_confirmed(self):
        db = _FakeDB()
        service = AgentChatIntakeService(db_manager=db)
        draft, meta = service.create_intake_v2(
            message="分析 600519",
            session_id="session-a",
            session_generation="gen-1",
            request_id="req-1",
        )
        assert draft.status.value == "confirmed"
        assert draft.schema_version == 2
        assert meta["plan"]["auto_start"] is True
        assert json.loads(db.rows[draft.intake_id]["answers_json"]) == []

    def test_blocking_draft_stays_pending(self):
        db = _FakeDB()
        service = AgentChatIntakeService(db_manager=db)
        draft, meta = service.create_intake_v2(
            message="换一只帮我看看",
            session_id="session-a",
            session_generation="gen-1",
            request_id="req-2",
        )
        assert draft.status.value == "pending"
        assert meta["plan"]["auto_start"] is False
        assert meta["resolution"]["status"] == "missing"
