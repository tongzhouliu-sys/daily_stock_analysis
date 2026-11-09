# -*- coding: utf-8 -*-
"""
行情数据源多源降级统一路由层

第一性原则：
- 任何单一数据源的失效，不得导致行情获取功能整体不可用
- 所有通道必须可替换、可验证、有明确降级路径

核心功能：
- 交易日历：运行时零网络依赖，完全离线可用
- 实时快照：当前价格、OHLC、成交量
- 日K线：日线 OHLCV，支持指定日期区间或最近 N 条
- 分钟K线：至少覆盖 1/5/15/30/60 分钟
- 多源自动降级：按优先级顺序逐源尝试，单源失败不阻断整体
- 北交所边界处理：明确哪些源支持北交所、哪些不支持
- 频率合规：对每个源施加硬性请求间隔/配额
- 可观测性：记录每次请求实际命中的源、延迟、失败原因
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from .calendar import get_calendar
from .rate_limiter import RateLimiter
from .observability import Observability
from .chains import RealtimeChain, DailyChain, MinuteChain

logger = logging.getLogger(__name__)


class MarketDataRouter:
    """
    行情数据源统一路由层
    
    对外暴露统一的接口，内部实现多源自动降级、频率控制、可观测性等功能
    """
    
    def __init__(self, log_dir: str = "./logs"):
        """
        初始化路由层
        
        Args:
            log_dir: 日志目录
        """
        # 初始化频率控制器和可观测性模块
        self.rate_limiter = RateLimiter()
        self.observability = Observability(log_dir=log_dir)
        
        # 初始化三条降级链
        self.realtime_chain = RealtimeChain(self.rate_limiter, self.observability)
        self.daily_chain = DailyChain(self.rate_limiter, self.observability)
        self.minute_chain = MinuteChain(self.rate_limiter, self.observability)
        
        logger.info("MarketDataRouter 初始化完成")
    
    def get_calendar(self, start: str, end: str) -> List[str]:
        """
        返回区间内交易日列表
        
        离线实现，零网络依赖
        
        Args:
            start: 开始日期，格式 YYYY-MM-DD
            end: 结束日期，格式 YYYY-MM-DD
            
        Returns:
            交易日列表，格式 ['YYYY-MM-DD', ...]
            
        Raises:
            CalendarRangeError: 超出覆盖范围
        """
        return get_calendar(start, end)
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量获取实时快照
        
        按降级链逐源尝试：
        1. S1 腾讯实时快照
        2. S3 新浪实时快照
        3. S4 ashareapi quote
        4. S5 zzshare rt_k
        5. S6 ApocData quote
        6. S8 iTick（仅在已注册且 Token 已配置时尝试）
        
        Args:
            codes: 股票代码列表
            
        Returns:
            {code: {open, high, low, close, volume, ts} 或 None}
            单只标的全部源失败时该 code 对应 None，不抛异常
        """
        return self.realtime_chain.get_realtime(codes)
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """
        获取单只标的日线
        
        按降级链逐源尝试：
        1. S2 腾讯K线（北交所跳过）
        2. S4 ashareapi kline
        3. S5 zzshare daily
        4. S7 Tushare daily（仅在已注册且 Token 已配置时尝试）
        5. S6 ApocData（若其接口覆盖日线）
        6. Baostock（仅作为历史日线兜底）
        
        Args:
            code: 股票代码
            start: 开始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD
            count: 最近N条（与 start/end 二选一）
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount(可选)
            全部失败返回 None
        """
        return self.daily_chain.get_daily(code, start, end, count)
    
    def get_minute(
        self,
        code: str,
        freq: str,
        count: int,
    ) -> Optional[pd.DataFrame]:
        """
        获取分钟K线
        
        按降级链逐源尝试：
        1. S5 zzshare stk_mins
        2. S2 腾讯分钟K线（北交所跳过）
        3. S4 ashareapi kline（付费端点，仅在已配置 Key 时尝试）
        4. S8 iTick 分钟线（仅在已注册且 Token 已配置时尝试）
        
        Args:
            code: 股票代码
            freq: 频率，'1min', '5min', '15min', '30min', '60min'
            count: 最近N条
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            全部失败返回 None
        """
        return self.minute_chain.get_minute(code, freq, count)
    
    def get_last_call_metadata(self) -> Optional[Dict[str, Any]]:
        """
        获取最近一次调用的元信息
        
        Returns:
            {
                "code": "600519",
                "data_type": "realtime|daily|minute",
                "provider_hit": "S1_tencent|S2_tencent_kline|...|ALL_FAILED",
                "attempts": [
                    {"provider": "S1_tencent", "success": False, "latency_ms": 120, "reason": "timeout"},
                    {"provider": "S3_sina", "success": True, "latency_ms": 340, "reason": None},
                ],
                "success": True
            }
        """
        return self.observability.get_last_call_metadata()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计数据
        
        Returns:
            {
                "total_calls": 100,
                "success_calls": 95,
                "failed_calls": 5,
                "success_rate": 0.95,
                "provider_hits": {"S1_tencent": 50, "S2_tencent_kline": 30, ...},
                "data_type_stats": {"realtime": {"total": 40, "success": 38}, ...}
            }
        """
        return self.observability.get_stats()
    
    def reset_stats(self) -> None:
        """重置统计数据"""
        self.observability.reset_stats()
        self.rate_limiter.reset()


# 全局单例
_router_instance = None


def get_router(log_dir: str = "./logs") -> MarketDataRouter:
    """
    获取 MarketDataRouter 单例
    
    Args:
        log_dir: 日志目录
        
    Returns:
        MarketDataRouter 实例
    """
    global _router_instance
    if _router_instance is None:
        _router_instance = MarketDataRouter(log_dir=log_dir)
    return _router_instance
