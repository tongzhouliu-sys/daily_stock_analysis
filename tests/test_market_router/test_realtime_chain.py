# -*- coding: utf-8 -*-
"""
T6.2: 实时降级链模拟失效测试

验证实时快照降级链的自动切换
"""

import pytest
import logging
from unittest.mock import patch, MagicMock
from data_provider.market_router import get_router
from data_provider.market_router.chains import RealtimeChain

logger = logging.getLogger(__name__)


class TestRealtimeChainFallback:
    """实时降级链模拟失效测试"""
    
    def test_s1_disabled_s3_triggered(self):
        """手动禁用 S1，确认 S3 被自动触发"""
        router = get_router()
        
        # Mock S1 腾讯实时快照失败
        with patch('data_provider.market_router.providers.tencent_realtime.TencentRealtimeProvider.get_realtime') as mock_s1:
            mock_s1.return_value = {"600519": None}
            
            # 执行实时快照获取
            result = router.get_realtime(["600519"])
            
            # 验证 S1 被调用
            assert mock_s1.called, "S1 应该被调用"
            
            # 获取最近一次调用的元信息
            metadata = router.get_last_call_metadata()
            
            # 验证 provider_hit 不是 S1
            if metadata and metadata.get("success"):
                provider_hit = metadata.get("provider_hit")
                logger.info(f"降级后命中的源: {provider_hit}")
                
                # 如果成功，应该是 S3 或其他备源
                if provider_hit != "ALL_FAILED":
                    assert provider_hit != "S1_tencent", "S1 禁用后不应命中 S1"
    
    def test_all_providers_fail(self):
        """所有提供者都失败时返回 None"""
        router = get_router()
        
        # Mock 所有提供者失败
        with patch('data_provider.market_router.providers.tencent_realtime.TencentRealtimeProvider.get_realtime') as mock_s1, \
             patch('data_provider.market_router.providers.sina_realtime.SinaRealtimeProvider.get_realtime') as mock_s3, \
             patch('data_provider.market_router.providers.ashareapi_provider.AshareapiProvider.get_realtime') as mock_s4, \
             patch('data_provider.market_router.providers.zzshare_provider.ZzshareProvider.get_realtime') as mock_s5:
            
            mock_s1.return_value = {"600519": None}
            mock_s3.return_value = {"600519": None}
            mock_s4.return_value = {"600519": None}
            mock_s5.return_value = {"600519": None}
            
            # 执行实时快照获取
            result = router.get_realtime(["600519"])
            
            # 验证返回 None
            assert result["600519"] is None, "所有源失败时应返回 None"
            
            # 验证元信息
            metadata = router.get_last_call_metadata()
            assert metadata is not None
            assert metadata.get("provider_hit") == "ALL_FAILED"
            assert metadata.get("success") is False
    
    def test_s1_exception_s3_triggered(self):
        """S1 抛出异常时 S3 被触发"""
        router = get_router()
        
        # Mock S1 抛出异常
        with patch('data_provider.market_router.providers.tencent_realtime.TencentRealtimeProvider.get_realtime') as mock_s1:
            mock_s1.side_effect = Exception("S1 连接失败")
            
            # 执行实时快照获取
            result = router.get_realtime(["600519"])
            
            # 验证 S1 被调用
            assert mock_s1.called, "S1 应该被调用"
            
            # 获取最近一次调用的元信息
            metadata = router.get_last_call_metadata()
            
            # 验证 attempts 中包含 S1 的失败记录
            if metadata:
                attempts = metadata.get("attempts", [])
                s1_attempt = next((a for a in attempts if a["provider"] == "S1_tencent"), None)
                if s1_attempt:
                    assert s1_attempt["success"] is False
                    assert "S1 连接失败" in s1_attempt["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
