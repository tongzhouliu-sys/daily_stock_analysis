"""T30: 评估闭环回归测试（E12 / E14 / E15 / E16 / E10 / E11 / C7）。"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.config import Config
from src.core.backtest_engine import (
    BACKTEST_STATUS_COMPLETED,
    BacktestEngine,
    EvaluationConfig,
)
from src.storage import (
    AnalysisHistory,
    BacktestResult,
    DatabaseManager,
    DecisionSignalOutcomeRecord,
    DecisionSignalRecord,
    StockDaily,
)


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "t30_outcomes.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _bar(day: date, *, open_price: float | None = None, close: float) -> SimpleNamespace:
    return SimpleNamespace(
        date=day,
        open=open_price if open_price is not None else close,
        high=max(close, open_price if open_price is not None else close) + 1.0,
        low=min(close, open_price if open_price is not None else close) - 1.0,
        close=close,
    )


# --------------------------------------------------------------------------- E12


def test_candidates_retry_insufficient_data_records(isolated_db) -> None:
    from src.repositories.backtest_repo import BacktestRepository

    created_at = datetime.now() - timedelta(days=30)
    with isolated_db.session_scope() as session:
        history = AnalysisHistory(
            query_id="q-e12",
            code="600519",
            name="贵州茅台",
            report_type="simple",
            operation_advice="买入",
            created_at=created_at,
        )
        session.add(history)
        session.flush()
        history_id = history.id
        session.add(
            BacktestResult(
                analysis_history_id=history_id,
                code="600519",
                eval_window_days=5,
                engine_version="v1",
                eval_status="insufficient_data",
            )
        )

    repo = BacktestRepository(isolated_db)
    candidates = repo.get_candidates(
        code=None,
        min_age_days=7,
        limit=50,
        eval_window_days=5,
        engine_version="v1",
        force=False,
    )
    assert [row.id for row in candidates] == [history_id]

    with isolated_db.session_scope() as session:
        row = (
            session.query(BacktestResult)
            .filter(BacktestResult.analysis_history_id == history_id)
            .one()
        )
        row.eval_status = BACKTEST_STATUS_COMPLETED

    remaining = repo.get_candidates(
        code=None,
        min_age_days=7,
        limit=50,
        eval_window_days=5,
        engine_version="v1",
        force=False,
    )
    assert remaining == []


# --------------------------------------------------------------------------- E14


def test_not_up_has_symmetric_neutral_band() -> None:
    cfg = EvaluationConfig(eval_window_days=1, neutral_band_pct=2.0, engine_version="v1")
    neutral = BacktestEngine.evaluate_decision_signal(
        direction_expected="not_up",
        anchor_date=date(2026, 1, 5),
        start_price=100.0,
        forward_bars=[_bar(date(2026, 1, 6), close=101.5)],
        config=cfg,
    )
    hit = BacktestEngine.evaluate_decision_signal(
        direction_expected="not_up",
        anchor_date=date(2026, 1, 5),
        start_price=100.0,
        forward_bars=[_bar(date(2026, 1, 6), close=97.0)],
        config=cfg,
    )

    assert neutral["outcome"] == "neutral"
    assert neutral["direction_correct"] is None
    assert hit["outcome"] == "hit"


def test_not_up_neutral_is_excluded_from_win_rate() -> None:
    base = dict(
        eval_status="completed",
        position_recommendation="cash",
        hit_stop_loss=None,
        hit_take_profit=None,
        first_hit=None,
        first_hit_trading_days=None,
        operation_advice="卖出",
        simulated_return_pct=0.0,
    )
    rows = [
        SimpleNamespace(**base, outcome="neutral", direction_correct=None, stock_return_pct=1.5),
        SimpleNamespace(**base, outcome="win", direction_correct=True, stock_return_pct=-5.0),
    ]
    summary = BacktestEngine.compute_summary(
        results=rows,
        scope="stock",
        code="600519",
        eval_window_days=1,
        engine_version="v1",
    )
    assert summary["win_rate_pct"] == 100.0
    assert summary["neutral_count"] == 1
    # 中性记录不进命中率分母
    assert summary["win_count"] + summary["loss_count"] == 1


# --------------------------------------------------------------------------- E11


def test_stop_loss_fills_at_worse_of_open_and_stop() -> None:
    cfg = EvaluationConfig(eval_window_days=1, neutral_band_pct=2.0, engine_version="v1")
    result = BacktestEngine.evaluate_single(
        operation_advice="买入",
        analysis_date=date(2026, 1, 5),
        start_price=100.0,
        forward_bars=[_bar(date(2026, 1, 6), open_price=89.0, close=90.0)],
        stop_loss=95.0,
        take_profit=120.0,
        config=cfg,
    )
    assert result["first_hit"] == "stop_loss"
    assert result["simulated_exit_price"] == 89.0
    assert result["simulated_return_pct"] == pytest.approx(-11.0)


def test_stop_loss_intraday_break_fills_at_stop() -> None:
    cfg = EvaluationConfig(eval_window_days=1, neutral_band_pct=2.0, engine_version="v1")
    result = BacktestEngine.evaluate_single(
        operation_advice="买入",
        analysis_date=date(2026, 1, 5),
        start_price=100.0,
        forward_bars=[_bar(date(2026, 1, 6), open_price=99.0, close=94.0)],
        stop_loss=95.0,
        take_profit=None,
        config=cfg,
    )
    assert result["first_hit"] == "stop_loss"
    assert result["simulated_exit_price"] == 95.0


def test_stop_loss_above_entry_is_marked_invalid() -> None:
    cfg = EvaluationConfig(eval_window_days=1, neutral_band_pct=2.0, engine_version="v1")
    result = BacktestEngine.evaluate_single(
        operation_advice="买入",
        analysis_date=date(2026, 1, 5),
        start_price=100.0,
        forward_bars=[_bar(date(2026, 1, 6), open_price=101.0, close=102.0)],
        stop_loss=105.0,
        take_profit=None,
        config=cfg,
    )
    assert result["first_hit"] == "invalid_stop"
    assert result["hit_stop_loss"] is None
    assert result["simulated_exit_reason"] == "invalid_stop"


# --------------------------------------------------------------------------- E16


def _add_signal(db: DatabaseManager, *, code: str, horizon: str = "intraday") -> int:
    with db.session_scope() as session:
        row = DecisionSignalRecord(
            stock_code=code,
            stock_name="贵州茅台",
            market="cn",
            source_type="analysis",
            trigger_source="pipeline",
            action="buy",
            horizon=horizon,
            status="active",
            metadata_json='{"market_phase_summary": {"session_date": "2026-01-05"}}',
        )
        session.add(row)
        session.flush()
        return int(row.id)


def _seed_daily(db: DatabaseManager, *, code: str, closes: list[float]) -> None:
    with db.session_scope() as session:
        session.add(
            StockDaily(
                code=code,
                date=date(2026, 1, 5),
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
            )
        )
        for index, close in enumerate(closes, start=1):
            session.add(
                StockDaily(
                    code=code,
                    date=date(2026, 1, 5 + index),
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                )
            )


def test_intraday_horizon_is_evaluated_as_one_day(isolated_db) -> None:
    from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService

    signal_id = _add_signal(isolated_db, code="600519", horizon="intraday")
    _seed_daily(isolated_db, code="600519", closes=[102.0, 103.0, 104.0])

    item = DecisionSignalOutcomeService(db_manager=isolated_db).run_outcomes(
        signal_id=signal_id
    )["items"][0]

    assert item["eval_status"] == "completed"
    assert item["horizon"] == "1d"
    assert item["eval_window_days"] == 1


def test_expiry_uses_trading_calendar() -> None:
    from src.services.decision_signal_service import DecisionSignalService

    friday = datetime(2026, 1, 9, 16, 0)  # 周五盘后
    expires = DecisionSignalService._expires_at_from_base(
        horizon="1d",
        market="cn",
        metadata=None,
        base=friday,
    )
    assert expires == datetime(2026, 1, 12, 16, 0)  # 下周一同一时刻


def test_expiry_falls_back_to_natural_days_without_calendar() -> None:
    from src.services.decision_signal_service import DecisionSignalService

    base = datetime(2026, 1, 9, 16, 0)
    expires = DecisionSignalService._expires_at_from_base(
        horizon="3d",
        market="unknown-market",
        metadata=None,
        base=base,
    )
    assert expires == base + timedelta(days=3)


# --------------------------------------------------------------------------- E10


def test_intraday_analysis_anchors_at_same_session_close() -> None:
    from src.services.stock_daily_start_resolver import resolve_stock_daily_start

    snapshot = {
        "market_phase_summary": {
            "market": "cn",
            "phase": "intraday",
            "session_date": "2026-01-06",
            "effective_daily_bar_date": "2026-01-05",
        }
    }
    resolution = resolve_stock_daily_start(
        stock_code="600519",
        context_snapshot=snapshot,
        analysis_date=date(2026, 1, 6),
    )

    assert resolution.expected_start_date == date(2026, 1, 6)
    assert resolution.legacy_backtest_start_date == date(2026, 1, 5)


def test_premarket_analysis_keeps_previous_close_anchor() -> None:
    from src.services.stock_daily_start_resolver import resolve_stock_daily_start

    snapshot = {
        "market_phase_summary": {
            "market": "cn",
            "phase": "premarket",
            "session_date": "2026-01-06",
            "effective_daily_bar_date": "2026-01-05",
        }
    }
    resolution = resolve_stock_daily_start(
        stock_code="600519",
        context_snapshot=snapshot,
        analysis_date=date(2026, 1, 6),
    )

    assert resolution.expected_start_date == date(2026, 1, 5)


# --------------------------------------------------------------------------- E15


def test_auto_backtest_runs_signal_outcome_pass(monkeypatch) -> None:
    import main as main_module

    calls: list[str] = []
    monkeypatch.setattr(main_module, "_run_backtest_pass", lambda config: calls.append("backtest"))
    monkeypatch.setattr(
        main_module,
        "_run_signal_outcome_pass",
        lambda config: calls.append("outcomes"),
    )

    main_module._run_auto_backtest(SimpleNamespace(backtest_enabled=True))

    assert calls == ["backtest", "outcomes"]


def test_signal_outcome_pass_is_fail_open(monkeypatch) -> None:
    import main as main_module
    from src.services import decision_signal_outcome_service

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(
        decision_signal_outcome_service,
        "DecisionSignalOutcomeService",
        Boom,
    )

    # 不能抛，否则会把整轮分析判定为失败
    main_module._run_signal_outcome_pass(SimpleNamespace(backtest_enabled=True))


# --------------------------------------------------------------------------- C7


def test_recent_stock_outcomes_requires_three_completed_rows(isolated_db) -> None:
    from src.services.decision_signal_outcome_service import (
        DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
        DecisionSignalOutcomeService,
    )

    def seed(count: int) -> None:
        with isolated_db.session_scope() as session:
            for index in range(count):
                signal = DecisionSignalRecord(
                    stock_code="600519",
                    stock_name="贵州茅台",
                    market="cn",
                    source_type="analysis",
                    trigger_source="pipeline",
                    action="buy",
                    horizon="3d",
                    status="active",
                )
                session.add(signal)
                session.flush()
                session.add(
                    DecisionSignalOutcomeRecord(
                        signal_id=signal.id,
                        horizon="3d",
                        engine_version=DECISION_SIGNAL_OUTCOME_ENGINE_VERSION,
                        eval_status="completed",
                        outcome="hit",
                        direction_correct=True,
                        anchor_date=date(2026, 1, 5) + timedelta(days=index),
                        stock_return_pct=3.0,
                        action="buy",
                        market="cn",
                    )
                )

    seed(2)
    service = DecisionSignalOutcomeService(db_manager=isolated_db)
    assert service.recent_stock_outcomes(stock_code="600519") == []

    seed(1)
    items = service.recent_stock_outcomes(stock_code="600519")
    assert len(items) == 3
    assert items[0]["outcome"] == "hit"


def test_prompt_includes_outcome_history_table() -> None:
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    context = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "current_price": 1500.0,
        "outcome_history": [
            {
                "anchor_date": "2026-01-05",
                "horizon": "3d",
                "action": "buy",
                "outcome": "hit",
                "direction_correct": True,
                "stock_return_pct": 3.2,
            },
            {
                "anchor_date": "2026-01-02",
                "horizon": "3d",
                "action": "buy",
                "outcome": "miss",
                "direction_correct": False,
                "stock_return_pct": -2.4,
            },
            {
                "anchor_date": "2025-12-30",
                "horizon": "1d",
                "action": "sell",
                "outcome": "neutral",
                "direction_correct": None,
                "stock_return_pct": 0.8,
            },
        ],
    }
    prompt = GeminiAnalyzer._format_prompt(analyzer, context, "贵州茅台")

    assert "近 N 次建议与事后表现（同一只股票）" in prompt
    assert "2026-01-05" in prompt
    assert "direction correct" in prompt


def test_prompt_omits_outcome_history_below_three_rows() -> None:
    from src.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    context = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "current_price": 1500.0,
        "outcome_history": [
            {
                "anchor_date": "2026-01-05",
                "horizon": "3d",
                "action": "buy",
                "outcome": "hit",
                "direction_correct": True,
                "stock_return_pct": 3.2,
            },
        ],
    }
    prompt = GeminiAnalyzer._format_prompt(analyzer, context, "贵州茅台")

    assert "近 N 次建议与事后表现" not in prompt
