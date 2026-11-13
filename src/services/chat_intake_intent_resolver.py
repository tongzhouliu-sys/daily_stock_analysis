"""
意图推断服务 - 从用户问题提取分析背景维度

T3: 意图推断与会话背景继承

从用户原始问题中提取 6 个维度的分析背景：
1. goal (目标): 分析目的
2. horizon (周期): 时间范围
3. position (持仓): 当前持仓状态
4. risk_preference (风险偏好): 风险承受能力
5. focus (研究重点): 关注的分析维度
6. presentation (呈现方式): 期望的回答风格

支持会话继承：从上一轮 brief_snapshot 继承未明确改变的维度。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.agent_intake import (
    AnalysisBrief,
    BriefSource,
    ObjectIdentity,
)


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class DimensionInference:
    """单个维度的推断结果"""
    value: str
    source: BriefSource  # explicit_message, system_inferred, session_inherited, user_default
    confidence: float = 1.0  # 0.0-1.0
    evidence: Optional[str] = None  # 原文片段


@dataclass
class IntentInferenceResult:
    """意图推断完整结果"""
    goal: Optional[DimensionInference] = None
    horizon: Optional[DimensionInference] = None
    position: Optional[DimensionInference] = None
    risk_preference: Optional[DimensionInference] = None
    focus: Optional[DimensionInference] = None
    presentation: Optional[DimensionInference] = None
    
    assumptions: List[str] = field(default_factory=list)
    unknown_fields: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    
    def to_brief_sources(self) -> Dict[str, BriefSource]:
        """转换为 BriefSource 字典"""
        sources = {}
        for dim_name in ['goal', 'horizon', 'position', 'risk_preference', 'focus', 'presentation']:
            inference = getattr(self, dim_name)
            if inference:
                sources[dim_name] = inference.source
        return sources
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典"""
        result = {}
        for dim_name in ['goal', 'horizon', 'position', 'risk_preference', 'focus', 'presentation']:
            inference = getattr(self, dim_name)
            if inference:
                result[dim_name] = {
                    'value': inference.value,
                    'source': inference.source.value,
                    'confidence': inference.confidence,
                    'evidence': inference.evidence,
                }
        result['assumptions'] = self.assumptions
        result['unknown_fields'] = self.unknown_fields
        result['conflicts'] = self.conflicts
        return result


# ============================================================================
# 规则定义
# ============================================================================

# 目标维度规则
GOAL_RULES = [
    # (正则模式, 推断值, 来源类型)
    (r'(能不能买|值不值得买|可以买吗|适合买入)', 'evaluate_buy', 'explicit_message'),
    (r'(值不值得关注|要不要关注|是否值得关注)', 'evaluate_watch', 'explicit_message'),
    (r'(已持有|持有中|持仓中)', 'evaluate_hold', 'explicit_message'),
    (r'(被套|套牢|亏损中)', 'evaluate_loss', 'explicit_message'),
    (r'(为什么涨|为什么跌|涨跌原因)', 'explain_movement', 'explicit_message'),
    (r'(怎么分析|如何分析|分析方法)', 'learn_method', 'explicit_message'),
    (r'(分析|评估|看看|研究)', 'general_analysis', 'system_inferred'),
]

# 周期维度规则
HORIZON_RULES = [
    (r'(短线|几天内|短期)', 'short_term', 'explicit_message'),
    (r'(1-4周|几周|一个月左右)', 'medium_term', 'explicit_message'),
    (r'(半年|6个月|中长期)', 'long_term', 'explicit_message'),
    (r'(长期|一年以上|几年)', 'very_long_term', 'explicit_message'),
    (r'(今年|年内)', 'yearly', 'explicit_message'),
    (r'(日内|当天|今天)', 'intraday', 'explicit_message'),
]

# 持仓维度规则
POSITION_RULES = [
    (r'(空仓|没买|未持有|没有持仓)', 'not_holding', 'explicit_message'),
    (r'(持有|已买|持仓|有仓位)', 'holding', 'explicit_message'),
    (r'(盈利|赚钱|有收益)', 'holding_profit', 'explicit_message'),
    (r'(被套|套牢|亏损|亏钱)', 'holding_loss', 'explicit_message'),
    (r'(加仓|增持|补仓)', 'adding_position', 'explicit_message'),
    (r'(减仓|减持|卖出部分)', 'reducing_position', 'explicit_message'),
]

# 风险偏好规则
RISK_RULES = [
    (r'(稳健|保守|低风险|怕回撤)', 'conservative', 'explicit_message'),
    (r'(能接受波动|可以承受风险|中等风险)', 'moderate', 'explicit_message'),
    (r'(激进|高风险|追求高收益)', 'aggressive', 'explicit_message'),
]

# 研究重点规则
FOCUS_RULES = [
    (r'(估值|PE|PB|市盈率|市净率)', 'valuation', 'explicit_message'),
    (r'(基本面|财务|业绩|营收|利润)', 'fundamental', 'explicit_message'),
    (r'(消息|新闻|事件|公告)', 'news_event', 'explicit_message'),
    (r'(技术面|K线|均线|MACD|RSI)', 'technical', 'explicit_message'),
    (r'(风险|问题|隐患|担忧)', 'risk', 'explicit_message'),
    (r'(对比|比较|vs|和.*比)', 'comparison', 'explicit_message'),
]

# 呈现方式规则
PRESENTATION_RULES = [
    (r'(简单说|简要|概括|总结)', 'concise', 'explicit_message'),
    (r'(详细|深入|全面|完整)', 'detailed', 'explicit_message'),
    (r'(先给结论|结论先行)', 'conclusion_first', 'explicit_message'),
    (r'(通俗|易懂|简单解释)', 'plain_language', 'explicit_message'),
]


# ============================================================================
# 主服务类
# ============================================================================

class ChatIntakeIntentResolver:
    """意图推断服务"""
    
    def __init__(self):
        self.goal_rules = GOAL_RULES
        self.horizon_rules = HORIZON_RULES
        self.position_rules = POSITION_RULES
        self.risk_rules = RISK_RULES
        self.focus_rules = FOCUS_RULES
        self.presentation_rules = PRESENTATION_RULES
    
    def infer_intent(
        self,
        message: str,
        brief_snapshot: Optional[Dict[str, Any]] = None,
        active_stock: Optional[ObjectIdentity] = None,
    ) -> IntentInferenceResult:
        """
        从用户问题推断意图
        
        Args:
            message: 用户原始问题
            brief_snapshot: 上一轮 brief 快照（用于会话继承）
            active_stock: 当前会话标的（用于判断是否切换标的）
        
        Returns:
            IntentInferenceResult: 推断结果
        """
        result = IntentInferenceResult()
        
        # 1. 从当前消息提取各维度
        result.goal = self._infer_dimension(message, self.goal_rules)
        result.horizon = self._infer_dimension(message, self.horizon_rules)
        result.position = self._infer_dimension(message, self.position_rules)
        result.risk_preference = self._infer_dimension(message, self.risk_rules)
        result.focus = self._infer_dimension(message, self.focus_rules)
        result.presentation = self._infer_dimension(message, self.presentation_rules)
        
        # 2. 会话继承：从 brief_snapshot 继承未明确改变的维度
        if brief_snapshot:
            self._apply_session_inheritance(result, brief_snapshot, active_stock)
        
        # 3. 检测冲突
        self._detect_conflicts(result, message)
        
        # 4. 生成假设
        self._generate_assumptions(result)
        
        # 5. 标记未知字段
        self._mark_unknown_fields(result)
        
        return result
    
    def _infer_dimension(
        self,
        message: str,
        rules: List[Tuple[str, str, str]],
    ) -> Optional[DimensionInference]:
        """从消息中推断单个维度"""
        message_lower = message.lower()
        
        for pattern, value, source_type in rules:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                evidence = match.group(0)
                source = BriefSource(source_type)
                
                # 计算置信度
                confidence = 1.0
                if source == BriefSource.SYSTEM_INFERRED:
                    confidence = 0.7  # 系统推断的置信度较低
                
                return DimensionInference(
                    value=value,
                    source=source,
                    confidence=confidence,
                    evidence=evidence,
                )
        
        return None
    
    def _apply_session_inheritance(
        self,
        result: IntentInferenceResult,
        brief_snapshot: Dict[str, Any],
        active_stock: Optional[ObjectIdentity],
    ) -> None:
        """
        应用会话继承逻辑
        
        规则：
        1. 如果当前消息没有明确改变某个维度，从 brief_snapshot 继承
        2. 如果切换了标的，清理持仓相关的继承
        """
        # 检查是否切换了标的
        stock_changed = self._check_stock_changed(brief_snapshot, active_stock)
        
        # 遍历各维度
        dimensions = ['goal', 'horizon', 'position', 'risk_preference', 'focus', 'presentation']
        
        for dim_name in dimensions:
            current_inference = getattr(result, dim_name)
            
            # 如果当前消息没有明确推断该维度
            if not current_inference or current_inference.source == BriefSource.SYSTEM_INFERRED:
                # 从 brief_snapshot 继承
                snapshot_value = brief_snapshot.get(dim_name)
                snapshot_source = brief_snapshot.get('sources', {}).get(dim_name)
                
                if snapshot_value and snapshot_source:
                    # 如果切换了标的，不继承 position
                    if stock_changed and dim_name == 'position':
                        continue
                    
                    # 继承该维度
                    setattr(result, dim_name, DimensionInference(
                        value=snapshot_value,
                        source=BriefSource.SESSION_INHERITED,
                        confidence=0.9,
                        evidence=None,
                    ))
    
    def _check_stock_changed(
        self,
        brief_snapshot: Dict[str, Any],
        active_stock: Optional[ObjectIdentity],
    ) -> bool:
        """检查是否切换了标的"""
        if not active_stock:
            return False
        
        snapshot_object = brief_snapshot.get('object')
        if not snapshot_object:
            return False
        
        # 比较标的代码
        snapshot_code = snapshot_object.get('stock_code')
        current_code = active_stock.stock_code
        
        return snapshot_code != current_code
    
    def _detect_conflicts(
        self,
        result: IntentInferenceResult,
        message: str,
    ) -> None:
        """检测维度间的冲突"""
        message_lower = message.lower()
        
        # 冲突 1: 长期 vs 只看明天
        if result.horizon:
            if result.horizon.value == 'very_long_term' and '明天' in message_lower:
                result.conflicts.append('horizon_conflict: 长期 vs 明天')
        
        # 冲突 2: 空仓 vs 被套
        if result.position:
            if result.position.value == 'not_holding' and '被套' in message_lower:
                result.conflicts.append('position_conflict: 空仓 vs 被套')
        
        # 冲突 3: 保守 vs 激进
        # 这个需要在规则匹配时检测，这里简化处理
    
    def _generate_assumptions(self, result: IntentInferenceResult) -> None:
        """生成假设说明"""
        dimensions = ['goal', 'horizon', 'position', 'risk_preference', 'focus', 'presentation']
        
        for dim_name in dimensions:
            inference = getattr(result, dim_name)
            if inference:
                # 只对 system_inferred 和 user_default 生成假设
                if inference.source in [BriefSource.SYSTEM_INFERRED, BriefSource.USER_DEFAULT]:
                    assumption = f"{dim_name} 为系统推断: {inference.value}"
                    result.assumptions.append(assumption)
    
    def _mark_unknown_fields(self, result: IntentInferenceResult) -> None:
        """标记未知字段"""
        dimensions = ['goal', 'horizon', 'position', 'risk_preference', 'focus', 'presentation']
        
        for dim_name in dimensions:
            inference = getattr(result, dim_name)
            if not inference:
                result.unknown_fields.append(dim_name)


# ============================================================================
# 便捷函数
# ============================================================================

def infer_chat_intent(
    message: str,
    brief_snapshot: Optional[Dict[str, Any]] = None,
    active_stock: Optional[ObjectIdentity] = None,
) -> IntentInferenceResult:
    """
    推断用户意图的便捷函数
    
    Args:
        message: 用户原始问题
        brief_snapshot: 上一轮 brief 快照
        active_stock: 当前会话标的
    
    Returns:
        IntentInferenceResult: 推断结果
    """
    resolver = ChatIntakeIntentResolver()
    return resolver.infer_intent(message, brief_snapshot, active_stock)


__all__ = [
    'ChatIntakeIntentResolver',
    'DimensionInference',
    'IntentInferenceResult',
    'infer_chat_intent',
]
