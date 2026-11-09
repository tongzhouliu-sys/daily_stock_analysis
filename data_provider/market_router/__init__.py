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

使用示例：
    from data_provider.market_router import get_router
    
    router = get_router()
    
    # 获取交易日历
    trading_days = router.get_calendar("2024-01-01", "2024-12-31")
    
    # 获取实时快照
    quotes = router.get_realtime(["600519", "000001"])
    
    # 获取日K线
    df = router.get_daily("600519", start="2024-01-01", end="2024-12-31")
    
    # 获取分钟K线
    df = router.get_minute("600519", freq="5min", count=100)
"""

from .router import MarketDataRouter, get_router
from .calendar import get_calendar, is_trade_day, next_trade_day, CalendarRangeError
from .rate_limiter import RateLimiter
from .observability import Observability

__all__ = [
    "MarketDataRouter",
    "get_router",
    "get_calendar",
    "is_trade_day",
    "next_trade_day",
    "CalendarRangeError",
    "RateLimiter",
    "Observability",
]

__version__ = "1.0.0"
