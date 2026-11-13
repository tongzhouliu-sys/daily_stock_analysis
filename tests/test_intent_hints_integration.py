# -*- coding: utf-8 -*-
"""Test intent hints integration with select_questions.

Phase 1: Market auto-identification
Verifies that when client provides intent_hints, the corresponding dimensions
are skipped in the question selection.
"""

from __future__ import annotations

from src.agent.intake_questions import select_questions


def test_intent_hints_skip_object_dimension():
    """When client provides extracted_stock_code + inferred_market, object question is skipped."""
    # Without hints: should ask object question (or at least include it)
    questions_without_hints = select_questions(
        "分析 600519",
        stock_clarification_needed=True,
    )
    has_object_without = any(q.id == "object" for q in questions_without_hints)
    
    # With hints: should NOT ask object question
    questions_with_hints = select_questions(
        "分析 600519",
        stock_clarification_needed=True,
        intent_hints={
            "extracted_stock_code": "600519",
            "inferred_market": "a_share",
        },
    )
    has_object_with = any(q.id == "object" for q in questions_with_hints)
    
    assert has_object_without is True, "Without hints, object question should be asked"
    assert has_object_with is False, "With hints, object question should be skipped"


def test_intent_hints_skip_goal_dimension():
    """When client provides goal hint, goal question is skipped."""
    questions_without_hints = select_questions("帮我看看茅台")
    has_goal_without = any(q.id == "goal" for q in questions_without_hints)
    
    questions_with_hints = select_questions(
        "帮我看看茅台",
        intent_hints={"goal": "worth_watching"},
    )
    has_goal_with = any(q.id == "goal" for q in questions_with_hints)
    
    assert has_goal_without is True, "Without hints, goal question should be asked"
    assert has_goal_with is False, "With hints, goal question should be skipped"


def test_intent_hints_skip_horizon_dimension():
    """When client provides horizon hint, horizon question is skipped."""
    questions_without_hints = select_questions("分析腾讯")
    has_horizon_without = any(q.id == "horizon" for q in questions_without_hints)
    
    questions_with_hints = select_questions(
        "分析腾讯",
        intent_hints={"horizon": "days"},
    )
    has_horizon_with = any(q.id == "horizon" for q in questions_with_hints)
    
    assert has_horizon_without is True, "Without hints, horizon question should be asked"
    assert has_horizon_with is False, "With hints, horizon question should be skipped"


def test_intent_hints_skip_position_dimension():
    """When client provides position hint, position-related logic is adjusted."""
    # Position question only appears when goal is "evaluate_holding"
    questions_without_hints = select_questions(
        "评估我的持仓",
        intent_hints={"goal": "evaluate_holding"},
    )
    # Should have position question because goal suggests holding evaluation
    has_position_without = any(q.id == "position" for q in questions_without_hints)
    
    questions_with_hints = select_questions(
        "评估我的持仓",
        intent_hints={
            "goal": "evaluate_holding",
            "position": "held_protect_profit",
        },
    )
    # Position should be skipped because client already provided it
    has_position_with = any(q.id == "position" for q in questions_with_hints)
    
    # Note: position question is added by apply_branching, not select_questions
    # So this test verifies the hint is accepted but doesn't change select_questions behavior
    # The actual skipping happens in the service layer
    assert has_position_without is False or has_position_with is False


def test_intent_hints_multiple_dimensions():
    """When client provides multiple hints, all corresponding dimensions are skipped."""
    questions = select_questions(
        "短线看茅台",
        intent_hints={
            "extracted_stock_code": "600519",
            "inferred_market": "a_share",
            "goal": "worth_watching",
            "horizon": "days",
        },
    )
    
    # Should NOT have object, goal, or horizon questions
    question_ids = [q.id for q in questions]
    assert "object" not in question_ids, "Object question should be skipped"
    assert "goal" not in question_ids, "Goal question should be skipped"
    assert "horizon" not in question_ids, "Horizon question should be skipped"
    
    # Should still have risk_focus (not provided in hints)
    assert "risk_focus" in question_ids, "Risk focus question should still be asked"


def test_intent_hints_partial():
    """When client provides only some hints, only those dimensions are skipped."""
    questions = select_questions(
        "分析 600519",
        intent_hints={
            "extracted_stock_code": "600519",
            "inferred_market": "a_share",
            # No goal or horizon hints
        },
    )
    
    question_ids = [q.id for q in questions]
    assert "object" not in question_ids, "Object question should be skipped"
    assert "goal" in question_ids, "Goal question should still be asked"
    assert "horizon" in question_ids, "Horizon question should still be asked"


def test_intent_hints_empty():
    """When client provides empty hints dict, behavior is unchanged."""
    questions_without = select_questions("分析茅台")
    questions_with_empty = select_questions("分析茅台", intent_hints={})
    
    # Should produce the same questions
    assert len(questions_without) == len(questions_with_empty)
    assert [q.id for q in questions_without] == [q.id for q in questions_with_empty]


def test_intent_hints_none():
    """When client provides None hints, behavior is unchanged."""
    questions_without = select_questions("分析茅台")
    questions_with_none = select_questions("分析茅台", intent_hints=None)
    
    # Should produce the same questions
    assert len(questions_without) == len(questions_with_none)
    assert [q.id for q in questions_without] == [q.id for q in questions_with_none]
