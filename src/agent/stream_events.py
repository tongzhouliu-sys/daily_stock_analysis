# -*- coding: utf-8 -*-
"""Helpers for building agent progress stream events.

The SSE endpoint forwards plain dictionaries, so these helpers intentionally
return dicts and keep existing field names stable for older clients.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Tool name → display label mapping for UI tags
# Maps internal tool names to user-friendly Chinese labels
TOOL_DISPLAY_LABELS: Dict[str, str] = {
    # K线数据获取
    "get_daily_history": "K线获取",
    "get_intraday_data": "分时数据",
    # 基本面分析
    "get_stock_info": "基本面分析",
    "get_financial_data": "财务数据",
    # 行情获取/分析
    "get_realtime_quote": "行情获取",
    "get_market_indices": "行情分析",
    "get_sector_rankings": "行情分析",
    # 技术分析
    "analyze_trend": "技术分析",
    "analyze_pattern": "技术分析",
    "calculate_ma": "技术分析",
    "get_volume_analysis": "技术分析",
    # 情报搜索
    "search_stock_news": "新闻搜索",
    "search_comprehensive_intel": "情报搜索",
    # 其他工具
    "get_chip_distribution": "筹码分布",
    "get_analysis_context": "分析上下文",
    "get_skill_backtest_summary": "技能回测",
    "get_strategy_backtest_summary": "策略回测",
    "get_stock_backtest_summary": "个股回测",
}

# Category mapping for grouping tools into UI tags
# Maps tool names to their display category
TOOL_CATEGORIES: Dict[str, str] = {
    # K线获取
    "get_daily_history": "kline",
    "get_intraday_data": "kline",
    # 基本面分析
    "get_stock_info": "fundamental",
    "get_financial_data": "fundamental",
    # 行情获取
    "get_realtime_quote": "quote",
    # 行情分析
    "get_market_indices": "market",
    "get_sector_rankings": "market",
    # 技术分析
    "analyze_trend": "technical",
    "analyze_pattern": "technical",
    "calculate_ma": "technical",
    "get_volume_analysis": "technical",
    # 情报搜索
    "search_stock_news": "news",
    "search_comprehensive_intel": "intel",
    # 其他
    "get_chip_distribution": "other",
    "get_analysis_context": "other",
    "get_skill_backtest_summary": "other",
    "get_strategy_backtest_summary": "other",
    "get_stock_backtest_summary": "other",
}

# Outcome values for tool/stage results
OUTCOME_SUCCESS = "success"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_SKIPPED = "skipped"

# Category display labels for UI tags
CATEGORY_DISPLAY_LABELS: Dict[str, Dict[str, str]] = {
    "kline": {"zh": "K线获取", "en": "K-line Data"},
    "fundamental": {"zh": "基本面分析", "en": "Fundamental Analysis"},
    "quote": {"zh": "行情获取", "en": "Quote Fetch"},
    "market": {"zh": "行情分析", "en": "Market Analysis"},
    "technical": {"zh": "技术分析", "en": "Technical Analysis"},
    "news": {"zh": "新闻搜索", "en": "News Search"},
    "intel": {"zh": "情报搜索", "en": "Intel Search"},
    "analysis_steps": {"zh": "分析步骤", "en": "Analysis Steps"},
    "agent_collaboration": {"zh": "Agent 协作", "en": "Agent Collaboration"},
    "other": {"zh": "工具调用", "en": "Tool Calls"},
}

# Outcome display labels
OUTCOME_DISPLAY_LABELS: Dict[str, Dict[str, str]] = {
    "success": {"zh": "成功", "en": "Success"},
    "partial": {"zh": "部分可用", "en": "Partial"},
    "failed": {"zh": "失败", "en": "Failed"},
    "timeout": {"zh": "超时", "en": "Timeout"},
    "skipped": {"zh": "已跳过", "en": "Skipped"},
    "incomplete": {"zh": "未完成", "en": "Incomplete"},
}

# Event sequence counter for ordering and deduplication
# Each turn maintains its own sequence starting from 0
_event_sequence_counter = 0


def reset_event_sequence() -> None:
    """Reset the event sequence counter (call at the start of each turn)."""
    global _event_sequence_counter
    _event_sequence_counter = 0


def next_event_sequence() -> int:
    """Get the next event sequence number."""
    global _event_sequence_counter
    seq = _event_sequence_counter
    _event_sequence_counter += 1
    return seq


def stream_event(event_type: str, **fields: Any) -> Dict[str, Any]:
    """Build a progress event while dropping unset fields.

    Existing clients depend on top-level fields such as ``type``, ``step``,
    ``tool``, ``success`` and ``duration``.  Newer events can add fields like
    ``stage`` or ``meta`` without changing the callback/SSE contract.
    
    New fields for displayable events (T1):
    - call_id: Unique identifier for this tool call within the turn
    - stage_id: Unique identifier for a stage (multi-agent)
    - parent_stage_id: Parent stage ID for nested calls
    - outcome: Result outcome (success/partial/failed/timeout/skipped)
    - detail: Public-facing detail fields (data_source, date_range, etc.)
    - display_label: User-friendly label for UI display
    - sequence: Monotonic sequence number for ordering and deduplication
    """
    event: Dict[str, Any] = {"type": event_type}
    # Add sequence number for ordering and deduplication
    event["sequence"] = next_event_sequence()
    event.update({key: value for key, value in fields.items() if value is not None})
    return event


def get_category_for_tool(tool_name: str) -> str:
    """Get the display category for a tool name."""
    return TOOL_CATEGORIES.get(tool_name, "other")


def get_category_display_label(category: str, language: str = "zh") -> str:
    """Get the display label for a category in the specified language."""
    labels = CATEGORY_DISPLAY_LABELS.get(category, CATEGORY_DISPLAY_LABELS["other"])
    return labels.get(language, labels["zh"])


def get_outcome_display_label(outcome: str, language: str = "zh") -> str:
    """Get the display label for an outcome in the specified language."""
    labels = OUTCOME_DISPLAY_LABELS.get(outcome, OUTCOME_DISPLAY_LABELS["failed"])
    return labels.get(language, labels["zh"])


def tool_start_event(
    step: int,
    tool: str,
    *,
    call_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    parent_stage_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a tool_start event with displayable metadata.
    
    Args:
        step: Current step number
        tool: Tool name
        call_id: Unique call identifier (auto-generated if not provided)
        stage_id: Stage identifier for multi-agent
        parent_stage_id: Parent stage for nested calls
    
    Returns:
        Event dict with display_label and call_id
    """
    import uuid
    
    if call_id is None:
        call_id = str(uuid.uuid4())[:8]
    
    display_label = TOOL_DISPLAY_LABELS.get(tool, tool)
    
    return stream_event(
        "tool_start",
        step=step,
        tool=tool,
        call_id=call_id,
        stage_id=stage_id,
        parent_stage_id=parent_stage_id,
        display_label=display_label,
    )


def tool_done_event(
    step: int,
    tool: str,
    *,
    success: bool,
    duration: float,
    call_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    parent_stage_id: Optional[str] = None,
    timeout: bool = False,
    detail: Optional[Dict[str, Any]] = None,
    cached: bool = False,
) -> Dict[str, Any]:
    """Build a tool_done event with displayable metadata.
    
    Args:
        step: Current step number
        tool: Tool name
        success: Whether the tool succeeded
        duration: Execution duration in seconds
        call_id: Unique call identifier (should match tool_start)
        stage_id: Stage identifier for multi-agent
        parent_stage_id: Parent stage for nested calls
        timeout: Whether the tool timed out
        detail: Public-facing detail fields
        cached: Whether result was from cache
    
    Returns:
        Event dict with outcome, display_label, and detail
    """
    # Determine outcome
    if timeout:
        outcome = OUTCOME_TIMEOUT
    elif success:
        outcome = OUTCOME_SUCCESS
    else:
        outcome = OUTCOME_FAILED
    
    display_label = TOOL_DISPLAY_LABELS.get(tool, tool)
    
    event = stream_event(
        "tool_done",
        step=step,
        tool=tool,
        success=success,
        duration=duration,
        call_id=call_id,
        stage_id=stage_id,
        parent_stage_id=parent_stage_id,
        outcome=outcome,
        display_label=display_label,
    )
    
    # Add detail fields if provided
    if detail:
        event["detail"] = detail
    
    if cached:
        event["cached"] = True
    
    return event


def stage_start_event(
    stage: str,
    *,
    stage_id: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a stage_start event for multi-agent orchestration.
    
    Args:
        stage: Stage/agent name
        stage_id: Unique stage identifier
        message: Optional status message
    
    Returns:
        Event dict with stage_id
    """
    import uuid
    
    if stage_id is None:
        stage_id = str(uuid.uuid4())[:8]
    
    return stream_event(
        "stage_start",
        stage=stage,
        stage_id=stage_id,
        message=message,
    )


def stage_done_event(
    stage: str,
    *,
    status: str,
    duration: Optional[float] = None,
    stage_id: Optional[str] = None,
    message: Optional[str] = None,
    outcome: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a stage_done event for multi-agent orchestration.
    
    Args:
        stage: Stage/agent name
        status: Status string (completed/failed/timeout/skipped)
        duration: Stage duration in seconds
        stage_id: Unique stage identifier
        message: Optional status message
        outcome: Explicit outcome (defaults to status mapping)
    
    Returns:
        Event dict with outcome
    """
    # Map status to outcome if not provided
    if outcome is None:
        outcome_map = {
            "completed": OUTCOME_SUCCESS,
            "failed": OUTCOME_FAILED,
            "timeout": OUTCOME_TIMEOUT,
            "skipped": OUTCOME_SKIPPED,
        }
        outcome = outcome_map.get(status, OUTCOME_FAILED)
    
    return stream_event(
        "stage_done",
        stage=stage,
        status=status,
        duration=duration,
        stage_id=stage_id,
        message=message,
        outcome=outcome,
    )


def content_delta_event(step: int, text: str) -> Dict[str, Any]:
    """Build a content_delta event for streaming content.
    
    T5: 流式输出内容增量事件
    
    Args:
        step: Current step number
        text: Incremental text content
    
    Returns:
        Event dict with step and text
    """
    return stream_event("content_delta", step=step, text=text)


def content_reset_event(step: int, reason: str = "") -> Dict[str, Any]:
    """Build a content_reset event to clear streaming content.
    
    T5: 流式输出内容重置事件
    
    Args:
        step: Current step number
        reason: Optional reason for reset (e.g., "tool_call", "error")
    
    Returns:
        Event dict with step and optional reason
    """
    return stream_event("content_reset", step=step, reason=reason if reason else None)
