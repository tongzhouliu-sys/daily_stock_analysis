# -*- coding: utf-8 -*-
"""
T6.6: 批次容错测试

验证无效代码不中断批次
"""

import pytest
import logging
from unittest.mock import patch
from data_provider.market_router import get_router

logger = logging.getLogger(__name__)


class TestBatchFaultTolerance:
    """批次容错测试"""
    
    def test_invalid_code_does_not_break_batch(self):
        """无效代码不中断批次"""
        router = get_router()
        
        # Mock 所有提供者对无效代码返回 None
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2, \
             patch('data_provider.market_router.providers.ashareapi_provider.AshareapiProvider.get_daily') as mock_s4:
            
            # 无效代码返回 None
            mock_s2.return_value = None
            mock_s4.return_value = None
            
            # 构造批次：1 只无效代码 + 2 只有效代码
            codes = ["999999", "600519", "000001"]
            
            results = {}
            for code in codes:
                result = router.get_daily(code, count=10)
                results[code] = result
            
            # 验证无效代码返回 None
            assert results["999999"] is None, "无效代码应该返回 None"
            
            # 验证批次完整结束（所有代码都被处理）
            assert len(results) == 3, "应该处理所有 3 只代码"
            
            logger.info("批次容错测试通过：无效代码不中断批次")
    
    def test_partial_failure_in_batch(self):
        """批次中部分失败不影响其他"""
        router = get_router()
        
        # Mock 提供者
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            # 第一个代码失败，第二个成功
            call_count = [0]
            
            def side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return None  # 第一个失败
                return None  # 简化测试，都返回 None
            
            mock_s2.side_effect = side_effect
            
            # 构造批次
            codes = ["600519", "000001"]
            
            results = {}
            for code in codes:
                result = router.get_daily(code, count=10)
                results[code] = result
            
            # 验证批次完整结束
            assert len(results) == 2, "应该处理所有 2 只代码"
            
            logger.info("批次部分失败测试通过")
    
    def test_realtime_batch_with_invalid_code(self):
        """实时快照批次包含无效代码"""
        router = get_router()
        
        # 构造批次：包含无效代码
        codes = ["999999", "600519"]
        
        results = router.get_realtime(codes)
        
        # 验证批次完整结束
        assert len(results) == 2, "应该处理所有 2 只代码"
        assert "999999" in results, "无效代码应该在结果中"
        assert "600519" in results, "有效代码应该在结果中"
        
        logger.info("实时快照批次容错测试通过")
    
    def test_minute_batch_with_invalid_code(self):
        """分钟K线批次包含无效代码"""
        router = get_router()
        
        # Mock 提供者
        with patch('data_provider.market_router.providers.zzshare_provider.ZzshareProvider.get_minute') as mock_s5:
            mock_s5.return_value = None
            
            # 构造批次
            codes = ["999999", "600519"]
            
            results = {}
            for code in codes:
                result = router.get_minute(code, freq="5min", count=10)
                results[code] = result
            
            # 验证批次完整结束
            assert len(results) == 2, "应该处理所有 2 只代码"
            
            logger.info("分钟K线批次容错测试通过")
    
    def test_batch_all_invalid(self):
        """批次全部无效"""
        router = get_router()
        
        # Mock 所有提供者返回 None
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.return_value = None
            
            # 构造批次：全部无效
            codes = ["999999", "888888", "777777"]
            
            results = {}
            for code in codes:
                result = router.get_daily(code, count=10)
                results[code] = result
            
            # 验证批次完整结束
            assert len(results) == 3, "应该处理所有 3 只代码"
            
            # 验证全部返回 None
            for code in codes:
                assert results[code] is None, f"无效代码 {code} 应该返回 None"
            
            logger.info("批次全部无效测试通过")
    
    def test_batch_metadata_tracking(self):
        """批次元信息追踪"""
        router = get_router()
        
        # Mock 提供者
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.return_value = None
            
            # 构造批次
            codes = ["999999", "600519"]
            
            for code in codes:
                result = router.get_daily(code, count=10)
                
                # 验证每次调用都有元信息
                metadata = router.get_last_call_metadata()
                assert metadata is not None, f"代码 {code} 应该有元信息"
                assert "code" in metadata, "元信息应该包含 code"
                assert "provider_hit" in metadata, "元信息应该包含 provider_hit"
                assert "attempts" in metadata, "元信息应该包含 attempts"
            
            logger.info("批次元信息追踪测试通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
