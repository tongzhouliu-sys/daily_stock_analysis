# -*- coding: utf-8 -*-
"""Agent Chat Intake - Question bank and branching logic.

T1-2: Controlled question bank with rule-based branching.

This module provides:
- Question definitions for each dimension
- Branching rules based on user answers
- Question planning logic (which questions to ask)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from src.schemas.agent_intake import (
    Answer,
    Question,
    QuestionDimension,
    QuestionOption,
    QuestionType,
)

logger = logging.getLogger(__name__)

# Maximum number of questions per intake session
MAX_QUESTIONS = 5


# ============================================================================
# Question definitions
# ============================================================================

def _make_option(id: str, label: str, is_unknown: bool = False) -> QuestionOption:
    """Helper to create a question option."""
    return QuestionOption(id=id, label=label, is_unknown=is_unknown)


# Object dimension - for disambiguation
OBJECT_QUESTION = Question(
    id="object",
    dimension=QuestionDimension.OBJECT,
    type=QuestionType.SINGLE,
    title="你关注哪个市场的标的？",
    help="不同市场的数据和分析维度不同",
    options=[
        _make_option("a_share", "A 股"),
        _make_option("hk", "港股"),
        _make_option("us", "美股"),
        _make_option("concept", "只了解概念，不针对具体标的"),
        _make_option("unknown", "暂不确定", is_unknown=True),
    ],
    required=False,
)

# Goal dimension
GOAL_QUESTION = Question(
    id="goal",
    dimension=QuestionDimension.GOAL,
    type=QuestionType.SINGLE,
    title="这次最想解决什么问题？",
    help="帮助我选择分析深度和侧重点",
    options=[
        _make_option("evaluate", "判断是否值得关注"),
        _make_option("position", "评估已有持仓"),
        _make_option("explain", "了解涨跌原因"),
        _make_option("learn", "学习分析方法"),
        _make_option("unknown", "暂不确定", is_unknown=True),
    ],
    required=False,
)

# Horizon dimension
HORIZON_QUESTION = Question(
    id="horizon",
    dimension=QuestionDimension.HORIZON,
    type=QuestionType.SINGLE,
    title="你更关注多长时间？",
    help="这会帮助我选择分析尺度",
    options=[
        _make_option("days", "数日内"),
        _make_option("weeks", "1–4 周"),
        _make_option("months", "1–6 个月"),
        _make_option("long", "6 个月以上"),
        _make_option("unknown", "还没确定", is_unknown=True),
    ],
    required=False,
)

# Position dimension
POSITION_QUESTION = Question(
    id="position",
    dimension=QuestionDimension.POSITION,
    type=QuestionType.SINGLE,
    title="你目前持有这只股票吗？",
    help="持仓信息会改变回答的侧重点",
    options=[
        _make_option("no", "尚未持有"),
        _make_option("yes_profit", "已持有，关注盈利保护"),
        _make_option("yes_risk", "已持有，关注回撤"),
        _make_option("unknown", "暂不提供", is_unknown=True),
    ],
    required=False,
)

# Risk focus dimension
RISK_FOCUS_QUESTION = Question(
    id="risk_focus",
    dimension=QuestionDimension.RISK_FOCUS,
    type=QuestionType.SINGLE,
    title="这次分析优先考虑什么？",
    help="不等于正式风险评级，只是了解你的偏好",
    options=[
        _make_option("downside", "控制下行风险"),
        _make_option("balanced", "机会与风险均衡"),
        _make_option("volatile", "愿意接受较大波动"),
        _make_option("unknown", "暂不确定", is_unknown=True),
    ],
    required=False,
)

# Research focus dimension (multiple choice)
RESEARCH_FOCUS_QUESTION = Question(
    id="research_focus",
    dimension=QuestionDimension.RESEARCH_FOCUS,
    type=QuestionType.MULTIPLE,
    title="哪些内容对你最有帮助？",
    help="最多选 2 项，综合分析与其他项互斥",
    options=[
        _make_option("trend", "趋势与关键位置"),
        _make_option("fundamental", "业务与估值"),
        _make_option("news", "新闻与事件"),
        _make_option("risk", "风险与反例"),
        _make_option("comprehensive", "综合分析"),
    ],
    required=False,
    max_selections=2,
)

# Reading style dimension
READING_STYLE_QUESTION = Question(
    id="reading_style",
    dimension=QuestionDimension.READING_STYLE,
    type=QuestionType.SINGLE,
    title="你希望怎样阅读答案？",
    help="选择适合你的解释方式",
    options=[
        _make_option("highlights", "先看重点"),
        _make_option("detailed", "详细研究"),
        _make_option("plain", "用通俗语言解释"),
        _make_option("unknown", "都可以", is_unknown=True),
    ],
    required=False,
)

# Confirmation question
CONFIRM_QUESTION = Question(
    id="confirm",
    dimension=QuestionDimension.GOAL,  # Reuse GOAL dimension
    type=QuestionType.SINGLE,
    title="按这些条件分析？",
    help="确认背景后开始研究",
    options=[
        _make_option("confirm", "按这些背景开始分析"),
        _make_option("adjust", "调整条件"),
        _make_option("generic", "只做通用解读"),
    ],
    required=True,
)


# ============================================================================
# Question planning logic
# ============================================================================

def get_all_questions() -> Dict[str, Question]:
    """Return all available questions indexed by ID."""
    return {
        "object": OBJECT_QUESTION,
        "goal": GOAL_QUESTION,
        "horizon": HORIZON_QUESTION,
        "position": POSITION_QUESTION,
        "risk_focus": RISK_FOCUS_QUESTION,
        "research_focus": RESEARCH_FOCUS_QUESTION,
        "reading_style": READING_STYLE_QUESTION,
        "confirm": CONFIRM_QUESTION,
    }


def get_question_by_id(question_id: str) -> Optional[Question]:
    """Get a question by its ID."""
    return get_all_questions().get(question_id)


def plan_questions(
    message: str,
    existing_answers: Optional[List[Answer]] = None,
    *,
    has_object: bool = False,
    is_concept_question: bool = False,
) -> List[str]:
    """Plan which questions to ask based on context.
    
    Args:
        message: Original user message
        existing_answers: Previously collected answers
        has_object: Whether stock object is already resolved
        is_concept_question: Whether this is a concept-only question
    
    Returns:
        List of question IDs to ask (in order)
    
    Logic:
    - If object is ambiguous, ask object first
    - Then ask goal, horizon, and relevant constraints
    - Concept questions skip position/risk questions
    - Maximum 5 questions total
    - If information is sufficient, only ask confirmation
    """
    questions: List[str] = []
    answered_ids: Set[str] = {a.question_id for a in (existing_answers or [])}
    
    # If object is not resolved and not a concept question, ask object first
    if not has_object and not is_concept_question:
        if "object" not in answered_ids:
            questions.append("object")
    
    # Concept questions skip position/risk questions
    if is_concept_question:
        # Only ask reading style for concept questions
        if "reading_style" not in answered_ids:
            questions.append("reading_style")
        return questions[:MAX_QUESTIONS]
    
    # For stock analysis, ask goal and horizon
    if "goal" not in answered_ids:
        questions.append("goal")
    
    if "horizon" not in answered_ids:
        questions.append("horizon")
    
    # Check if we need position question
    # Ask position if goal is "evaluate" or "position"
    goal_answer = _find_answer(existing_answers, "goal")
    if goal_answer and goal_answer.option_ids[0] in ("evaluate", "position"):
        if "position" not in answered_ids:
            questions.append("position")
    
    # Always ask risk focus for stock analysis
    if "risk_focus" not in answered_ids:
        questions.append("risk_focus")
    
    # Ask research focus if we have room
    if len(questions) < MAX_QUESTIONS - 1:  # Leave room for confirm
        if "research_focus" not in answered_ids:
            questions.append("research_focus")
    
    # Ask reading style if we have room
    if len(questions) < MAX_QUESTIONS - 1:
        if "reading_style" not in answered_ids:
            questions.append("reading_style")
    
    # Add confirmation question at the end
    if "confirm" not in answered_ids:
        questions.append("confirm")
    
    return questions[:MAX_QUESTIONS]


def _find_answer(answers: Optional[List[Answer]], question_id: str) -> Optional[Answer]:
    """Find an answer by question ID."""
    if not answers:
        return None
    for answer in answers:
        if answer.question_id == question_id:
            return answer
    return None


def should_ask_question(
    question_id: str,
    answers: List[Answer],
) -> bool:
    """Determine if a question should be asked based on previous answers.
    
    This implements branching logic:
    - Position question only if goal is evaluate/position
    - Research focus and reading style are optional
    """
    if question_id == "position":
        goal_answer = _find_answer(answers, "goal")
        if not goal_answer:
            return True  # Ask if goal not answered yet
        return goal_answer.option_ids[0] in ("evaluate", "position")
    
    # All other questions can be asked
    return True


def invalidate_dependent_answers(
    changed_question_id: str,
    answers: List[Answer],
) -> List[Answer]:
    """Remove answers that depend on a changed question.
    
    When a user changes a previous answer, dependent answers should be removed.
    For example, changing goal from "evaluate" to "learn" should remove position answer.
    """
    if changed_question_id == "goal":
        # Changing goal invalidates position answer
        return [a for a in answers if a.question_id != "position"]
    
    # For now, no other dependencies
    return answers


def build_analysis_brief(
    answers: List[Answer],
    *,
    object_scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an AnalysisBrief from collected answers.
    
    Args:
        answers: Collected answers
        object_scope: Resolved stock code or object identifier
    
    Returns:
        Dictionary suitable for AnalysisBrief construction
    """
    brief: Dict[str, Any] = {
        "brief_version": 1,
        "object_scope": object_scope,
        "unknown_fields": [],
    }
    
    for answer in answers:
        question = get_question_by_id(answer.question_id)
        if not question:
            continue
        
        # Check if user selected "unknown" option
        is_unknown = any(
            opt.is_unknown
            for opt in question.options
            if opt.id in answer.option_ids
        )
        
        if is_unknown:
            brief["unknown_fields"].append(question.dimension.value)
            continue
        
        # Map answer to brief field
        if question.dimension == QuestionDimension.GOAL:
            brief["goal"] = answer.option_ids[0]
        elif question.dimension == QuestionDimension.HORIZON:
            brief["horizon"] = answer.option_ids[0]
        elif question.dimension == QuestionDimension.POSITION:
            brief["position"] = answer.option_ids[0]
        elif question.dimension == QuestionDimension.RISK_FOCUS:
            brief["risk_focus"] = answer.option_ids[0]
        elif question.dimension == QuestionDimension.RESEARCH_FOCUS:
            brief["focus_areas"] = answer.option_ids
        elif question.dimension == QuestionDimension.READING_STYLE:
            brief["reading_style"] = answer.option_ids[0]
    
    return brief


def validate_answer(
    question: Question,
    answer: Answer,
) -> Optional[str]:
    """Validate an answer against its question.
    
    Returns:
        Error message if invalid, None if valid
    """
    # Check question ID matches
    if answer.question_id != question.id:
        return f"Answer question_id {answer.question_id} does not match question {question.id}"
    
    # Check option IDs are valid
    valid_option_ids = {opt.id for opt in question.options}
    for option_id in answer.option_ids:
        if option_id not in valid_option_ids:
            return f"Invalid option_id: {option_id}"
    
    # Check single vs multiple
    if question.type == QuestionType.SINGLE:
        if len(answer.option_ids) != 1:
            return f"Single-choice question requires exactly 1 option, got {len(answer.option_ids)}"
    elif question.type == QuestionType.MULTIPLE:
        if question.max_selections and len(answer.option_ids) > question.max_selections:
            return f"Multiple-choice question allows at most {question.max_selections} options, got {len(answer.option_ids)}"
    
    # Check research focus mutual exclusion
    if question.id == "research_focus":
        if "comprehensive" in answer.option_ids and len(answer.option_ids) > 1:
            return "综合分析与其他项互斥"
    
    return None


__all__ = [
    "MAX_QUESTIONS",
    "OBJECT_QUESTION",
    "GOAL_QUESTION",
    "HORIZON_QUESTION",
    "POSITION_QUESTION",
    "RISK_FOCUS_QUESTION",
    "RESEARCH_FOCUS_QUESTION",
    "READING_STYLE_QUESTION",
    "CONFIRM_QUESTION",
    "get_all_questions",
    "get_question_by_id",
    "plan_questions",
    "should_ask_question",
    "invalidate_dependent_answers",
    "build_analysis_brief",
    "validate_answer",
]
