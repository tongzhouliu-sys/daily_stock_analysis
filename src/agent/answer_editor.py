"""
T4: Stock Answer Editor Service

Post-processes analysis responses into a structured, conclusion-first format
while preserving all authoritative facts, actions, and risk notes.
"""
import asyncio
import logging
import re
import time
from typing import Optional

from src.schemas.agent_intake import AnalysisBrief, AnswerMeta

logger = logging.getLogger(__name__)

EDITOR_VERSION = "1.0.0"
EDITOR_TIMEOUT_SECONDS = 8.0


class StockAnswerEditorService:
    """
    Applies the stock-answer-editor skill to format analysis responses.
    
    Flow:
    1. Validate inputs (original response + analysis brief)
    2. Call LLM with structured prompt
    3. Verify facts preserved (ticker, price, dates, actions, risks)
    4. Return formatted response with metadata
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def format_response(
        self,
        original_response: str,
        analysis_brief: Optional[AnalysisBrief],
        source_refs: Optional[list[str]] = None,
    ) -> tuple[str, AnswerMeta]:
        """
        Format the original response into structured output.
        
        Returns:
            Tuple of (formatted_response, answer_meta)
        """
        start_time = time.time()
        
        # If no brief or no LLM client, return normalized original
        if not analysis_brief or not self.llm_client:
            return self._normalize_original(original_response), self._build_meta(
                "fallback", "no_brief_or_client", start_time, source_refs
            )

        try:
            # Build prompt and call LLM with timeout
            prompt = self._build_editor_prompt(original_response, analysis_brief)
            
            formatted = await asyncio.wait_for(
                self._call_llm(prompt),
                timeout=EDITOR_TIMEOUT_SECONDS,
            )

            # Verify facts preserved
            if not self._verify_facts(original_response, formatted, analysis_brief):
                logger.warning("Answer editor fact verification failed, using normalized original")
                return self._normalize_original(original_response), self._build_meta(
                    "normalized", "fact_verification_failed", start_time, source_refs
                )

            return formatted, self._build_meta(
                "applied", "success", start_time, source_refs, analysis_brief
            )

        except asyncio.TimeoutError:
            logger.warning("Answer editor timed out after %ss", EDITOR_TIMEOUT_SECONDS)
            return self._normalize_original(original_response), self._build_meta(
                "fallback", "timeout", start_time, source_refs
            )
        except Exception as e:
            logger.error("Answer editor failed: %s", e)
            return self._normalize_original(original_response), self._build_meta(
                "fallback", str(e), start_time, source_refs
            )

    def _build_editor_prompt(
        self, original_response: str, brief: AnalysisBrief
    ) -> str:
        """Build the LLM prompt for answer editing."""
        brief_context = []
        if brief.object_scope:
            brief_context.append(f"分析对象: {brief.object_scope}")
        if brief.goal:
            brief_context.append(f"分析目标: {brief.goal}")
        if brief.horizon:
            brief_context.append(f"时间框架: {brief.horizon}")
        if brief.position:
            brief_context.append(f"持仓状态: {brief.position}")
        if brief.risk_focus:
            brief_context.append(f"风险关注: {brief.risk_focus}")
        if brief.focus_areas:
            brief_context.append(f"研究重点: {', '.join(brief.focus_areas)}")
        if brief.reading_style:
            brief_context.append(f"阅读偏好: {brief.reading_style}")

        brief_text = "\n".join(brief_context) if brief_context else "无特定简报"

        return f"""你是一名专业的股票分析回答精编师。请将以下分析回答重新组织为结论先行、证据支撑、风险可见的结构化格式。

## 用户确认的分析简报
{brief_text}

## 原始分析回答
{original_response}

## 输出要求

请按以下结构重新组织回答：

### 结论摘要
用 1-3 句话概括核心结论，明确标的、方向、时间框架和关键数据点。

### 核心论据
按重要性排序列出 3-5 个核心论据，每个包含：
- 论点标题
- 支撑数据/证据
- 逻辑链条

### 风险提示
列出关键风险因素，每条包含：
- 风险描述
- 触发条件
- 影响程度（高/中/低）

### 后续建议
最多 3 个建议的后续问题。

## 严格规则
1. 不得编造原始回答中不存在的数据
2. 不得改变原始结论的方向性
3. 不得删除或弱化风险提示
4. 保留所有股票代码、价格、日期、技术指标数值
5. 保留最终操作建议（买入/持有/卖出）
6. 如果原始回答缺少某些结构部分，跳过该部分而非编造

请直接输出结构化后的 Markdown 内容，不要添加额外解释。"""

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM to format the response."""
        if hasattr(self.llm_client, 'chat'):
            result = await self.llm_client.chat(
                messages=[
                    {"role": "system", "content": "你是专业的股票分析回答精编师，擅长将分析报告重组为结论先行、证据支撑的结构化格式。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            return result.get("content", "") if isinstance(result, dict) else str(result)
        elif hasattr(self.llm_client, 'generate'):
            return await self.llm_client.generate(prompt)
        else:
            raise ValueError("LLM client has no supported call method")

    def _verify_facts(
        self, original: str, formatted: str, brief: AnalysisBrief
    ) -> bool:
        """
        Verify that key facts from the original are preserved in the formatted output.
        Returns True if verification passes, False otherwise.
        """
        # Extract ticker symbols from original
        ticker_pattern = r'\b([A-Z]{1,5}|\d{6}|HK\d{4,5})\b'
        original_tickers = set(re.findall(ticker_pattern, original))
        formatted_tickers = set(re.findall(ticker_pattern, formatted))
        
        # All tickers from original must appear in formatted
        if original_tickers and not original_tickers.issubset(formatted_tickers):
            missing = original_tickers - formatted_tickers
            logger.warning("Fact check failed: missing tickers %s", missing)
            return False

        # Extract price levels (numbers with decimal points)
        price_pattern = r'\b\d+\.\d{2}\b'
        original_prices = set(re.findall(price_pattern, original))
        formatted_prices = set(re.findall(price_pattern, formatted))
        
        # At least 80% of prices should be preserved
        if original_prices:
            preserved_ratio = len(original_prices & formatted_prices) / len(original_prices)
            if preserved_ratio < 0.8:
                logger.warning("Fact check failed: only %.0f%% prices preserved", preserved_ratio * 100)
                return False

        # Check action words preserved
        action_words = ['买入', '卖出', '持有', '加仓', '减仓', 'buy', 'sell', 'hold']
        original_actions = {w for w in action_words if w in original.lower()}
        formatted_actions = {w for w in action_words if w in formatted.lower()}
        
        if original_actions and not original_actions.issubset(formatted_actions):
            missing_actions = original_actions - formatted_actions
            logger.warning("Fact check failed: missing actions %s", missing_actions)
            return False

        # Check risk keywords preserved
        risk_keywords = ['风险', '注意', '警惕', 'risk', 'warning', 'caution']
        original_has_risk = any(kw in original.lower() for kw in risk_keywords)
        formatted_has_risk = any(kw in formatted.lower() for kw in risk_keywords)
        
        if original_has_risk and not formatted_has_risk:
            logger.warning("Fact check failed: risk warnings removed")
            return False

        return True

    def _normalize_original(self, original: str) -> str:
        """
        Normalize the original response without LLM transformation.
        Adds minimal structure while preserving all content.
        """
        # Strip excessive whitespace
        lines = [line.rstrip() for line in original.split('\n')]
        # Remove consecutive blank lines (keep max 2)
        result = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= 2:
                    result.append(line)
            else:
                blank_count = 0
                result.append(line)
        return '\n'.join(result).strip()

    def _build_meta(
        self,
        status: str,
        reason: str,
        start_time: float,
        source_refs: Optional[list[str]] = None,
        brief: Optional[AnalysisBrief] = None,
    ) -> AnswerMeta:
        """Build AnswerMeta for the response."""
        elapsed = time.time() - start_time
        return AnswerMeta(
            schema_version=1,
            editor_version=EDITOR_VERSION,
            editor_status=status,
            processing_time_ms=int(elapsed * 1000),
            fallback_reason=reason if status != "applied" else None,
            brief_snapshot=brief,
            source_refs=source_refs or [],
        )
