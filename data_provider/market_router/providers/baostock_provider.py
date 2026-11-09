# -*- coding: utf-8 -*-
"""
Baostock 历史数据提供者

接口：baostock 库
覆盖范围：历史日线/分钟线
频率限制：每日 ≤ 50,000 次
北交所：不支持
使用模式：仅用于历史日线/分钟线的本地缓存填充，不进入实时降级链
"""

import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BaseProvider, is_bse_code, normalize_columns

logger = logging.getLogger(__name__)


class BaostockProvider(BaseProvider):
    """Baostock 提供者"""
    
    provider_id = "baostock"
    supports_bse = False  # 不支持北交所
    
    def __init__(self):
        self._bs = None
    
    def _get_baostock(self):
        """延迟加载 baostock"""
        if self._bs is None:
            try:
                import baostock as bs
                self._bs = bs
            except ImportError as e:
                logger.error(f"baostock 未安装: {e}")
                raise ImportError("baostock 未安装，请执行: pip install baostock")
        return self._bs
    
    def is_available(self) -> bool:
        """检查是否可用"""
        try:
            self._get_baostock()
            return True
        except ImportError:
            return False
    
    @contextmanager
    def _baostock_session(self):
        """Baostock 连接上下文管理器"""
        bs = self._get_baostock()
        
        try:
            # 登录
            lg = bs.login()
            if lg.error_code != '0':
                raise Exception(f"Baostock 登录失败: {lg.error_msg}")
            
            yield bs
            
        finally:
            # 登出
            try:
                bs.logout()
            except Exception as e:
                logger.warning(f"Baostock 登出失败: {e}")
    
    def get_realtime(self, codes: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Baostock 不支持实时快照"""
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
            logger.warning(f"Baostock 不支持北交所: {code}")
            return None
        
        bs = self._get_baostock()
        
        # 转换代码格式
        bs_code = self._to_baostock_code(code)
        if not bs_code:
            logger.warning(f"无法转换代码: {code}")
            return None
        
        try:
            with self._baostock_session() as bs_session:
                # 获取日K线
                if start and end:
                    rs = bs_session.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount",
                        start_date=start,
                        end_date=end,
                        frequency="d",
                        adjustflag="3",  # 不复权
                    )
                elif count:
                    # Baostock 不支持 count，需要估算日期范围
                    from datetime import datetime, timedelta
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=int(count * 1.5))
                    rs = bs_session.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount",
                        start_date=start_date.strftime("%Y-%m-%d"),
                        end_date=end_date.strftime("%Y-%m-%d"),
                        frequency="d",
                        adjustflag="3",
                    )
                else:
                    # 默认获取最近 100 条
                    from datetime import datetime, timedelta
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=150)
                    rs = bs_session.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount",
                        start_date=start_date.strftime("%Y-%m-%d"),
                        end_date=end_date.strftime("%Y-%m-%d"),
                        frequency="d",
                        adjustflag="3",
                    )
                
                if rs.error_code != '0':
                    logger.error(f"Baostock 查询失败: {rs.error_msg}")
                    return None
                
                # 转换为 DataFrame
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                
                if not data_list:
                    logger.warning(f"Baostock 日K线返回空数据: {code}")
                    return None
                
                df = pd.DataFrame(data_list, columns=rs.fields)
                df = normalize_columns(df)
                
                # 过滤日期范围
                if start and end:
                    df = df[(df["date"] >= start) & (df["date"] <= end)]
                
                # 限制条数
                if count:
                    df = df.tail(count)
                
                return df
        
        except Exception as e:
            logger.error(f"Baostock 日K线失败 {code}: {e}")
            return None
    
    def get_minute(self, code: str, freq: str, count: int) -> Optional[pd.DataFrame]:
        """获取分钟K线"""
        # 北交所不支持
        if is_bse_code(code):
            logger.warning(f"Baostock 不支持北交所: {code}")
            return None
        
        bs = self._get_baostock()
        
        # 转换代码格式
        bs_code = self._to_baostock_code(code)
        if not bs_code:
            logger.warning(f"无法转换代码: {code}")
            return None
        
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
            with self._baostock_session() as bs_session:
                # 获取分钟K线
                from datetime import datetime, timedelta
                end_date = datetime.now()
                start_date = end_date - timedelta(days=5)  # 分钟线只获取最近 5 天
                
                rs = bs_session.query_history_k_data_plus(
                    bs_code,
                    "date,time,open,high,low,close,volume,amount",
                    start_date=start_date.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d"),
                    frequency=minute_freq,
                    adjustflag="3",
                )
                
                if rs.error_code != '0':
                    logger.error(f"Baostock 查询失败: {rs.error_msg}")
                    return None
                
                # 转换为 DataFrame
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                
                if not data_list:
                    logger.warning(f"Baostock 分钟K线返回空数据: {code} {freq}")
                    return None
                
                df = pd.DataFrame(data_list, columns=rs.fields)
                
                # 合并日期和时间
                if "date" in df.columns and "time" in df.columns:
                    df["date"] = df["date"] + " " + df["time"]
                    df = df.drop(columns=["time"])
                
                df = normalize_columns(df)
                
                # 限制条数
                if count:
                    df = df.tail(count)
                
                return df
        
        except Exception as e:
            logger.error(f"Baostock 分钟K线失败 {code}: {e}")
            return None
    
    def _to_baostock_code(self, code: str) -> str:
        """转换为 Baostock 格式的代码"""
        raw_code = code.strip().upper()
        
        # 去除前缀
        if raw_code.startswith(("SH", "SS")):
            code_num = raw_code[2:]
            return f"sh.{code_num}"
        if raw_code.startswith("SZ"):
            code_num = raw_code[2:]
            return f"sz.{code_num}"
        
        # 6位数字代码
        if len(code) == 6 and code.isdigit():
            if code.startswith(("6", "5", "9")):
                return f"sh.{code}"
            elif code.startswith(("0", "1", "2", "3")):
                return f"sz.{code}"
        
        return ""
