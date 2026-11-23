# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================

职责：
1. 管理整个分析流程
2. 协调数据获取、存储、搜索、分析、通知等模块
3. 实现并发控制和异常处理
4. 提供股票分析的核心功能
"""

import logging
import inspect
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Dict, Any, Optional, Tuple, Callable

import pandas as pd

from src.config import FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, get_config, Config
from src.core.task_cancellation import TaskCancelledError
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import is_bse_code, normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_price_position_if_needed,
    normalize_chip_structure_availability,
    populate_decision_action_fields,
    stabilize_decision_with_structure,
)
from src.notification import NotificationService, NotificationChannel  # noqa: F401 - re-exported for tests
from src.schemas.decision_action import normalize_decision_action
from src.report_language import (
    get_placeholder_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.search_service import SearchService
from src.analysis_context_pack_prompt import format_analysis_context_pack_prompt_section
from src.analysis_context_pack_overview import render_analysis_context_pack_overview
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY, render_market_phase_summary
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.agent.final_explanation import (
    PipelineActionAdjustment,
    build_pipeline_final_explanation,
    capture_pipeline_action_adjustment,
)
from src.agent.news_evidence import (
    activate_news_evidence_scope,
    get_current_news_evidence,
    reset_news_evidence_scope,
)
from src.services.empty_news import news_evidence_present
from src.services.news_confidence import reconcile_news_confidence_reason
from src.services.analysis_progress import (
    PROGRESS_FUNDAMENTALS,
    PROGRESS_GENERATING,
    PROGRESS_NEWS,
)
from src.phase_decision_guardrail import apply_phase_decision_guardrails
from src.services.daily_market_context import (
    DailyMarketContext,
    DailyMarketContextService,
    format_daily_market_context_prompt_section,
)
from src.services.social_sentiment_service import SocialSentimentService
from src.services.intelligence_service import IntelligenceService
from src.services.market_hotspot_service import MarketHotspotService
from src.services.analysis_context_builder import (
    AnalysisContextBuilder,
    PipelineAnalysisArtifacts,
)
from src.services.market_structure_service import MarketStructureService
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    get_current_diagnostic_context,
    record_history_run,
    record_llm_run,
    record_llm_run_started,
    record_notification_run,
    reset_run_diagnostic_context,
    sanitize_diagnostic_text,
)
from src.services.decision_signal_extractor import (
    extract_and_persist_from_analysis_result,
    resolve_decision_signal_action_fields,
)
from src.services.stock_list_parser import AnalysisTarget, ParseStatus
from src.services.decision_signal_summary import summarize_decision_signal
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import (
    build_market_phase_context,
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from data_provider.us_index_mapping import is_us_stock_code
from bot.models import BotMessage


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index capability matrix — the single authority for which analysis modules
# are skipped for index targets (Story 1.5). Skipping must happen before any
# composite service, snapshot persistence, or the traditional/Agent branch
# split, so the underlying provider calls are never reached.
# ---------------------------------------------------------------------------
INDEX_SKIP_MODULES = frozenset({
    "chip_distribution",
    "fundamental",
    "belong_boards",
    "capital_flow",
    "lhb",
    "corporate_events",
})


def _share_image_payload(result: Any) -> Optional[Dict[str, Any]]:
    """Return structured poster data when the result exposes the real contract."""

    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = to_dict()
    except Exception as exc:
        logger.debug("构建分享图片结构化数据失败，回退 Markdown: %s", exc)
        return None
    return payload if isinstance(payload, dict) and payload else None


def _coerce_context_date(value: Any) -> Optional[date]:
    """Parse a context bar date (str/date/datetime) or return None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()[:10]).date()
        except ValueError:
            return None
    return None


def _supports_explicit_keyword(callable_obj: Any, keyword: str) -> bool:
    """Avoid breaking custom notifier overrides that predate an optional kwarg."""

    try:
        return keyword in inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False


# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()
_DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD = threading.Lock()


def _symbol_scope_lookup_values(code: str, market: str) -> List[str]:
    """Return accepted persisted-intelligence symbol spellings for lookup."""
    raw = str(code or "").strip()
    normalized = normalize_stock_code(raw) if raw else ""
    values: List[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)

    def add_case_variants(value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        add(text)
        add(text.upper())
        add(text.lower())

    add_case_variants(normalized)
    add_case_variants(raw)

    normalized_upper = normalized.upper()
    if normalized_upper.startswith("HK") and normalized_upper[2:].isdigit():
        digits = normalized_upper[2:]
        trimmed_digits = digits.lstrip("0") or digits
        add_case_variants(normalized_upper)
        add_case_variants(digits)
        add_case_variants(trimmed_digits)
        add_case_variants(f"HK{trimmed_digits}")
        add_case_variants(f"{trimmed_digits}.HK")
        add_case_variants(f"{digits}.HK")
        return values

    if (market or "").strip().lower() != "cn":
        return values
    if not (normalized.isdigit() and len(normalized) == 6):
        return values

    raw_upper = raw.upper()
    exchange = ""
    if raw_upper.startswith(("SH", "SS")) or raw_upper.endswith((".SH", ".SS")):
        exchange = "SH"
    elif raw_upper.startswith("SZ") or raw_upper.endswith(".SZ"):
        exchange = "SZ"
    elif raw_upper.startswith("BJ") or raw_upper.endswith(".BJ"):
        exchange = "BJ"
    elif is_bse_code(normalized):
        exchange = "BJ"
    elif normalized.startswith(("5", "6", "9")):
        exchange = "SH"
    else:
        exchange = "SZ"

    add_case_variants(f"{exchange}{normalized}")
    add_case_variants(f"{exchange}.{normalized}")
    add_case_variants(f"{normalized}.{exchange}")
    if exchange == "SH":
        add_case_variants(f"SS.{normalized}")
        add_case_variants(f"{normalized}.SS")
    return values


# T29: 单股分析的主取数窗口。MA60 至少需要 60 根，多取一些才能算均线变化；
# Agent 工具默认请求 120 根，这里一次取足，避免工具层再触发第二次网络请求。
PRIMARY_HISTORY_DAYS = 130


@contextmanager
def _measure_fetch_step(sink: Dict[str, float], label: str):
    """T28: 记录单股取数各步骤耗时，便于定位慢在哪个接口。

    ``sink`` 是本次分析的局部累加器，异常时也写入，保证日志完整。
    """
    started = time.monotonic()
    try:
        yield
    finally:
        try:
            sink[label] = sink.get(label, 0.0) + (time.monotonic() - started)
        except Exception:  # pragma: no cover - 纯诊断信息，不允许影响主流程
            pass


def _format_fetch_timings(sink: Dict[str, float]) -> str:
    """把耗时累加器格式化成单行日志片段。"""
    if not sink:
        return ""
    ordered = sorted(sink.items(), key=lambda item: item[1], reverse=True)
    parts = [f"{label}={seconds:.2f}s" for label, seconds in ordered]
    return f"取数耗时: {' '.join(parts)} (合计 {sum(sink.values()):.2f}s)"


class StockAnalysisPipeline:
    """
    股票分析主流程调度器
    
    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        analysis_skills: Optional[List[str]] = None,
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
        daily_market_context_enabled: Optional[bool] = None,
        daily_market_context_allow_generate: bool = True,
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.trace_id = trace_id or query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self.analysis_skills = list(analysis_skills) if analysis_skills is not None else None
        self.analysis_phase = analysis_phase or "auto"
        self.portfolio_context = dict(portfolio_context) if isinstance(portfolio_context, dict) else None
        self.daily_market_context_enabled = (
            bool(getattr(self.config, "daily_market_context_enabled", True))
            if daily_market_context_enabled is None
            else bool(daily_market_context_enabled)
        )
        self.daily_market_context_allow_generate = daily_market_context_allow_generate
        
        # 初始化各模块
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        self.analyzer = GeminiAnalyzer(config=self.config, skills=self.analysis_skills)
        self.notifier = NotificationService(source_message=source_message)
        self.market_structure_service = MarketStructureService(fetcher_manager=self.fetcher_manager)
        self.market_hotspot_service: Optional[MarketHotspotService] = None
        try:
            self.market_hotspot_service = MarketHotspotService(
                fetcher_manager=self.fetcher_manager,
            )
        except Exception as exc:
            logger.debug("market hotspot service init failed (fail-open): %s", exc)
        self._single_stock_notify_lock = threading.Lock()
        self._daily_market_context_service_lock = threading.Lock()
        self._concept_rankings_cache_lock = threading.Lock()
        self._concept_rankings_cache: Dict[str, Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
        self._last_local_report_path: Optional[str] = None
        self._last_local_report_error: Optional[str] = None
        
        # 初始化搜索服务（可选，初始化失败不应阻断主分析流程）
        try:
            self.search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                serpapi_key_usage_limit=getattr(self.config, "serpapi_key_usage_limit", None),
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                searxng_timeout_seconds=getattr(self.config, "searxng_timeout_seconds", None),
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("搜索服务初始化失败，将以无搜索模式运行: %s", exc, exc_info=True)
            self.search_service = None
        
        logger.info('调度器初始化完成，最大并发数: %s', self.max_workers)
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info('实时行情已启用 (优先级: %s)', self.config.realtime_source_priority)
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service is None:
            logger.warning("搜索服务未启用（初始化失败或依赖缺失）")
        elif self.search_service.is_available:
            logger.info("搜索服务已启用")
        else:
            logger.warning("搜索服务未启用（未配置搜索能力）")

        # 初始化社交舆情服务（仅美股，可选）
        try:
            self.social_sentiment_service = SocialSentimentService(
                api_key=self.config.social_sentiment_api_key,
                api_url=self.config.social_sentiment_api_url,
            )
            if self.social_sentiment_service.is_available:
                logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")
        except Exception as exc:
            logger.warning(
                "社交舆情服务初始化失败，将跳过舆情分析: %s",
                exc,
                exc_info=True,
            )
            self.social_sentiment_service = None

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except TaskCancelledError:
            # R13: 取消是任务级控制流，不是"进度回调失败"，必须传到任务队列。
            raise
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据
        
        断点续传逻辑：
        1. 检查数据库是否已有最新可复用交易日数据
        2. 如果有且不强制刷新，则跳过网络请求
        3. 否则从数据源获取并保存
        
        Args:
            code: 股票代码
            force_refresh: 是否强制刷新（忽略本地缓存）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断
            analysis_target: 结构化分析目标（指数目标用于推导 market=cn 与日期语义）
            
        Returns:
            Tuple[是否成功, 错误信息]
        """
        stock_name = code
        try:
            # 首先获取股票名称
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            target_date = self._resolve_resume_target_date(
                code, current_time=current_time, analysis_target=analysis_target
            )

            # 断点续传检查：如果最新可复用交易日的数据已存在，则跳过
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info('%s(%s) %s 数据已存在，跳过获取（断点续传）', stock_name, code, target_date)
                return True, None

            # 从数据源获取数据
            logger.info('%s(%s) 开始从数据源获取数据...', stock_name, code)
            df, source_name = self.fetcher_manager.get_daily_data(
                code, days=PRIMARY_HISTORY_DAYS
            )

            if df is None or df.empty:
                return False, "获取数据为空"

            # C11: 盘中抓到的当日 K 线是累计值，落库会被当成完整日线，
            # 且让收盘后同日重跑被“今日已有数据”挡住。这里只保留到最近一个
            # 已收盘交易日，未收盘行不入库。
            from src.utils.daily_bar_freshness import drop_unclosed_daily_bars

            df, dropped = drop_unclosed_daily_bars(df, target_date, code=code)
            if dropped:
                logger.info(
                    "%s(%s) 跳过 %s 根未收盘日线（最近完整交易日 %s）",
                    stock_name,
                    code,
                    dropped,
                    target_date,
                )
            if df is None or df.empty:
                return False, "获取数据为空"

            # 保存到数据库
            saved_count = self.db.save_daily_data(df, code, source_name)
            logger.info('%s(%s) 数据保存成功（来源: %s，新增 %s 条）', stock_name, code, source_name, saved_count)

            return True, None

        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error('%s(%s) %s', stock_name, code, error_msg)
            return False, error_msg
    
    def analyze_stock(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        
        流程：
        1. 获取实时行情（量比、换手率）- 通过 DataFetcherManager 自动故障切换
        2. 获取筹码分布 - 通过 DataFetcherManager 带熔断保护
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析
        
        Args:
            query_id: 查询链路关联 id
            code: 股票代码
            report_type: 报告类型
            current_time: 本轮运行冻结的参考时间，用于统一市场阶段上下文
            analysis_target: 结构化分析目标（指数目标用于推导 market=cn 与能力矩阵）
            
        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            portfolio_context = getattr(self, "portfolio_context", None)
            if not isinstance(portfolio_context, dict):
                portfolio_context = None
            is_index = (
                analysis_target is not None
                and analysis_target.asset_type == ParseStatus.INDEX
            )
            market = (
                "cn"
                if is_index
                else get_market_for_stock(normalize_stock_code(code))
            )
            market_phase_context = build_market_phase_context(
                market=market,
                current_time=current_time,
                trigger_source=self.query_source,
                analysis_phase=getattr(self, "analysis_phase", "auto"),
            )
            market_phase_context_dict = market_phase_context.to_dict()
            market_phase_summary = render_market_phase_summary(market_phase_context_dict)
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
            daily_market_target_date = self._coerce_daily_market_context_date(
                getattr(market_phase_context, "effective_daily_bar_date", None)
                or market_phase_context_dict.get("effective_daily_bar_date")
            )
            if daily_market_target_date is None:
                daily_market_target_date = get_effective_trading_date(
                    market,
                    current_time=current_time,
                )
            daily_market_context = self._load_daily_market_context(
                market,
                target_date=daily_market_target_date,
            )

            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            # T28: 采集各取数步骤耗时，定位慢接口
            fetch_timings: Dict[str, float] = {}
            # 获取股票名称（先走轻量名称路径，后续若 realtime_quote 有 name 再覆盖）
            # 指数目标使用注册表中文显示名（matched_index.display_name），避免把
            # raw alias / canonical code / 六码机器码带入搜索查询（Story 1.5 V7）。
            if is_index and analysis_target is not None:
                index_name = getattr(
                    getattr(analysis_target, "matched_index", None),
                    "display_name",
                    None,
                )
                stock_name = index_name or analysis_target.display_code or code
            else:
                with _measure_fetch_step(fetch_timings, "名称"):
                    stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            # Step 1: 获取实时行情（量比、换手率等）- 使用统一入口，自动故障切换
            realtime_quote = None
            try:
                if self.config.enable_realtime_quote:
                    with _measure_fetch_step(fetch_timings, "实时行情"):
                        realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        # 股票使用实时行情名称；指数保持注册表中文名为权威显示名。
                        if realtime_quote.name and not is_index:
                            stock_name = realtime_quote.name
                        # 兼容不同数据源的字段（有些数据源可能没有 volume_ratio）
                        volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                        turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                        logger.info('%s(%s) 实时行情: 价格=%s, 量比=%s, 换手率=%s%% (来源: %s)', stock_name, code, realtime_quote.price, volume_ratio, turnover_rate, realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown')
                    else:
                        logger.warning('%s(%s) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析', stock_name, code)
                else:
                    logger.info('%s(%s) 实时行情已禁用，使用历史收盘价继续分析', stock_name, code)
            except Exception as e:
                logger.warning('%s(%s) 实时行情链路异常，已降级为历史收盘价继续分析: %s', stock_name, code, e)

            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f'股票{code}'

            # Step 2: 获取筹码分布 - 使用统一入口，带熔断保护
            # 指数目标跳过筹码分布（INDEX_SKIP_MODULES 能力矩阵）
            chip_data = None
            if is_index and "chip_distribution" in INDEX_SKIP_MODULES:
                logger.debug('%s(%s) 指数目标跳过筹码分布', stock_name, code)
            else:
                try:
                    with _measure_fetch_step(fetch_timings, "筹码"):
                        chip_data = self.fetcher_manager.get_chip_distribution(code)
                    if chip_data:
                        logger.info(
                            "%s(%s) 筹码分布: 获利比例=%.1f%%, 90%%集中度=%.2f%%",
                            stock_name,
                            code,
                            chip_data.profit_ratio * 100,
                            chip_data.concentration_90 * 100,
                        )
                    else:
                        logger.debug('%s(%s) 筹码分布获取失败或已禁用', stock_name, code)
                except Exception as e:
                    logger.warning('%s(%s) 获取筹码分布失败: %s', stock_name, code, e)

            # If agent mode is explicitly enabled, or specific agent skills are configured, use the Agent analysis pipeline.
            # NOTE: use config.agent_mode (explicit opt-in) instead of
            # config.is_agent_available() so that users who only configured an
            # API Key for the traditional analysis path are not silently
            # switched to Agent mode (which is slower and more expensive).
            use_agent = getattr(self.config, 'agent_mode', False)
            if not use_agent:
                if self.analysis_skills:
                    use_agent = True
                    logger.info('%s(%s) Auto-enabled agent mode due to request skills: %s', stock_name, code, self.analysis_skills)
            if not use_agent:
                # Auto-enable agent mode when specific skills are configured (e.g., scheduled task with strategy)
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info('%s(%s) Auto-enabled agent mode due to configured skills: %s', stock_name, code, configured_skills)

            self._emit_progress(PROGRESS_FUNDAMENTALS, f"{stock_name}：正在聚合基本面与趋势数据")

            # Step 2.5: 基本面能力聚合（统一入口，异常降级）
            # - 失败时返回 partial/failed，不影响既有技术面/新闻链路
            # - 关闭开关时仍返回 not_supported 结构
            # - 指数目标跳过基本面/板块/资金流/龙虎榜/公司事件（INDEX_SKIP_MODULES）
            fundamental_context = None
            fundamental_started = time.monotonic()
            fundamental_modules = {
                "fundamental",
                "belong_boards",
                "capital_flow",
                "lhb",
                "corporate_events",
            }
            if is_index and INDEX_SKIP_MODULES.intersection(fundamental_modules):
                logger.debug('%s(%s) 指数目标跳过基本面聚合', stock_name, code)
                fundamental_context = self.fetcher_manager.build_not_supported_fundamental_context(
                    code,
                    "index target: fundamental modules skipped",
                )
            else:
                try:
                    fundamental_context = self.fetcher_manager.get_fundamental_context(
                        code,
                        realtime_quote=realtime_quote,
                        budget_seconds=getattr(
                            self.config,
                            'fundamental_stage_timeout_seconds',
                            FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                        ),
                    )
                except Exception as e:
                    logger.warning('%s(%s) 基本面聚合失败: %s', stock_name, code, e)
                    fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

            fetch_timings["基本面"] = time.monotonic() - fundamental_started
            if is_index and "belong_boards" in INDEX_SKIP_MODULES:
                logger.debug('%s(%s) 指数目标跳过板块归属', stock_name, code)
                if isinstance(fundamental_context, dict):
                    fundamental_context = dict(fundamental_context)
                    fundamental_context["belong_boards"] = []
            else:
                fundamental_context = self._attach_belong_boards_to_fundamental_context(
                    code,
                    fundamental_context,
                )
            market_structure_context = self._build_market_structure_context(
                code=code,
                stock_name=stock_name,
                market=market,
                fundamental_context=fundamental_context,
                trade_date=daily_market_target_date,
                market_phase_summary=market_phase_summary,
            )

            # P0: write-only snapshot, fail-open, no read dependency on this table.
            if not is_index:
                try:
                    self.db.save_fundamental_snapshot(
                        query_id=query_id,
                        code=code,
                        payload=fundamental_context,
                        source_chain=fundamental_context.get("source_chain", []),
                        coverage=fundamental_context.get("coverage", {}),
                    )
                except Exception as e:
                    logger.debug('%s(%s) 基本面快照写入失败: %s', stock_name, code, e)

            # Step 3: 趋势分析（基于交易理念）— 在 Agent 分支之前执行，供两条路径共用
            trend_result: Optional[TrendAnalysisResult] = None
            try:
                from src.services.history_loader import get_frozen_target_date
                _mkt = get_market_for_stock(normalize_stock_code(code))
                frozen = get_frozen_target_date()
                end_date = frozen if frozen else get_market_now(_mkt).date()
                # T29: 约 100 个交易日，足够 MA60 及均线变化，不再卡在 60 根边缘
                start_date = end_date - timedelta(days=150)
                historical_bars = self.db.get_data_range(code, start_date, end_date)
                if historical_bars:
                    df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                    is_partial_bar = False
                    # Issue #234: Augment with realtime for intraday MA calculation
                    if self.config.enable_realtime_quote and realtime_quote:
                        df = self._augment_historical_with_realtime(
                            df, realtime_quote, code, market=market
                        )
                        # T10: a bar built from an in-session quote is a partial
                        # (accumulated) bar; volume must not be scored against
                        # full-day averages.
                        phase = (market_phase_context_dict or {}).get("phase")
                        is_partial_bar = bool(
                            (market_phase_context_dict or {}).get("is_partial_bar")
                        ) or phase in ("intraday", "lunch_break", "closing_auction")
                    trend_result = self.trend_analyzer.analyze(
                        df, code, is_partial_bar=is_partial_bar
                    )
                    logger.info('%s(%s) 趋势分析: %s, 买入信号=%s, 评分=%s', stock_name, code, trend_result.trend_status.value, trend_result.buy_signal.value, trend_result.signal_score)
            except Exception as e:
                logger.warning('%s(%s) 趋势分析失败: %s', stock_name, code, e, exc_info=True)

            if fetch_timings:
                logger.info("%s(%s) %s", stock_name, code, _format_fetch_timings(fetch_timings))

            if use_agent:
                logger.info('%s(%s) 启用 Agent 模式进行分析', stock_name, code)
                self._emit_progress(PROGRESS_GENERATING, f"{stock_name}：正在启用深度分析")
                return self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
                    market_phase_context=market_phase_context_dict,
                    market_phase_summary=market_phase_summary,
                    daily_market_context=daily_market_context,
                    portfolio_context=portfolio_context,
                    market_structure_context=market_structure_context,
                    analysis_target=analysis_target,
                )

            # Step 4: 多维度情报搜索（最新消息+风险排查+业绩预期）
            news_context = None
            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
                market=market or "cn",
            )
            news_result_count: Optional[int] = None
            self._emit_progress(PROGRESS_NEWS, f"{stock_name}：正在检索新闻与舆情")
            if self.search_service is not None and self.search_service.is_available:
                logger.info('%s(%s) 开始多维度情报搜索...', stock_name, code)

                # 检索已发起：此后即使一条都没拿到，也是「执行了但零命中」而非
                # 「未执行检索」。若停留在 None，搜索源全线失败这一最该提示的场景
                # 反而不会提示。
                news_result_count = 0

                # 使用多维度搜索（最多5次搜索）
                # 指数目标：查询 subject 仅用注册表中文名称，不把 canonical code /
                # 六码机器码带入查询（Story 1.5 V7）。
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=("" if is_index else code),
                    stock_name=stock_name,
                    max_searches=5
                )

                # 格式化情报报告
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(
                        len(r.results) for r in intel_results.values() if r.success
                    )
                    news_result_count = total_results
                    logger.info('%s(%s) 情报搜索完成: 共 %s 条结果', stock_name, code, total_results)
                    logger.debug('%s(%s) 情报搜索结果:\n%s', stock_name, code, news_context)

                    # 保存新闻情报到数据库（用于后续复盘与查询）
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                self.db.save_news_intel(
                                    code=code,
                                    name=stock_name,
                                    dimension=dim_name,
                                    query=response.query,
                                    response=response,
                                    query_context=query_context
                                )
                    except Exception as e:
                        logger.warning('%s(%s) 保存新闻情报失败: %s', stock_name, code, e)
            else:
                logger.info('%s(%s) 搜索服务不可用，跳过情报搜索', stock_name, code)

            # Step 4.5: Social sentiment intelligence (US stocks only)
            social_evidence_context: Optional[str] = None
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        logger.info('%s(%s) Social sentiment data retrieved', stock_name, code)
                        social_evidence_context = social_context
                        if news_context:
                            news_context = news_context + "\n\n" + social_context
                        else:
                            news_context = social_context
                except Exception as e:
                    logger.warning('%s(%s) Social sentiment fetch failed: %s', stock_name, code, e)

            if persisted_intelligence_context:
                news_context = (
                    f"{news_context}\n\n{persisted_intelligence_context}"
                    if news_context
                    else persisted_intelligence_context
                )

            # 预测市场证据（默认关闭，fail-open 追加；不改既有 news_context 语义）
            prediction_market_context, prediction_market_events = self._load_prediction_market_evidence(
                code=code,
                stock_name=stock_name,
                market=market or "cn",
            )

            # Step 5: 获取分析上下文（技术面数据）
            self._emit_progress(PROGRESS_GENERATING, f"{stock_name}：正在整理分析上下文")
            with _measure_fetch_step(fetch_timings, "分析上下文"):
                context = self._get_analysis_context_with_market_fallback(
                    code, analysis_target=analysis_target, current_time=current_time
                )

            if context is None:
                logger.warning('%s(%s) 无法获取历史行情数据，将仅基于新闻和实时行情分析', stock_name, code)
                _mkt_date = get_market_now(
                    get_market_for_stock(normalize_stock_code(code))
                ).date()
                context = {
                    'code': code,
                    'stock_name': stock_name,
                    'date': _mkt_date.isoformat(),
                    'data_missing': True,
                    'today': {},
                    'yesterday': {}
                }
            
            # Step 6: 增强上下文数据（添加实时行情、筹码、趋势分析结果、股票名称）
            enhanced_context = self._enhance_context(
                context, 
                realtime_quote, 
                chip_data,
                trend_result,
                stock_name,  # 传入股票名称
                fundamental_context,
                market_phase_context=market_phase_context_dict,
                portfolio_context=portfolio_context,
            )
            enhanced_context["market_phase_context"] = market_phase_context_dict
            self._attach_daily_market_context(
                enhanced_context,
                daily_market_context,
                report_language=report_language,
            )
            if portfolio_context is not None:
                enhanced_context["portfolio_context"] = dict(portfolio_context)
            if isinstance(market_structure_context, dict):
                enhanced_context["market_structure_context"] = market_structure_context
            
            # Step 7: 调用 AI 分析（传入增强的上下文和新闻）
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_legacy_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context_dict,
                    context=context,
                    enhanced_context=enhanced_context,
                    realtime_quote=realtime_quote,
                    trend_result=trend_result,
                    chip_data=chip_data,
                    fundamental_context=fundamental_context,
                    news_context=news_context,
                    news_result_count=news_result_count,
                    query_id=query_id,
                    portfolio_context=portfolio_context,
                    prediction_market_context=prediction_market_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            llm_progress_state = {"last_progress": 64}

            def _on_llm_stream(chars_received: int) -> None:
                dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
                if dynamic_progress <= llm_progress_state["last_progress"]:
                    return
                llm_progress_state["last_progress"] = dynamic_progress
                self._emit_progress(
                    dynamic_progress,
                    f"{stock_name}：正在生成报告 · 已生成约 {chars_received} 字",
                )

            self._emit_progress(64, f"{stock_name}：正在请求 LLM 生成报告")
            llm_started_at = time.monotonic()
            try:
                record_llm_run_started(
                    model=getattr(self.config, "litellm_model", None),
                    call_type="analysis",
                )
                result = self.analyzer.analyze(
                    enhanced_context,
                    news_context=news_context,
                    progress_callback=self._emit_progress,
                    stream_progress_callback=_on_llm_stream,
                    analysis_context_pack_summary=analysis_context_pack_summary,
                    prediction_market_context=prediction_market_context,
                )
                llm_duration_ms = int((time.monotonic() - llm_started_at) * 1000)
                if result is not None:
                    # 交给展示层区分「未配置渠道」「检索零命中」和「正常命中」。
                    # 该值此前只进了诊断快照，报告层拿不到。
                    result.news_result_count = news_result_count
                    # 三路来源逐个登记，不看拼好的 news_context 整段：
                    # format_intel_report() 零命中时仍输出占位文本，整段永远非空，
                    # 拿它判定会把「搜了但一条没拿到」误判成有证据。
                    result.news_evidence_present = news_evidence_present(
                        news_result_count,
                        social_evidence_context,
                        persisted_intelligence_context,
                    )
                record_llm_run(
                    success=bool(result and getattr(result, "success", True)),
                    model=getattr(result, "model_used", None) if result else None,
                    call_type="analysis",
                    duration_ms=llm_duration_ms,
                    error_type=(
                        None
                        if result and getattr(result, "success", True)
                        else "AnalysisResultError"
                    ),
                    error_message=(
                        getattr(result, "error_message", None)
                        if result and not getattr(result, "success", True)
                        else ("LLM returned empty result" if result is None else None)
                    ),
                )
            except Exception as exc:
                record_llm_run(
                    success=False,
                    model=getattr(self.config, "litellm_model", None),
                    call_type="analysis",
                    duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=exc,
                )
                raise

            # Step 7.5: 填充分析时的价格信息到 result
            if result:
                self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
                result.query_id = query_id
                realtime_data = enhanced_context.get('realtime', {})
                result.current_price = realtime_data.get('price')
                result.change_pct = realtime_data.get('change_pct')

            # Step 7.6: chip_structure fallback (Issue #589) and unavailable collapse
            if result:
                normalize_chip_structure_availability(result, chip_data)

            # Step 7.7: price_position fallback
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                action_source_advice = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied adjustments for %s: %s", code, adjustments)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=enhanced_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                if isinstance(market_structure_context, dict):
                    result.market_structure_context = market_structure_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=action_source_advice,
                )

            if result:
                self._append_daily_data_source(result, context, analysis_target)

            # Step 8: 保存分析历史记录
            if result and result.success:
                try:
                    self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                    context_snapshot = self._build_context_snapshot(
                        enhanced_context=enhanced_context,
                        news_content=news_context,
                        news_result_count=news_result_count,
                        realtime_quote=realtime_quote,
                        chip_data=chip_data,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    result.diagnostic_context_snapshot = context_snapshot
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=news_context,
                        context_snapshot=context_snapshot,
                        save_snapshot=self.save_context_snapshot
                    )
                    self._persist_prediction_market_evidence(
                        saved_history_id, prediction_market_events
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=context_snapshot,
                            portfolio_context=portfolio_context,
                            analysis_target=analysis_target,
                        )
                except TaskCancelledError:
                    # R13: 取消检查点发生在保存之前，取消后不写历史记录。
                    raise
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning('%s(%s) 保存分析历史失败: %s', stock_name, code, e)

            return result

        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error('%s(%s) 分析失败: %s', stock_name, code, e)
            logger.exception('%s(%s) 详细错误信息:', stock_name, code)
            return None
    
    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None,
        market_phase_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        
        将实时行情、筹码分布、趋势分析结果、股票名称添加到上下文中
        
        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据（UnifiedRealtimeQuote 或 None）
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            stock_name: 股票名称
            market_phase_context: 已构建的市场阶段上下文，用于标记盘中 partial bar
            
        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "zh"))
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and getattr(realtime_quote, 'name', None):
            enhanced['stock_name'] = realtime_quote.name
        if isinstance(portfolio_context, dict):
            enhanced["portfolio_context"] = dict(portfolio_context)

        # 将运行时搜索窗口透传给 analyzer，避免与全局配置重新读取产生窗口不一致
        enhanced['news_window_days'] = getattr(self.search_service, "news_window_days", 3)
        
        # 添加实时行情（兼容不同数据源的字段差异）
        if realtime_quote:
            # 使用 getattr 安全获取字段，缺失字段返回 None 或默认值
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            quote_source = getattr(realtime_quote, 'source', None)
            quote_source_name = getattr(quote_source, 'value', quote_source)
            quote_source_name = str(quote_source_name) if quote_source_name is not None else None
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio else '无数据',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'change_60d': getattr(realtime_quote, 'change_60d', None),
                'source': quote_source_name,
                'fetched_at': getattr(realtime_quote, 'fetched_at', None),
                'provider_timestamp': getattr(realtime_quote, 'provider_timestamp', None),
                'is_stale': getattr(realtime_quote, 'is_stale', None),
                'stale_seconds': getattr(realtime_quote, 'stale_seconds', None),
                'fallback_from': getattr(realtime_quote, 'fallback_from', None),
            }
            # 移除 None 值以减少上下文大小
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # 添加筹码分布
        if chip_data:
            current_price = getattr(realtime_quote, 'price', 0) if realtime_quote else 0
            enhanced['chip'] = {
                'profit_ratio': chip_data.profit_ratio,
                'avg_cost': chip_data.avg_cost,
                'concentration_90': chip_data.concentration_90,
                'concentration_70': chip_data.concentration_70,
                'chip_status': chip_data.get_chip_status(current_price or 0),
            }
        
        # 添加趋势分析结果
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'bias_ma5': trend_result.bias_ma5,
                'bias_ma10': trend_result.bias_ma10,
                'volume_status': trend_result.volume_status.value,
                'volume_trend': trend_result.volume_trend,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
            }

        # Issue #234：盘中分析使用实时 OHLC 与趋势 MA 覆盖 today。
        # 防护条件：trend_result.ma5 > 0 表示 MA 计算已成功且数据量充足。
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, 'price', None)
            if price is not None and price > 0:
                orig_today = enhanced.get('today') or {}
                stale_today = orig_today
                market_code = get_market_for_stock(normalize_stock_code(enhanced.get('code', '')))
                market_now = get_market_now(market_code)
                market_today = market_now.date().isoformat()
                phase_ctx = market_phase_context if isinstance(market_phase_context, dict) else {}
                session_started = (
                    phase_ctx.get('is_trading_day') is True
                    and phase_ctx.get('phase') in (
                        'intraday', 'lunch_break', 'closing_auction', 'postmarket',
                    )
                )
                session_date = str(phase_ctx.get('session_date') or market_today)[:10]
                orig_today_date = _coerce_context_date(orig_today.get('date')) if isinstance(orig_today, dict) else None
                # T9: 会话已开始、且库里最新一行早于当前会话日时，它其实是"昨日"。
                # 先顺移，再让实时行情覆盖今日，否则昨收/涨跌额/振幅会按前前日算。
                if (
                    session_started
                    and orig_today_date is not None
                    and orig_today_date < date.fromisoformat(session_date)
                    and self._is_realtime_quote_for_session(
                        realtime_quote,
                        session_date=session_date,
                        market_tz=market_now.tzinfo,
                    )
                ):
                    enhanced['yesterday'] = dict(orig_today) if isinstance(orig_today, dict) else orig_today
                    yesterday_close = orig_today.get('close') if isinstance(orig_today, dict) else None
                    orig_today = {}
                else:
                    yesterday_close = None
                pre_close = getattr(realtime_quote, 'pre_close', None)
                if pre_close is not None:
                    try:
                        pre_close_value = float(pre_close)
                    except (TypeError, ValueError):
                        pre_close_value = 0.0
                    if pre_close_value > 0:
                        yesterday_close = pre_close_value
                if yesterday_close is None and enhanced.get('yesterday') and isinstance(enhanced['yesterday'], dict):
                    yesterday_close = enhanced['yesterday'].get('close')
                source = getattr(realtime_quote, 'source', None)
                source_name = getattr(source, 'value', source)
                source_name = str(source_name) if source_name is not None else 'unknown'
                open_p = getattr(realtime_quote, 'open_price', None) or getattr(
                    realtime_quote, 'pre_close', None
                ) or yesterday_close or orig_today.get('open') or price
                high_p = getattr(realtime_quote, 'high', None) or price
                low_p = getattr(realtime_quote, 'low', None) or price
                vol = getattr(realtime_quote, 'volume', None)
                amt = getattr(realtime_quote, 'amount', None)
                pct = getattr(realtime_quote, 'change_pct', None)
                fetched_at = getattr(realtime_quote, 'fetched_at', None)
                provider_timestamp = getattr(realtime_quote, 'provider_timestamp', None)
                fallback_from = getattr(realtime_quote, 'fallback_from', None)
                realtime_today = {
                    'close': price,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'ma5': trend_result.ma5,
                    'ma10': trend_result.ma10,
                    'ma20': trend_result.ma20,
                    'date': market_today,
                    'data_source': f"realtime:{source_name}",
                    'realtime_source': source_name,
                    'is_estimated': True,
                }
                estimated_fields = [
                    'close', 'open', 'high', 'low', 'ma5', 'ma10', 'ma20',
                ]
                if vol is not None:
                    realtime_today['volume'] = vol
                    estimated_fields.append('volume')
                if amt is not None:
                    realtime_today['amount'] = amt
                    estimated_fields.append('amount')
                if pct is not None:
                    realtime_today['pct_chg'] = pct
                    estimated_fields.append('pct_chg')
                realtime_today['estimated_fields'] = estimated_fields
                if isinstance(market_phase_context, dict) and "is_partial_bar" in market_phase_context:
                    realtime_today['is_partial_bar'] = market_phase_context.get("is_partial_bar")
                if fetched_at is not None:
                    realtime_today['fetched_at'] = fetched_at
                if provider_timestamp is not None:
                    realtime_today['provider_timestamp'] = provider_timestamp
                if fallback_from is not None:
                    realtime_today['fallback_from'] = fallback_from
                realtime_owned_fields = {
                    'open', 'high', 'low', 'close',
                    'volume', 'amount', 'pct_chg', 'pctChg',
                    'date', 'data_source', 'dataSource', 'source',
                    'realtime_source', 'realtimeSource',
                    'is_partial_bar', 'isPartialBar', 'is_estimated',
                    'isEstimated', 'estimated_fields', 'estimatedFields',
                    'fetched_at', 'fetchedAt', 'provider_timestamp',
                    'providerTimestamp', 'fallback_from', 'fallbackFrom',
                }
                for k, v in orig_today.items():
                    if k not in realtime_today and k not in realtime_owned_fields and v is not None:
                        realtime_today[k] = v
                # 顺移后原“今日”已属于昨日，只保留标的标识，不再把旧交易日
                # 的其他字段带进新的今日。
                if 'code' not in realtime_today and stale_today.get('code'):
                    realtime_today['code'] = stale_today['code']
                enhanced['today'] = realtime_today
                enhanced['ma_status'] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced['date'] = market_today
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced['price_change_ratio'] = round(
                                (price - yc) / yc * 100, 2
                            )
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get('yesterday'):
                    yest_vol = enhanced['yesterday'].get('volume') if isinstance(
                        enhanced['yesterday'], dict
                    ) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced['volume_change_ratio'] = round(
                                    float(vol) / yv, 2
                                )
                        except (TypeError, ValueError):
                            pass

        # ETF/index flag for analyzer prompt (Fixes #274)
        enhanced['is_index_etf'] = SearchService.is_index_or_etf(
            context.get('code', ''), enhanced.get('stock_name', stock_name)
        )

        # P0: append unified fundamental block; keep as additional context only
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )

        # C7: 把同一只股票过去几次建议的事后表现带进上下文，让模型看到自己的
        # 历史命中情况；不足 3 条时不注入，避免用小样本误导判断。
        enhanced["outcome_history"] = self._load_outcome_history(context.get("code", ""))

        return enhanced

    def _load_outcome_history(self, code: Any) -> List[Dict[str, Any]]:
        """Load recent completed signal outcomes for one stock (fail-open)."""

        stock_code = str(code or "").strip()
        if not stock_code:
            return []
        try:
            from src.services.decision_signal_outcome_service import (
                DecisionSignalOutcomeService,
            )

            return DecisionSignalOutcomeService(db_manager=self.db).recent_stock_outcomes(
                stock_code=stock_code,
                limit=5,
                min_records=3,
            )
        except Exception:
            logger.debug("加载历史评估失败: %s", stock_code, exc_info=True)
            return []

    def _attach_belong_boards_to_fundamental_context(
        self,
        code: str,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Attach A-share board membership as a top-level supplemental field.

        Keep this as a shallow copy so cached fundamental contexts are not
        mutated in place after retrieval.
        """
        if isinstance(fundamental_context, dict):
            enriched_context = dict(fundamental_context)
        else:
            enriched_context = self.fetcher_manager.build_failed_fundamental_context(
                code,
                "invalid fundamental context",
            )

        market = enriched_context.get("market")
        if not isinstance(market, str) or not market.strip():
            market = get_market_for_stock(normalize_stock_code(code))

        existing_boards = enriched_context.get("belong_boards")
        existing_board_list = list(existing_boards) if isinstance(existing_boards, list) else None
        if existing_board_list:
            enriched_context["belong_boards"] = existing_board_list
            self._attach_concept_rankings_to_fundamental_context(code, enriched_context, market)
            return enriched_context

        boards_block = enriched_context.get("boards")
        boards_status = boards_block.get("status") if isinstance(boards_block, dict) else None
        coverage = enriched_context.get("coverage")
        boards_coverage = coverage.get("boards") if isinstance(coverage, dict) else None

        # For HK/US: the offshore adapter already populates belong_boards from
        # yfinance sector/industry. Don't overwrite it (and we have no AkShare
        # 板块 endpoint for those markets anyway). Default to [] when callers
        # pass a minimal context without the key.
        if market != "cn":
            enriched_context["belong_boards"] = existing_board_list or []
            return enriched_context

        if boards_status == "not_supported" or boards_coverage == "not_supported":
            enriched_context["belong_boards"] = existing_board_list or []
            return enriched_context

        boards: List[Dict[str, Any]] = []
        try:
            raw_boards = self.fetcher_manager.get_belong_boards(code)
            if isinstance(raw_boards, list):
                boards = raw_boards
        except Exception as e:
            logger.debug("%s attach belong_boards failed (fail-open): %s", code, e)

        enriched_context["belong_boards"] = boards or existing_board_list or []
        self._attach_concept_rankings_to_fundamental_context(code, enriched_context, market)
        return enriched_context

    def _attach_concept_rankings_to_fundamental_context(
        self,
        code: str,
        enriched_context: Dict[str, Any],
        market: str,
    ) -> None:
        """Attach concept/theme rankings for A-share related-board signals."""
        if market != "cn" or isinstance(enriched_context.get("concept_boards"), dict):
            return

        top_concepts, bottom_concepts = self._get_concept_rankings_for_market(market)

        concept_data: Dict[str, Any] = {
            "top": top_concepts,
            "bottom": bottom_concepts,
        }
        if not top_concepts and not bottom_concepts:
            # Empty lists are removed while fundamental contexts are merged.
            # Keep a non-empty internal marker so downstream consumers can
            # distinguish an attempted empty result from a missing preload.
            concept_data["fetch_attempted"] = True
        enriched_context["concept_boards"] = {
            "status": "ok" if top_concepts and bottom_concepts else "partial",
            "data": concept_data,
        }

    def _get_concept_rankings_for_market(
        self,
        market: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch market-wide concept rankings once per pipeline run."""
        if market != "cn":
            return [], []

        service = getattr(self, "market_hotspot_service", None)
        if service is None:
            try:
                service = MarketHotspotService(fetcher_manager=self.fetcher_manager)
            except Exception as exc:
                logger.debug(
                    "market hotspot service init failed in concept ranking path (fail-open): %s",
                    exc,
                )
                service = None
            else:
                self.market_hotspot_service = service

        cache = getattr(self, "_concept_rankings_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._concept_rankings_cache = cache

        lock = getattr(self, "_concept_rankings_cache_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._concept_rankings_cache_lock = lock

        with lock:
            if market in cache:
                top_concepts, bottom_concepts = cache[market]
                return list(top_concepts), list(bottom_concepts)

            top_concepts: List[Dict[str, Any]] = []
            bottom_concepts: List[Dict[str, Any]] = []
            try:
                if service is None:
                    fetch_rankings = getattr(self.fetcher_manager, "get_concept_rankings", None)
                    if callable(fetch_rankings):
                        rankings = fetch_rankings(5)
                        if isinstance(rankings, tuple) and len(rankings) == 2:
                            raw_top, raw_bottom = rankings
                            if isinstance(raw_top, list):
                                top_concepts = list(raw_top)
                            if isinstance(raw_bottom, list):
                                bottom_concepts = list(raw_bottom)
                else:
                    top_concepts, bottom_concepts = service.get_concept_rankings(5)
            except Exception as e:
                logger.debug("attach concept_rankings failed (fail-open): %s", e)

            cache[market] = (top_concepts, bottom_concepts)
            return list(top_concepts), list(bottom_concepts)

    def _build_market_structure_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        fundamental_context: Optional[Dict[str, Any]],
        trade_date: Any = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build market structure context without blocking the main analysis."""
        service = getattr(self, "market_structure_service", None)
        if service is None:
            try:
                service = MarketStructureService(fetcher_manager=self.fetcher_manager)
                self.market_structure_service = service
            except Exception as exc:
                logger.debug("market structure service init failed (fail-open): %s", exc)
                return None
        try:
            return service.build_context(
                code=code,
                stock_name=stock_name,
                market=market,
                fundamental_context=fundamental_context,
                trade_date=trade_date,
                market_phase_summary=market_phase_summary,
            )
        except Exception as exc:
            logger.debug(
                "%s market structure context build failed (fail-open): %s",
                code,
                exc,
                exc_info=True,
            )
            return None

    def _ensure_agent_history(
        self,
        code: str,
        min_days: int = 240,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> None:
        """Ensure at least *min_days* of K-line history is in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(
                code, analysis_target=analysis_target
            )
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                from src.utils.daily_bar_freshness import drop_unclosed_daily_bars

                df, _dropped = drop_unclosed_daily_bars(df, target, code=code)
                if df is not None and not df.empty:
                    self.db.save_daily_data(df, code, source)
                logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    def _filter_agent_tools_for_index(self, executor: Any) -> Any:
        """Return an executor whose tool registry excludes index-incompatible tools.

        Maps ``INDEX_SKIP_MODULES`` to the agent tool names that would otherwise
        invoke the skipped bottom-layer providers (chip distribution, fundamental
        aggregation, capital flow). The filtered registry carries the source
        category-timeout map so per-category ceilings survive the subset copy.
        """
        tool_modules = {
            "get_chip_distribution": {"chip_distribution"},
            "get_stock_info": {
                "fundamental",
                "belong_boards",
                "lhb",
                "corporate_events",
            },
            "get_capital_flow": {"capital_flow"},
        }
        index_skip_tool_names = {
            name
            for name, modules in tool_modules.items()
            if INDEX_SKIP_MODULES.intersection(modules)
        }
        registry = getattr(executor, "tool_registry", None)
        if registry is None:
            return executor
        from src.agent.tools.registry import ToolRegistry as _TR
        filtered = _TR(category_timeout_map=registry.category_timeout_map)
        for name in registry.list_names():
            if name in index_skip_tool_names:
                continue
            tool_def = registry.get(name)
            if tool_def is not None:
                filtered.register(tool_def)
        executor.tool_registry = filtered
        return executor

    def _analyze_with_agent(
        self, 
        code: str, 
        report_type: ReportType, 
        query_id: str,
        stock_name: str,
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
        *,
        market_phase_context: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
        daily_market_context: Optional[DailyMarketContext] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
        market_structure_context: Optional[Dict[str, Any]] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        使用 Agent 模式分析单只股票。
        """
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

            is_index = (
                analysis_target is not None
                and analysis_target.asset_type == ParseStatus.INDEX
            )

            requested_skills = (
                self.analysis_skills
                if self.analysis_skills is not None
                else (getattr(self.config, 'agent_skills', None) or None)
            )
            # Build executor from shared factory (ToolRegistry and SkillManager prototype are cached)
            executor = build_agent_executor(self.config, requested_skills)

            # 指数目标：从 Agent 工具面剔除与 INDEX_SKIP_MODULES 对应的底层 provider
            # 工具（筹码/基本面/资金流），确保 Agent 分支同样零调用（Story 1.5 V6）。
            if is_index:
                executor = self._filter_agent_tools_for_index(executor)

            # Build initial context to avoid redundant tool calls
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
            }
            if isinstance(portfolio_context, dict):
                initial_context["portfolio_context"] = dict(portfolio_context)
            if self.analysis_skills is not None:
                initial_context["skills"] = self.analysis_skills
            if market_phase_context is not None:
                initial_context["market_phase_context"] = market_phase_context
            if isinstance(market_structure_context, dict):
                initial_context["market_structure_context"] = market_structure_context
            self._attach_daily_market_context(
                initial_context,
                daily_market_context,
                report_language=report_language,
            )
            
            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if chip_data:
                initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            # Agent path: inject social sentiment as news_context so both
            # executor (_build_user_message) and orchestrator (ctx.set_data)
            # can consume it through the existing news_context channel
            social_evidence_context: Optional[str] = None
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        social_evidence_context = social_context
                        existing = initial_context.get("news_context")
                        if existing:
                            initial_context["news_context"] = existing + "\n\n" + social_context
                        else:
                            initial_context["news_context"] = social_context
                        logger.info('[%s] Agent mode: social sentiment data injected into news_context', code)
                except Exception as e:
                    logger.warning('[%s] Agent mode: social sentiment fetch failed: %s', code, e)

            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
                market=("cn" if is_index else get_market_for_stock(normalize_stock_code(code)) or "cn"),
            )
            if persisted_intelligence_context:
                existing = initial_context.get("news_context")
                initial_context["news_context"] = (
                    f"{existing}\n\n{persisted_intelligence_context}"
                    if existing
                    else persisted_intelligence_context
                )
                logger.info('[%s] Agent mode: local intelligence evidence injected into news_context', code)

            prediction_market_context, prediction_market_events = self._load_prediction_market_evidence(
                code=code,
                stock_name=stock_name,
                market=("cn" if is_index else get_market_for_stock(normalize_stock_code(code)) or "cn"),
            )
            if prediction_market_context:
                initial_context["prediction_market_context"] = prediction_market_context
                logger.info('[%s] Agent mode: prediction market evidence injected into context', code)
            if prediction_market_events:
                initial_context["prediction_market_events"] = prediction_market_events

            # Issue #1066: ensure deep history is in DB before agent tools run
            if analysis_target is None:
                self._ensure_agent_history(code)
            else:
                self._ensure_agent_history(code, analysis_target=analysis_target)

            analysis_context = self._load_agent_analysis_context(
                code, stock_name, analysis_target=analysis_target
            )
            market = "cn" if is_index else get_market_for_stock(normalize_stock_code(code))
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_agent_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context,
                    initial_context=initial_context,
                    fundamental_context=fundamental_context,
                    query_id=query_id,
                    base_context=analysis_context,
                    portfolio_context=portfolio_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            if analysis_context_pack_summary:
                initial_context["analysis_context_pack_summary"] = analysis_context_pack_summary

            # 运行 Agent
            if report_language in ("en", "ko"):
                message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON."
            else:
                message = f"请分析股票 {code} ({stock_name})，并生成决策仪表盘报告。"
            llm_started_at = time.monotonic()
            # Agent 自己调用搜索工具取证，所以披露计数只能来自这些工具的真实返回；
            # 分析结束后补打的 search_stock_news() 与 Agent 消费的证据无关。
            # 累加器对象在这里持有引用，reset 之后仍可安全读取。
            news_evidence_token = activate_news_evidence_scope()
            news_evidence = get_current_news_evidence()

            def _agent_progress(event) -> None:
                """把 Agent 工具事件桥接成任务进度。

                Agent 分支以前从 58% 直接跳到 94%，中间是最长的一段，用户会
                以为任务已经死掉（H3）。每完成一个工具就更新一次任务文案，
                进度值保持在生成阶段内（任务进度只增不减，不会回退）。
                """
                event_type = event.get("type") if isinstance(event, dict) else None
                if event_type == "tool_done":
                    label = event.get("display_label") or event.get("tool") or "数据"
                    self._emit_progress(PROGRESS_GENERATING, f"{stock_name}：正在{label}")
                elif event_type == "generating":
                    self._emit_progress(PROGRESS_GENERATING, f"{stock_name}：正在生成报告")

            try:
                record_llm_run_started(
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                )
                agent_result = executor.run(
                    message, context=initial_context, progress_callback=_agent_progress,
                )
            except Exception as exc:
                record_llm_run(
                    success=False,
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                    duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=exc,
                )
                raise
            finally:
                reset_news_evidence_scope(news_evidence_token)

            # R13: Agent 运行期间没有进度回调；运行结束后先过一次取消检查点，
            # 用户已取消时不再整理、保存或推送结果。
            self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")

            # 转换为 AnalysisResult
            result = self._agent_result_to_analysis_result(
                agent_result,
                code,
                stock_name,
                report_type,
                query_id,
                trend_result=trend_result,
            )

            # 三态计数取自 Agent 实际消费的搜索工具结果：渠道不可用为 None（未执行
            # 检索），渠道可用则从 0 起步、拿到多少算多少。
            if result is not None and news_evidence is not None:
                result.news_result_count = news_evidence.resolve(
                    search_available=bool(
                        self.search_service is not None
                        and self.search_service.is_available
                    ),
                )
                # 与普通路径同样按来源逐个登记：Agent 运行期自己搜到的条数、注入的
                # 社交情绪、注入的本地资讯池。这条路径不经过 format_intel_report()，
                # 但仍不传拼好的整段，避免以后有人往里加会造占位文本的来源。
                result.news_evidence_present = news_evidence_present(
                    result.news_result_count,
                    social_evidence_context,
                    persisted_intelligence_context,
                )

                # 分析开始前构建的 context pack 没有预填 Agent 自己检索到的新闻，
                # 会让质量分和置信度理由误报「新闻数据块缺失」。跑完后用本次工具
                # 返回的真实新闻回填新闻块、重算质量分并更新 news_result_count。
                agent_news_context = news_evidence.summary()
                if not agent_news_context and result.news_result_count:
                    agent_news_context = self._agent_news_count_fallback(
                        result.news_result_count,
                        report_language,
                    )
                initial_context["news_context"] = self._merge_news_context(
                    initial_context.get("news_context"),
                    agent_news_context,
                )
                rebuilt_summary, rebuilt_overview = self._build_analysis_context_pack_outputs(
                    self._build_agent_analysis_artifacts(
                        code=code,
                        stock_name=stock_name,
                        market=market,
                        phase=market_phase_context,
                        initial_context=initial_context,
                        fundamental_context=fundamental_context,
                        query_id=query_id,
                        base_context=analysis_context,
                        portfolio_context=portfolio_context,
                        news_result_count=result.news_result_count,
                    ),
                    report_language=report_language,
                    code=code,
                    query_id=query_id,
                )
                if rebuilt_overview is not None:
                    analysis_context_pack_summary = rebuilt_summary
                    analysis_context_pack_overview = rebuilt_overview

                if reconcile_news_confidence_reason(
                    result,
                    evidence_present=bool(result.news_evidence_present),
                    news_result_count=result.news_result_count,
                    language=report_language,
                ):
                    logger.info(
                        '[%s] Agent 模式: 已按真实新闻证据修正置信度理由', code
                    )
            record_llm_run(
                success=bool(result and getattr(result, "success", True)),
                model=getattr(result, "model_used", None) if result else getattr(agent_result, "model", None),
                call_type="agent_analysis",
                duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                error_type=(
                    None
                    if result and getattr(result, "success", True)
                    else "AgentResultError"
                ),
                error_message=(
                    getattr(result, "error_message", None)
                    if result and not getattr(result, "success", True)
                    else ("Agent returned empty result" if result is None else None)
                ),
            )
            if result:
                result.query_id = query_id
            # Agent weak integrity: placeholder fill only, no LLM retry
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import check_content_integrity, apply_placeholder_fill

                pass_integrity, missing = check_content_integrity(
                    result,
                    require_phase_decision=isinstance(market_phase_summary, dict),
                )
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    logger.info(
                        "[LLM完整性] integrity_mode=agent_weak 必填字段缺失 %s，已占位补全",
                        missing,
                    )
            # chip_structure fallback (Issue #589), before save_analysis_history
            if result and chip_data is not None:
                normalize_chip_structure_availability(result, chip_data)

            # price_position fallback (same as non-agent path Step 7.7)
            if result:
                pipeline_adjustments: list[PipelineActionAdjustment] = []
                runtime_facts = getattr(agent_result, "runtime_facts", None)
                pipeline_start_signal = getattr(result, "decision_type", "hold")
                risk_application = (
                    getattr(runtime_facts, "risk_override_application", None)
                    if runtime_facts is not None
                    else None
                )
                if risk_application is not None:
                    pipeline_start_signal = risk_application.post_risk_signal.value
                initial_action_advice = getattr(result, "operation_advice", None)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=initial_action_advice,
                )
                pipeline_start_action = normalize_decision_action(
                    getattr(result, "action", None)
                )
                action_chain_valid = pipeline_start_action is not None
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                realtime_data = initial_context.get("realtime_quote", {})
                if isinstance(realtime_data, dict):
                    result.current_price = realtime_data.get("price")
                    result.change_pct = realtime_data.get("change_pct")
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="structure_and_fundamentals",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied agent adjustments for %s: %s", code, adjustments)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="market_phase",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=initial_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied agent adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="daily_market_context",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                if isinstance(market_structure_context, dict):
                    result.market_structure_context = market_structure_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                final_action = normalize_decision_action(getattr(result, "action", None))
                if isinstance(result.dashboard, dict):
                    result.dashboard.pop("agent_disagreement_explanation", None)
                if (
                    runtime_facts is not None
                    and action_chain_valid
                    and pipeline_start_action is not None
                    and final_action is not None
                ):
                    if not isinstance(result.dashboard, dict):
                        result.dashboard = {}
                    result.dashboard["agent_disagreement_explanation"] = (
                        build_pipeline_final_explanation(
                            runtime_facts=runtime_facts,
                            pipeline_start_signal=pipeline_start_signal,
                            pipeline_start_action=pipeline_start_action,
                            final_action=final_action,
                            pipeline_adjustments=pipeline_adjustments,
                            data_quality=(
                                analysis_context_pack_overview.get("data_quality")
                                if isinstance(analysis_context_pack_overview, dict)
                                else None
                            ),
                        )
                    )

            if result:
                self._append_daily_data_source(
                    result,
                    analysis_context,
                    analysis_target,
                )

            resolved_stock_name = result.name if result and result.name else stock_name

            # 保存新闻情报到数据库（Agent 工具结果仅用于 LLM 上下文，未持久化，Fixes #396）
            # 使用 search_stock_news（与 Agent 工具调用逻辑一致），仅 1 次 API 调用，无额外延迟
            if self.search_service is not None and self.search_service.is_available:
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=("" if is_index else code),
                        stock_name=resolved_stock_name,
                        max_results=5
                    )
                    # 这次补查只为持久化新闻情报（Fixes #396），刻意不写
                    # result.news_result_count：它发生在分析结束之后，与 Agent 实际
                    # 消费的证据无关，用它做披露判定会两个方向都失真。真正的计数在
                    # executor.run() 的证据作用域里收集（见上文）。
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context
                        )
                        logger.info('[%s] Agent 模式: 新闻情报已保存 %s 条', code, len(news_response.results))
                except Exception as e:
                    logger.warning('[%s] Agent 模式保存新闻情报失败: %s', code, e)

            # 保存分析历史记录
            if result and result.success:
                # 与普通链路一致：保存前的取消检查点放在 try 之外，避免被保存失败分支吞掉。
                self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                try:
                    agent_context_snapshot = self._build_context_snapshot(
                        enhanced_context={
                            **self._without_runtime_prompt_context(initial_context),
                            "stock_name": resolved_stock_name,
                        },
                        news_content=initial_context.get("news_context"),
                        news_result_count=getattr(result, "news_result_count", None),
                        realtime_quote=realtime_quote,
                        chip_data=chip_data,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    result.diagnostic_context_snapshot = agent_context_snapshot
                    agent_context_snapshot["stock_name"] = resolved_stock_name
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=initial_context.get("news_context"),
                        context_snapshot=agent_context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                    )
                    self._persist_prediction_market_evidence(
                        saved_history_id, initial_context.get("prediction_market_events") or []
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        self._persist_skill_opinion_samples_after_history_save(
                            runtime_facts=getattr(agent_result, "runtime_facts", None),
                            analysis_history_id=saved_history_id,
                            stock_code=code,
                            analysis_context_pack_overview=analysis_context_pack_overview,
                        )
                        self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=agent_context_snapshot,
                            portfolio_context=portfolio_context,
                            analysis_target=analysis_target,
                        )
                    latest_diagnostic_snapshot = current_diagnostic_snapshot()
                    if latest_diagnostic_snapshot is not None:
                        agent_context_snapshot["diagnostics"] = latest_diagnostic_snapshot
                        result.diagnostic_context_snapshot = agent_context_snapshot
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning('[%s] 保存 Agent 分析历史失败: %s', code, e)

            return result

        except TaskCancelledError:
            # 取消必须传到任务队列，不能被当成"Agent 分析失败"返回 None。
            raise
        except Exception as e:
            logger.error('[%s] Agent 分析失败: %s', code, e)
            logger.exception('[%s] Agent 详细错误信息:', code)
            return None

    def _load_agent_analysis_context(
        self,
        code: str,
        stock_name: str,
        analysis_target: Optional[AnalysisTarget] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Load daily-bar context for Agent pack summaries without blocking analysis."""
        try:
            context = self._get_analysis_context_with_market_fallback(
                code, analysis_target=analysis_target, current_time=current_time
            )
        except Exception as exc:
            logger.warning(
                "[%s] Agent analysis context load failed; daily_bars will be marked missing: %s",
                code,
                exc,
            )
            context = None

        if isinstance(context, dict) and context:
            enriched = dict(context)
            enriched.setdefault("code", code)
            if stock_name:
                enriched.setdefault("stock_name", stock_name)
            return enriched

        return {
            "code": code,
            "stock_name": stock_name,
            "data_missing": True,
            "today": {},
            "yesterday": {},
        }

    def _get_analysis_context_with_market_fallback(
        self,
        code: str,
        analysis_target: Optional[AnalysisTarget] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Load analysis context, fetching JP/KR/TW daily bars when DB has no context.

        C12: the returned context always carries the freshness of the newest
        stored daily bar relative to the expected trading session, so a stale
        fallback can be disclosed instead of being read as today's quote.
        """
        context = self.db.get_analysis_context(code)
        if isinstance(context, dict) and context:
            return self._attach_context_freshness(
                context, code, analysis_target=analysis_target, current_time=current_time
            )

        if analysis_target is not None and analysis_target.asset_type == ParseStatus.INDEX:
            market = "cn"
        else:
            market = get_market_for_stock(normalize_stock_code(code))
        if market not in {"jp", "kr", "tw"}:
            return self._attach_context_freshness(
                context, code, analysis_target=analysis_target, current_time=current_time
            )

        try:
            df, source_name = self.fetcher_manager.get_daily_data(code, days=60)
        except Exception as exc:
            logger.warning("[%s] JP/KR daily fallback fetch failed: %s", code, exc)
            return self._attach_context_freshness(
                context, code, analysis_target=analysis_target, current_time=current_time
            )

        if df is None or df.empty:
            logger.warning("[%s] JP/KR daily fallback returned empty data", code)
            return self._attach_context_freshness(
                context, code, analysis_target=analysis_target, current_time=current_time
            )

        try:
            from src.utils.daily_bar_freshness import drop_unclosed_daily_bars

            target = self._resolve_resume_target_date(
                code, current_time=current_time, analysis_target=analysis_target
            )
            df, _dropped = drop_unclosed_daily_bars(df, target, code=code)
            self.db.save_daily_data(df, code, source_name)
            refreshed = self.db.get_analysis_context(code)
            if isinstance(refreshed, dict) and refreshed:
                return self._attach_context_freshness(
                    refreshed, code, analysis_target=analysis_target, current_time=current_time
                )
        except Exception as exc:
            logger.warning("[%s] JP/KR daily fallback persistence failed: %s", code, exc)

        return self._attach_context_freshness(
            self._build_analysis_context_from_daily_df(code, df),
            code,
            analysis_target=analysis_target,
            current_time=current_time,
        )

    def _attach_context_freshness(
        self,
        context: Optional[Dict[str, Any]],
        code: str,
        *,
        analysis_target: Optional[AnalysisTarget] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(context, dict) or not context:
            return context
        if analysis_target is not None and analysis_target.asset_type == ParseStatus.INDEX:
            market = "cn"
        else:
            market = get_market_for_stock(normalize_stock_code(code))
        expected = self._resolve_resume_target_date(
            code, current_time=current_time, analysis_target=analysis_target
        )
        from src.utils.daily_bar_freshness import attach_daily_bar_freshness

        return attach_daily_bar_freshness(context, market=market, expected_as_of=expected)

    def _build_analysis_context_from_daily_df(self, code: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None

        frame = df.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        if "date" in frame.columns:
            frame = frame.sort_values("date")
        frame = frame.tail(2)
        rows = frame.to_dict(orient="records")
        if not rows:
            return None

        def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
            normalized: Dict[str, Any] = {"code": row.get("code") or code}
            for key in ("open", "high", "low", "close", "volume", "amount", "pct_chg", "ma5", "ma10", "ma20", "volume_ratio"):
                value = row.get(key)
                if pd.notna(value):
                    normalized[key] = float(value)
            row_date = row.get("date")
            if hasattr(row_date, "date"):
                row_date = row_date.date()
            normalized["date"] = row_date.isoformat() if hasattr(row_date, "isoformat") else str(row_date)
            return normalized

        today = normalize_row(rows[-1])
        context: Dict[str, Any] = {
            "code": code,
            "date": today.get("date"),
            "today": today,
        }
        if len(rows) > 1:
            yesterday = normalize_row(rows[-2])
            context["yesterday"] = yesterday
            yesterday_volume = yesterday.get("volume")
            if yesterday_volume:
                context["volume_change_ratio"] = round(float(today.get("volume", 0)) / float(yesterday_volume), 2)
            yesterday_close = yesterday.get("close")
            if yesterday_close:
                context["price_change_ratio"] = round(
                    (float(today.get("close", 0)) - float(yesterday_close)) / float(yesterday_close) * 100,
                    2,
                )
            context["ma_status"] = self.db._analyze_ma_status(SimpleNamespace(**today))

        return context

    def _load_daily_market_context(
        self,
        market: str,
        *,
        force_refresh: bool = False,
        target_date: Optional[date] = None,
    ) -> Optional[DailyMarketContext]:
        """Load/generate today's market context when market review is explicitly enabled."""
        if getattr(self, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "market_review_enabled", None) is not True:
            return None

        try:
            service = getattr(self, "_daily_market_context_service", None)
            if service is None:
                service_lock = self._get_daily_market_context_service_lock()
                with service_lock:
                    service = getattr(self, "_daily_market_context_service", None)
                    if service is None:
                        service = DailyMarketContextService(db_manager=self.db)
                        self._daily_market_context_service = service
            get_context_kwargs = {
                "region": market,
                "config": self.config,
                "notifier": self.notifier,
                "analyzer": self.analyzer,
                "search_service": self.search_service,
                "force_refresh": force_refresh,
                "allow_generate": getattr(self, "daily_market_context_allow_generate", True),
                "target_date": target_date,
            }
            current_query_id = getattr(self, "query_id", None)
            if isinstance(current_query_id, str) and current_query_id.strip():
                get_context_kwargs["current_query_id"] = current_query_id
            return service.get_context(**get_context_kwargs)
        except Exception as exc:
            logger.warning("加载大盘环境上下文失败，个股分析继续: %s", exc, exc_info=True)
            return None

    def _get_daily_market_context_service_lock(self) -> threading.Lock:
        service_lock = getattr(self, "_daily_market_context_service_lock", None)
        if service_lock is not None:
            return service_lock
        with _DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD:
            service_lock = getattr(self, "_daily_market_context_service_lock", None)
            if service_lock is None:
                service_lock = threading.Lock()
                self._daily_market_context_service_lock = service_lock
            return service_lock

    @staticmethod
    def _coerce_daily_market_context_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _attach_daily_market_context(
        target_context: Dict[str, Any],
        daily_market_context: Optional[DailyMarketContext],
        *,
        report_language: str,
    ) -> None:
        """Attach only the safe daily market summary to runtime analysis context."""
        if daily_market_context is None:
            return
        safe_context = daily_market_context.to_safe_dict()
        prompt_section = format_daily_market_context_prompt_section(
            safe_context,
            report_language=report_language,
        )
        if not prompt_section:
            return
        target_context["daily_market_context"] = safe_context
        target_context["daily_market_context_summary"] = prompt_section

    def _agent_result_to_analysis_result(
        self,
        agent_result,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
        trend_result: Optional[TrendAnalysisResult] = None,
    ) -> AnalysisResult:
        """
        将 AgentResult 转换为 AnalysisResult。
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        dash = None
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=get_unknown_text(report_language),
            operation_advice=localize_operation_advice("观望", report_language),
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name

            nested_dashboard = dash.get("dashboard") if isinstance(dash, dict) else None

            raw_score = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "sentiment_score",
                scalar=True,
            )
            if self._is_agent_field_missing(raw_score, scalar=True):
                fallback_score = self._trend_score_fallback(trend_result)
                if fallback_score is not None:
                    result.sentiment_score = fallback_score
                    self._mark_trend_fallback_source(result)
            else:
                result.sentiment_score = self._safe_int(raw_score, 50)

            raw_trend = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "trend_prediction",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_trend, scalar=True, expect_text=True):
                trend_label = self._trend_label_fallback(
                    trend_result,
                    report_language,
                )
                if trend_label:
                    result.trend_prediction = trend_label
                    self._mark_trend_fallback_source(result)
            else:
                result.trend_prediction = str(raw_trend)

            raw_advice = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "operation_advice",
                scalar=True,
                allow_dict=True,
                expect_text=True,
            )
            extracted_advice = ""
            if isinstance(raw_advice, dict):
                # LLM may return {"no_position": "...", "has_position": "..."}
                extracted_advice = self._extract_advice_text_from_dict(raw_advice)
                if extracted_advice:
                    result.operation_advice = localize_operation_advice(
                        extracted_advice,
                        report_language,
                    )
                else:
                    signal_label = self._trend_signal_fallback(
                        trend_result,
                        report_language,
                    )
                    if signal_label:
                        result.operation_advice = signal_label
                        self._mark_trend_fallback_source(result)
            elif not self._is_agent_field_missing(
                raw_advice,
                scalar=True,
                allow_dict=True,
                expect_text=True,
            ):
                result.operation_advice = str(raw_advice) if raw_advice else (localize_operation_advice("观望", report_language))
            else:
                signal_label = self._trend_signal_fallback(trend_result, report_language)
                if signal_label:
                    result.operation_advice = signal_label
                    self._mark_trend_fallback_source(result)
            from src.agent.protocols import normalize_decision_signal

            raw_decision = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "decision_type",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_decision, scalar=True, expect_text=True):
                trend_decision = self._trend_decision_fallback(trend_result)
                decision_from_advice = infer_decision_type_from_advice(
                    result.operation_advice,
                    default="",
                )
                if decision_from_advice:
                    result.decision_type = decision_from_advice
                    if (
                        self._is_agent_field_missing(
                            raw_advice,
                            scalar=True,
                            allow_dict=True,
                            expect_text=True,
                        )
                        and not extracted_advice
                        and trend_decision
                    ):
                        self._mark_trend_fallback_source(result)
                else:
                    result.decision_type = trend_decision or "hold"
                    if trend_decision:
                        self._mark_trend_fallback_source(result)
            else:
                result.decision_type = normalize_decision_signal(raw_decision)
            result.confidence_level = localize_confidence_level(
                self._agent_dashboard_value(dash, nested_dashboard, "confidence_level")
                or result.confidence_level,
                report_language,
            )
            raw_summary = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "analysis_summary",
                scalar=True,
                expect_text=True,
            )
            if not self._is_agent_field_missing(raw_summary, scalar=True, expect_text=True):
                result.analysis_summary = str(raw_summary)
            else:
                result.analysis_summary = self._summary_fallback_from_result(result, report_language)
            top_level_phase_decision = dash.get("phase_decision") if isinstance(dash, dict) else None
            if isinstance(nested_dashboard, dict) and isinstance(top_level_phase_decision, dict):
                nested_dashboard = dict(nested_dashboard)
                nested_dashboard.setdefault("phase_decision", top_level_phase_decision)

            # The AI returns a top-level dict that contains a nested 'dashboard' sub-key
            # with core_conclusion / battle_plan / intelligence.  AnalysisResult's helper
            # methods (get_sniper_points, get_core_conclusion, etc.) expect that inner
            # structure, so we unwrap it here.
            result.dashboard = nested_dashboard or dash
            self._backfill_agent_dashboard_fields(result, trend_result, report_language)
        else:
            self._apply_trend_fallback(result, trend_result, report_language)
            if trend_result is not None:
                result.analysis_summary = (
                    result.analysis_summary
                    or self._summary_fallback_from_result(result, report_language)
                )
                self._backfill_agent_dashboard_fields(result, trend_result, report_language)
            if not result.error_message:
                result.error_message = (
                    "Agent failed to generate a valid decision dashboard" if report_language == "en"
                    else "에이전트가 유효한 결정 대시보드를 생성하지 못했습니다" if report_language == "ko"
                    else "Agent 未能生成有效的决策仪表盘"
                )

        explicit_action = dash.get("action") if isinstance(dash, dict) else None
        if explicit_action is None and isinstance(getattr(result, "dashboard", None), dict):
            explicit_action = result.dashboard.get("action")
        return populate_decision_action_fields(result, explicit_action=explicit_action)

    @staticmethod
    def _refresh_decision_action_for_final_result(
        result: AnalysisResult,
        *,
        report_type: Any,
        previous_operation_advice: Any,
    ) -> AnalysisResult:
        # A guardrail may rewrite the advice after the Agent action was parsed;
        # discard that stale action before using the same resolver as the
        # downstream DecisionSignal builder.
        previous_advice = str(previous_operation_advice or "").strip()
        current_advice = str(getattr(result, "operation_advice", None) or "").strip()
        if previous_advice != current_advice:
            result.action = None
            result.action_label = None
        fields = resolve_decision_signal_action_fields(
            result,
            report_type=str(report_type or ""),
        )
        result.action = fields["action"]
        result.action_label = fields["action_label"]
        return result

    @staticmethod
    def _agent_dashboard_value(
        dash: Dict[str, Any],
        nested_dashboard: Any,
        key: str,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> Any:
        """Read a scalar from top-level agent payload, then nested dashboard fallback."""
        value = dash.get(key) if isinstance(dash, dict) else None
        if isinstance(nested_dashboard, dict) and StockAnalysisPipeline._is_agent_field_missing(
            value,
            scalar=scalar,
            allow_dict=allow_dict,
            expect_text=expect_text,
        ):
            nested_value = nested_dashboard.get(key)
            if not StockAnalysisPipeline._is_agent_field_missing(
                nested_value,
                scalar=scalar,
                allow_dict=allow_dict,
                expect_text=expect_text,
            ):
                value = nested_value
        return value

    @staticmethod
    def _extract_advice_text_from_dict(raw_advice: dict) -> str:
        for field in ("has_position", "no_position"):
            if isinstance(raw_advice.get(field), str):
                text = raw_advice[field].strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        for value in raw_advice.values():
            if isinstance(value, str):
                text = value.strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        return ""

    @staticmethod
    def _is_agent_placeholder_text(text: str) -> bool:
        if not text:
            return True
        return text.lower() in {"n/a", "na", "none", "null", "unknown", "tbd"} or text in {
            "未知",
            "待补充",
            "数据缺失",
            "无",
        }

    @staticmethod
    def _is_agent_field_missing(
        value: Any,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> bool:
        if scalar and isinstance(value, dict):
            if not allow_dict or not value:
                return True
            return not StockAnalysisPipeline._extract_advice_text_from_dict(value)
        if value is None:
            return True
        if expect_text and scalar:
            if not isinstance(value, str):
                return True
        if isinstance(value, str):
            text = value.strip()
            return StockAnalysisPipeline._is_agent_placeholder_text(text)
        if isinstance(value, dict):
            if scalar:
                return not allow_dict
            return not value
        if scalar and isinstance(value, (list, tuple, set)):
            return True
        return False

    @staticmethod
    def _trend_score_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[int]:
        if trend_result is None:
            return None
        try:
            score = int(getattr(trend_result, "signal_score", 0))
        except (TypeError, ValueError):
            return None
        return score if score > 0 else None

    @staticmethod
    def _trend_label_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        trend_status = getattr(trend_result, "trend_status", None)
        value = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if report_language != "en":
            return value
        return localize_trend_prediction(value, report_language)

    @staticmethod
    def _trend_signal_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        buy_signal = getattr(trend_result, "buy_signal", None)
        value = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        return localize_operation_advice(value, report_language)

    @staticmethod
    def _trend_decision_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[str]:
        if trend_result is None:
            return None
        signal_name = getattr(getattr(trend_result, "buy_signal", None), "name", "").lower()
        return {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }.get(signal_name)

    @staticmethod
    def _append_daily_data_source(
        result: AnalysisResult,
        context: Any,
        analysis_target: Optional[AnalysisTarget],
    ) -> None:
        """Append an index's persisted daily provider without inferring sources."""
        if (
            analysis_target is None
            or analysis_target.asset_type != ParseStatus.INDEX
        ):
            return
        if not isinstance(context, dict):
            return
        today = context.get("today")
        if not isinstance(today, dict):
            return
        source = today.get("data_source")
        if not isinstance(source, str):
            return
        source = source.strip()
        if (
            not source
            or source.casefold() == "unknown"
            or source.casefold().startswith("realtime:")
        ):
            return

        existing = result.data_sources
        if existing is None:
            existing_tokens = []
        elif isinstance(existing, str):
            existing_tokens = [
                item.strip() for item in existing.split(",") if item.strip()
            ]
        else:
            return
        token = f"daily:{source}"
        if token in existing_tokens:
            return
        result.data_sources = ",".join([*existing_tokens, token])

    @staticmethod
    def _mark_trend_fallback_source(result: AnalysisResult) -> None:
        if "trend:fallback" in (result.data_sources or ""):
            return
        result.data_sources = (
            f"{result.data_sources},trend:fallback"
            if result.data_sources
            else "trend:fallback"
        )

    @staticmethod
    def _summary_fallback_from_result(result: AnalysisResult, report_language: str) -> str:
        trend = (result.trend_prediction or "").strip()
        advice = (result.operation_advice or "").strip()
        if trend and advice:
            if report_language == "en":
                return f"Trend view: {trend}; action advice: {advice}."
            if report_language == "ko":
                return f"추세 결론: {trend}; 대응 전략: {advice}."
            return f"趋势结论：{trend}；操作建议：{advice}。"
        return ""

    def _backfill_agent_dashboard_fields(
        self,
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        dashboard = result.dashboard

        for key in (
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "decision_type",
            "confidence_level",
            "analysis_summary",
        ):
            current = dashboard.get(key)
            if key == "sentiment_score":
                if self._is_agent_field_missing(current, scalar=True):
                    dashboard[key] = getattr(result, key)
            elif self._is_agent_field_missing(current, scalar=True, expect_text=True):
                dashboard[key] = getattr(result, key)

        core = dashboard.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
            dashboard["core_conclusion"] = core
        if self._is_agent_field_missing(core.get("one_sentence"), scalar=True):
            core["one_sentence"] = result.analysis_summary or self._summary_fallback_from_result(
                result,
                report_language,
            ) or (
                "Analysis pending" if report_language == "en"
                else "분석 보완 예정" if report_language == "ko"
                else "分析待补充"
            )

        intelligence = dashboard.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
            dashboard["intelligence"] = intelligence
        risk_alerts = intelligence.get("risk_alerts")
        if (
            "risk_alerts" not in intelligence
            or self._is_agent_field_missing(risk_alerts)
            or not isinstance(risk_alerts, list)
        ):
            risk_factors = getattr(trend_result, "risk_factors", None) or []
            intelligence["risk_alerts"] = list(risk_factors)

        if result.decision_type in ("buy", "hold"):
            battle = dashboard.get("battle_plan")
            if not isinstance(battle, dict):
                battle = {}
                dashboard["battle_plan"] = battle
            sniper_points = battle.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle["sniper_points"] = sniper_points
            if self._is_agent_field_missing(sniper_points.get("stop_loss"), scalar=True):
                sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                    trend_result,
                    report_language,
                )

    @staticmethod
    def _stop_loss_fallback_from_trend(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> Any:
        levels = getattr(trend_result, "support_levels", None) if trend_result else None
        if levels:
            return levels[0]
        return get_placeholder_text(report_language)

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = localize_operation_advice("观望", report_language)
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_label = StockAnalysisPipeline._trend_label_fallback(trend_result, report_language)
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = StockAnalysisPipeline._trend_signal_fallback(
            trend_result,
            report_language,
        )
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = localize_operation_advice("观望", report_language)

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """Return True when the stock name is missing or placeholder-like."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith("股票"):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """安全地将值转换为整数。"""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re
            match = re.search(r'-?\d+', value)
            if match:
                return int(match.group())
        return default
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述
        
        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"

    @staticmethod
    def _is_realtime_quote_for_session(
        realtime_quote: Any,
        *,
        session_date: str,
        market_tz: Any = None,
    ) -> bool:
        """Require provider-side date evidence before shifting the daily-bar baseline."""
        for attr in ("session_date", "trade_date", "quote_date", "provider_timestamp"):
            raw_val = getattr(realtime_quote, attr, None)
            if raw_val is None:
                continue
            quote_date: Optional[str] = None
            if isinstance(raw_val, datetime):
                dt = raw_val
                if dt.tzinfo is not None and market_tz is not None:
                    dt = dt.astimezone(market_tz)
                quote_date = dt.date().isoformat()
            elif isinstance(raw_val, date):
                quote_date = raw_val.isoformat()
            else:
                text = str(raw_val).strip()
                if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
                    try:
                        quote_date = date.fromisoformat(text).isoformat()
                    except ValueError:
                        quote_date = None
                elif text:
                    iso_text = f"{text[:-1]}+00:00" if text.endswith("Z") else text
                    try:
                        dt = datetime.fromisoformat(iso_text)
                        if dt.tzinfo is not None and market_tz is not None:
                            dt = dt.astimezone(market_tz)
                        quote_date = dt.date().isoformat()
                    except ValueError:
                        quote_date = None
            if quote_date is not None:
                return quote_date == session_date

        # Cached providers may omit timestamps and OHLC/pre_close entirely.
        # Neither price comparisons nor fetched_at establish the quote's session.
        return False

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    def _augment_historical_with_realtime(
        self,
        df: pd.DataFrame,
        realtime_quote: Any,
        code: str,
        market: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        使用当日实时行情补齐历史 OHLCV，用于盘中 MA 计算。
        Issue #234：技术指标使用实时价格，而不是沿用昨日收盘价。
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # 非交易日可跳过实时补齐；异常情况下保持失败开放。
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        if market is None:
            market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # 使用实时收盘价更新最后一行；先复制，避免修改调用方传入的 df。
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # 追加一行虚拟的当日实时 K 线。
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    def _build_context_snapshot(
        self,
        enhanced_context: Dict[str, Any],
        news_content: Optional[str],
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution],
        news_result_count: Optional[int] = None,
        analysis_context_pack_overview: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        构建分析上下文快照
        """
        snapshot = {
            "enhanced_context": self._without_runtime_prompt_context(enhanced_context),
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
        }
        market_structure_context = enhanced_context.get("market_structure_context")
        if isinstance(market_structure_context, dict):
            snapshot["market_structure_context"] = market_structure_context
        if news_content is not None:
            snapshot["news_retrieval_content"] = news_content
        if news_result_count is not None:
            snapshot["news_result_count"] = news_result_count
        if analysis_context_pack_overview is not None:
            snapshot["analysis_context_pack_overview"] = analysis_context_pack_overview
        if market_phase_summary is not None:
            snapshot[MARKET_PHASE_SUMMARY_KEY] = market_phase_summary
        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            snapshot["diagnostics"] = diagnostic_snapshot
        if self.analysis_skills is not None:
            snapshot["skills"] = list(self.analysis_skills)
        return snapshot

    def _persist_skill_opinion_samples_after_history_save(
        self,
        *,
        runtime_facts: Any,
        analysis_history_id: int,
        stock_code: str,
        analysis_context_pack_overview: Optional[Dict[str, Any]],
    ) -> None:
        """Best-effort persistence for valid individual skill opinions."""
        opinions = getattr(runtime_facts, "skill_opinions", ()) if runtime_facts else ()
        if not opinions:
            return

        quality_level = None
        if isinstance(analysis_context_pack_overview, dict):
            quality = analysis_context_pack_overview.get("data_quality")
            if isinstance(quality, dict):
                quality_level = quality.get("level")

        try:
            from src.services.skill_opinion_sample_service import SkillOpinionSampleService

            SkillOpinionSampleService(db_manager=self.db).persist(
                analysis_history_id=analysis_history_id,
                stock_code=stock_code,
                opinions=opinions,
                data_quality_level=quality_level,
            )
        except Exception as exc:
            logger.warning(
                "Skill opinion sample persistence skipped after history save: "
                "stock_code=%s error_type=%s",
                stock_code,
                type(exc).__name__,
            )

    def _extract_decision_signal_after_history_save(
        self,
        *,
        result: AnalysisResult,
        query_id: str,
        source_report_id: int,
        report_type: str,
        context_snapshot: Dict[str, Any],
        portfolio_context: Optional[Dict[str, Any]] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> None:
        """Best-effort DecisionSignal extraction after analysis history is saved."""

        assert (
            isinstance(source_report_id, int)
            and not isinstance(source_report_id, bool)
            and source_report_id > 0
        )

        try:
            diagnostic_context = get_current_diagnostic_context()
            trace_id = (
                getattr(diagnostic_context, "trace_id", None)
                or getattr(self, "trace_id", None)
                or query_id
            )
            # 指数目标：信号以 market=cn 持久化（canonical code / 中文名由 result 携带）
            market_override = (
                "cn"
                if analysis_target is not None
                and analysis_target.asset_type == ParseStatus.INDEX
                else None
            )
            signal_result = extract_and_persist_from_analysis_result(
                result,
                context_snapshot=context_snapshot,
                source_report_id=source_report_id,
                trace_id=str(trace_id),
                query_source=getattr(self, "query_source", None) or "system",
                report_type=report_type,
                portfolio_context=portfolio_context,
                profile_source="auto_default",
                market_override=market_override,
            )
            if isinstance(signal_result, dict):
                summary = summarize_decision_signal(signal_result.get("item"))
                if summary:
                    setattr(result, "decision_signal_summary", summary)
        except Exception as exc:
            logger.warning(
                "Decision signal extraction skipped after history save: query_id=%s stock_code=%s error=%s",
                query_id,
                getattr(result, "code", None),
                exc,
                exc_info=True,
            )

    @staticmethod
    def _build_notification_run_snapshot(
        *,
        channel: str,
        status: str,
        success: bool,
        attempts: int = 1,
        error_message: Optional[Any] = None,
    ) -> Dict[str, Any]:
        payload = {
            "channel": channel,
            "status": status,
            "success": success,
            "attempts": attempts,
            "created_at": datetime.now().isoformat(),
        }
        sanitized_error = sanitize_diagnostic_text(error_message)
        if sanitized_error:
            payload["error_message_sanitized"] = sanitized_error
        return payload

    def _refresh_saved_diagnostic_snapshot(
        self,
        *,
        result: Optional[AnalysisResult] = None,
        results: Optional[List[AnalysisResult]] = None,
        fallback_code: Optional[str] = None,
        notification_run: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Patch persisted history diagnostics with notification outcomes."""
        if not getattr(self, "save_context_snapshot", True):
            return

        db = getattr(self, "db", None)
        updater = getattr(db, "update_analysis_history_diagnostics", None)
        if not callable(updater):
            return

        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            query_id = (
                diagnostic_snapshot.get("query_id")
                or getattr(result, "query_id", None)
                or getattr(self, "query_id", None)
            )
            code = (
                getattr(result, "code", None)
                or fallback_code
                or diagnostic_snapshot.get("stock_code")
            )
            if not query_id:
                return
            try:
                updater(query_id=query_id, code=code, diagnostics=diagnostic_snapshot)
            except Exception as exc:
                logger.warning("回写运行诊断快照失败（fail-open）: %s", exc)
            return

        if notification_run is None:
            return

        target_results = list(results or ([] if result is None else [result]))
        for item in target_results:
            query_id = getattr(item, "query_id", None) or getattr(self, "query_id", None)
            if not query_id:
                continue
            code = getattr(item, "code", None) or fallback_code
            try:
                updater(
                    query_id=query_id,
                    code=code,
                    notification_runs=[notification_run],
                )
            except Exception as exc:
                logger.warning("回写通知诊断快照失败（fail-open）: %s", exc)

    def _load_persisted_intelligence_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        limit: int = 6,
    ) -> Optional[str]:
        """Load locally persisted intelligence as fail-open evidence context."""
        try:
            service = IntelligenceService(config=self.config)
            service.refresh_auto_sources()
            days = max(1, int(self.config.get_effective_news_window_days() or 1))
            collected: list[Dict[str, Any]] = []
            seen_urls: set[str] = set()
            symbol_filters = [
                {"scope_type": "symbol", "scope_value": scope_value, "market": market}
                for scope_value in _symbol_scope_lookup_values(code, market)
            ]
            for filters in symbol_filters + [{"scope_type": "market", "market": market}]:
                payload = service.list_items(published_days=days, page=1, page_size=limit, **filters)
                for item in payload.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    collected.append(item)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
            if not collected:
                return None
            lines = [f"## 本地资讯证据池（{stock_name}/{code}）"]
            for idx, item in enumerate(collected[:limit], 1):
                title = str(item.get("title") or "未命名资讯").strip()
                summary = str(item.get("summary") or "").strip()
                source = str(item.get("source") or item.get("source_name") or "local-intel").strip()
                published = str(item.get("published_at") or "").strip()
                url = str(item.get("url") or "").strip()
                meta = " / ".join(part for part in (source, published) if part)
                lines.append(f"{idx}. {title}" + (f"（{meta}）" if meta else ""))
                if summary:
                    lines.append(f"   摘要：{summary[:220]}")
                if url and not url.startswith("no-url:intel:"):
                    lines.append(f"   来源：{url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("读取本地资讯证据失败（fail-open）: %s", exc)
            return None

    def _load_prediction_market_evidence(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        limit: int = 6,
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Load prediction-market text + structured events (fail-open, disabled by default)."""
        try:
            from src.services.prediction_market_service import PredictionMarketService

            service = PredictionMarketService(config=self.config)
            if not service.is_enabled():
                return None, []
            text, events = service.collect_evidence(
                symbol=code,
                stock_name=stock_name,
                market=market,
                limit=limit,
            )
            overview = service.build_overview_context(market=market)
            combined = "\n\n".join(part for part in (overview, text) if part)
            return combined or None, events
        except Exception as exc:
            logger.debug("读取预测市场证据失败（fail-open）: %s", exc)
            return None, []

    def _load_prediction_market_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        limit: int = 6,
    ) -> Optional[str]:
        """Backward-compatible text-only wrapper."""
        text, _events = self._load_prediction_market_evidence(
            code=code, stock_name=stock_name, market=market, limit=limit
        )
        return text

    def _persist_prediction_market_evidence(
        self, analysis_history_id: int, events: Optional[List[Dict[str, Any]]]
    ) -> None:
        """Best-effort sidecar write; never blocks the main analysis/history flow."""
        if not analysis_history_id or not events:
            return
        try:
            from src.repositories.prediction_market_evidence_repo import (
                PredictionMarketEvidenceRepository,
            )

            PredictionMarketEvidenceRepository(self.db).replace_for_history(
                analysis_history_id, events
            )
        except Exception as exc:
            logger.debug("写入预测市场 sidecar 失败（fail-open）: %s", exc)

    def _build_legacy_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        context: Dict[str, Any],
        enhanced_context: Dict[str, Any],
        realtime_quote: Any,
        trend_result: Optional[TrendAnalysisResult],
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]],
        news_context: Optional[str],
        news_result_count: Optional[int],
        query_id: str,
        portfolio_context: Optional[Dict[str, Any]] = None,
        prediction_market_context: Optional[str] = None,
    ) -> PipelineAnalysisArtifacts:
        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=context,
            enhanced_context=enhanced_context,
            realtime_quote=realtime_quote,
            trend_result=trend_result,
            chip_data=chip_data,
            fundamental_context=fundamental_context,
            news_context=news_context,
            news_result_count=news_result_count,
            metadata={
                "query_id": query_id,
                "trigger_source": self.query_source,
            },
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
            prediction_market_context=prediction_market_context,
        )

    @staticmethod
    def _merge_news_context(*parts: Any) -> Optional[str]:
        """合并多路新闻文本，保留来源顺序并去除重复与空段。"""
        merged: List[str] = []
        for part in parts:
            text = str(part or "").strip()
            if text and text not in merged:
                merged.append(text)
        return "\n\n".join(merged) or None

    @staticmethod
    def _agent_news_count_fallback(count: int, report_language: str) -> str:
        """搜索工具只登记了计数、没保留条目时的低敏新闻块占位。"""
        language = normalize_report_language(report_language)
        if language == "en":
            return (
                "【Agent news evidence】\n"
                f"The agent retrieved {count} news results for this analysis; "
                "titles and snippets were not retained."
            )
        if language == "ko":
            return (
                "【이번 분석에서 검색된 뉴스 정보】\n"
                f"에이전트가 이번 분석에서 뉴스 결과 {count}건을 검색했으며, "
                "제목과 요약은 보관되지 않았습니다."
            )
        return (
            "【本次分析检索到的新闻情报】\n"
            f"Agent 在本次分析中检索到 {count} 条新闻结果，未保留标题与摘要。"
        )

    def _build_agent_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        initial_context: Dict[str, Any],
        fundamental_context: Optional[Dict[str, Any]],
        query_id: str,
        base_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
        news_result_count: Optional[int] = None,
    ) -> PipelineAnalysisArtifacts:
        context_candidate = base_context
        if not isinstance(context_candidate, dict):
            context_candidate = initial_context.get("analysis_context")
        if isinstance(context_candidate, dict) and context_candidate:
            daily_context = dict(context_candidate)
            daily_context.setdefault("code", code)
            if stock_name:
                daily_context.setdefault("stock_name", stock_name)
        else:
            daily_context = {
                "code": code,
                "stock_name": stock_name,
                "data_missing": True,
                "today": {},
                "yesterday": {},
            }

        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=daily_context,
            enhanced_context={},
            realtime_quote=initial_context.get("realtime_quote"),
            trend_result=initial_context.get("trend_result"),
            chip_data=initial_context.get("chip_distribution"),
            fundamental_context=fundamental_context,
            news_context=initial_context.get("news_context"),
            news_result_count=news_result_count,
            metadata={
                "query_id": query_id,
                "trigger_source": self.query_source,
            },
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
            prediction_market_context=initial_context.get("prediction_market_context"),
        )

    def _build_analysis_context_pack_outputs(
        self,
        artifacts: PipelineAnalysisArtifacts,
        *,
        report_language: str,
        code: str,
        query_id: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            pack = AnalysisContextBuilder.build(artifacts)
            summary = format_analysis_context_pack_prompt_section(
                pack,
                report_language=report_language,
            )
            overview = render_analysis_context_pack_overview(
                pack,
                report_language=report_language,
            )
            return summary, overview
        except Exception as exc:
            logger.warning(
                "AnalysisContextPack output generation failed for %s query_id=%s: %s",
                code,
                query_id,
                exc,
            )
            return "", None

    @staticmethod
    def _without_runtime_prompt_context(context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a shallow copy without runtime-only prompt context.

        Market phase and AnalysisContextPack summaries are prompt inputs only.
        P4 stores only the separately rendered public overview at snapshot top level.
        """
        sanitized = dict(context)
        sanitized.pop("market_phase_context", None)
        sanitized.pop("portfolio_context", None)
        sanitized.pop("analysis_context_pack", None)
        sanitized.pop("analysis_context_pack_summary", None)
        sanitized.pop("daily_market_context_summary", None)
        enhanced_context = sanitized.get("enhanced_context")
        if isinstance(enhanced_context, dict):
            enhanced_context = dict(enhanced_context)
            enhanced_context.pop("daily_market_context_summary", None)
            sanitized["enhanced_context"] = enhanced_context
        return sanitized

    _without_market_phase_context = _without_runtime_prompt_context

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.

        For index targets the market is derived from the structured target
        (``market=cn``) rather than from ``get_market_for_stock`` on the
        canonical code, which would return ``None`` for CSI indices.
        """
        if analysis_target is not None and analysis_target.asset_type == ParseStatus.INDEX:
            market = "cn"
        else:
            market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                return None
        return None

    def _resolve_query_source(self, query_source: Optional[str] = None) -> str:
        """
        解析请求来源。

        优先级（从高到低）：
        1. 显式传入的 query_source：调用方明确指定时优先使用，便于覆盖推断结果或兼容未来 source_message 来自非 bot 的场景
        2. 存在 source_message 时推断为 "bot"：当前约定为机器人会话上下文
        3. 存在 query_id 时推断为 "web"：Web 触发的请求会带上 query_id
        4. 默认 "system"：定时任务或 CLI 等无上述上下文时

        Args:
            query_source: 调用方显式指定的来源，如 "bot" / "web" / "cli" / "system"

        Returns:
            归一化后的来源标识字符串，如 "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if getattr(self, "source_message", None):
            return "bot"
        if getattr(self, "query_id", None):
            return "web"
        return "system"

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        """
        生成用户查询关联信息
        """
        effective_query_id = query_id or self.query_id or ""

        context: Dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update({
                "requester_platform": self.source_message.platform or "",
                "requester_user_id": self.source_message.user_id or "",
                "requester_user_name": self.source_message.user_name or "",
                "requester_chat_id": self.source_message.chat_id or "",
                "requester_message_id": self.source_message.message_id or "",
                "requester_query": self.source_message.content or "",
            })

        return context
    
    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程

        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断
            analysis_target: 结构化分析目标（指数目标用于推导 market=cn 与能力矩阵）

        Returns:
            AnalysisResult 或 None
        """
        logger.info('========== 开始处理 %s ==========', code)

        from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
        frozen_td = self._resolve_resume_target_date(
            code, current_time=current_time, analysis_target=analysis_target
        )
        token = set_frozen_target_date(frozen_td)
        effective_query_id = analysis_query_id or getattr(self, "query_id", None) or uuid.uuid4().hex
        effective_trace_id = getattr(self, "trace_id", None) or effective_query_id
        diag_token = None
        if get_current_diagnostic_context() is None:
            diag_token = activate_run_diagnostic_context(
                trace_id=effective_trace_id,
                query_id=effective_query_id,
                stock_code=code,
                trigger_source=getattr(self, "query_source", None),
            )
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            success, error = self.fetch_and_save_stock_data(
                code, current_time=current_time, analysis_target=analysis_target
            )
            
            if not success:
                logger.warning('[%s] 数据获取失败: %s', code, error)
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info('[%s] 跳过 AI 分析（dry-run 模式）', code)
                return None
            
            analyze_kwargs = {"query_id": effective_query_id}
            if current_time is not None:
                analyze_kwargs["current_time"] = current_time
            if analysis_target is not None:
                analyze_kwargs["analysis_target"] = analysis_target
            result = self.analyze_stock(code, report_type, **analyze_kwargs)
            
            if result and result.success:
                logger.info('[%s] 分析完成: %s, 评分 %s', code, result.operation_advice, result.sentiment_score)
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning('[%s] 分析未成功: %s', code, result.error_message or '未知错误')
            
            return result
            
        except TaskCancelledError:
            # R13: 用户取消必须穿出单股处理，交给任务队列标成已取消。
            raise
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception('[%s] 处理过程发生未知异常: %s', code, e)
            return None
        finally:
            reset_run_diagnostic_context(diag_token)
            reset_frozen_target_date(token)
    
    def run(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False,
        current_time: Optional[datetime] = None,
        analysis_targets: Optional[List[AnalysisTarget]] = None,
    ) -> List[AnalysisResult]:
        """
        运行完整的分析流程

        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知

        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）
            current_time: 本轮运行冻结的参考时间；为空时在 run 内生成
            analysis_targets: 与 stock_codes 对齐的结构化分析目标列表（可选，指数目标用于推导 market=cn 与能力矩阵）

        Returns:
            分析结果列表
        """
        start_time = time.time()
        
        # 使用配置中的股票列表
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []
        
        # 过滤 unsupported 目标并按 canonical identity 去重，均发生在
        # prefetch/provider 调用前；无结构化 target 的旧入口保持原行为。
        if analysis_targets is not None:
            if len(analysis_targets) != len(stock_codes):
                raise ValueError("analysis_targets must align with stock_codes")
            supported_codes = []
            supported_targets = []
            seen_identities = set()
            for code, target in zip(stock_codes, analysis_targets):
                if target is not None and target.asset_type == ParseStatus.UNSUPPORTED:
                    logger.warning(
                        "跳过 unsupported 目标 %r（%s），不触发网络请求",
                        code,
                        target.unsupported_reason or "unsupported",
                    )
                    continue
                identity = (
                    target.canonical_id
                    if target is not None and target.asset_type == ParseStatus.INDEX
                    else code
                )
                if identity in seen_identities:
                    logger.info("跳过重复分析目标 %s（canonical_id=%s）", code, identity)
                    continue
                seen_identities.add(identity)
                supported_codes.append(code)
                supported_targets.append(target)
            stock_codes = supported_codes
            analysis_targets = supported_targets
            if not stock_codes:
                logger.warning("批次中所有目标均 unsupported，无任务可执行")
                return []

        logger.info('===== 开始分析 %s 只股票 =====', len(stock_codes))
        logger.info('股票列表: %s', ', '.join(stock_codes))
        logger.info('并发数: %s, 模式: %s', self.max_workers, '仅获取数据' if dry_run else '完整分析')

        # 冻结本轮运行的统一参考时间，避免跨市场收盘边界时同批股票使用不同目标交易日。
        resume_reference_time = current_time or datetime.now(timezone.utc)
        
        # === 批量预取实时行情（优化：避免每只股票都触发全量拉取）===
        # 只有股票数量 >= 5 时才进行预取，少量股票直接逐个查询更高效
        if len(stock_codes) >= 5:
            daily_prefetch_count = self.fetcher_manager.prefetch_daily_klines(
                stock_codes, days=PRIMARY_HISTORY_DAYS
            )
            if daily_prefetch_count > 0:
                logger.info(
                    "[prefetch] component=daily_kline_prefetch action=complete "
                    "provider=TickFlowFetcher cached=%d stock_count=%d",
                    daily_prefetch_count,
                    len(stock_codes),
                )

            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(stock_codes)
            if prefetch_count > 0:
                logger.info('已启用批量预取架构：一次拉取全市场数据，%s 只股票共享缓存', len(stock_codes))

        # Issue #455: 预取股票名称，避免并发分析时显示「股票xxxxx」
        # dry_run 仅做数据拉取，不需要名称预取，避免额外网络开销
        if not dry_run:
            self.fetcher_manager.prefetch_stock_names(stock_codes, use_bulk=False)

        # 单股推送模式（#55）：从配置读取
        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        # Issue #119: 从配置读取报告类型
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        if report_type_str == 'brief':
            report_type = ReportType.BRIEF
        elif report_type_str == 'full':
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: 从配置读取分析间隔
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        if single_stock_notify:
            logger.info(
                "已启用单股推送模式：分析仍并发执行，通知改为在结果收集侧串行发送（报告类型: %s）",
                report_type_str,
            )
        
        results: List[AnalysisResult] = []
        
        # 使用线程池并发处理
        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {}
            for idx, code in enumerate(stock_codes):
                target = (
                    analysis_targets[idx]
                    if analysis_targets is not None and idx < len(analysis_targets)
                    else None
                )
                submit_kwargs = {
                    "skip_analysis": dry_run,
                    "single_stock_notify": False,
                    "report_type": report_type,
                    "analysis_query_id": uuid.uuid4().hex,
                    "current_time": resume_reference_time,
                }
                if target is not None:
                    submit_kwargs["analysis_target"] = target
                future = executor.submit(
                    self.process_single_stock,
                    code,
                    **submit_kwargs,
                )
                future_to_code[future] = code
            
            # 收集结果
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning('[%s] 分析结果标记为失败，不计入汇总: %s', code, result.error_message or '未知原因')

                    # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # 注意：此 sleep 发生在“主线程收集 future 的循环”中，
                        # 并不会阻止线程池中的任务同时发起网络请求。
                        # 因此它对降低并发请求峰值的效果有限；真正的峰值主要由 max_workers 决定。
                        # 该行为目前保留（按需求不改逻辑）。
                        logger.debug('等待 %s 秒后继续下一只股票...', analysis_delay)
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error('[%s] 任务执行失败: %s', code, e)
        
        # 统计
        elapsed_time = time.time() - start_time
        
        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的最新可复用交易日数据已存在
            success_count = 0
            for idx, code in enumerate(stock_codes):
                target = (
                    analysis_targets[idx]
                    if analysis_targets is not None and idx < len(analysis_targets)
                    else None
                )
                if self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(
                        code,
                        current_time=resume_reference_time,
                        analysis_target=target,
                    ),
                ):
                    success_count += 1
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        logger.info("===== 分析完成 =====")
        logger.info('成功: %s, 失败: %s, 耗时: %.2f 秒', success_count, fail_count, elapsed_time)
        
        # 保存报告到本地文件（无论是否推送通知都保存）
        if results and not dry_run:
            self._save_local_report(results, report_type)

        # 发送通知（单股推送模式下跳过汇总推送，避免重复）
        if results and send_notification and not dry_run:
            if single_stock_notify:
                # 单股推送模式：只保存汇总报告，不再重复推送
                logger.info("单股推送模式：跳过汇总推送，仅保存报告到本地")
                self._send_notifications(results, report_type, skip_push=True)
            elif merge_notification:
                # 合并模式（Issue #190）：仅保存，不推送，由 main 层合并个股+大盘后统一发送
                logger.info("合并推送模式：跳过本次推送，将在个股+大盘复盘后统一发送")
                self._send_notifications(results, report_type, skip_push=True)
            else:
                self._send_notifications(results, report_type)
        
        return results

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)

        with notify_lock:
            try:
                if report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info('[%s] 使用完整报告格式', stock_code)
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info('[%s] 使用简洁报告格式', stock_code)
                else:
                    report_content = self.notifier.generate_single_stock_report(result)
                    logger.info('[%s] 使用精简报告格式', stock_code)

                save_report = getattr(self.notifier, "save_report_to_file", None)
                if callable(save_report):
                    try:
                        date_str = datetime.now().strftime('%Y%m%d')
                        filename = f"report_{date_str}_{stock_code}.md"
                        filepath = save_report(report_content, filename=filename)
                        logger.info('[%s] 单股报告已保存到本地: %s', stock_code, filepath)
                    except Exception as exc:
                        logger.warning('[%s] 单股报告保存失败: %s', stock_code, exc)

                if not self.notifier.is_available():
                    notification_run = self._build_notification_run_snapshot(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    record_notification_run(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        result=result,
                        fallback_code=fallback_code,
                        notification_run=notification_run,
                    )
                    return

                send_kwargs: Dict[str, Any] = {
                    "email_stock_codes": [stock_code],
                    "route_type": "report",
                    "severity": "info",
                    "dedup_key": f"report:single:{stock_code}:{report_type.value}",
                    "cooldown_key": f"report:single:{stock_code}:{report_type.value}",
                }
                if _supports_explicit_keyword(self.notifier.send, "structured_payload"):
                    send_kwargs["structured_payload"] = _share_image_payload(result)
                # F2: 单股推送传递 results 和 report_type，供飞书结构化渲染使用
                # 注意：这会影响企业微信的 use_wechat_dashboard 判断
                # use_wechat_dashboard = bool(results) and bool(report_type)
                # 但单股推送时企业微信应该走仪表盘，所以这是预期行为
                send_kwargs["results"] = [result]
                send_kwargs["report_type"] = report_type.value
                sent = self.notifier.send(report_content, **send_kwargs)
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                record_notification_run(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                if sent:
                    logger.info('[%s] 单股推送成功', stock_code)
                else:
                    logger.warning('[%s] 单股推送失败', stock_code)
            except Exception as e:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                record_notification_run(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                logger.error('[%s] 单股推送异常: %s', stock_code, e)

    def _save_local_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> Optional[str]:
        """保存分析报告到本地文件（与通知推送解耦）"""
        self._last_local_report_path = None
        self._last_local_report_error = None
        report: Optional[str] = None
        try:
            report = self._generate_aggregate_report(results, report_type)
        except Exception as e:
            self._last_local_report_error = str(e)
            logger.error("生成本地报告内容失败: %s", e)
            return None

        try:
            filepath = self.notifier.save_report_to_file(report)
            if filepath:
                filepath = str(filepath)
                self._last_local_report_path = filepath
                logger.info('决策仪表盘日报已保存: %s', filepath)
                return filepath
            self._last_local_report_error = "notifier returned empty report path"
            logger.error("保存本地报告失败: 通知服务未返回报告路径")
        except Exception as e:
            self._last_local_report_error = str(e)
            logger.error('保存本地报告失败: %s', e)

        logger.warning("尝试回退到本地文件系统路径保存聚合报告")
        fallback_path = self._fallback_save_report_to_file(report)
        if fallback_path:
            self._last_local_report_path = fallback_path
            self._last_local_report_error = None
            logger.warning("回退保存本地报告成功: %s", fallback_path)
            return fallback_path
        if self._last_local_report_error is None:
            self._last_local_report_error = "fallback local report save failed"
            return None
        return None

    @staticmethod
    def _default_report_filename() -> str:
        """Build default local report filename (aligned with notifier defaults)."""
        date_str = datetime.now().strftime('%Y%m%d')
        return f"report_{date_str}.md"

    @staticmethod
    def _report_output_dir() -> Path:
        """Default report output directory (kept same as NotificationService behavior)."""
        return Path(__file__).resolve().parents[2] / 'reports'

    @classmethod
    def _fallback_save_report_to_file(
        cls,
        content: str,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """Persist report content via local filesystem as resilient fallback."""
        try:
            reports_dir = cls._report_output_dir()
            reports_dir.mkdir(parents=True, exist_ok=True)
            filename = filename or cls._default_report_filename()
            filepath = reports_dir / filename
            filepath.write_text(content, encoding='utf-8')
            logger.info("决策仪表盘日报已回退写入: %s", filepath)
            return str(filepath)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.error("回退写入报告失败: %s", exc)
            return None

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        发送分析结果通知
        
        生成决策仪表盘格式的报告
        
        Args:
            results: 分析结果列表
            skip_push: 是否跳过推送（仅保存到本地，用于单股推送模式）
        """
        noise_decision = None
        noise_finalized = False
        try:
            logger.info("生成决策仪表盘日报...")
            report = self._generate_aggregate_report(results, report_type)
            
            # 跳过推送（单股推送模式 / 合并模式：报告已由 _save_local_report 保存）
            if skip_push:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                return
            
            # 推送通知：渠道分发统一收敛到 NotificationService.send_with_results，
            # 这里只负责生成报告、记录诊断与处理未配置渠道的分支。
            if self.notifier.is_available():
                def _record_channel_result(
                    channel_label: str,
                    success: bool,
                    error_message: Optional[BaseException] = None,
                    target_results: Optional[List[AnalysisResult]] = None,
                ) -> None:
                    notification_run = self._build_notification_run_snapshot(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    record_notification_run(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results if target_results is None else target_results,
                        notification_run=notification_run,
                    )

                single_share_payload = (
                    _share_image_payload(results[0]) if len(results) == 1 else None
                )
                # R24a: keep the aggregate dedup/cooldown identity per stock set;
                # otherwise two different watchlists share one cooldown window.
                report_type_key = (
                    report_type.value
                    if isinstance(report_type, ReportType)
                    else str(report_type)
                )
                codes_key = ",".join(
                    sorted(str(getattr(result, "code", "") or "") for result in results)
                )
                aggregate_noise_key = f"report:aggregate:{report_type_key}:{codes_key}"
                dispatch = self.notifier.send_with_results(
                    report,
                    route_type="report",
                    dedup_key=aggregate_noise_key,
                    cooldown_key=aggregate_noise_key,
                    structured_payload=single_share_payload,
                    results=results,
                    report_builder=lambda group: self._generate_aggregate_report(group, report_type),
                    report_type=report_type_key,
                    on_channel_result=_record_channel_result,
                )

                if dispatch.status == "no_channel":
                    notification_run = self._build_notification_run_snapshot(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    record_notification_run(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results,
                        notification_run=notification_run,
                    )
                    logger.info("通知渠道未配置，跳过推送")
                else:
                    static_runs = [
                        run for run in dispatch.channel_results
                        if run.channel != "__context__"
                    ]
                    if not static_runs and not dispatch.success:
                        status = "skipped" if dispatch.status == "noise_suppressed" else "failed"
                        notification_run = self._build_notification_run_snapshot(
                            channel="report",
                            status=status,
                            success=False,
                            attempts=0,
                        )
                        record_notification_run(
                            channel="report",
                            status=status,
                            success=False,
                            attempts=0,
                        )
                        self._refresh_saved_diagnostic_snapshot(
                            results=results,
                            notification_run=notification_run,
                        )
                    if dispatch.success:
                        logger.info("决策仪表盘推送成功")
                    else:
                        logger.warning("决策仪表盘推送失败")
            else:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                logger.info("通知渠道未配置，跳过推送")

        except Exception as e:
            notification_run = self._build_notification_run_snapshot(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            record_notification_run(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            self._refresh_saved_diagnostic_snapshot(
                results=results,
                notification_run=notification_run,
            )
            if (
                noise_decision is not None
                and not noise_finalized
                and hasattr(self.notifier, "release_noise_control")
            ):
                self.notifier.release_noise_control(noise_decision)
            import traceback
            logger.error('发送通知失败: %s\n%s', e, traceback.format_exc())

    def _generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType,
    ) -> str:
        """Generate aggregate report with backward-compatible notifier fallback."""
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            return generator(results, report_type)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)
