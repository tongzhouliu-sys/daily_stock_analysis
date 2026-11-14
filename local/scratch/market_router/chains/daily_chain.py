# -*- coding: utf-8 -*-
"""
日K线降级链

降级顺序：
1. S2 腾讯K线 (web.ifzq.gtimg.cn) - 北交所跳过
2. S4 ashareapi kline
3. S5 zzshare daily
4. S7 Tushare daily (仅在已注册且 Token 已配置、且不复权口径可接受时尝试)
5. S6 ApocData (若其接口覆盖日线)
6. Baostock (仅作为历史日线兜底)

北交所边界：
- S2 腾讯K线不支持北交所，遇 bj/920 开头代码直接跳过
- 从 S4 或 S5 开始尝试
"""

import logging
from typing import Optional

import pandas as pd

from ..rate_limiter import RateLimiter
from ..observability import Observability
from ..providers import (
    TencentKlineProvider,
    AshareapiProvider,
    ZzshareProvider,
    TushareProvider,
    ApocdataProvider,
    BaostockProvider,
    is_bse_code,
)

logger = logging.getLogger(__name__)


class DailyChain:
    """日K线降级链"""
    
    def __init__(self, rate_limiter: RateLimiter, observability: Observability):
        self.rate_limiter = rate_limiter
        self.observability = observability
        
        # 初始化提供者（按降级顺序）
        self.providers = [
            TencentKlineProvider(),   # S2
            AshareapiProvider(),      # S4
            ZzshareProvider(),        # S5
            TushareProvider(),        # S7（可选）
            ApocdataProvider(),       # S6
            BaostockProvider(),       # Baostock
        ]
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """
        获取日K线
        
        Args:
            code: 股票代码
            start: 开始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD
            count: 最近N条（与 start/end 二选一）
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount(可选)
            失败返回 None
        """
        import time
        
        attempts = []
        provider_hit = "ALL_FAILED"
        start_time = time.time()
        is_bse = is_bse_code(code)
        
        for provider in self.providers:
            # 北交所跳过 S2
            if is_bse and provider.provider_id == "S2_tencent_kline":
                logger.info(f"北交所代码跳过 S2: {code}")
                continue
            
            # 检查是否可用
            if not provider.is_available():
                continue
            
            # 检查频率限制
            if not self.rate_limiter.acquire(provider.provider_id):
                logger.warning(f"频率限制，跳过 {provider.provider_id}")
                continue
            
            try:
                # 尝试获取数据
                data = provider.get_daily(code, start, end, count)
                
                # 检查是否成功获取到数据
                if data is not None and not data.empty:
                    # 成功
                    self.rate_limiter.record_success(provider.provider_id)
                    provider_hit = provider.provider_id
                    
                    attempts.append({
                        "provider": provider.provider_id,
                        "success": True,
                        "latency_ms": (time.time() - start_time) * 1000,
                        "reason": None,
                    })
                    
                    logger.info(f"日K线成功: {provider.provider_id}")
                    
                    # 记录可观测性数据
                    latency_ms = (time.time() - start_time) * 1000
                    self.observability.log_call(
                        code=code,
                        data_type="daily",
                        provider_hit=provider_hit,
                        success=True,
                        latency_ms=latency_ms,
                        attempts=attempts,
                    )
                    
                    return data
                else:
                    # 失败，记录并重试下一个
                    self.rate_limiter.record_failure(provider.provider_id, "no_data")
                    attempts.append({
                        "provider": provider.provider_id,
                        "success": False,
                        "latency_ms": (time.time() - start_time) * 1000,
                        "reason": "no_data",
                    })
                    logger.warning(f"日K线失败: {provider.provider_id}，尝试下一个")
            
            except Exception as e:
                # 异常，记录并重试下一个
                self.rate_limiter.record_failure(provider.provider_id, str(e))
                attempts.append({
                    "provider": provider.provider_id,
                    "success": False,
                    "latency_ms": (time.time() - start_time) * 1000,
                    "reason": str(e),
                })
                logger.error(f"日K线异常: {provider.provider_id} - {e}")
        
        # 全部失败
        latency_ms = (time.time() - start_time) * 1000
        self.observability.log_call(
            code=code,
            data_type="daily",
            provider_hit=provider_hit,
            success=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )
        
        return None
