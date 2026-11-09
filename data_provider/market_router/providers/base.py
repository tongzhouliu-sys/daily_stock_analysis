# -*- coding: utf-8 -*-
"""
数据源提供者基类与通用工具
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def is_bse_code(code: str) -> bool:
    """
    识别北交所代码
    
    北交所代码规则：
    - 8xxxxx（老代码）
    - 4xxxxx（老代码）
    - 920xxx（新代码段）
    
    Args:
        code: 股票代码（6位数字或带前缀）
        
    Returns:
        True 表示北交所代码
    """
    # 去除前缀
    code = code.strip().upper()
    if code.startswith("BJ"):
        code = code[2:]
    if code.startswith("BJ."):
        code = code[3:]
    if code.endswith(".BJ"):
        code = code[:-3]
    
    # 检查是否为6位数字
    if not code.isdigit() or len(code) != 6:
        return False
    
    # 北交所代码规则
    if code.startswith("8") or code.startswith("4") or code.startswith("920"):
        return True
    
    return False


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    统一返回列
    
    所有 DataFrame 必须至少包含：
    date, open, high, low, close, volume, amount(可选)
    
    Args:
        df: 原始 DataFrame
        
    Returns:
        标准化后的 DataFrame
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    
    # 列名映射
    column_mapping = {
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Amount": "amount",
        "成交额": "amount",
        "成交量": "volume",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "日期": "date",
    }
    
    df = df.rename(columns=column_mapping)
    
    # 确保必需列存在
    required_cols = ["date", "open", "high", "low", "close", "volume"]
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"缺少必需列: {col}")
            return pd.DataFrame(columns=required_cols)
    
    # 数值类型转换
    numeric_cols = ["open", "high", "low", "close", "volume"]
    if "amount" in df.columns:
        numeric_cols.append("amount")
    
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    
    # 日期格式标准化
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
    
    # 只保留需要的列
    keep_cols = ["date", "open", "high", "low", "close", "volume"]
    if "amount" in df.columns:
        keep_cols.append("amount")
    
    return df[keep_cols].copy()


class BaseProvider(ABC):
    """数据源提供者基类"""
    
    provider_id: str = "unknown"
    supports_bse: bool = False
    
    @abstractmethod
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量实时快照
        
        Args:
            codes: 股票代码列表
            
        Returns:
            {code: {open, high, low, close, volume, ts} 或 None}
        """
        pass
    
    @abstractmethod
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """
        日K线
        
        Args:
            code: 股票代码
            start: 开始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD
            count: 最近N条（与 start/end 二选一）
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount(可选)
            失败返回 None
        """
        pass
    
    @abstractmethod
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """
        分钟K线
        
        Args:
            code: 股票代码
            freq: 频率，'1min', '5min', '15min', '30min', '60min'
            count: 条数
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            失败返回 None
        """
        pass
    
    def is_available(self) -> bool:
        """检查该源是否可用（如是否已配置 Token）"""
        return True
    
    def supports_code(self, code: str) -> bool:
        """检查是否支持该代码"""
        if is_bse_code(code) and not self.supports_bse:
            return False
        return True
