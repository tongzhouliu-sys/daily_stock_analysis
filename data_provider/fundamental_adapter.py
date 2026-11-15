# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# T28: 单接口限时。基本面聚合一次会串行打 6 组不同接口，任何一组卡住都会
# 吃掉整轮预算并把已取到的块一起作废。这里给每次 AkShare 调用一个独立
# 超时窗口，超时后放弃该次调用（线程为 daemon，不影响主流程返回），
# 让其它块继续按已有数据返回。
_FUNDAMENTAL_MIN_CALL_TIMEOUT_SECONDS = 1.0
_FUNDAMENTAL_CALL_TIMEOUT_RATIO = 0.35


# T29: 基本面并行块共用的按数据源限速器。
# 基本面 4 个块改并行之后会同时调用 akshare，而这些调用不经过
# DataFetcherManager 的单实例调用锁，也不走 fetcher 的 ``_enforce_rate_limit``，
# 单只股票对上游的并发量约为串行实现的 4 倍。这里按数据源共享一个模块级限速器：
# 同一数据源的请求保持与 fetcher 一致的最小间隔（``akshare_sleep_min``），
# 同时在途请求数不超过 ``_FUNDAMENTAL_SOURCE_MAX_CONCURRENCY``。
_FUNDAMENTAL_SOURCE_MAX_CONCURRENCY = 2
_FUNDAMENTAL_DEFAULT_SOURCE = "akshare"


def _resolve_source_min_interval() -> float:
    """读取与 fetcher 一致的最小请求间隔；读不到时按 0 处理（不额外限速）。"""
    try:
        from src.config import get_config

        return max(0.0, float(getattr(get_config(), "akshare_sleep_min", 0.0) or 0.0))
    except Exception:  # noqa: BLE001 - 配置不可用时不影响取数
        return 0.0


class _SourceRateLimiter:
    """按数据源共享的最小间隔 + 并发上限限速器。

    预约（记账）在短锁内完成，睡眠发生在限速器自己的信号量之外，
    因此等待中的调用不会占住并发槽位。
    """

    def __init__(self, max_concurrency: int = _FUNDAMENTAL_SOURCE_MAX_CONCURRENCY) -> None:
        self._max_concurrency = max(1, int(max_concurrency))
        self._slots = threading.BoundedSemaphore(self._max_concurrency)
        self._accounting_lock = threading.Lock()
        self._last_request_time: Optional[float] = None
        self._min_interval: Optional[float] = None

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def min_interval(self) -> float:
        if self._min_interval is None:
            self._min_interval = _resolve_source_min_interval()
        return self._min_interval

    @min_interval.setter
    def min_interval(self, value: float) -> None:
        self._min_interval = max(0.0, float(value))

    def acquire(self, max_wait: Optional[float] = None) -> bool:
        """预约并等待槽位。

        Args:
            max_wait: 允许等待的上限（秒）。``None`` 表示不限。超出上限时不预约、
                不等待，返回 ``False``，由调用方按"预算耗尽"处理——块级预算仍然
                是硬约束，限速不能把一次聚合拖过预算。

        Returns:
            ``True`` 表示已拿到槽位（调用方必须 ``release``）。
        """
        with self._accounting_lock:
            now = time.time()
            interval = self.min_interval
            delay = 0.0
            if interval > 0 and self._last_request_time is not None:
                delay = max(0.0, interval - (now - self._last_request_time))
            if max_wait is not None and delay > max_wait:
                return False
            if interval > 0:
                self._last_request_time = now + delay
        if delay > 0:
            time.sleep(delay)
        self._slots.acquire()
        return True

    def release(self) -> None:
        self._slots.release()


_SOURCE_RATE_LIMITERS: Dict[str, _SourceRateLimiter] = {}
_SOURCE_RATE_LIMITERS_LOCK = threading.Lock()


def _get_source_rate_limiter(source: str) -> _SourceRateLimiter:
    with _SOURCE_RATE_LIMITERS_LOCK:
        limiter = _SOURCE_RATE_LIMITERS.get(source)
        if limiter is None:
            limiter = _SourceRateLimiter()
            _SOURCE_RATE_LIMITERS[source] = limiter
        return limiter


def _clear_source_rate_limiters_for_tests() -> None:
    """测试用：清空模块级限速器，避免测试之间互相污染最小间隔。"""
    with _SOURCE_RATE_LIMITERS_LOCK:
        _SOURCE_RATE_LIMITERS.clear()


# T28: 全市场表进程内按天缓存。
# 板块资金流排名、龙虎榜、业绩预告/快报、机构持股这类整表接口在同一个交易日内
# 内容稳定，但一次分析里可能在多个 block / 多只股票上被重复拉取。这里按
# 「接口名 + 参数 + 自然日」缓存一份副本，跨日自动失效。
_DAILY_CACHEABLE_AKSHARE_INTERFACES = {
    "stock_sector_fund_flow_rank",
    "stock_lhb_stock_statistic_em",
    "stock_lhb_detail_em",
    "stock_lhb_jgmmtj_em",
    "stock_yjyg_em",
    "stock_yjkb_em",
    "stock_institute_hold",
}
_DAILY_TABLE_CACHE_MAX_ENTRIES = 64
_daily_table_cache: Dict[Tuple[str, str], Tuple[str, Any]] = {}
_daily_table_cache_lock = threading.Lock()


def _daily_cache_key(func_name: str, kwargs: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if func_name not in _DAILY_CACHEABLE_AKSHARE_INTERFACES:
        return None
    try:
        token = repr(sorted((str(k), repr(v)) for k, v in kwargs.items()))
    except Exception:
        token = repr(kwargs)
    return func_name, token


def _load_daily_table(key: Optional[Tuple[str, str]]):
    if key is None:
        return None
    today = datetime.now().date().isoformat()
    with _daily_table_cache_lock:
        item = _daily_table_cache.get(key)
        if not item:
            return None
        day, cached = item
        if day != today:
            _daily_table_cache.pop(key, None)
            return None
        try:
            return cached.copy()
        except Exception:
            return cached


def _store_daily_table(key: Optional[Tuple[str, str]], df: Any) -> None:
    if key is None:
        return
    today = datetime.now().date().isoformat()
    with _daily_table_cache_lock:
        if len(_daily_table_cache) >= _DAILY_TABLE_CACHE_MAX_ENTRIES:
            stale = [k for k, (day, _) in _daily_table_cache.items() if day != today]
            for k in stale:
                _daily_table_cache.pop(k, None)
            if len(_daily_table_cache) >= _DAILY_TABLE_CACHE_MAX_ENTRIES:
                _daily_table_cache.clear()
        try:
            _daily_table_cache[key] = (today, df.copy())
        except Exception:
            _daily_table_cache[key] = (today, df)


def _clear_daily_table_cache() -> None:
    """测试用：清空进程内全市场表缓存。"""
    with _daily_table_cache_lock:
        _daily_table_cache.clear()


def _resolve_call_timeout(
    deadline: Optional[float],
    budget_seconds: Optional[float],
) -> Optional[float]:
    """按剩余预算推导单次接口超时；无预算约束时返回 None（不限时）。"""
    if budget_seconds is None or budget_seconds <= 0:
        return None
    remaining = budget_seconds if deadline is None else max(0.0, deadline - time.monotonic())
    if remaining <= 0:
        return 0.0
    cap = max(
        _FUNDAMENTAL_MIN_CALL_TIMEOUT_SECONDS,
        float(budget_seconds) * _FUNDAMENTAL_CALL_TIMEOUT_RATIO,
    )
    return min(cap, remaining)


def _call_with_deadline(
    func: Callable[..., Any],
    kwargs: Dict[str, Any],
    timeout: Optional[float],
    label: str,
) -> Any:
    """在独立线程里执行一次接口调用，超过 timeout 即放弃等待。"""
    if timeout is None:
        return func(**kwargs)
    if timeout <= 0:
        raise TimeoutError(f"{label} 无剩余预算")

    box: Dict[str, Any] = {}
    errors: Dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            box["value"] = func(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - 原样抛回调用方
            errors["value"] = exc

    worker = threading.Thread(
        target=_runner,
        name=f"fundamental-call-{label}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"{label} 调用超过 {timeout:g}s，已放弃等待")
    if "value" in errors:
        raise errors["value"]
    return box.get("value")


_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}


def _sum_recent_net_inflow(
    stock_df: "pd.DataFrame",
    dates: "pd.Series",
    latest_net_inflow: float,
) -> Tuple[Optional[float], Optional[float]]:
    """T29: 用同一份资金流历史求近 5/10 个交易日主力净流入之和。

    返回 ``(inflow_5d, inflow_10d)``；样本不足对应窗口时返回 None，
    不用部分样本冒充完整窗口。单位与接口一致（元）。
    """
    if "主力净流入-净额" not in stock_df.columns:
        return None, None
    try:
        work = pd.DataFrame(
            {
                "date": dates,
                "net": pd.to_numeric(stock_df["主力净流入-净额"], errors="coerce"),
            }
        )
        work = work.loc[work["date"].notna()].drop_duplicates(subset="date")
        work = work.sort_values("date", ascending=False)
    except Exception:
        return None, None
    if work.empty:
        return None, None
    # 最新一行若净额缺失，用调用方已解析好的值补齐；不能先删掉这一行再覆盖下一行。
    if math.isfinite(latest_net_inflow) and pd.isna(work.iloc[0]["net"]):
        work.iloc[0, work.columns.get_loc("net")] = latest_net_inflow

    def _window_sum(days: int) -> Optional[float]:
        window = work.head(days)
        if len(window) < days:
            return None
        # 窗口内缺任何一天的净额都不算完整窗口，避免用部分样本冒充
        if window["net"].isna().any():
            return None
        total = float(window["net"].sum())
        return total if math.isfinite(total) else None

    return _window_sum(5), _window_sum(10)


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except Exception:
        return None


def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s


def _pick_by_keywords(row: pd.Series, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)


def _filter_rows_by_code(
    df: pd.DataFrame, stock_code: str, *, require_code: bool = False,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))]
    if not code_cols:
        if require_code:
            raise ValueError("Market-wide result is missing a stock code column")
        return df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()


def _recent_report_dates(now: Optional[datetime] = None) -> List[str]:
    """Bound report-period fallback to the two most recently completed quarters."""
    now = now or datetime.now()
    quarter_start = datetime(now.year, (now.month - 1) // 3 * 3 + 1, 1)
    dates = []
    for _ in range(2):
        end = quarter_start - timedelta(days=1)
        dates.append(end.strftime("%Y%m%d"))
        quarter_start = datetime(end.year, (end.month - 1) // 3 * 3 + 1, 1)
    return dates


def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _earnings_forecast_summary(row: pd.Series) -> str:
    """Read disclosure text by exact column, never an announcement date/rate."""
    for column in ("业绩变动", "业绩变动原因", "预告类型", "预告", "摘要"):
        value = row.get(column)
        if isinstance(value, str) and value.strip().lower() not in ("", "-", "nan", "none", "nat", "<na>"):
            return value.strip()[:200]
    return ""


def _earnings_quick_summary(row: pd.Series) -> str:
    """Summarize AkShare's numeric quick-report columns, excluding metadata."""
    fields = (
        ("营业收入-营业收入", "营业收入", "元"),
        ("营业收入-同比增长", "营收同比", "%"),
        ("净利润-净利润", "净利润", "元"),
        ("净利润-同比增长", "净利润同比", "%"),
        ("每股收益", "每股收益", "元"),
        ("净资产收益率", "净资产收益率", "%"),
    )
    parts = []
    for column, label, unit in fields:
        value = _safe_float(row.get(column))
        if value is not None and math.isfinite(value):
            parts.append(f"{label}{value:.12g}{unit}")
    return "；".join(parts)[:200]


def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[:max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]


def _financial_abstract_row(df: pd.DataFrame) -> Optional[pd.Series]:
    """Normalize Sina's metric rows and report-period columns into one row."""
    periods = [c for c in df.columns if re.fullmatch(r"\d{8}", str(c)) and _normalize_report_date(str(c))]
    if not periods:
        return None
    period = max(periods, key=str)
    metrics = df.drop_duplicates(subset=["指标"]).set_index("指标")[period]
    # Include AkShare's unit-bearing labels; exact aliases keep percentages
    # separate from amounts and preserve qualifiers such as weighted ROE.
    aliases = {
        "营业总收入": ["营业总收入", "营业收入", "营业总收入(元)"],
        "归母净利润": ["归母净利润", "归属于母公司股东的净利润", "归属净利润(元)"],
        "经营活动产生的现金流量净额": [
            "经营活动产生的现金流量净额", "经营活动产生的现金流量净额(元)", "经营现金流量净额(元)", "经营现金流量净额",
        ],
        "营业收入同比": ["营业总收入同比增长率", "营业收入同比增长率", "营业总收入同比增长(%)", "营业总收入增长率"],
        "净利润同比": ["净利润同比增长率", "归母净利润同比增长率", "归属净利润同比增长(%)", "归属母公司净利润增长率"],
        "净资产收益率": ["净资产收益率", "净资产收益率(加权)", "净资产收益率(%)", "净资产收益率(加权)(%)", "净资产收益率(ROE)"],
        "毛利率": ["销售毛利率", "毛利率", "销售毛利率(%)"],
    }
    values = {"报告期": str(period)}
    for key, names in aliases.items():
        for name in names:
            value = _safe_float(metrics.get(name))
            if value is not None and math.isfinite(value):
                values[key] = value
                break
    return pd.Series(values)


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
        *,
        stock_code: Optional[str] = None,
        deadline: Optional[float] = None,
        budget_seconds: Optional[float] = None,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            cache_key = _daily_cache_key(func_name, kwargs)
            cached_df = _load_daily_table(cache_key)
            if cached_df is not None:
                if stock_code is not None:
                    cached_df = _filter_rows_by_code(cached_df, stock_code, require_code=True)
                if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                    return cached_df, f"{func_name}:daily_cache", errors
            call_timeout = _resolve_call_timeout(deadline, budget_seconds)
            if call_timeout is not None and call_timeout <= 0:
                errors.append(f"{func_name}:budget_exhausted")
                break
            # T29: 同一数据源的 akshare 调用共用模块级限速器，
            # 保证并行块之间的请求间隔与并发上限。槽位等待不能突破块级预算：
            # 等不到槽位就按预算耗尽跳过，避免限速把一次聚合拖过预算。
            limiter = _get_source_rate_limiter(_FUNDAMENTAL_DEFAULT_SOURCE)
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if not limiter.acquire(max_wait=remaining):
                errors.append(f"{func_name}:budget_exhausted")
                break
            try:
                call_timeout = _resolve_call_timeout(deadline, budget_seconds)
                if call_timeout is not None and call_timeout <= 0:
                    errors.append(f"{func_name}:budget_exhausted")
                    break
                df = _call_with_deadline(fn, dict(kwargs), call_timeout, func_name)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    # 缓存整表原始结果（过滤前），避免把单只股票的过滤结果存成全局表。
                    _store_daily_table(cache_key, df)
                if isinstance(df, pd.DataFrame) and not df.empty and stock_code is not None:
                    # Market-wide endpoints must contain the requested stock.
                    # A nonempty table for other stocks must not stop fallback.
                    df = _filter_rows_by_code(df, stock_code, require_code=True)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
            finally:
                limiter.release()
        return None, None, errors

    def get_fundamental_bundle(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.

        T28: ``budget_seconds`` 是整组聚合的总预算。每个接口有独立的限时窗口，
        预算耗尽后跳过剩余接口但仍返回已取到的块，避免一个慢接口把整轮作废。
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }
        deadline = (
            time.monotonic() + float(budget_seconds)
            if budget_seconds is not None and budget_seconds > 0
            else None
        )

        # Reuse the provider's market mapping after package initialization;
        # importing it at module scope would cycle through data_provider.base.
        from .akshare_fetcher import _to_sina_tx_symbol

        shareholder_symbol = _to_sina_tx_symbol(stock_code)
        stock_code = _normalize_code(stock_code)
        report_dates = _recent_report_dates()

        # Financial indicators
        fin_df, fin_source, fin_errors = self._call_df_candidates([
            ("stock_financial_abstract", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {}),
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(fin_errors)
        if fin_df is not None:
            row = _financial_abstract_row(fin_df) if "指标" in fin_df else _extract_latest_row(fin_df, stock_code)
            if row is not None:
                revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"]))
                roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))
                gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"]))
                report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
                is_wide = "指标" in fin_df
                revenue = _safe_float(
                    row.get("营业总收入") if is_wide else _pick_by_keywords(row, ["营业总收入", "营业收入", "营收"])
                )
                net_profit_parent = _safe_float(
                    row.get("归母净利润") if is_wide else _pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"])
                )
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                )
                growth_payload = {
                    "revenue_yoy": revenue_yoy,
                    "net_profit_yoy": profit_yoy,
                    "roe": roe,
                    "gross_margin": gross_margin,
                }
                financial_report_payload = {
                    "report_date": report_date,
                    "revenue": revenue,
                    "net_profit_parent": net_profit_parent,
                    "operating_cash_flow": operating_cash_flow,
                    "roe": roe,
                }
                if any(v is not None for k, v in financial_report_payload.items() if k != "report_date"):
                    result["earnings"]["financial_report"] = financial_report_payload
                if any(v is not None for v in growth_payload.values()):
                    result["growth"] = growth_payload
                if result["growth"] or result["earnings"].get("financial_report"):
                    result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast
        forecast_df, forecast_source, forecast_errors = self._call_df_candidates([
            ("stock_yjyg_em", {"date": report_date})
            for report_date in report_dates
        ], stock_code=stock_code, deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(forecast_errors)
        if forecast_df is not None:
            row = _extract_latest_row(forecast_df, stock_code)
            if row is not None:
                summary = _earnings_forecast_summary(row)
                if summary:
                    result["earnings"]["forecast_summary"] = summary
                    result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report
        quick_df, quick_source, quick_errors = self._call_df_candidates([
            ("stock_yjkb_em", {"date": report_date})
            for report_date in report_dates
        ], stock_code=stock_code, deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(quick_errors)
        if quick_df is not None:
            row = _extract_latest_row(quick_df, stock_code)
            if row is not None:
                summary = _earnings_quick_summary(row)
                if summary:
                    result["earnings"]["quick_report_summary"] = summary
                    result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        dividend_df, dividend_source, dividend_errors = self._call_df_candidates([
            ("stock_fhps_detail_em", {"symbol": stock_code}),
            ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "分红", "date": ""}),
            ("stock_dividend_cninfo", {"symbol": stock_code}),
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(dividend_errors)
        if dividend_df is not None:
            dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
            if dividend_payload:
                result["earnings"]["dividend"] = dividend_payload
                result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        inst_df, inst_source, inst_errors = self._call_df_candidates([
            ("stock_institute_hold", {
                "symbol": f"{report_date[:4]}{int(report_date[4:6]) // 3}",
            })
            for report_date in report_dates
        ], stock_code=stock_code, deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(inst_errors)
        if inst_df is not None:
            row = _extract_latest_row(inst_df, stock_code)
            if row is not None:
                inst_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "变动", "持股变化"]))
                result["institution"]["institution_holding_change"] = inst_change
                result["source_chain"].append(f"institution:{inst_source}")

        top10_df, top10_source, top10_errors = self._call_df_candidates([
            ("stock_gdfx_top_10_em", {"symbol": shareholder_symbol, "date": report_date})
            for report_date in report_dates
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(top10_errors)
        if top10_df is not None:
            row = _extract_latest_row(top10_df, stock_code)
            if row is not None:
                holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                result["institution"]["top10_holder_change"] = holder_change
                result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_capital_flow(
        self,
        stock_code: str,
        top_n: int = 5,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return stock + sector capital flow.

        T28: 传入 ``budget_seconds`` 时，个股资金流与板块排名的两次接口各自限时，
        预算耗尽后保留已取到的部分。
        """
        deadline = (
            time.monotonic() + float(budget_seconds)
            if budget_seconds is not None and budget_seconds > 0
            else None
        )
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        from .akshare_fetcher import _to_sina_tx_symbol

        market = _to_sina_tx_symbol(stock_code)[:2]
        stock_code = _normalize_code(stock_code)
        stock_df, stock_source, stock_errors = self._call_df_candidates([
            ("stock_individual_fund_flow", {"stock": stock_code, "market": market}),
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(stock_errors)
        if stock_df is not None and "日期" in stock_df:
            dates = pd.to_datetime(stock_df["日期"], errors="coerce")
            if dates.notna().any():
                row = stock_df.loc[dates.idxmax()]
                net_inflow = _safe_float(row.get("主力净流入-净额"))
                if net_inflow is not None and math.isfinite(net_inflow):
                    # T29: 同一份历史数据里已含近 5/10 个交易日的主力净额，
                    # 直接求和，不再把两个窗口写死为 None。
                    inflow_5d, inflow_10d = _sum_recent_net_inflow(
                        stock_df, dates, net_inflow
                    )
                    result["stock_flow"] = {
                        "main_net_inflow": net_inflow,
                        "inflow_5d": inflow_5d,
                        "inflow_10d": inflow_10d,
                    }
                    result["source_chain"].append(f"capital_stock:{stock_source}")

        sector_df, sector_source, sector_errors = self._call_df_candidates([
            ("stock_sector_fund_flow_rank", {}),
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(sector_errors)
        if sector_df is not None:
            name_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None)
            flow_col = "今日主力净流入-净额" if "今日主力净流入-净额" in sector_df else None
            if name_col and flow_col:
                work_df = sector_df[[name_col, flow_col]].copy()
                work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
                work_df = work_df.loc[work_df[flow_col].between(-math.inf, math.inf, inclusive="neither")]
                top_df = work_df.nlargest(top_n, flow_col)
                bottom_df = work_df.nsmallest(top_n, flow_col)
                result["sector_rankings"] = {
                    "top": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()],
                    "bottom": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in bottom_df.iterrows()],
                }
                result["source_chain"].append(f"capital_sector:{sector_source}")

        has_content = bool(result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"])
        if has_content:
            result["status"] = "partial"
        elif result["errors"]:
            # R11: `not_supported` means "there is no callable interface at all".
            # An interface that was called and failed (timeout, upstream error)
            # is a fetch failure; the instrument itself may still publish flow.
            result["status"] = "failed"
        else:
            result["status"] = "not_supported"
        return result

    def get_dragon_tiger_flag(
        self,
        stock_code: str,
        lookback_days: int = 20,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.

        T28: 传入 ``budget_seconds`` 时对候选接口各自限时。
        """
        deadline = (
            time.monotonic() + float(budget_seconds)
            if budget_seconds is not None and budget_seconds > 0
            else None
        )
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        df, source, errors = self._call_df_candidates([
            ("stock_lhb_stock_statistic_em", {}),
            ("stock_lhb_detail_em", {}),
            ("stock_lhb_jgmmtj_em", {}),
        ], deadline=deadline, budget_seconds=budget_seconds)
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next((c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None)
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = max(recent_dates).date().isoformat() if recent_dates else (
            max(parsed_dates).date().isoformat() if parsed_dates else None
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result
