# -*- coding: utf-8 -*-
"""
S5: zzshare 提供者

接口：zzshare 库
覆盖范围：日线、分钟线、实时快照
频率限制：匿名 ≥ 1s，配置 Token 后可适当降低
北交所：stock_basic 含 BJ 交易所
数据口径：日线、分钟线均支持
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class ZzshareProvider(BaseProvider):
    """zzshare 提供者"""
    
    provider_id = "S5_zzshare"
    supports_bse = True  # 支持北交所
    
    def __init__(self):
        self._api = None
        self._token = os.getenv("ZZSHARE_TOKEN")
    
    def _get_api(self):
        """延迟加载 zzshare"""
        if self._api is None:
            try:
                from zzshare import DataApi
                self._api = DataApi(token=self._token)
            except ImportError as e:
                logger.error(f"zzshare 未安装: {e}")
                raise ImportError("zzshare 未安装，请执行: pip install zzshare")
        return self._api
    
    def is_available(self) -> bool:
        """检查是否可用"""
        try:
            self._get_api()
            return True
        except ImportError:
            return False
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """批量获取实时快照"""
        result = {}
        api = self._get_api()
        
        for code in codes:
            try:
                # 获取实时快照
                data = api.rt_k(code)
                
                if data and isinstance(data, pd.DataFrame) and not data.empty:
                    # 取最新一条
                    latest = data.iloc[-1]
                    result[code] = {
                        "open": float(latest.get("open", 0)) if "open" in latest else None,
                        "high": float(latest.get("high", 0)) if "high" in latest else None,
                        "low": float(latest.get("low", 0)) if "low" in latest else None,
                        "close": float(latest.get("close", 0)) if "close" in latest else None,
                        "volume": int(latest.get("volume", 0)) if "volume" in latest else None,
                        "amount": float(latest.get("amount", 0)) if "amount" in latest else None,
                        "ts": str(latest.get("date", "")),
                    }
                else:
                    result[code] = None
            
            except Exception as e:
                logger.error(f"zzshare 实时快照失败 {code}: {e}")
                result[code] = None
        
        return result
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取日K线"""
        api = self._get_api()
        
        try:
            # 获取日K线
            if count:
                data = api.daily(code, count=count)
            elif start and end:
                data = api.daily(code, start_date=start, end_date=end)
            else:
                data = api.daily(code, count=100)
            
            if data is None or (isinstance(data, pd.DataFrame) and data.empty):
                logger.warning(f"zzshare 日K线返回空数据: {code}")
                return None
            
            # 转换为 DataFrame
            if isinstance(data, pd.DataFrame):
                df = normalize_columns(data)
                
                # 过滤日期范围
                if start and end:
                    df = df[(df["date"] >= start) & (df["date"] <= end)]
                
                return df
            
            return None
        
        except Exception as e:
            logger.error(f"zzshare 日K线失败 {code}: {e}")
            return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """获取分钟K线"""
        api = self._get_api()
        
        # 频率映射
        freq_map = {
            "1min": "1",
            "5min": "5",
            "15min": "15",
            "30min": "30",
            "60min": "60",
        }
        
        minute_freq = freq_map.get(freq)
        if not minute_freq:
            logger.warning(f"不支持的频率: {freq}")
            return None
        
        try:
            # 获取分钟K线
            data = api.stk_mins(code, freq=minute_freq, count=count)
            
            if data is None or (isinstance(data, pd.DataFrame) and data.empty):
                logger.warning(f"zzshare 分钟K线返回空数据: {code} {freq}")
                return None
            
            # 转换为 DataFrame
            if isinstance(data, pd.DataFrame):
                df = normalize_columns(data)
                return df
            
            return None
        
        except Exception as e:
            logger.error(f"zzshare 分钟K线失败 {code}: {e}")
            return None
