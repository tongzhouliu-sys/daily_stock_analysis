# -*- coding: utf-8 -*-
"""
S2: 腾讯K线提供者

接口：
- 日线：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
- 分钟线：https://web.ifzq.gtimg.cn/appstock/app/kline/mkline
覆盖范围：沪深个股日/周/月及 1/5/15/30/60 分钟
频率限制：单次返回上限 500 根
北交所：不支持
数据口径：日线及以上前复权；分钟线不复权
"""

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class TencentKlineProvider(BaseProvider):
    """腾讯K线提供者"""
    
    provider_id = "S2_tencent_kline"
    supports_bse = False  # 不支持北交所
    
    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _MINUTE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/kline/mkline"
    _HTTP_TIMEOUT_SECONDS = 8
    
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        })
    
    def _to_tencent_symbol(self, code: str) -> str:
        """转换为腾讯格式的代码"""
        raw_code = code.strip().upper()
        
        # 去除前缀
        if raw_code.startswith(("SH", "SS")):
            code_num = raw_code[2:]
            return f"sh{code_num}"
        if raw_code.startswith("SZ"):
            code_num = raw_code[2:]
            return f"sz{code_num}"
        
        # 6位数字代码
        if len(code) == 6 and code.isdigit():
            if code.startswith(("6", "5", "9")):
                return f"sh{code}"
            elif code.startswith(("0", "1", "2", "3")):
                return f"sz{code}"
        
        return ""
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """腾讯K线不支持实时快照"""
        return {code: None for code in codes}
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取日K线"""
        # 北交所不支持
        if is_bse_code(code):
            logger.warning(f"腾讯K线不支持北交所: {code}")
            return None
        
        symbol = self._to_tencent_symbol(code)
        if not symbol:
            logger.warning(f"无法转换代码: {code}")
            return None
        
        # 计算请求参数
        if count:
            lookback = count
        elif start and end:
            # 估算交易日数
            from datetime import datetime
            try:
                start_dt = datetime.strptime(start, "%Y-%m-%d")
                end_dt = datetime.strptime(end, "%Y-%m-%d")
                days = (end_dt - start_dt).days
                lookback = int(days * 0.7)  # 交易日约占 70%
            except Exception:
                lookback = 100
        else:
            lookback = 100
        
        try:
            # 构建请求参数
            params = {
                "param": f"{symbol},day,,{lookback},qfq",
            }
            
            response = self._session.get(
                self._KLINE_ENDPOINT,
                params=params,
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            
            data = response.json()
            
            # 解析响应
            kline_data = data.get("data", {}).get(symbol, {})
            day_data = kline_data.get("qfqday", []) or kline_data.get("day", [])
            
            if not day_data:
                logger.warning(f"腾讯K线返回空数据: {code}")
                return None
            
            # 转换为 DataFrame
            rows = []
            for item in day_data:
                if len(item) >= 6:
                    rows.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5]) * 100,  # 手 -> 股
                        "amount": float(item[6]) if len(item) > 6 else None,
                    })
            
            df = pd.DataFrame(rows)
            df = normalize_columns(df)
            
            # 过滤日期范围
            if start and end:
                df = df[(df["date"] >= start) & (df["date"] <= end)]
            
            return df
        
        except Exception as e:
            logger.error(f"腾讯K线请求失败 {code}: {e}")
            return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """获取分钟K线"""
        # 北交所不支持
        if is_bse_code(code):
            logger.warning(f"腾讯K线不支持北交所: {code}")
            return None
        
        symbol = self._to_tencent_symbol(code)
        if not symbol:
            logger.warning(f"无法转换代码: {code}")
            return None
        
        # 频率映射
        freq_map = {
            "1min": "m1",
            "5min": "m5",
            "15min": "m15",
            "30min": "m30",
            "60min": "m60",
        }
        
        minute_type = freq_map.get(freq)
        if not minute_type:
            logger.warning(f"不支持的频率: {freq}")
            return None
        
        try:
            # 构建请求参数
            params = {
                "param": f"{symbol},{minute_type},,{count}",
            }
            
            response = self._session.get(
                self._MINUTE_ENDPOINT,
                params=params,
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            
            data = response.json()
            
            # 解析响应
            kline_data = data.get("data", {}).get(symbol, {})
            minute_data = kline_data.get(minute_type, [])
            
            if not minute_data:
                logger.warning(f"腾讯分钟K线返回空数据: {code} {freq}")
                return None
            
            # 转换为 DataFrame
            rows = []
            for item in minute_data:
                if len(item) >= 6:
                    rows.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5]) * 100,  # 手 -> 股
                    })
            
            df = pd.DataFrame(rows)
            df = normalize_columns(df)
            
            return df
        
        except Exception as e:
            logger.error(f"腾讯分钟K线请求失败 {code}: {e}")
            return None
