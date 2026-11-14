# -*- coding: utf-8 -*-
"""
可观测性模块

实现结构化日志记录和监控指标
"""

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Observability:
    """可观测性管理器"""
    
    def __init__(self, log_dir: str = "./logs"):
        """
        初始化可观测性模块
        
        Args:
            log_dir: 日志目录
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.log_file = self.log_dir / "market_data_router.log"
        
        # 统计数据
        self._stats = {
            "total_calls": 0,
            "success_calls": 0,
            "failed_calls": 0,
            "provider_hits": defaultdict(int),
            "data_type_stats": defaultdict(lambda: {"total": 0, "success": 0}),
        }
        
        # 最近一次调用的元信息
        self._last_call_metadata: Optional[Dict[str, Any]] = None
    
    def log_call(
        self,
        code: str,
        data_type: str,
        provider_hit: str,
        success: bool,
        latency_ms: float,
        attempts: List[Dict[str, Any]],
    ) -> None:
        """
        记录一次调用
        
        Args:
            code: 股票代码
            data_type: 数据类型 (realtime/daily/minute)
            provider_hit: 命中的数据源
            success: 是否成功
            latency_ms: 延迟（毫秒）
            attempts: 尝试记录列表
        """
        timestamp = datetime.now().isoformat()
        
        # 构建日志记录
        log_entry = {
            "timestamp": timestamp,
            "code": code,
            "data_type": data_type,
            "provider_hit": provider_hit,
            "success": success,
            "latency_ms": round(latency_ms, 2),
            "attempts": attempts,
        }
        
        # 写入 JSON 行格式日志
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"写入日志文件失败: {e}")
        
        # 更新统计
        self._stats["total_calls"] += 1
        if success:
            self._stats["success_calls"] += 1
        else:
            self._stats["failed_calls"] += 1
        
        self._stats["provider_hits"][provider_hit] += 1
        self._stats["data_type_stats"][data_type]["total"] += 1
        if success:
            self._stats["data_type_stats"][data_type]["success"] += 1
        
        # 保存最近一次调用的元信息
        self._last_call_metadata = log_entry
        
        # 检查主源占比
        self._check_primary_provider_ratio()
    
    def _check_primary_provider_ratio(self) -> None:
        """检查主源占比，低于 50% 时告警"""
        # 主源列表
        primary_providers = {"S1_tencent", "S2_tencent_kline", "S5_zzshare"}
        
        # 计算主源占比
        total_hits = sum(self._stats["provider_hits"].values())
        if total_hits == 0:
            return
        
        primary_hits = sum(
            self._stats["provider_hits"].get(p, 0) for p in primary_providers
        )
        ratio = primary_hits / total_hits
        
        # 如果主源占比低于 50%，记录告警
        if ratio < 0.5 and total_hits >= 10:  # 至少 10 次调用才检查
            logger.warning(
                f"[监控告警] 主源占比 {ratio:.2%} 低于 50%，主源可能受限或失效"
            )
    
    def get_last_call_metadata(self) -> Optional[Dict[str, Any]]:
        """获取最近一次调用的元信息"""
        return self._last_call_metadata
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计数据"""
        return {
            "total_calls": self._stats["total_calls"],
            "success_calls": self._stats["success_calls"],
            "failed_calls": self._stats["failed_calls"],
            "success_rate": (
                self._stats["success_calls"] / self._stats["total_calls"]
                if self._stats["total_calls"] > 0
                else 0
            ),
            "provider_hits": dict(self._stats["provider_hits"]),
            "data_type_stats": dict(self._stats["data_type_stats"]),
        }
    
    def reset_stats(self) -> None:
        """重置统计数据"""
        self._stats = {
            "total_calls": 0,
            "success_calls": 0,
            "failed_calls": 0,
            "provider_hits": defaultdict(int),
            "data_type_stats": defaultdict(lambda: {"total": 0, "success": 0}),
        }


# 全局单例
_observability_instance = None


def get_observability(log_dir: str = "./logs") -> Observability:
    """获取可观测性模块单例"""
    global _observability_instance
    if _observability_instance is None:
        _observability_instance = Observability(log_dir=log_dir)
    return _observability_instance
