# -*- coding: utf-8 -*-
"""
交易日历模块 - 离线实现

使用 tj-calendar 实现零网络依赖的交易日历
覆盖范围：1990-12-19 至 2035-12-31
支持市场：SSE/SZSE（沪深）、BSE（北交所，2021-11-15 起）
"""

import logging
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)


class CalendarRangeError(Exception):
    """交易日历范围错误"""
    pass


class TradingCalendar:
    """交易日历（离线实现）"""
    
    def __init__(self):
        """初始化交易日历"""
        self._calendar_module = None
    
    def _get_calendar_module(self):
        """延迟加载 tj-calendar 模块"""
        if self._calendar_module is None:
            try:
                from tj_calendar import is_trade_day, next_trade_day, trade_days_between
                self._calendar_module = {
                    'is_trade_day': is_trade_day,
                    'next_trade_day': next_trade_day,
                    'trade_days_between': trade_days_between,
                }
            except ImportError as e:
                raise ImportError(
                    "tj-calendar 未安装，请执行: pip install tj-calendar"
                ) from e
        return self._calendar_module
    
    def get_calendar(self, start: str, end: str) -> List[str]:
        """
        返回区间内交易日列表
        
        Args:
            start: 开始日期，格式 YYYY-MM-DD
            end: 结束日期，格式 YYYY-MM-DD
            
        Returns:
            交易日列表，格式 ['YYYY-MM-DD', ...]
            
        Raises:
            CalendarRangeError: 超出覆盖范围
        """
        module = self._get_calendar_module()
        
        # 验证日期范围
        self._validate_date_range(start, end)
        
        try:
            return module['trade_days_between'](start, end)
        except Exception as e:
            logger.error(f"获取交易日历失败: {start} ~ {end}, 错误: {e}")
            raise CalendarRangeError(f"获取交易日历失败: {e}") from e
    
    def is_trade_day(self, date: str) -> bool:
        """
        判断是否为交易日
        
        Args:
            date: 日期，格式 YYYY-MM-DD
            
        Returns:
            True 表示交易日，False 表示非交易日
            
        Raises:
            CalendarRangeError: 超出覆盖范围
        """
        module = self._get_calendar_module()
        
        # 验证日期范围
        self._validate_date(date)
        
        try:
            return module['is_trade_day'](date)
        except Exception as e:
            logger.error(f"判断交易日失败: {date}, 错误: {e}")
            raise CalendarRangeError(f"判断交易日失败: {e}") from e
    
    def next_trade_day(self, date: str) -> str:
        """
        返回下一个交易日
        
        Args:
            date: 日期，格式 YYYY-MM-DD
            
        Returns:
            下一个交易日，格式 YYYY-MM-DD
            
        Raises:
            CalendarRangeError: 超出覆盖范围
        """
        module = self._get_calendar_module()
        
        # 验证日期范围
        self._validate_date(date)
        
        try:
            return module['next_trade_day'](date)
        except Exception as e:
            logger.error(f"获取下一交易日失败: {date}, 错误: {e}")
            raise CalendarRangeError(f"获取下一交易日失败: {e}") from e
    
    def _validate_date(self, date: str) -> None:
        """验证日期格式和范围"""
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError as e:
            raise CalendarRangeError(f"日期格式错误: {date}，应为 YYYY-MM-DD") from e
        
        # 覆盖范围：1990-12-19 至 2035-12-31
        if dt.year < 1990 or (dt.year == 1990 and dt.month < 12) or (dt.year == 1990 and dt.month == 12 and dt.day < 19):
            raise CalendarRangeError(f"日期 {date} 超出覆盖范围（最早 1990-12-19）")
        
        if dt.year > 2035:
            raise CalendarRangeError(f"日期 {date} 超出覆盖范围（最晚 2035-12-31）")
    
    def _validate_date_range(self, start: str, end: str) -> None:
        """验证日期区间"""
        self._validate_date(start)
        self._validate_date(end)
        
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
            end_dt = datetime.strptime(end, "%Y-%m-%d")
            
            if start_dt > end_dt:
                raise CalendarRangeError(f"开始日期 {start} 晚于结束日期 {end}")
        except ValueError as e:
            raise CalendarRangeError(f"日期格式错误: {e}") from e


# 全局单例
_calendar_instance = None


def _get_calendar_instance() -> TradingCalendar:
    """获取交易日历单例"""
    global _calendar_instance
    if _calendar_instance is None:
        _calendar_instance = TradingCalendar()
    return _calendar_instance


# 便捷函数
def get_calendar(start: str, end: str) -> List[str]:
    """返回区间内交易日列表"""
    return _get_calendar_instance().get_calendar(start, end)


def is_trade_day(date: str) -> bool:
    """判断是否为交易日"""
    return _get_calendar_instance().is_trade_day(date)


def next_trade_day(date: str) -> str:
    """返回下一个交易日"""
    return _get_calendar_instance().next_trade_day(date)
