# -*- coding: utf-8 -*-
"""
S3: 新浪实时快照提供者

接口：https://hq.sinajs.cn/list={code}
覆盖范围：A股实时快照、指数
频率限制：单 IP 日请求 ≤ 1050 次，并发 ≤ 5 QPS
北交所：需实测
"""

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class SinaRealtimeProvider(BaseProvider):
    """新浪实时快照提供者"""
    
    provider_id = "S3_sina"
    supports_bse = False  # 北交所支持待实测，默认不支持
    
    _QUOTE_ENDPOINT = "https://hq.sinajs.cn/list="
    _HTTP_TIMEOUT_SECONDS = 8
    
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn",
        })
    
    def _to_sina_symbol(self, code: str) -> str:
        """转换为新浪格式的代码"""
        raw_code = code.strip().upper()
        
        # 去除前缀
        if raw_code.startswith(("SH", "SS")):
            code_num = raw_code[2:]
            return f"sh{code_num}"
        if raw_code.startswith("SZ"):
            code_num = raw_code[2:]
            return f"sz{code_num}"
        if raw_code.startswith("BJ"):
            code_num = raw_code[2:]
            return f"bj{code_num}"
        
        # 6位数字代码
        if len(code) == 6 and code.isdigit():
            if code.startswith(("6", "5", "9")):
                return f"sh{code}"
            elif code.startswith(("0", "1", "2", "3")):
                return f"sz{code}"
            elif code.startswith(("8", "4")):
                return f"bj{code}"
        
        return ""
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """批量获取实时快照"""
        result = {}
        
        # 批量查询
        symbols = []
        code_map = {}
        for code in codes:
            symbol = self._to_sina_symbol(code)
            if symbol:
                symbols.append(symbol)
                code_map[symbol] = code
        
        if not symbols:
            return {code: None for code in codes}
        
        try:
            # 批量请求
            query = ",".join(symbols)
            response = self._session.get(
                f"{self._QUOTE_ENDPOINT}{query}",
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            response.encoding = "gbk"
            
            # 解析响应
            content = response.text.strip()
            lines = content.split("\n")
            
            for line in lines:
                if not line.strip():
                    continue
                
                # 格式：var hq_str_sh600519="...";
                import re
                match = re.match(r'var hq_str_(\w+)="(.*)";', line)
                if not match:
                    continue
                
                symbol = match.group(1)
                data_str = match.group(2)
                
                if not data_str:
                    continue
                
                fields = data_str.split(",")
                if len(fields) < 32:
                    continue
                
                code = code_map.get(symbol)
                if not code:
                    continue
                
                try:
                    # 解析字段
                    result[code] = {
                        "open": float(fields[1]) if fields[1] else None,
                        "high": float(fields[4]) if fields[4] else None,
                        "low": float(fields[5]) if fields[5] else None,
                        "close": float(fields[3]) if fields[3] else None,  # 最新价
                        "volume": int(float(fields[8]) / 100) if fields[8] else None,  # 股 -> 手
                        "amount": float(fields[9]) if fields[9] else None,  # 成交额（元）
                        "ts": f"{fields[30]} {fields[31]}" if len(fields) > 31 else None,
                    }
                except (ValueError, IndexError) as e:
                    logger.warning(f"解析新浪实时数据失败 {code}: {e}")
                    result[code] = None
        
        except Exception as e:
            logger.error(f"新浪实时快照请求失败: {e}")
            return {code: None for code in codes}
        
        # 填充未返回的代码
        for code in codes:
            if code not in result:
                result[code] = None
        
        return result
    
    def get_daily(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """新浪实时快照不支持日K线"""
        return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """新浪实时快照不支持分钟K线"""
        return None
