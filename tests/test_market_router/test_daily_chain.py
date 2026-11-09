# -*- coding: utf-8 -*-
"""
T6.3: 日线降级链模拟失效测试

验证日K线降级链的自动切换
"""

import pytest
import logging
from unittest.mock import patch
from data_provider.market_router import get_router

logger = logging.getLogger(__name__)


class TestDailyChainFallback:
    """日线降级链模拟失效测试"""
    
    def test_s2_disabled_s4_or_s5_triggered(self):
        """手动禁用 S2，确认 S4 或 S5 被自动触发"""
        router = get_router()
        
        # Mock S2 腾讯K线失败
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.return_value = None
            
            # 执行日K线获取
            result = router.get_daily("600519", count=10)
            
            # 验证 S2 被调用
            assert mock_s2.called, "S2 应该被调用"
            
            # 获取最近一次调用的元信息
            metadata = router.get_last_call_metadata()
            
            # 验证 provider_hit 不是 S2
            if metadata and metadata.get("success"):
                provider_hit = metadata.get("provider_hit")
                logger.info(f"降级后命中的源: {provider_hit}")
                
                # 如果成功，应该是 S4 或 S5 或其他备源
                if provider_hit != "ALL_FAILED":
                    assert provider_hit != "S2_tencent_kline", "S2 禁用后不应命中 S2"
    
    def test_all_providers_fail(self):
        """所有提供者都失败时返回 None"""
        router = get_router()
        
        # Mock 所有提供者失败
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2, \
             patch('data_provider.market_router.providers.ashareapi_provider.AshareapiProvider.get_daily') as mock_s4, \
             patch('data_provider.market_router.providers.zzshare_provider.ZzshareProvider.get_daily') as mock_s5, \
             patch('data_provider.market_router.providers.baostock_provider.BaostockProvider.get_daily') as mock_baostock:
            
            mock_s2.return_value = None
            mock_s4.return_value = None
            mock_s5.return_value = None
            mock_baostock.return_value = None
            
            # 执行日K线获取
            result = router.get_daily("600519", count=10)
            
            # 验证返回 None
            assert result is None, "所有源失败时应返回 None"
            
            # 验证元信息
            metadata = router.get_last_call_metadata()
            assert metadata is not None
            assert metadata.get("provider_hit") == "ALL_FAILED"
            assert metadata.get("success") is False
    
    def test_s2_exception_s4_triggered(self):
        """S2 抛出异常时 S4 被触发"""
        router = get_router()
        
        # Mock S2 抛出异常
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.side_effect = Exception("S2 连接失败")
            
            # 执行日K线获取
            result = router.get_daily("600519", count=10)
            
            # 验证 S2 被调用
            assert mock_s2.called, "S2 应该被调用"
            
            # 获取最近一次调用的元信息
            metadata = router.get_last_call_metadata()
            
            # 验证 attempts 中包含 S2 的失败记录
            if metadata:
                attempts = metadata.get("attempts", [])
                s2_attempt = next((a for a in attempts if a["provider"] == "S2_tencent_kline"), None)
                if s2_attempt:
                    assert s2_attempt["success"] is False
                    assert "S2 连接失败" in s2_attempt["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
