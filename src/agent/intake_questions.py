# -*- coding: utf-8 -*-
"""Intake question bank and branching logic.

Defines the controlled set of questions used for first-time question guidance.
The model may assist in ordering and phrasing, but the server validates all
enumerations, types, dependencies, and counts. The model cannot freely output
unparseable forms or add a 6th question.

T1: Contract and question bank convergence.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.schemas.agent_intake import (
    AnalysisBrief,
    Answer,
    Question,
    QuestionDimension,
    QuestionOption,
    QuestionType,
)

# Maximum number of questions per intake
MAX_QUESTIONS = 5

# ─────────────────────────────────────────────────────────────────────────────
# Question bank definitions
# ─────────────────────────────────────────────────────────────────────────────

QUESTION_OBJECT = Question(
    id="object",
    dimension=QuestionDimension.OBJECT,
    type=QuestionType.SINGLE,
    title="你关注哪个市场的标的？",
    help="明确市场可以避免混淆不同交易所的同名标的。",
    required=False,
    depends_on=None,
    options=[
        QuestionOption(id="a_share", label="A 股"),
        QuestionOption(id="hk", label="港股"),
        QuestionOption(id="us", label="美股"),
        QuestionOption(id="unsure", label="暂不确定", is_unknown=True),
    ],
)

QUESTION_GOAL = Question(
    id="goal",
    dimension=QuestionDimension.GOAL,
    type=QuestionType.SINGLE,
    title="这次最想解决什么问题？",
    help="了解你的目标可以帮助我调整分析侧重点。",
    required=False,
    depends_on=None,
    options=[
        QuestionOption(id="worth_watching", label="判断是否值得关注"),
        QuestionOption(id="evaluate_holding", label="评估已有持仓"),
        QuestionOption(id="understand_move", label="了解涨跌原因"),
        QuestionOption(id="learn_method", label="学习分析方法"),
        QuestionOption(id="unsure", label="暂不确定", is_unknown=True),
    ],
)

QUESTION_HORIZON = Question(
    id="horizon",
    dimension=QuestionDimension.HORIZON,
    type=QuestionType.SINGLE,
    title="你更关注多长时间？",
    help="这会帮助我选择分析尺度。",
    required=False,
    depends_on=None,
    options=[
        QuestionOption(id="days", label="数日内"),
        QuestionOption(id="weeks", label="1–4 周"),
        QuestionOption(id="months", label="1–6 个月"),
        QuestionOption(id="long", label="6 个月以上"),
        QuestionOption(id="unsure", label="还没确定", is_unknown=True),
    ],
)

QUESTION_POSITION = Question(
    id="position",
    dimension=QuestionDimension.POSITION,
    type=QuestionType.SINGLE,
    title="你目前持有这只股票吗？",
    help="持仓信息会影响分析的侧重点。",
    required=False,
    depends_on="goal",
    options=[
        QuestionOption(id="not_held", label="尚未持有"),
        QuestionOption(id="held_protect_profit", label="已持有，关注盈利保护"),
        QuestionOption(id="held_control_drawdown", label="已持有，关注回撤"),
        QuestionOption(id="decline", label="暂不提供", is_unknown=True),
    ],
)

QUESTION_RISK_FOCUS = Question(
    id="risk_focus",
    dimension=QuestionDimension.RISK_FOCUS,
    type=QuestionType.SINGLE,
    title="这次分析优先考虑什么？",
    help="这是决策偏好，不是正式风险评级。",
    required=False,
    depends_on=None,
    options=[
        QuestionOption(id="downside", label="控制下行风险"),
        QuestionOption(id="balanced", label="机会与风险均衡"),
        QuestionOption(id="tolerant", label="愿意接受较大波动"),
        QuestionOption(id="unsure", label="暂不确定", is_unknown=True),
    ],
)

QUESTION_RESEARCH_FOCUS = Question(
    id="research_focus",
    dimension=QuestionDimension.RESEARCH_FOCUS,
    type=QuestionType.MULTIPLE,
    title="哪些内容对你最有帮助？",
    help="最多选 2 项。「综合分析」与其他项互斥。",
    required=False,
    depends_on=None,
    max_selections=2,
    options=[
        QuestionOption(id="trend_levels", label="趋势与关键位置"),
        QuestionOption(id="business_valuation", label="业务与估值"),
        QuestionOption(id="news_events", label="新闻与事件"),
        QuestionOption(id="risks_counterpoints", label="风险与反例"),
        QuestionOption(id="comprehensive", label="综合分析"),
        QuestionOption(id="unsure", label="暂不确定", is_unknown=True),
    ],
)

QUESTION_READING_STYLE = Question(
    id="reading_style",
    dimension=QuestionDimension.READING_STYLE,
    type=QuestionType.SINGLE,
    title="你希望怎样阅读答案？",
    help="选择适合你的呈现方式。",
    required=False,
    depends_on=None,
    options=[
        QuestionOption(id="highlights", label="先看重点"),
        QuestionOption(id="detailed", label="详细研究"),
        QuestionOption(id="plain", label="用通俗语言解释"),
        QuestionOption(id="unsure", label="暂不确定", is_unknown=True),
    ],
)

# Master question bank
QUESTION_BANK: Dict[str, Question] = {
    "object": QUESTION_OBJECT,
    "goal": QUESTION_GOAL,
    "horizon": QUESTION_HORIZON,
    "position": QUESTION_POSITION,
    "risk_focus": QUESTION_RISK_FOCUS,
    "research_focus": QUESTION_RESEARCH_FOCUS,
    "reading_style": QUESTION_READING_STYLE,
}

# ─────────────────────────────────────────────────────────────────────────────
# Intent classification for question selection
# ─────────────────────────────────────────────────────────────────────────────

# Patterns for concept questions (no holding/risk needed)
CONCEPT_PATTERNS = [
    r"什么是", r"解释.*一下", r"什么叫", r"是什么意思",
    r"如何理解", r"怎么理解", r"讲讲.*原理",
    r"what\s+is", r"explain", r"how\s+to\s+understand",
]

# Patterns for comparison questions
COMPARISON_PATTERNS = [
    r"比较", r"对比", r"和.*哪个", r"vs", r"versus",
    r"compare", r"versus", r"vs\.?",
]

# Patterns indicating explicit object info
OBJECT_EXPLICIT_PATTERNS = [
    r"^(?:帮我)?(?:看看|分析|研究|诊断)\s+",
    r"(?:aapl|msft|googl?|amzn|tsla|meta|nvda|nvo)",
    r"(?:600519|300750|002594|688981|hk00700)",
]

# Patterns indicating explicit goal
GOAL_PATTERNS = {
    "worth_watching": [r"值得.*关注", r"值得.*买", r"能不能.*买"],
    "evaluate_holding": [r"持仓", r"持有", r"仓位", r"已买"],
    "understand_move": [r"为什么.*涨", r"为什么.*跌", r"涨跌.*原因"],
    "learn_method": [r"怎么分析", r"分析方法", r"学习"],
}

# Patterns indicating explicit horizon
HORIZON_PATTERNS = {
    "days": [r"几天", r"短线", r"日内", r"数日"],
    "weeks": [r"几周", r"短期", r"1.*4.*周"],
    "months": [r"几个月", r"中期", r"1.*6.*月", r"半年"],
    "long": [r"长期", r"半年以上", r"一年", r"数年"],
}

# Patterns indicating explicit position
POSITION_PATTERNS = {
    "not_held": [r"没有.*持仓", r"未.*持有", r"空仓", r"想.*买"],
    "held_protect_profit": [r"持有.*盈利", r"持仓.*赚", r"保护.*利润"],
    "held_control_drawdown": [r"持有.*亏", r"持仓.*套", r"控制.*回撤"],
}

# Patterns indicating explicit risk focus
RISK_PATTERNS = {
    "downside": [r"控制.*风险", r"下行.*风险", r"稳健"],
    "balanced": [r"均衡", r"机会.*风险"],
    "tolerant": [r"波动", r"激进", r"高风险.*高收益"],
}


def classify_question_intent(message: str) -> Dict[str, Any]:
    """Classify the user's question intent to determine which questions to ask.

    Returns a dict with:
    - question_type: "research" | "concept" | "comparison"
    - explicit_dims: set of dimensions already provided in the message
    - inferred_dims: set of dimensions that can be inferred
    """
    text = message.strip().lower()

    # Determine question type
    is_concept = any(re.search(p, text, re.IGNORECASE) for p in CONCEPT_PATTERNS)
    is_comparison = any(re.search(p, text, re.IGNORECASE) for p in COMPARISON_PATTERNS)

    if is_concept:
        question_type = "concept"
    elif is_comparison:
        question_type = "comparison"
    else:
        question_type = "research"

    # Detect explicit dimensions
    explicit_dims: Set[str] = set()

    # Object detection
    if any(re.search(p, text, re.IGNORECASE) for p in OBJECT_EXPLICIT_PATTERNS):
        explicit_dims.add("object")

    # Goal detection
    for goal_id, patterns in GOAL_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            explicit_dims.add("goal")
            break

    # Horizon detection
    for horizon_id, patterns in HORIZON_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            explicit_dims.add("horizon")
            break

    # Position detection
    for position_id, patterns in POSITION_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            explicit_dims.add("position")
            break

    # Risk detection
    for risk_id, patterns in RISK_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            explicit_dims.add("risk_focus")
            break

    return {
        "question_type": question_type,
        "explicit_dims": explicit_dims,
    }


def select_questions(
    message: str,
    *,
    stock_clarification_needed: bool = False,
    report_follow_up: bool = False,
) -> List[Question]:
    """Select 1-5 questions based on the user's message and context.

    Rules:
    - Max 5 questions total (including stock clarification if needed)
    - Resolve object ambiguity first
    - For concept questions, only ask reading_style
    - For report follow-ups, only fill missing background
    - If most info is explicit, show a single confirmation question
    """
    intent = classify_question_intent(message)
    question_type = intent["question_type"]
    explicit_dims = intent["explicit_dims"]

    selected: List[Question] = []
    asked_dims: Set[str] = set()

    # 1. Object clarification counts as first question
    if stock_clarification_needed:
        selected.append(QUESTION_OBJECT)
        asked_dims.add("object")

    # 2. For concept questions, only ask reading style
    if question_type == "concept":
        if "reading_style" not in explicit_dims:
            selected.append(QUESTION_READING_STYLE)
        return selected[:MAX_QUESTIONS]

    # 3. For comparison questions, focus on research_focus and reading_style
    if question_type == "comparison":
        if "research_focus" not in explicit_dims:
            selected.append(QUESTION_RESEARCH_FOCUS)
        if "reading_style" not in explicit_dims:
            selected.append(QUESTION_READING_STYLE)
        return selected[:MAX_QUESTIONS]

    # 4. For research questions, follow the priority order
    # Priority: goal -> horizon -> risk_focus -> position -> research_focus -> reading_style

    # Goal (if not explicit)
    if "goal" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_GOAL)
        asked_dims.add("goal")

    # Horizon (for trend/valuation/position questions)
    if "horizon" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_HORIZON)
        asked_dims.add("horizon")

    # Risk focus (for decision-oriented research)
    if "risk_focus" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_RISK_FOCUS)
        asked_dims.add("risk_focus")

    # Position (only if goal is "evaluate_holding" or similar)
    # We'll add it conditionally based on goal answer later
    # For now, include it if we have room
    if "position" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_POSITION)
        asked_dims.add("position")

    # Research focus (if room)
    if "research_focus" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_RESEARCH_FOCUS)
        asked_dims.add("research_focus")

    # Reading style (if room and not explicit)
    if "reading_style" not in explicit_dims and len(selected) < MAX_QUESTIONS:
        selected.append(QUESTION_READING_STYLE)

    # If everything is explicit, return a single confirmation question
    if not selected and not stock_clarification_needed:
        # Create a confirmation-only question
        confirm_question = Question(
            id="confirm",
            dimension=QuestionDimension.GOAL,
            type=QuestionType.SINGLE,
            title="按这些条件分析？",
            help="你的问题已包含足够的分析背景信息。",
            required=False,
            depends_on=None,
            options=[
                QuestionOption(id="proceed", label="按这些条件分析"),
                QuestionOption(id="adjust", label="调整条件"),
                QuestionOption(id="generic", label="只做通用解读"),
            ],
        )
        selected.append(confirm_question)

    # Report follow-up: only fill missing background, max 2 questions
    if report_follow_up:
        selected = selected[:2]

    return selected[:MAX_QUESTIONS]


def apply_branching(
    questions: List[Question],
    answers: List[Answer],
) -> List[Question]:
    """Apply branching logic: remove questions whose dependencies are not met.

    For example, if the user selects "暂不提供" for position, we don't need
    to ask follow-up questions about position.
    """
    answer_map = {a.question_id: a for a in answers}
    result: List[Question] = []

    for q in questions:
        if q.depends_on is None:
            result.append(q)
            continue

        # Check if the dependency is satisfied
        dep_answer = answer_map.get(q.depends_on)
        if dep_answer is None:
            # Dependency not answered yet, include the question
            result.append(q)
            continue

        # Check if the answer makes this question irrelevant
        dep_option_ids = set(dep_answer.option_ids)
        # If user selected "unsure" or "decline" for goal, skip position
        if q.depends_on == "goal" and dep_option_ids & {"unsure"}:
            continue
        # If user selected "not_held" for position, some questions may not apply
        # (but we still include them for now)

        result.append(q)

    return result


def invalidate_dependent_answers(
    questions: List[Question],
    answers: List[Answer],
    changed_question_id: str,
) -> List[Answer]:
    """When a前置 answer changes, remove answers that depend on it."""
    # Find questions that depend on the changed question
    dependent_ids: Set[str] = set()
    for q in questions:
        if q.depends_on == changed_question_id:
            dependent_ids.add(q.id)

    # Remove answers for dependent questions
    return [a for a in answers if a.question_id not in dependent_ids]


def build_analysis_brief(
    questions: List[Question],
    answers: List[Answer],
) -> AnalysisBrief:
    """Build the confirmed analysis brief from questions and answers."""
    answer_map = {a.question_id: a for a in answers}
    unknown_fields: List[str] = []

    brief = AnalysisBrief(brief_version=1)

    for q in questions:
        answer = answer_map.get(q.id)
        if answer is None:
            continue

        dim = q.dimension.value
        option_ids = answer.option_ids

        # Check if user selected "unknown"
        if answer.source == "unknown" or any(
            opt.is_unknown for opt in q.options if opt.id in option_ids
        ):
            unknown_fields.append(dim)
            continue

        # Map dimension to brief field
        if dim == "object":
            brief.object_scope = option_ids[0] if option_ids else None
        elif dim == "goal":
            brief.goal = option_ids[0] if option_ids else None
        elif dim == "horizon":
            brief.horizon = option_ids[0] if option_ids else None
        elif dim == "position":
            brief.position = option_ids[0] if option_ids else None
        elif dim == "risk_focus":
            brief.risk_focus = option_ids[0] if option_ids else None
        elif dim == "research_focus":
            brief.focus_areas = option_ids[:2]  # Max 2
        elif dim == "reading_style":
            brief.reading_style = option_ids[0] if option_ids else None

    brief.unknown_fields = unknown_fields
    return brief


def validate_answers(
    questions: List[Question],
    answers: List[Answer],
) -> Tuple[bool, Optional[str]]:
    """Validate that answers conform to the question definitions.

    Returns (is_valid, error_message).
    """
    question_map = {q.id: q for q in questions}

    for answer in answers:
        q = question_map.get(answer.question_id)
        if q is None:
            return False, f"Unknown question_id: {answer.question_id}"

        # Check option_ids are valid
        valid_option_ids = {opt.id for opt in q.options}
        for opt_id in answer.option_ids:
            if opt_id not in valid_option_ids:
                return False, f"Invalid option_id '{opt_id}' for question '{answer.question_id}'"

        # Check single vs multiple
        if q.type == QuestionType.SINGLE and len(answer.option_ids) > 1:
            return False, f"Question '{answer.question_id}' is single-choice"

        if q.type == QuestionType.MULTIPLE:
            if q.max_selections and len(answer.option_ids) > q.max_selections:
                return False, (
                    f"Question '{answer.question_id}' allows max {q.max_selections} selections"
                )

        # Check mutual exclusivity for research_focus
        if answer.question_id == "research_focus":
            if "comprehensive" in answer.option_ids and len(answer.option_ids) > 1:
                return False, "'comprehensive' is mutually exclusive with other options"

    return True, None


__all__ = [
    "MAX_QUESTIONS",
    "QUESTION_BANK",
    "classify_question_intent",
    "select_questions",
    "apply_branching",
    "invalidate_dependent_answers",
    "build_analysis_brief",
    "validate_answers",
]
