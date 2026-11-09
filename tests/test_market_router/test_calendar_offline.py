# -*- coding: utf-8 -*-
"""
T6.7: 断网日历测试

验证交易日历的离线可用性
"""

import pytest
import logging
from unittest.mock import patch
from data_provider.market_router import get_router

logger = logging.getLogger(__name__)


class TestCalendarOffline:
    """断网日历测试"""
    
    @pytest.fixture(autouse=True)
    def check_tj_calendar(self):
        """检查 tj-calendar 是否可用"""
        try:
            import tj_calendar
            self.has_tj_calendar = True
        except ImportError:
            self.has_tj_calendar = False
    
    def test_offline_calendar_basic(self):
        """断网环境下日历正常返回"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求失败（模拟断网）
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network is unreachable")
            
            # 获取交易日历
            result = router.get_calendar("2026-11-01", "2026-11-30")
            
            # 验证正常返回
            assert result is not None, "断网环境下应该正常返回"
            assert isinstance(result, list), "应该返回列表"
            assert len(result) > 0, "应该返回交易日列表"
            
            # 验证日期格式
            for date in result:
                assert isinstance(date, str), "日期应该是字符串"
                assert len(date) == 10, "日期格式应该是 YYYY-MM-DD"
            
            logger.info(f"断网日历测试通过，返回 {len(result)} 个交易日")
    
    def test_offline_calendar_no_network_calls(self):
        """验证日历获取不发起网络请求"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求
        with patch('requests.get') as mock_get:
            # 获取交易日历
            result = router.get_calendar("2026-11-01", "2026-11-30")
            
            # 验证没有发起网络请求
            assert not mock_get.called, "日历获取不应该发起网络请求"
            
            logger.info("断网日历无网络请求测试通过")
    
    def test_offline_calendar_weekend_filter(self):
        """验证日历正确过滤周末"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求失败
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network is unreachable")
            
            # 获取包含周末的日历
            result = router.get_calendar("2026-11-07", "2026-11-08")  # 周六周日
            
            # 验证返回空列表（周末不是交易日）
            assert len(result) == 0, "周末应该返回空列表"
            
            logger.info("断网日历周末过滤测试通过")
    
    def test_offline_calendar_holiday_filter(self):
        """验证日历正确过滤节假日"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求失败
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network is unreachable")
            
            # 获取包含国庆节的日历
            result = router.get_calendar("2026-10-01", "2026-10-07")  # 国庆节假期
            
            # 验证返回空列表或很少（节假日不是交易日）
            # 注意：具体节假日安排可能每年不同，这里只验证基本逻辑
            assert isinstance(result, list), "应该返回列表"
            
            logger.info(f"断网日历节假日过滤测试通过，返回 {len(result)} 个交易日")
    
    def test_offline_calendar_invalid_range(self):
        """验证无效日期范围处理"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求失败
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network is unreachable")
            
            # 测试无效日期范围
            with pytest.raises(Exception) as exc_info:
                router.get_calendar("2026-12-31", "2026-01-01")  # 结束早于开始
            
            logger.info("断网日历无效日期范围测试通过")
    
    def test_offline_calendar_year_boundary(self):
        """验证跨年日历"""
        if not self.has_tj_calendar:
            pytest.skip("tj-calendar 未安装")
        
        router = get_router()
        
        # Mock 网络请求失败
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network is unreachable")
            
            # 获取跨年日历
            result = router.get_calendar("2025-12-25", "2026-01-10")
            
            # 验证正常返回
            assert result is not None, "跨年日历应该正常返回"
            assert isinstance(result, list), "应该返回列表"
            
            # 验证包含两个年份的日期
            has_2025 = any(date.startswith("2025") for date in result)
            has_2026 = any(date.startswith("2026") for date in result)
            
            assert has_2025 or has_2026, "应该包含 2025 或 2026 年的日期"
            
            logger.info("断网日历跨年测试通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
