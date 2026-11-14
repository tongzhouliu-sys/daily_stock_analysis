# -*- coding: utf-8 -*-
"""
分钟K线降级链

降级顺序：
1. S5 zzshare stk_mins
2. S2 腾讯分钟K线 (kline/mkline) - 北交所跳过
3. S4 ashareapi kline(period='m1'...'m60') (付费端点，仅在已配置 Key 时尝试)
4. S8 iTick 分钟线 (仅在已注册且 Token 已配置时尝试)

北交所边界：
- 跳过 S2，从 S5 开始尝试

验收事实：
- 分钟线是免费源覆盖最薄弱的部分
- 不要求分钟线链路在所有场景下都有免注册兜底
"""

import logging
from typing import Optional

import pandas as pd

from ..rate_limiter import RateLimiter
from ..observability import Observability
from ..providers import (
    ZzshareProvider,
    TencentKlineProvider,
    AshareapiProvider,
    ItickProvider,
    is_bse_code,
)

logger = logging.getLogger(__name__)


class MinuteChain:
    """分钟K线降级链"""
    
    def __init__(self, rate_limiter: RateLimiter, observability: Observability):
        self.rate_limiter = rate_limiter
        self.observability = observability
        
        # 初始化提供者（按降级顺序）
        self.providers = [
            ZzshareProvider(),        # S5
            TencentKlineProvider(),   # S2
            AshareapiProvider(),      # S4
            ItickProvider(),          # S8（可选）
        ]
    
    def get_minute(
        self,
        code: str,
        freq: str,
        count: int,
    ) -> Optional[pd.DataFrame]:
        """
        获取分钟K线
        
        Args:
            code: 股票代码
            freq: 频率，'1min', '5min', '15min', '30min', '60min'
            count: 最近N条
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume
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
                data = provider.get_minute(code, freq, count)
                
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
                    
                    logger.info(f"分钟K线成功: {provider.provider_id}")
                    
                    # 记录可观测性数据
                    latency_ms = (time.time() - start_time) * 1000
                    self.observability.log_call(
                        code=code,
                        data_type="minute",
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
                    logger.warning(f"分钟K线失败: {provider.provider_id}，尝试下一个")
            
            except Exception as e:
                # 异常，记录并重试下一个
                self.rate_limiter.record_failure(provider.provider_id, str(e))
                attempts.append({
                    "provider": provider.provider_id,
                    "success": False,
                    "latency_ms": (time.time() - start_time) * 1000,
                    "reason": str(e),
                })
                logger.error(f"分钟K线异常: {provider.provider_id} - {e}")
        
        # 全部失败
        latency_ms = (time.time() - start_time) * 1000
        self.observability.log_call(
            code=code,
            data_type="minute",
            provider_hit=provider_hit,
            success=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )
        
        return None
