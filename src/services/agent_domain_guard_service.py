# -*- coding: utf-8 -*-
"""Agent Domain Guard Service - Investment relevance classification.

T1: Shared domain judgment service.

This service determines whether a user question is investment-related
before it enters research, tool calling, or intake questionnaires.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.agent_domain_guard import (
    DomainAction,
    DomainGuardDecision,
    DomainGuardMeta,
    DomainReasonCode,
    POLICY_VERSION,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Message templates (controlled, bilingual)
# =============================================================================

_MESSAGES_ZH = {
    "off_topic": (
        "本系统是投资分析系统，主要提供股票及投资相关分析。"
        "你的这个问题与股票或投资无关，建议到其他通用问答或相关专业平台提问。"
        "你也可以继续询问股票、市场、投资策略或风险管理问题。"
    ),
    "ambiguous": (
        "本系统是投资分析系统。目前还不确定你的问题与股票或投资的关联；"
        "如果是非投资问题，建议到其他通用问答或相关专业平台提问。"
        "若你希望从投资角度分析，请补充关注的对象或分析目的。"
    ),
    "mixed": (
        "本系统是投资分析系统，我会先处理你问题中的投资分析部分。"
        "其余与投资无关的内容，建议到其他通用问答或相关专业平台提问。"
    ),
    "check_unavailable": (
        "本系统是投资分析系统，目前暂时无法完成问题范围判断。"
        "请稍后重试，或补充你关注的投资对象和分析目的；"
        "若是非投资问题，建议到其他平台提问。"
    ),
    "social": (
        "你好！我是投资分析助手，专注于股票、市场、投资策略和风险管理分析。"
        "请问有什么投资相关的问题我可以帮助你？"
    ),
}

_MESSAGES_EN = {
    "off_topic": (
        "This is an investment analysis system, primarily providing stock and investment-related analysis. "
        "Your question does not appear to be related to stocks or investment. "
        "We suggest using other general Q&A or specialized platforms. "
        "You can also continue to ask about stocks, markets, investment strategies, or risk management."
    ),
    "ambiguous": (
        "This is an investment analysis system. The investment relevance of your question is unclear; "
        "if it's a non-investment question, we suggest using other general Q&A or specialized platforms. "
        "If you'd like analysis from an investment perspective, please specify the subject or analysis purpose."
    ),
    "mixed": (
        "This is an investment analysis system. I'll process the investment analysis portion of your question first. "
        "For content unrelated to investment, we suggest using other general Q&A or specialized platforms."
    ),
    "check_unavailable": (
        "This is an investment analysis system. Unable to complete scope classification at this time. "
        "Please try again later, or specify your investment subject and analysis purpose; "
        "if it's a non-investment question, please use another platform."
    ),
    "social": (
        "Hello! I'm an investment analysis assistant, focused on stocks, markets, investment strategies, and risk management. "
        "What investment-related question can I help you with?"
    ),
}


def _get_message(reason_code: DomainReasonCode, lang: str = "zh") -> str:
    """Get the appropriate message template."""
    messages = _MESSAGES_EN if lang == "en" else _MESSAGES_ZH
    return messages.get(reason_code.value, messages.get("ambiguous", ""))


# =============================================================================
# Rule-based classification
# =============================================================================

# Investment-related keywords (Chinese and English)
_INVESTMENT_KEYWORDS_ZH = [
    # 股票与投资分析
    "股票", "分析", "走势", "行情", "K线", "技术面", "基本面", "估值",
    "市盈率", "市净率", "股息", "分红", "财报", "业绩", "营收", "利润",
    "市值", "股价", "涨跌", "涨停", "跌停", "换手率", "成交量", "成交额",
    # 投资知识
    "投资", "理财", "基金", "ETF", "指数", "债券", "期权", "期货",
    "仓位", "持仓", "加仓", "减仓", "止损", "止盈", "风控", "风险管理",
    "资产配置", "分散投资", "回撤", "夏普比率", "波动率", "收益率",
    # 市场相关
    "A股", "港股", "美股", "大盘", "板块", "行业", "概念", "题材",
    "龙头", "蓝筹", "白马", "成长股", "价值股", "题材股",
    # 操作相关
    "买入", "卖出", "建仓", "平仓", "做多", "做空", "抄底", "逃顶",
    "套牢", "解套", "割肉", "追涨", "杀跌",
    # 技术分析
    "均线", "MACD", "KDJ", "RSI", "布林带", "支撑位", "压力位",
    "趋势", "形态", "突破", "回调", "反弹",
    # 基本面分析
    "PE", "PB", "ROE", "EPS", "现金流", "负债率", "毛利率",
    # 行业术语
    "半导体", "新能源", "医药", "消费", "金融", "科技", "地产",
    # 宏观相关（有投资关联）
    "降息", "加息", "通胀", "GDP", "货币政策", "财政政策",
    "美联储", "央行", "利率", "汇率",
]

_INVESTMENT_KEYWORDS_EN = [
    # Stock & Investment Analysis
    "stock", "share", "analyze", "analysis", "trend", "quote", "candlestick",
    "technical", "fundamental", "valuation", "PE", "PB", "dividend", "earnings",
    "revenue", "profit", "market cap", "price", "volume", "turnover",
    # Investment Knowledge
    "invest", "portfolio", "fund", "ETF", "index", "bond", "option", "future",
    "position", "risk management", "asset allocation", "diversification",
    "drawdown", "sharpe", "volatility", "return", "yield",
    # Market Related
    "market", "sector", "industry", "blue chip", "growth stock", "value stock",
    # Trading Actions
    "buy", "sell", "long", "short", "entry", "exit", "stop loss", "take profit",
    # Technical Analysis
    "moving average", "MACD", "RSI", "bollinger", "support", "resistance",
    "breakout", "pullback", "rebound",
    # Fundamental Analysis
    "earnings per share", "cash flow", "debt ratio", "gross margin",
    # Sectors
    "semiconductor", "renewable energy", "pharma", "consumer", "financial", "tech",
    # Macro (investment-related)
    "interest rate", "inflation", "GDP", "monetary policy", "fiscal policy",
    "fed", "central bank", "exchange rate",
]

# System help keywords
_SYSTEM_HELP_KEYWORDS_ZH = [
    "怎么导出", "如何查看", "怎么修改", "如何设置", "怎么配置",
    "导出报告", "查看历史", "修改密码", "登录问题", "注册问题",
    "系统帮助", "使用说明", "操作指南", "功能介绍",
]

_SYSTEM_HELP_KEYWORDS_EN = [
    "how to export", "how to view", "how to change", "how to set", "how to configure",
    "export report", "view history", "change password", "login issue", "registration",
    "help", "usage guide", "user manual", "feature introduction",
]

# Social/greeting keywords
_SOCIAL_KEYWORDS_ZH = [
    "你好", "您好", "谢谢", "感谢", "再见", "拜拜",
    "你能做什么", "你会什么", "有什么功能",
]

_SOCIAL_KEYWORDS_EN = [
    "hello", "hi", "hey", "thanks", "thank you", "goodbye", "bye",
    "what can you do", "what are your features", "help me",
]

# Clearly off-topic keywords
_OFF_TOPIC_KEYWORDS_ZH = [
    "做菜", "烹饪", "食谱", "旅游", "行程", "情书", "写诗",
    "作业", "数学题", "英语翻译", "程序调试", "代码bug",
    "天气", "新闻", "娱乐", "八卦", "明星", "电影", "游戏",
]

_OFF_TOPIC_KEYWORDS_EN = [
    "recipe", "cooking", "travel", "itinerary", "love letter", "poem",
    "homework", "math problem", "translation", "debug code", "code bug",
    "weather forecast", "entertainment", "gossip", "celebrity", "movie", "game",
]

# Compile regex patterns for efficiency
def _compile_patterns(keywords: List[str]) -> re.Pattern:
    """Compile keywords into a single regex pattern."""
    escaped = [re.escape(kw) for kw in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


_PATTERNS_ZH = {
    "investment": _compile_patterns(_INVESTMENT_KEYWORDS_ZH),
    "system_help": _compile_patterns(_SYSTEM_HELP_KEYWORDS_ZH),
    "social": _compile_patterns(_SOCIAL_KEYWORDS_ZH),
    "off_topic": _compile_patterns(_OFF_TOPIC_KEYWORDS_ZH),
}

_PATTERNS_EN = {
    "investment": _compile_patterns(_INVESTMENT_KEYWORDS_EN),
    "system_help": _compile_patterns(_SYSTEM_HELP_KEYWORDS_EN),
    "social": _compile_patterns(_SOCIAL_KEYWORDS_EN),
    "off_topic": _compile_patterns(_OFF_TOPIC_KEYWORDS_EN),
}


# =============================================================================
# Context analysis helpers
# =============================================================================

def _has_stock_context(context: Optional[Dict[str, Any]]) -> bool:
    """Check if there's an active stock context."""
    if not context:
        return False
    return bool(
        context.get("stock_code") or
        context.get("active_stock_context") or
        context.get("stock_name")
    )


def _has_report_context(context: Optional[Dict[str, Any]]) -> bool:
    """Check if there's an active report context."""
    if not context:
        return False
    return bool(
        context.get("active_report_context") or
        context.get("report_record_id")
    )


def _has_recent_investment_history(history_messages: Optional[List[Dict[str, Any]]]) -> bool:
    """Check if recent history contains investment-related discussion."""
    if not history_messages:
        return False
    # Check last 3 messages for investment keywords
    for msg in history_messages[-3:]:
        content = msg.get("content", "")
        if _PATTERNS_ZH["investment"].search(content) or _PATTERNS_EN["investment"].search(content):
            return True
    return False


def _is_followup_question(message: str) -> bool:
    """Check if the message appears to be a follow-up question."""
    followup_patterns = [
        r"^(那|这|它|他|她)?(呢|吗|吧)?[？?]?$",  # 那风险呢？
        r"^(继续|接着|然后|再)",  # 继续...
        r"(第二点|第三点|上面|刚才|上次)",  # 解释第二点
        r"(用|换)(英文|中文|英文)?(总结|说)",  # 用英文总结
        r"^(为什么|怎么|如何)(会|能|可以)",  # 为什么会...
    ]
    for pattern in followup_patterns:
        if re.search(pattern, message):
            return True
    return False


def _detect_language(message: str) -> str:
    """Detect if the message is primarily Chinese or English."""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", message))
    english_chars = len(re.findall(r"[a-zA-Z]", message))
    return "en" if english_chars > chinese_chars else "zh"


# =============================================================================
# Mixed request detection and separation
# =============================================================================

def _detect_mixed_request(message: str) -> Tuple[bool, Optional[str]]:
    """Detect if the message contains both investment and non-investment parts.
    
    Returns:
        Tuple of (is_mixed, investment_sub_request)
    """
    # Split by common separators
    separators = ["，", ",", "；", ";", "然后", "接着", "顺便", "另外", "还有"]
    parts = [message]
    for sep in separators:
        new_parts = []
        for part in parts:
            new_parts.extend(part.split(sep))
        parts = new_parts
    
    parts = [p.strip() for p in parts if p.strip()]
    
    if len(parts) < 2:
        return False, None
    
    investment_parts = []
    non_investment_parts = []
    
    for part in parts:
        is_investment = (
            _PATTERNS_ZH["investment"].search(part) or
            _PATTERNS_EN["investment"].search(part)
        )
        is_off_topic = (
            _PATTERNS_ZH["off_topic"].search(part) or
            _PATTERNS_EN["off_topic"].search(part)
        )
        
        if is_investment and not is_off_topic:
            investment_parts.append(part)
        elif is_off_topic:
            non_investment_parts.append(part)
        else:
            # Ambiguous part - treat as potentially investment-related
            investment_parts.append(part)
    
    if investment_parts and non_investment_parts:
        return True, "，".join(investment_parts)
    
    return False, None


# =============================================================================
# Main classification service
# =============================================================================

class AgentDomainGuardService:
    """Service for classifying investment relevance of user questions."""
    
    def __init__(self, config: Any = None):
        self.config = config
        self._model_timeout_seconds = getattr(config, "agent_domain_guard_model_timeout", 5) if config else 5
    
    def classify(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[DomainGuardDecision, DomainGuardMeta]:
        """Classify the investment relevance of a user message.
        
        Args:
            message: The user's message
            context: Session context (stock, report, etc.)
            history_messages: Recent conversation history
            
        Returns:
            Tuple of (decision, metadata)
        """
        start_time = time.time()
        lang = _detect_language(message)
        
        # Step 1: Rule-based classification
        decision, source = self._rule_based_classify(message, context, history_messages, lang)
        
        # Step 2: If rules are uncertain, try model-based classification
        if decision is None:
            decision, source = self._model_based_classify(message, context, history_messages, lang)
        
        # Step 3: If still uncertain, return check_unavailable
        if decision is None:
            decision = DomainGuardDecision(
                action=DomainAction.CLARIFY,
                reason_code=DomainReasonCode.CHECK_UNAVAILABLE,
                message=_get_message(DomainReasonCode.CHECK_UNAVAILABLE, lang),
            )
            source = "fallback"
        
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        meta = DomainGuardMeta(
            action=decision.action,
            reason_code=decision.reason_code,
            policy_version=POLICY_VERSION,
            elapsed_ms=elapsed_ms,
            source=source,
        )
        
        return decision, meta
    
    def _rule_based_classify(
        self,
        message: str,
        context: Optional[Dict[str, Any]],
        history_messages: Optional[List[Dict[str, Any]]],
        lang: str,
    ) -> Tuple[Optional[DomainGuardDecision], str]:
        """Rule-based classification. Returns (decision, source) or (None, "") if uncertain."""
        
        message_lower = message.lower().strip()
        
        # Check for empty or very short messages
        if len(message_lower) < 2:
            return DomainGuardDecision(
                action=DomainAction.DIRECT_REPLY,
                reason_code=DomainReasonCode.SOCIAL,
                message=_get_message(DomainReasonCode.SOCIAL, lang),
            ), "rule"
        
        # Check for mixed request first
        is_mixed, investment_sub = _detect_mixed_request(message)
        if is_mixed and investment_sub:
            return DomainGuardDecision(
                action=DomainAction.ALLOW,
                reason_code=DomainReasonCode.MIXED,
                message=_get_message(DomainReasonCode.MIXED, lang),
                allowed_request=investment_sub,
            ), "rule"
        
        # Check for system help
        if _PATTERNS_ZH["system_help"].search(message) or _PATTERNS_EN["system_help"].search(message):
            return DomainGuardDecision(
                action=DomainAction.ALLOW,
                reason_code=DomainReasonCode.SYSTEM_HELP,
            ), "rule"
        
        # Check for social/greeting
        if _PATTERNS_ZH["social"].search(message) or _PATTERNS_EN["social"].search(message):
            # Short social message
            if len(message) < 50:
                return DomainGuardDecision(
                    action=DomainAction.DIRECT_REPLY,
                    reason_code=DomainReasonCode.SOCIAL,
                    message=_get_message(DomainReasonCode.SOCIAL, lang),
                ), "rule"
        
        # Check for clearly off-topic
        off_topic_match = _PATTERNS_ZH["off_topic"].search(message) or _PATTERNS_EN["off_topic"].search(message)
        investment_match = _PATTERNS_ZH["investment"].search(message) or _PATTERNS_EN["investment"].search(message)
        
        if off_topic_match and not investment_match:
            return DomainGuardDecision(
                action=DomainAction.REDIRECT,
                reason_code=DomainReasonCode.OFF_TOPIC,
                message=_get_message(DomainReasonCode.OFF_TOPIC, lang),
            ), "rule"
        
        # Check for investment keywords
        if investment_match:
            return DomainGuardDecision(
                action=DomainAction.ALLOW,
                reason_code=DomainReasonCode.INVESTMENT,
            ), "rule"
        
        # Check for follow-up questions with context
        if _is_followup_question(message):
            if _has_stock_context(context) or _has_report_context(context):
                return DomainGuardDecision(
                    action=DomainAction.ALLOW,
                    reason_code=DomainReasonCode.INVESTMENT,
                ), "rule"
            if _has_recent_investment_history(history_messages):
                return DomainGuardDecision(
                    action=DomainAction.ALLOW,
                    reason_code=DomainReasonCode.INVESTMENT,
                ), "rule"
            # Follow-up without context - ambiguous
            return DomainGuardDecision(
                action=DomainAction.CLARIFY,
                reason_code=DomainReasonCode.AMBIGUOUS,
                message=_get_message(DomainReasonCode.AMBIGUOUS, lang),
            ), "rule"
        
        # Check for stock codes in message (e.g., "AAPL", "600519")
        stock_code_pattern = re.compile(r"\b([A-Z]{1,5}|\d{6}|HK\d{5})\b")
        if stock_code_pattern.search(message):
            # Has stock code but no clear investment intent - could be ambiguous
            # Check if there's any action verb
            action_patterns = [
                r"(分析|看看|研究|评估|诊断)",
                r"(analyze|check|research|evaluate|diagnose)",
                r"(怎么样|如何|为什么|怎么)",
                r"(how|what|why|is)",
            ]
            has_action = any(re.search(p, message, re.IGNORECASE) for p in action_patterns)
            if has_action:
                return DomainGuardDecision(
                    action=DomainAction.ALLOW,
                    reason_code=DomainReasonCode.INVESTMENT,
                ), "rule"
            # Stock code without action - ambiguous
            return DomainGuardDecision(
                action=DomainAction.CLARIFY,
                reason_code=DomainReasonCode.AMBIGUOUS,
                message=_get_message(DomainReasonCode.AMBIGUOUS, lang),
            ), "rule"
        
        # Rules cannot determine - return None for model fallback
        return None, ""
    
    def _model_based_classify(
        self,
        message: str,
        context: Optional[Dict[str, Any]],
        history_messages: Optional[List[Dict[str, Any]]],
        lang: str,
    ) -> Tuple[Optional[DomainGuardDecision], str]:
        """Model-based classification fallback. Returns (decision, source) or (None, "") if failed."""
        
        if not self.config:
            return None, ""
        
        try:
            from src.agent.llm_adapter import LLMToolAdapter
            from src.agent.factory import get_tool_registry
            
            adapter = LLMToolAdapter(self.config)
            
            # Build minimal context for classification
            context_hint = ""
            if _has_stock_context(context):
                context_hint = f"\n当前讨论标的: {context.get('stock_code', '')} {context.get('stock_name', '')}"
            if _has_report_context(context):
                context_hint += "\n已加载分析报告"
            
            system_prompt = f"""你是一个投资分析系统的领域分类器。判断用户问题是否与股票/投资相关。

分类规则：
1. investment: 明确与投资相关（股票分析、投资知识、市场研究、有投资关联的宏观事件）
2. off_topic: 明确与投资无关（做菜、旅游、情书、作业、娱乐）
3. ambiguous: 无法确定投资关联性（缺少上下文、含义模糊）
4. social: 简短社交问候（你好、谢谢）
5. system_help: 系统使用帮助（如何导出、怎么查看）

只输出 JSON 格式：{{"action": "allow|redirect|clarify|direct_reply", "reason": "investment|off_topic|ambiguous|social|system_help"}}
"""
            
            user_prompt = f"用户问题: {message}{context_hint}\n请分类："
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            # Call LLM with short timeout
            response = adapter.call_text(
                messages,
                temperature=0.1,
                max_tokens=100,
                timeout=self._model_timeout_seconds,
            )
            
            if response.provider == "error":
                logger.warning("Domain guard model call failed: %s", response.content)
                return None, ""
            
            # Parse response
            content = response.content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            
            import json
            parsed = json.loads(content)
            
            action_str = parsed.get("action", "")
            reason_str = parsed.get("reason", "")
            
            # Validate and map
            action_map = {
                "allow": DomainAction.ALLOW,
                "redirect": DomainAction.REDIRECT,
                "clarify": DomainAction.CLARIFY,
                "direct_reply": DomainAction.DIRECT_REPLY,
            }
            reason_map = {
                "investment": DomainReasonCode.INVESTMENT,
                "off_topic": DomainReasonCode.OFF_TOPIC,
                "ambiguous": DomainReasonCode.AMBIGUOUS,
                "social": DomainReasonCode.SOCIAL,
                "system_help": DomainReasonCode.SYSTEM_HELP,
            }
            
            action = action_map.get(action_str)
            reason = reason_map.get(reason_str)
            
            if action and reason:
                msg = None
                if action == DomainAction.REDIRECT:
                    msg = _get_message(DomainReasonCode.OFF_TOPIC, lang)
                elif action == DomainAction.CLARIFY:
                    msg = _get_message(DomainReasonCode.AMBIGUOUS, lang)
                elif action == DomainAction.DIRECT_REPLY:
                    msg = _get_message(DomainReasonCode.SOCIAL, lang)
                
                return DomainGuardDecision(
                    action=action,
                    reason_code=reason,
                    message=msg,
                ), "model"
            
            return None, ""
            
        except Exception as e:
            logger.warning("Domain guard model classification failed: %s", e)
            return None, ""


__all__ = [
    "AgentDomainGuardService",
    "DomainAction",
    "DomainGuardDecision",
    "DomainGuardMeta",
    "DomainReasonCode",
]
