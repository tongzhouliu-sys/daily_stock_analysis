# -*- coding: utf-8 -*-
"""
实时快照降级链

降级顺序：
1. S1 腾讯实时快照 (qt.gtimg.cn)
2. S3 新浪实时快照 (hq.sinajs.cn)
3. S4 ashareapi quote
4. S5 zzshare rt_k
5. S6 ApocData quote
6. S8 iTick (仅在已注册且 Token 已配置时尝试)

频率硬约束：
- S1 请求间隔 ≥ 100ms
- S3 单 IP 日请求硬上限 1050 次，并发 ≤ 5 QPS
- S8 ≤ 5 次/分钟
- 禁止在实时链路上使用 S7（Tushare）
"""

import logging
from typing import Any, Dict, List, Optional

from ..rate_limiter import RateLimiter
from ..observability import Observability
from ..providers import (
    TencentRealtimeProvider,
    SinaRealtimeProvider,
    AshareapiProvider,
    ZzshareProvider,
    ApocdataProvider,
    ItickProvider,
)

logger = logging.getLogger(__name__)


class RealtimeChain:
    """实时快照降级链"""
    
    def __init__(self, rate_limiter: RateLimiter, observability: Observability):
        self.rate_limiter = rate_limiter
        self.observability = observability
        
        # 初始化提供者（按降级顺序）
        self.providers = [
            TencentRealtimeProvider(),  # S1
            SinaRealtimeProvider(),     # S3
            AshareapiProvider(),        # S4
            ZzshareProvider(),          # S5
            ApocdataProvider(),         # S6
            ItickProvider(),            # S8（可选）
        ]
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量获取实时快照
        
        Args:
            codes: 股票代码列表
            
        Returns:
            {code: {open, high, low, close, volume, ts} 或 None}
        """
        import time
        
        result = {code: None for code in codes}
        attempts = []
        provider_hit = "ALL_FAILED"
        start_time = time.time()
        
        for provider in self.providers:
            # 检查是否可用
            if not provider.is_available():
                continue
            
            # 检查频率限制
            if not self.rate_limiter.acquire(provider.provider_id):
                logger.warning(f"频率限制，跳过 {provider.provider_id}")
                continue
            
            try:
                # 尝试获取数据
                data = provider.get_realtime(codes)
                
                # 检查是否成功获取到至少一个有效数据
                has_data = any(data.get(code) is not None for code in codes)
                
                if has_data:
                    # 成功
                    self.rate_limiter.record_success(provider.provider_id)
                    provider_hit = provider.provider_id
                    
                    # 合并结果
                    for code in codes:
                        if data.get(code) is not None:
                            result[code] = data[code]
                    
                    attempts.append({
                        "provider": provider.provider_id,
                        "success": True,
                        "latency_ms": (time.time() - start_time) * 1000,
                        "reason": None,
                    })
                    
                    logger.info(f"实时快照成功: {provider.provider_id}")
                    break
                else:
                    # 失败，记录并重试下一个
                    self.rate_limiter.record_failure(provider.provider_id, "no_data")
                    attempts.append({
                        "provider": provider.provider_id,
                        "success": False,
                        "latency_ms": (time.time() - start_time) * 1000,
                        "reason": "no_data",
                    })
                    logger.warning(f"实时快照失败: {provider.provider_id}，尝试下一个")
            
            except Exception as e:
                # 异常，记录并重试下一个
                self.rate_limiter.record_failure(provider.provider_id, str(e))
                attempts.append({
                    "provider": provider.provider_id,
                    "success": False,
                    "latency_ms": (time.time() - start_time) * 1000,
                    "reason": str(e),
                })
                logger.error(f"实时快照异常: {provider.provider_id} - {e}")
        
        # 记录可观测性数据
        latency_ms = (time.time() - start_time) * 1000
        self.observability.log_call(
            code=",".join(codes),
            data_type="realtime",
            provider_hit=provider_hit,
            success=provider_hit != "ALL_FAILED",
            latency_ms=latency_ms,
            attempts=attempts,
        )
        
        return result
