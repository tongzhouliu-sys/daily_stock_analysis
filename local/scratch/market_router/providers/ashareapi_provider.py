# -*- coding: utf-8 -*-
"""
S4: ashareapi 提供者

接口：ashareapi 库
覆盖范围：实时快照、日/周/月/季/年及分钟K线
频率限制：遇 429 退避 ≥ 2s，连续 3 次 429 则本批次跳过
北交所：需实测
数据口径：日线及以上前复权；分钟线不复权
"""

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class AshareapiProvider(BaseProvider):
    """ashareapi 提供者"""
    
    provider_id = "S4_ashareapi"
    supports_bse = False  # 北交所支持待实测
    
    def __init__(self):
        self._api = None
        self._consecutive_429 = 0
    
    def _get_api(self):
        """延迟加载 ashareapi"""
        if self._api is None:
            try:
                from ashareapi import AShareAPI
                self._api = AShareAPI()
            except ImportError as e:
                logger.error(f"ashareapi 未安装: {e}")
                raise ImportError("ashareapi 未安装，请执行: pip install 'ashareapi[pandas]'")
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
        
        # 检查连续 429 次数
        if self._consecutive_429 >= 3:
            logger.warning(f"ashareapi 连续 {self._consecutive_429} 次 429，跳过")
            return {code: None for code in codes}
        
        api = self._get_api()
        
        for code in codes:
            try:
                # 获取实时快照
                data = api.quote(code)
                
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
                    self._consecutive_429 = 0  # 重置
                else:
                    result[code] = None
            
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "rate limit" in error_msg.lower():
                    self._consecutive_429 += 1
                    logger.warning(f"ashareapi 429 错误 {code}: {e}")
                    # 退避
                    time.sleep(2)
                else:
                    logger.error(f"ashareapi 实时快照失败 {code}: {e}")
                
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
        # 检查连续 429 次数
        if self._consecutive_429 >= 3:
            logger.warning(f"ashareapi 连续 {self._consecutive_429} 次 429，跳过")
            return None
        
        api = self._get_api()
        
        try:
            # 获取日K线
            if count:
                data = api.kline(code, period="d", count=count)
            else:
                # 估算条数
                if start and end:
                    from datetime import datetime
                    try:
                        start_dt = datetime.strptime(start, "%Y-%m-%d")
                        end_dt = datetime.strptime(end, "%Y-%m-%d")
                        days = (end_dt - start_dt).days
                        count = int(days * 0.7)
                    except Exception:
                        count = 100
                else:
                    count = 100
                
                data = api.kline(code, period="d", count=count)
            
            if data is None or (isinstance(data, pd.DataFrame) and data.empty):
                logger.warning(f"ashareapi 日K线返回空数据: {code}")
                return None
            
            # 转换为 DataFrame
            if isinstance(data, pd.DataFrame):
                df = normalize_columns(data)
                
                # 过滤日期范围
                if start and end:
                    df = df[(df["date"] >= start) & (df["date"] <= end)]
                
                self._consecutive_429 = 0  # 重置
                return df
            
            return None
        
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate limit" in error_msg.lower():
                self._consecutive_429 += 1
                logger.warning(f"ashareapi 429 错误 {code}: {e}")
                time.sleep(2)
            else:
                logger.error(f"ashareapi 日K线失败 {code}: {e}")
            
            return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """获取分钟K线（付费端点）"""
        # 检查连续 429 次数
        if self._consecutive_429 >= 3:
            logger.warning(f"ashareapi 连续 {self._consecutive_429} 次 429，跳过")
            return None
        
        api = self._get_api()
        
        # 频率映射
        freq_map = {
            "1min": "m1",
            "5min": "m5",
            "15min": "m15",
            "30min": "m30",
            "60min": "m60",
        }
        
        period = freq_map.get(freq)
        if not period:
            logger.warning(f"不支持的频率: {freq}")
            return None
        
        try:
            # 获取分钟K线
            data = api.kline(code, period=period, count=count)
            
            if data is None or (isinstance(data, pd.DataFrame) and data.empty):
                logger.warning(f"ashareapi 分钟K线返回空数据: {code} {freq}")
                return None
            
            # 转换为 DataFrame
            if isinstance(data, pd.DataFrame):
                df = normalize_columns(data)
                self._consecutive_429 = 0  # 重置
                return df
            
            return None
        
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate limit" in error_msg.lower():
                self._consecutive_429 += 1
                logger.warning(f"ashareapi 429 错误 {code}: {e}")
                time.sleep(2)
            else:
                logger.error(f"ashareapi 分钟K线失败 {code}: {e}")
            
            return None
