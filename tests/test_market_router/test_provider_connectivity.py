# -*- coding: utf-8 -*-
"""
T6.1: 单源连通性测试

验证每个数据源的最小请求
"""

import pytest
import logging
pytestmark = pytest.mark.network
from data_provider.market_router.providers import (
    TencentRealtimeProvider,
    TencentKlineProvider,
    SinaRealtimeProvider,
    AshareapiProvider,
    ZzshareProvider,
    ApocdataProvider,
    TushareProvider,
    ItickProvider,
    BaostockProvider,
)

logger = logging.getLogger(__name__)


class TestProviderConnectivity:
    """单源连通性测试"""
    
    def test_s1_tencent_realtime(self):
        """S1 腾讯实时快照连通性测试"""
        provider = TencentRealtimeProvider()
        
        # 测试沪深标的
        codes = ["600519", "000001"]
        result = provider.get_realtime(codes)
        
        # 验证返回结构
        assert isinstance(result, dict)
        assert len(result) == len(codes)
        
        # 验证至少有一个标的返回有效数据
        has_valid_data = False
        for code, data in result.items():
            if data is not None:
                has_valid_data = True
                assert "open" in data
                assert "high" in data
                assert "low" in data
                assert "close" in data
                assert "volume" in data
        
        logger.info(f"S1 腾讯实时快照测试完成，有效数据: {has_valid_data}")
        assert has_valid_data, "S1 腾讯实时快照未返回有效数据"
    
    def test_s2_tencent_kline_daily(self):
        """S2 腾讯K线日线连通性测试"""
        provider = TencentKlineProvider()
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_daily(code, count=10)
        
        # 验证返回结构
        assert result is not None
        assert not result.empty
        assert len(result) > 0
        
        # 验证列名
        required_columns = ["date", "open", "high", "low", "close", "volume"]
        for col in required_columns:
            assert col in result.columns
        
        logger.info(f"S2 腾讯K线日线测试完成，返回 {len(result)} 条数据")
    
    def test_s2_tencent_kline_minute(self):
        """S2 腾讯K线分钟线连通性测试"""
        provider = TencentKlineProvider()
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_minute(code, freq="5min", count=10)
        
        # 验证返回结构
        assert result is not None
        assert not result.empty
        assert len(result) > 0
        
        # 验证列名
        required_columns = ["date", "open", "high", "low", "close", "volume"]
        for col in required_columns:
            assert col in result.columns
        
        logger.info(f"S2 腾讯K线分钟线测试完成，返回 {len(result)} 条数据")
    
    def test_s3_sina_realtime(self):
        """S3 新浪实时快照连通性测试"""
        provider = SinaRealtimeProvider()
        
        # 测试沪深标的
        codes = ["600519", "000001"]
        result = provider.get_realtime(codes)
        
        # 验证返回结构
        assert isinstance(result, dict)
        assert len(result) == len(codes)
        
        # 验证至少有一个标的返回有效数据
        has_valid_data = False
        for code, data in result.items():
            if data is not None:
                has_valid_data = True
                assert "open" in data
                assert "high" in data
                assert "low" in data
                assert "close" in data
                assert "volume" in data
        
        logger.info(f"S3 新浪实时快照测试完成，有效数据: {has_valid_data}")
        assert has_valid_data, "S3 新浪实时快照未返回有效数据"
    
    def test_s4_ashareapi_daily(self):
        """S4 ashareapi 日线连通性测试"""
        provider = AshareapiProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("ashareapi 未安装或不可用")
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_daily(code, count=10)
        
        # 验证返回结构
        if result is not None:
            assert not result.empty
            assert len(result) > 0
            
            # 验证列名
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            for col in required_columns:
                assert col in result.columns
        
        logger.info(f"S4 ashareapi 日线测试完成")
    
    def test_s5_zzshare_daily(self):
        """S5 zzshare 日线连通性测试"""
        provider = ZzshareProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("zzshare 未安装或不可用")
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_daily(code, count=10)
        
        # 验证返回结构
        if result is not None:
            assert not result.empty
            assert len(result) > 0
            
            # 验证列名
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            for col in required_columns:
                assert col in result.columns
        
        logger.info(f"S5 zzshare 日线测试完成")
    
    def test_s5_zzshare_minute(self):
        """S5 zzshare 分钟线连通性测试"""
        provider = ZzshareProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("zzshare 未安装或不可用")
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_minute(code, freq="5min", count=10)
        
        # 验证返回结构
        if result is not None:
            assert not result.empty
            assert len(result) > 0
            
            # 验证列名
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            for col in required_columns:
                assert col in result.columns
        
        logger.info(f"S5 zzshare 分钟线测试完成")
    
    def test_s6_apocdata_realtime(self):
        """S6 ApocData 实时快照连通性测试"""
        provider = ApocdataProvider()
        
        # 测试沪深标的
        codes = ["600519"]
        result = provider.get_realtime(codes)
        
        # 验证返回结构
        assert isinstance(result, dict)
        assert len(result) == len(codes)
        
        logger.info(f"S6 ApocData 实时快照测试完成")
    
    def test_s7_tushare_daily(self):
        """S7 Tushare 日线连通性测试"""
        provider = TushareProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("Tushare 未配置 Token 或不可用")
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_daily(code, count=10)
        
        # 验证返回结构
        if result is not None:
            assert not result.empty
            assert len(result) > 0
            
            # 验证列名
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            for col in required_columns:
                assert col in result.columns
        
        logger.info(f"S7 Tushare 日线测试完成")
    
    def test_s8_itick_realtime(self):
        """S8 iTick 实时快照连通性测试"""
        provider = ItickProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("iTick 未配置 Token 或不可用")
        
        # 测试沪深标的
        codes = ["600519"]
        result = provider.get_realtime(codes)
        
        # 验证返回结构
        assert isinstance(result, dict)
        assert len(result) == len(codes)
        
        logger.info(f"S8 iTick 实时快照测试完成")
    
    def test_baostock_daily(self):
        """Baostock 日线连通性测试"""
        provider = BaostockProvider()
        
        # 检查是否可用
        if not provider.is_available():
            pytest.skip("Baostock 未安装或不可用")
        
        # 测试沪深标的
        code = "600519"
        result = provider.get_daily(code, count=10)
        
        # 验证返回结构
        if result is not None:
            assert not result.empty
            assert len(result) > 0
            
            # 验证列名
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            for col in required_columns:
                assert col in result.columns
        
        logger.info(f"Baostock 日线测试完成")
    
    def test_multiple_providers_available(self):
        """验证至少 4 个源可用"""
        providers = [
            TencentRealtimeProvider(),
            TencentKlineProvider(),
            SinaRealtimeProvider(),
            AshareapiProvider(),
            ZzshareProvider(),
            ApocdataProvider(),
            TushareProvider(),
            ItickProvider(),
            BaostockProvider(),
        ]
        
        available_count = sum(1 for p in providers if p.is_available())
        
        logger.info(f"可用数据源数量: {available_count}")
        assert available_count >= 4, f"可用数据源数量不足，期望至少 4 个，实际 {available_count} 个"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
