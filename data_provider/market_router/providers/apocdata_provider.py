# -*- coding: utf-8 -*-
"""
S6: ApocData 提供者

接口：https://www.apocdata.com/api/blade-dataplatform/open/data/quote
覆盖范围：实时快照
频率限制：免费体验额度匿名可用
北交所：需实测
"""

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class ApocdataProvider(BaseProvider):
    """ApocData 提供者"""
    
    provider_id = "S6_apocdata"
    supports_bse = False  # 北交所支持待实测
    
    _BASE_URL = "https://www.apocdata.com/api/blade-dataplatform/open/data"
    _HTTP_TIMEOUT_SECONDS = 8
    
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0",
        })
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """批量获取实时快照"""
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
                logger.error(f"ApocData 实时快照失败 {code}: {e}")
                result[code] = None
        
        return result
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """ApocData 实时快照不支持日K线"""
        return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """ApocData 实时快照不支持分钟K线"""
        return None
