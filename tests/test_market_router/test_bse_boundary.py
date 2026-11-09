# -*- coding: utf-8 -*-
"""
T6.5: 北交所边界测试

验证北交所代码的特殊处理
"""

import pytest
import logging
from unittest.mock import patch
from data_provider.market_router import get_router
from data_provider.market_router.providers import is_bse_code

logger = logging.getLogger(__name__)


class TestBSEBoundary:
    """北交所边界测试"""
    
    def test_bse_code_identification(self):
        """北交所代码识别"""
        # 北交所代码
        assert is_bse_code("830799"), "830799 应该是北交所代码"
        assert is_bse_code("430047"), "430047 应该是北交所代码"
        assert is_bse_code("920748"), "920748 应该是北交所代码"
        
        # 沪深代码
        assert not is_bse_code("600519"), "600519 不应该是北交所代码"
        assert not is_bse_code("000001"), "000001 不应该是北交所代码"
        assert not is_bse_code("300750"), "300750 不应该是北交所代码"
        
        logger.info("北交所代码识别测试通过")
    
    def test_bse_daily_skips_s2(self):
        """北交所代码日K线跳过 S2"""
        router = get_router()
        
        # Mock S2 腾讯K线
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.return_value = None
            
            # 北交所代码
            bse_code = "830799"
            result = router.get_daily(bse_code, count=10)
            
            # 验证 S2 没有被调用
            assert not mock_s2.called, "北交所代码不应该调用 S2"
            
            logger.info(f"北交所代码 {bse_code} 跳过 S2 测试通过")
    
    def test_bse_realtime_uses_s1(self):
        """北交所代码实时快照使用 S1"""
        router = get_router()
        
        # Mock S1 腾讯实时快照
        with patch('data_provider.market_router.providers.tencent_realtime.TencentRealtimeProvider.get_realtime') as mock_s1:
            mock_s1.return_value = {"830799": {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1000}}
            
            # 北交所代码
            bse_code = "830799"
            result = router.get_realtime([bse_code])
            
            # 验证 S1 被调用
            assert mock_s1.called, "北交所代码应该调用 S1"
            
            # 验证返回数据
            assert result[bse_code] is not None, "应该返回数据"
            
            logger.info(f"北交所代码 {bse_code} 使用 S1 测试通过")
    
    def test_batch_with_bse_and_shenzhen(self):
        """混合批次：北交所 + 沪深代码"""
        router = get_router()
        
        # Mock S2 腾讯K线
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2:
            mock_s2.return_value = None
            
            # 混合批次
            codes = ["830799", "600519", "000001"]
            
            for code in codes:
                result = router.get_daily(code, count=10)
                
                if is_bse_code(code):
                    # 北交所代码不应该调用 S2
                    assert not mock_s2.called, f"北交所代码 {code} 不应该调用 S2"
                else:
                    # 沪深代码应该调用 S2
                    assert mock_s2.called, f"沪深代码 {code} 应该调用 S2"
                
                # 重置 mock
                mock_s2.reset_mock()
            
            logger.info("混合批次测试通过")
    
    def test_bse_minute_skips_s2(self):
        """北交所代码分钟K线跳过 S2"""
        router = get_router()
        
        # Mock S2 腾讯K线
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_minute') as mock_s2:
            mock_s2.return_value = None
            
            # 北交所代码
            bse_code = "830799"
            result = router.get_minute(bse_code, freq="5min", count=10)
            
            # 验证 S2 没有被调用
            assert not mock_s2.called, "北交所代码不应该调用 S2"
            
            logger.info(f"北交所代码 {bse_code} 分钟K线跳过 S2 测试通过")
    
    def test_bse_all_fail_records_all_failed(self):
        """北交所代码全部失败时记录 ALL_FAILED"""
        router = get_router()
        
        # Mock 所有提供者失败
        with patch('data_provider.market_router.providers.ashareapi_provider.AshareapiProvider.get_daily') as mock_s4, \
             patch('data_provider.market_router.providers.zzshare_provider.ZzshareProvider.get_daily') as mock_s5:
            
            mock_s4.return_value = None
            mock_s5.return_value = None
            
            # 北交所代码
            bse_code = "830799"
            result = router.get_daily(bse_code, count=10)
            
            # 验证返回 None
            assert result is None, "所有源失败时应返回 None"
            
            # 验证元信息
            metadata = router.get_last_call_metadata()
            assert metadata is not None
            assert metadata.get("provider_hit") == "ALL_FAILED"
            assert metadata.get("success") is False
            
            logger.info(f"北交所代码 {bse_code} 全部失败记录 ALL_FAILED 测试通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
