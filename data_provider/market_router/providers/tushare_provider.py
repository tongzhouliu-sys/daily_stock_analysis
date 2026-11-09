# -*- coding: utf-8 -*-
"""
S7: Tushare 提供者（可选，仅注册用户）

接口：tushare 库
覆盖范围：仅不复权日线
频率限制：≤ 50 次/分钟，≤ 8000 次/日
北交所：不支持
数据口径：仅不复权日线
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class TushareProvider(BaseProvider):
    """Tushare 提供者"""
    
    provider_id = "S7_tushare"
    supports_bse = False  # 不支持北交所
    
    def __init__(self):
        self._api = None
        self._token = os.getenv("TUSHARE_TOKEN")
    
    def _get_api(self):
        """延迟加载 tushare"""
        if self._api is None:
            if not self._token:
                raise ValueError("TUSHARE_TOKEN 未配置")
            
            try:
                import tushare as ts
                ts.set_token(self._token)
                self._api = ts.pro_api()
            except ImportError as e:
                logger.error(f"tushare 未安装: {e}")
                raise ImportError("tushare 未安装，请执行: pip install tushare")
        return self._api
    
    def is_available(self) -> bool:
        """检查是否可用"""
        if not self._token:
            return False
        try:
            self._get_api()
            return True
        except (ImportError, ValueError):
            return False
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Tushare 不支持实时快照（在实时链路中禁止使用）"""
        return {code: None for code in codes}
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取日K线（仅不复权）"""
        # 北交所不支持
        if is_bse_code(code):
            logger.warning(f"Tushare 不支持北交所: {code}")
            return None
        
        api = self._get_api()
        
        # 转换代码格式
        ts_code = self._to_tushare_code(code)
        if not ts_code:
            logger.warning(f"无法转换代码: {code}")
            return None
        
        try:
            # 获取日K线
            if start and end:
                data = api.daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            elif count:
                # Tushare 不支持 count，需要估算日期范围
                from datetime import datetime, timedelta
                end_date = datetime.now()
                start_date = end_date - timedelta(days=int(count * 1.5))
                data = api.daily(ts_code=ts_code, start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
            else:
                # 默认获取最近 100 条
                from datetime import datetime, timedelta
                end_date = datetime.now()
                start_date = end_date - timedelta(days=150)
                data = api.daily(ts_code=ts_code, start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
            
            if data is None or (isinstance(data, pd.DataFrame) and data.empty):
                logger.warning(f"Tushare 日K线返回空数据: {code}")
                return None
            
            # 转换为 DataFrame
            if isinstance(data, pd.DataFrame):
                # 转换日期格式
                if "trade_date" in data.columns:
                    data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.strftime("%Y-%m-%d")
                    data = data.rename(columns={"trade_date": "date"})
                
                df = normalize_columns(data)
                
                # 过滤日期范围
                if start and end:
                    df = df[(df["date"] >= start) & (df["date"] <= end)]
                
                # 限制条数
                if count:
                    df = df.tail(count)
                
                return df
            
            return None
        
        except Exception as e:
            logger.error(f"Tushare 日K线失败 {code}: {e}")
            return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """Tushare 不支持分钟K线（120积分档）"""
        return None
    
    def _to_tushare_code(self, code: str) -> str:
        """转换为 Tushare 格式的代码"""
        raw_code = code.strip().upper()
        
        # 去除前缀
        if raw_code.startswith(("SH", "SS")):
            code_num = raw_code[2:]
            return f"{code_num}.SH"
        if raw_code.startswith("SZ"):
            code_num = raw_code[2:]
            return f"{code_num}.SZ"
        
        # 6位数字代码
        if len(code) == 6 and code.isdigit():
            if code.startswith(("6", "5", "9")):
                return f"{code}.SH"
            elif code.startswith(("0", "1", "2", "3")):
                return f"{code}.SZ"
        
        return ""
