# -*- coding: utf-8 -*-
"""
Research command — deep research on a stock or market topic.

Usage:
    /research 600519                        -> Deep research on Kweichow Moutai
    /research 600519 近期业绩风险            -> Focused research with specific question
    /research 新能源板块前景分析              -> Topic-based research
"""

import logging
import re
import time
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.config import get_config

logger = logging.getLogger(__name__)

_RESEARCH_STOCK_CODE_RE = re.compile(
    r"^\d{6}$|^HK\d{5}$|^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$"
)


def _resolve_research_session_id(message: BotMessage) -> str:
    """Return the research session id for the current conversation scope."""
    base_session_id = f"{message.platform}_{message.user_id}"
    # Research uses a separate session scope from chat
    return f"{base_session_id}:research"


class ResearchCommand(BotCommand):
    """
    Research command handler — invoke the deep research agent.

    Usage:
        /research 600519                    -> Deep research on a stock
        /research 600519 业绩风险分析        -> Focused question
        /research 新能源板块 发展前景         -> Sector research
    """

    @property
    def name(self) -> str:
        return "research"

    @property
    def aliases(self) -> List[str]:
        return ["深研", "deepsearch"]

    @property
    def description(self) -> str:
        return "Deep research on a stock or market topic"

    @property
    def usage(self) -> str:
        return "/research <stock_code|topic> [specific question]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if not args:
            return BotResponse.text_response(
                f"Usage: {self.usage}\n"
                "Example: /research 600519 近期有哪些风险\n"
                "Example: /research 新能源板块前景分析"
            )

        config = get_config()

        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Agent 模式未开启，无法使用深度研究功能。\n请在配置中设置 `AGENT_MODE=true`。"
            )

        # Parse arguments — first arg may be stock code, rest is the question
        query_parts = list(args)
        stock_code: Optional[str] = None

        # Try to detect a stock code in the first argument
        first = query_parts[0].upper().replace("，", ",")
        if _RESEARCH_STOCK_CODE_RE.match(first):
            stock_code = first
            query_parts = query_parts[1:]

        # Build the research query
        if query_parts:
            question = " ".join(query_parts)
        elif stock_code:
            question = f"Comprehensive deep research on stock {stock_code}: fundamentals, technicals, news sentiment, and risk factors"
        else:
            question = " ".join(args)

        # T3: Domain guard check before research with context/history
        from src.services.agent_domain_guard_service import AgentDomainGuardService
        from src.schemas.agent_domain_guard import DomainAction
        from src.storage import get_db

        guard_service = AgentDomainGuardService(config)

        # Get session context for classification
        context = None
        history_messages = None
        try:
            session_id = _resolve_research_session_id(message)
            db = get_db()
            messages = db.get_conversation_messages(session_id, limit=10)
            if messages:
                history_messages = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in messages[-6:]
                ]
                # Extract active stock context from recent messages
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("stock_code"):
                        context = {
                            "stock_code": msg["stock_code"],
                            "stock_name": msg.get("stock_name"),
                        }
                        break
        except Exception as ctx_err:
            logger.debug("Failed to get bot research context for domain guard: %s", ctx_err)

        decision, meta = guard_service.classify(
            question,
            context=context,
            history_messages=history_messages,
        )

        if decision.action != DomainAction.ALLOW:
            # Return domain guard reminder without research header
            return BotResponse.text_response(decision.message or "Domain guard reminder")

        if stock_code:
            question = f"[Stock: {stock_code}] {question}"

        # Run the research agent
        try:
            from src.agent.research import ResearchAgent
            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter

            registry = get_tool_registry()
            llm_adapter = LLMToolAdapter(config)
            budget = getattr(config, "agent_deep_research_budget", 30000)

            agent = ResearchAgent(
                tool_registry=registry,
                llm_adapter=llm_adapter,
                token_budget=budget,
            )

            research_timeout = getattr(config, "agent_deep_research_timeout", 180)
            logger.info("[ResearchCommand] Starting deep research (timeout=%ds): %s", research_timeout, question[:100])
            t0 = time.time()
            result = agent.research(
                question,
                {"stock_code": stock_code, "stock_name": ""} if stock_code else None,
                timeout_seconds=research_timeout,
            )
            duration = result.duration_s or round(time.time() - t0, 1)

            if getattr(result, "timed_out", False):
                logger.warning("[ResearchCommand] Deep research timed out after %ss", duration)
                return BotResponse.text_response(
                    f"⏳ 深度研究超时（{duration}s / {research_timeout}s），请稍后重试或缩小研究范围。"
                )

            if result.success:
                # Build rich response
                header = f"🔬 **Deep Research Report**\n"
                if stock_code:
                    header += f"Stock: {stock_code}\n"
                header += f"Sub-questions: {len(result.sub_questions)} | Sources: {result.findings_count}\n"
                header += f"Time: {duration}s | Tokens: {result.total_tokens:,}\n"
                header += "─" * 40 + "\n\n"

                report = header + result.report

                # Truncate if too long for bot message
                max_len = 4000
                if len(report) > max_len:
                    report = report[:max_len] + "\n\n... (report truncated, full report available via API)"

                return BotResponse.markdown_response(report)
            else:
                return BotResponse.text_response(
                    f"⚠️ Research did not complete successfully.\n"
                    f"Partial results: {result.findings_count} findings collected.\n"
                    f"Time: {duration}s"
                )

        except Exception as exc:
            logger.error("[ResearchCommand] Error: %s", exc, exc_info=True)
            return BotResponse.text_response(f"❌ Research failed: {exc}")
