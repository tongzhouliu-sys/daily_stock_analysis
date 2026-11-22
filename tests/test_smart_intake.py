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
