# -*- coding: utf-8 -*-
"""
S8: iTick 提供者（可选，仅注册用户）

接口：iTick HTTP API
覆盖范围：实时快照、分钟K线
频率限制：≤ 5 次/分钟
北交所：不支持
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class ItickProvider(BaseProvider):
    """iTick 提供者"""
    
    provider_id = "S8_itick"
    supports_bse = False  # 不支持北交所
    
    _BASE_URL = "https://api.itick.org"
    _HTTP_TIMEOUT_SECONDS = 8
    
    def __init__(self):
        self._token = os.getenv("ITICK_TOKEN")
        self._session = requests.Session()
        if self._token:
            self._session.headers.update({
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "Mozilla/5.0",
            })
    
    def is_available(self) -> bool:
        """检查是否可用"""
        return bool(self._token)
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """批量获取实时快照"""
        if not self.is_available():
            return {code: None for code in codes}
        
        result = {}
        
        for code in codes:
            try:
                # 构建请求
                url = f"{self._BASE_URL}/quote"
                params = {"symbol": code}
                
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self._HTTP_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                
                data = response.json()
                
                if data and isinstance(data, dict):
                    result[code] = {
                        "open": float(data.get("open", 0)) if data.get("open") else None,
                        "high": float(data.get("high", 0)) if data.get("high") else None,
                        "low": float(data.get("low", 0)) if data.get("low") else None,
                        "close": float(data.get("price", 0)) if data.get("price") else None,
                        "volume": int(data.get("volume", 0)) if data.get("volume") else None,
                        "amount": float(data.get("amount", 0)) if data.get("amount") else None,
                        "ts": data.get("timestamp"),
                    }
                else:
                    result[code] = None
            
            except Exception as e:
                logger.error(f"iTick 实时快照失败 {code}: {e}")
                result[code] = None
        
        return result
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """iTick 不支持日K线（在日线链路中不使用）"""
        return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """获取分钟K线"""
        if not self.is_available():
            return None
        
        # 北交所不支持
        if is_bse_code(code):
            logger.warning(f"iTick 不支持北交所: {code}")
            return None
        
        # 频率映射
        freq_map = {
            "1min": "1m",
            "5min": "5m",
            "15min": "15m",
            "30min": "30m",
            "60min": "60m",
        }
        
        minute_freq = freq_map.get(freq)
        if not minute_freq:
            logger.warning(f"不支持的频率: {freq}")
            return None
        
        try:
            # 构建请求
            url = f"{self._BASE_URL}/kline"
            params = {
                "symbol": code,
                "period": minute_freq,
                "limit": count,
            }
            
            response = self._session.get(
                url,
                params=params,
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if not data or not isinstance(data, list):
                logger.warning(f"iTick 分钟K线返回空数据: {code} {freq}")
                return None
            
            # 转换为 DataFrame
            df = pd.DataFrame(data)
            df = normalize_columns(df)
            
            return df
        
        except Exception as e:
            logger.error(f"iTick 分钟K线失败 {code}: {e}")
            return None
