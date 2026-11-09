# -*- coding: utf-8 -*-
"""
T6.4: 频率合规测试

验证频率控制逻辑
"""

import pytest
import logging
import time
from data_provider.market_router import get_router
from data_provider.market_router.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


class TestRateLimiter:
    """频率合规测试"""
    
    def test_s1_request_interval(self):
        """S1 请求间隔 ≥ 100ms"""
        rate_limiter = get_rate_limiter()
        rate_limiter.reset()
        
        # 连续请求 S1
        start_time = time.time()
        
        for i in range(3):
            acquired = rate_limiter.acquire("S1_tencent")
            assert acquired, f"第 {i+1} 次请求应该成功"
            rate_limiter.record_success("S1_tencent")
        
        elapsed = time.time() - start_time
        
        # 3 次请求至少需要 200ms（2 个间隔）
        assert elapsed >= 0.2, f"请求间隔不足，实际耗时 {elapsed:.3f}s"
        
        logger.info(f"S1 请求间隔测试通过，3 次请求耗时 {elapsed:.3f}s")
    
    def test_s3_daily_limit(self):
        """S3 日请求计数在 1050 以内"""
        rate_limiter = get_rate_limiter()
        rate_limiter.reset()
        
        # 模拟 1050 次请求
        for i in range(1050):
            acquired = rate_limiter.acquire("S3_sina")
            assert acquired, f"第 {i+1} 次请求应该成功"
            rate_limiter.record_success("S3_sina")
        
        # 第 1051 次应该失败
        acquired = rate_limiter.acquire("S3_sina")
        assert not acquired, "第 1051 次请求应该失败"
        
        # 验证计数器
        count = rate_limiter.get_daily_count("S3_sina")
        assert count == 1050, f"日请求计数应该是 1050，实际是 {count}"
        
        logger.info(f"S3 日请求限制测试通过，计数 {count}")
    
    def test_s4_consecutive_429(self):
        """S4 连续 3 次 429 后跳过该源"""
        rate_limiter = get_rate_limiter()
        rate_limiter.reset()
        
        # 模拟连续 3 次 429
        for i in range(3):
            rate_limiter.record_failure("S4_ashareapi", "429 Too Many Requests")
        
        # 第 4 次应该被跳过
        acquired = rate_limiter.acquire("S4_ashareapi")
        assert not acquired, "连续 3 次 429 后应该跳过该源"
        
        # 验证计数器
        count = rate_limiter.get_consecutive_429_count("S4_ashareapi")
        assert count == 3, f"连续 429 计数应该是 3，实际是 {count}"
        
        logger.info(f"S4 连续 429 测试通过，计数 {count}")
    
    def test_s4_429_reset_on_success(self):
        """S4 成功后重置连续 429 计数"""
        rate_limiter = get_rate_limiter()
        rate_limiter.reset()
        
        # 模拟 2 次 429
        rate_limiter.record_failure("S4_ashareapi", "429 Too Many Requests")
        rate_limiter.record_failure("S4_ashareapi", "429 Too Many Requests")
        
        # 成功一次
        rate_limiter.record_success("S4_ashareapi")
        
        # 验证计数器重置
        count = rate_limiter.get_consecutive_429_count("S4_ashareapi")
        assert count == 0, f"成功后连续 429 计数应该重置为 0，实际是 {count}"
        
        logger.info(f"S4 429 重置测试通过")
    
    def test_batch_daily_requests(self):
        """连续请求 10 只标的的日线，不触发 HTTP 403/429"""
        router = get_router()
        
        codes = [
            "600519", "000001", "000002", "600036", "601318",
            "000858", "002714", "300750", "600276", "601888"
        ]
        
        # 连续请求 10 只标的
        for code in codes:
            result = router.get_daily(code, count=10)
            # 不验证结果，只验证不触发限流异常
        
        logger.info(f"批量日线请求测试通过，请求了 {len(codes)} 只标的")
    
    def test_rate_limiter_reset(self):
        """频率控制器重置功能"""
        rate_limiter = get_rate_limiter()
        
        # 设置一些状态
        rate_limiter.record_success("S1_tencent")
        rate_limiter.record_failure("S4_ashareapi", "429 Too Many Requests")
        
        # 重置
        rate_limiter.reset()
        
        # 验证状态清空
        count_s1 = rate_limiter.get_daily_count("S1_tencent")
        count_s4 = rate_limiter.get_consecutive_429_count("S4_ashareapi")
        
        assert count_s1 == 0, f"S1 日请求计数应该重置为 0，实际是 {count_s1}"
        assert count_s4 == 0, f"S4 连续 429 计数应该重置为 0，实际是 {count_s4}"
        
        logger.info(f"频率控制器重置测试通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
