# -*- coding: utf-8 -*-
"""
T6.8: 可观测性验证

验证日志完整性
"""

import pytest
import logging
import json
import os
from pathlib import Path
from data_provider.market_router import get_router

logger = logging.getLogger(__name__)


class TestObservability:
    """可观测性验证"""
    
    def test_log_file_created(self):
        """验证日志文件被创建"""
        router = get_router()
        
        # 执行一次调用
        router.get_realtime(["600519"])
        
        # 验证日志文件存在
        log_dir = Path("./logs")
        log_file = log_dir / "market_data_router.log"
        
        assert log_file.exists(), "日志文件应该被创建"
        
        logger.info("日志文件创建测试通过")
    
    def test_log_json_format(self):
        """验证日志格式为 JSON"""
        router = get_router()
        
        # 执行一次调用
        router.get_realtime(["600519"])
        
        # 读取日志文件
        log_file = Path("./logs/market_data_router.log")
        
        if log_file.exists():
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
                if lines:
                    # 验证最后一行是有效的 JSON
                    last_line = lines[-1].strip()
                    try:
                        log_entry = json.loads(last_line)
                        assert isinstance(log_entry, dict), "日志应该是字典格式"
                    except json.JSONDecodeError:
                        pytest.fail("日志格式不是有效的 JSON")
        
        logger.info("日志 JSON 格式测试通过")
    
    def test_log_required_fields(self):
        """验证日志包含必需字段"""
        router = get_router()
        
        # 执行一次调用
        router.get_daily("600519", count=10)
        
        # 获取元信息
        metadata = router.get_last_call_metadata()
        
        assert metadata is not None, "应该有元信息"
        
        # 验证必需字段
        required_fields = ["timestamp", "code", "data_type", "provider_hit", "success", "latency_ms", "attempts"]
        
        for field in required_fields:
            assert field in metadata, f"元信息应该包含字段: {field}"
        
        logger.info("日志必需字段测试通过")
    
    def test_log_provider_hit_field(self):
        """验证 provider_hit 字段"""
        router = get_router()
        
        # 执行一次调用
        router.get_daily("600519", count=10)
        
        # 获取元信息
        metadata = router.get_last_call_metadata()
        
        assert metadata is not None, "应该有元信息"
        assert "provider_hit" in metadata, "应该包含 provider_hit 字段"
        
        provider_hit = metadata["provider_hit"]
        
        # 验证 provider_hit 是有效的值
        valid_providers = [
            "S1_tencent", "S2_tencent_kline", "S3_sina", "S4_ashareapi",
            "S5_zzshare", "S6_apocdata", "S7_tushare", "S8_itick",
            "baostock", "ALL_FAILED"
        ]
        
        assert provider_hit in valid_providers, f"provider_hit 应该是有效的值，实际是: {provider_hit}"
        
        logger.info(f"provider_hit 字段测试通过，值: {provider_hit}")
    
    def test_log_success_field(self):
        """验证 success 字段"""
        router = get_router()
        
        # 执行一次调用
        router.get_daily("600519", count=10)
        
        # 获取元信息
        metadata = router.get_last_call_metadata()
        
        assert metadata is not None, "应该有元信息"
        assert "success" in metadata, "应该包含 success 字段"
        
        success = metadata["success"]
        
        # 验证 success 是布尔值
        assert isinstance(success, bool), "success 应该是布尔值"
        
        # 验证 success 与 provider_hit 一致
        provider_hit = metadata["provider_hit"]
        if provider_hit == "ALL_FAILED":
            assert success is False, "ALL_FAILED 时 success 应该是 False"
        else:
            assert success is True, "成功时 success 应该是 True"
        
        logger.info(f"success 字段测试通过，值: {success}")
    
    def test_log_latency_ms_field(self):
        """验证 latency_ms 字段"""
        router = get_router()
        
        # 执行一次调用
        router.get_daily("600519", count=10)
        
        # 获取元信息
        metadata = router.get_last_call_metadata()
        
        assert metadata is not None, "应该有元信息"
        assert "latency_ms" in metadata, "应该包含 latency_ms 字段"
        
        latency_ms = metadata["latency_ms"]
        
        # 验证 latency_ms 是数值
        assert isinstance(latency_ms, (int, float)), "latency_ms 应该是数值"
        assert latency_ms >= 0, "latency_ms 应该大于等于 0"
        
        logger.info(f"latency_ms 字段测试通过，值: {latency_ms}ms")
    
    def test_log_attempts_field(self):
        """验证 attempts 字段"""
        router = get_router()
        
        # 执行一次调用
        router.get_daily("600519", count=10)
        
        # 获取元信息
        metadata = router.get_last_call_metadata()
        
        assert metadata is not None, "应该有元信息"
        assert "attempts" in metadata, "应该包含 attempts 字段"
        
        attempts = metadata["attempts"]
        
        # 验证 attempts 是列表
        assert isinstance(attempts, list), "attempts 应该是列表"
        
        # 验证至少有一次尝试
        assert len(attempts) > 0, "应该至少有一次尝试"
        
        # 验证每次尝试的结构
        for attempt in attempts:
            assert isinstance(attempt, dict), "每次尝试应该是字典"
            assert "provider" in attempt, "尝试应该包含 provider 字段"
            assert "success" in attempt, "尝试应该包含 success 字段"
            assert "latency_ms" in attempt, "尝试应该包含 latency_ms 字段"
            assert "reason" in attempt, "尝试应该包含 reason 字段"
        
        logger.info(f"attempts 字段测试通过，尝试次数: {len(attempts)}")
    
    def test_log_all_failed_attempts(self):
        """验证 ALL_FAILED 时 attempts 包含完整记录"""
        router = get_router()
        
        # Mock 所有提供者失败
        from unittest.mock import patch
        
        with patch('data_provider.market_router.providers.tencent_kline.TencentKlineProvider.get_daily') as mock_s2, \
             patch('data_provider.market_router.providers.ashareapi_provider.AshareapiProvider.get_daily') as mock_s4, \
             patch('data_provider.market_router.providers.zzshare_provider.ZzshareProvider.get_daily') as mock_s5:
            
            mock_s2.return_value = None
            mock_s4.return_value = None
            mock_s5.return_value = None
            
            # 执行一次调用
            router.get_daily("600519", count=10)
            
            # 获取元信息
            metadata = router.get_last_call_metadata()
            
            assert metadata is not None, "应该有元信息"
            assert metadata["provider_hit"] == "ALL_FAILED", "应该是 ALL_FAILED"
            
            # 验证 attempts 包含完整记录
            attempts = metadata["attempts"]
            assert len(attempts) > 0, "ALL_FAILED 时应该有尝试记录"
            
            # 验证所有尝试都失败
            for attempt in attempts:
                assert attempt["success"] is False, "所有尝试都应该失败"
            
            logger.info(f"ALL_FAILED attempts 测试通过，尝试次数: {len(attempts)}")
    
    def test_stats_tracking(self):
        """验证统计追踪"""
        router = get_router()
        
        # 重置统计
        router.reset_stats()
        
        # 执行多次调用
        router.get_realtime(["600519"])
        router.get_daily("600519", count=10)
        router.get_minute("600519", freq="5min", count=10)
        
        # 获取统计
        stats = router.get_stats()
        
        assert stats is not None, "应该有统计"
        assert "total_calls" in stats, "应该包含 total_calls"
        assert "success_calls" in stats, "应该包含 success_calls"
        assert "failed_calls" in stats, "应该包含 failed_calls"
        assert "provider_hits" in stats, "应该包含 provider_hits"
        
        # 验证统计值
        assert stats["total_calls"] >= 3, "应该至少 3 次调用"
        
        logger.info(f"统计追踪测试通过，总调用: {stats['total_calls']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
