# -*- coding: utf-8 -*-
"""
频率控制器

实现各数据源的硬性频率控制，避免因请求过频被封禁
"""

import logging
import time
from collections import defaultdict
from threading import RLock
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# 各源硬约束常量
RATE_LIMITS = {
    "S1_tencent": {
        "min_interval_ms": 100,  # 请求间隔 ≥ 100ms
        "description": "腾讯实时快照",
    },
    "S2_tencent_kline": {
        "min_interval_ms": 200,  # 与 S1 共享腾讯域，建议 ≥ 200ms
        "description": "腾讯K线",
    },
    "S3_sina": {
        "max_daily": 1050,  # 日请求上限（预留 30% 余量）
        "max_qps": 5,  # 并发 ≤ 5 QPS
        "description": "新浪实时快照",
    },
    "S4_ashareapi": {
        "backoff_on_429_s": 2,  # 遇 429 退避 ≥ 2s
        "skip_after_429_count": 3,  # 连续 3 次 429 则本批次跳过该源
        "description": "ashareapi",
    },
    "S5_zzshare": {
        "min_interval_s": 1,  # 匿名 ≥ 1s
        "description": "zzshare",
    },
    "S6_apocdata": {
        "min_interval_s": 1,
        "description": "ApocData",
    },
    "S7_tushare": {
        "max_per_minute": 50,  # ≤ 50 次/分钟
        "max_daily": 8000,  # ≤ 8000 次/日
        "description": "Tushare",
    },
    "S8_itick": {
        "max_per_minute": 5,  # ≤ 5 次/分钟
        "description": "iTick",
    },
    "baostock": {
        "max_daily": 50000,  # 每日 ≤ 50,000 次
        "description": "Baostock",
    },
}


class RateLimiter:
    """频率控制器"""
    
    def __init__(self):
        self._lock = RLock()
        
        # 各源状态
        self._last_request_time: Dict[str, float] = {}
        self._daily_counts: Dict[str, int] = defaultdict(int)
        self._minute_counts: Dict[str, int] = defaultdict(int)
        self._consecutive_429: Dict[str, int] = defaultdict(int)
        self._day_reset_time: float = time.time()
        self._minute_reset_time: float = time.time()
    
    def acquire(self, provider_id: str) -> bool:
        """
        尝试获取请求许可
        
        Args:
            provider_id: 数据源 ID
            
        Returns:
            True 表示可以请求，False 表示应跳过该源
        """
        with self._lock:
            self._reset_counters_if_needed()
            
            limits = RATE_LIMITS.get(provider_id, {})
            
            # 检查日请求上限
            max_daily = limits.get("max_daily")
            if max_daily is not None:
                if self._daily_counts[provider_id] >= max_daily:
                    logger.warning(
                        f"[频率控制] {provider_id} 已达日请求上限 {max_daily}，跳过"
                    )
                    return False
            
            # 检查分钟请求上限
            max_per_minute = limits.get("max_per_minute")
            if max_per_minute is not None:
                if self._minute_counts[provider_id] >= max_per_minute:
                    logger.warning(
                        f"[频率控制] {provider_id} 已达分钟请求上限 {max_per_minute}，跳过"
                    )
                    return False
            
            # 检查最小请求间隔
            min_interval_ms = limits.get("min_interval_ms")
            if min_interval_ms is not None:
                last_time = self._last_request_time.get(provider_id, 0)
                elapsed_ms = (time.time() - last_time) * 1000
                if elapsed_ms < min_interval_ms:
                    sleep_time = (min_interval_ms - elapsed_ms) / 1000
                    logger.debug(
                        f"[频率控制] {provider_id} 请求间隔过短，等待 {sleep_time:.3f}s"
                    )
                    time.sleep(sleep_time)
            
            # 检查最小请求间隔（秒）
            min_interval_s = limits.get("min_interval_s")
            if min_interval_s is not None:
                last_time = self._last_request_time.get(provider_id, 0)
                elapsed_s = time.time() - last_time
                if elapsed_s < min_interval_s:
                    sleep_time = min_interval_s - elapsed_s
                    logger.debug(
                        f"[频率控制] {provider_id} 请求间隔过短，等待 {sleep_time:.3f}s"
                    )
                    time.sleep(sleep_time)
            
            # 检查连续 429 次数
            skip_after_429 = limits.get("skip_after_429_count")
            if skip_after_429 is not None:
                if self._consecutive_429[provider_id] >= skip_after_429:
                    logger.warning(
                        f"[频率控制] {provider_id} 连续 {self._consecutive_429[provider_id]} 次 429，跳过"
                    )
                    return False
            
            # 记录请求时间
            self._last_request_time[provider_id] = time.time()
            
            return True
    
    def record_success(self, provider_id: str) -> None:
        """记录成功请求"""
        with self._lock:
            self._daily_counts[provider_id] += 1
            self._minute_counts[provider_id] += 1
            self._consecutive_429[provider_id] = 0  # 重置连续 429 计数
    
    def record_failure(self, provider_id: str, reason: str) -> None:
        """
        记录失败请求
        
        Args:
            provider_id: 数据源 ID
            reason: 失败原因
        """
        with self._lock:
            # 如果是 429 错误，增加连续 429 计数
            if "429" in reason or "rate limit" in reason.lower():
                self._consecutive_429[provider_id] += 1
                logger.warning(
                    f"[频率控制] {provider_id} 连续 429 次数: {self._consecutive_429[provider_id]}"
                )
    
    def _reset_counters_if_needed(self) -> None:
        """重置计数器（如果需要）"""
        current_time = time.time()
        
        # 重置日计数器（每天 0 点）
        if current_time - self._day_reset_time > 86400:
            self._daily_counts.clear()
            self._day_reset_time = current_time
            logger.debug("[频率控制] 日计数器已重置")
        
        # 重置分钟计数器（每分钟）
        if current_time - self._minute_reset_time > 60:
            self._minute_counts.clear()
            self._minute_reset_time = current_time
            logger.debug("[频率控制] 分钟计数器已重置")
    
    def get_daily_count(self, provider_id: str) -> int:
        """获取日请求计数"""
        with self._lock:
            return self._daily_counts.get(provider_id, 0)
    
    def get_consecutive_429_count(self, provider_id: str) -> int:
        """获取连续 429 次数"""
        with self._lock:
            return self._consecutive_429.get(provider_id, 0)
    
    def reset(self, provider_id: Optional[str] = None) -> None:
        """重置计数器"""
        with self._lock:
            if provider_id:
                self._daily_counts[provider_id] = 0
                self._minute_counts[provider_id] = 0
                self._consecutive_429[provider_id] = 0
            else:
                self._daily_counts.clear()
                self._minute_counts.clear()
                self._consecutive_429.clear()


# 全局单例
_rate_limiter_instance = None


def get_rate_limiter() -> RateLimiter:
    """获取频率控制器单例"""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = RateLimiter()
    return _rate_limiter_instance
