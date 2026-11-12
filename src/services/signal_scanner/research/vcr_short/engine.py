# -*- coding: utf-8 -*-
"""Main backtest engine for VCR Short strategy.

Orchestrates signal generation, portfolio management, order matching,
and performance calculation.

Data format (per instrument):
{
    "dates": [...],
    "open": [...],
    "high": [...],
    "low": [...],
    "close": [...],
    "volume": [...]
}
"""

from typing import Dict, List, Any, Optional
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.signal import SignalGenerator
from src.services.signal_scanner.research.vcr_short.portfolio import Portfolio, Position
from src.services.signal_scanner.research.vcr_short.matching import OrderMatcher
from src.services.signal_scanner.research.vcr_short.ledger import Ledger
from src.services.signal_scanner.research.vcr_short.performance import PerformanceCalculator
from src.services.signal_scanner.research.vcr_short.sensitivity import SensitivityAnalyzer
from src.services.signal_scanner.research.vcr_short.report import ReportGenerator


class VCRShortBacktestEngine:
    """Main backtest engine for Volume Climax Reversal Short strategy."""

    def __init__(self, config: VCRShortConfig):
        self.config = config
        self.signal_generator = SignalGenerator(config)
        self.order_matcher = OrderMatcher(config)
        self.performance_calculator = PerformanceCalculator(config)
        self.sensitivity_analyzer = SensitivityAnalyzer(config)

    def run(
        self,
        data: Dict[str, Dict[str, List[Any]]],
        borrow_rates: Optional[Dict[str, float]] = None,
        dividend_data: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> Dict[str, Any]:
        """Run backtest on historical data.

        Args:
            data: Dictionary of instrument_id -> {dates, open, high, low, close, volume}
            borrow_rates: Dictionary of instrument_id -> annual borrow rate
            dividend_data: Dictionary of instrument_id -> list of dividend events

        Returns:
            Complete backtest results
        """
        portfolio = Portfolio(config=self.config, cash=self.config.initial_equity)
        ledger = Ledger(config=self.config)

        if borrow_rates is None:
            borrow_rates = {}
        if dividend_data is None:
            dividend_data = {}

        # Build a unified date index and per-instrument date->index mapping
        all_dates_set = set()
        for inst_data in data.values():
            all_dates_set.update(inst_data["dates"])
        all_dates = sorted(all_dates_set)

        # Build per-instrument lookup: date -> row index
        inst_date_idx = {}
        for instrument_id, inst_data in data.items():
            date_to_idx = {d: i for i, d in enumerate(inst_data["dates"])}
            inst_date_idx[instrument_id] = date_to_idx

        # Filter to backtest range
        backtest_dates = [d for d in all_dates if self.config.start_date <= d <= self.config.end_date]

        # Tracking
        all_signals = []
        all_trades = []
        pending_signals: Dict[str, List[Dict[str, Any]]] = {}

        for i, current_date in enumerate(backtest_dates):
            current_prices: Dict[str, float] = {}
            current_opens: Dict[str, float] = {}
            current_highs: Dict[str, float] = {}
            current_lows: Dict[str, float] = {}

            # Collect current day data
            for instrument_id, inst_data in data.items():
                date_to_idx = inst_date_idx[instrument_id]
                if current_date in date_to_idx:
                    idx = date_to_idx[current_date]
                    current_prices[instrument_id] = inst_data["close"][idx]
                    current_opens[instrument_id] = inst_data["open"][idx]
                    current_highs[instrument_id] = inst_data["high"][idx]
                    current_lows[instrument_id] = inst_data["low"][idx]

            # === Process Pending Signals (Entry) ===
            if current_date in pending_signals:
                for signal in pending_signals[current_date]:
                    instrument_id = signal["instrument_id"]

                    if instrument_id not in current_opens:
                        continue

                    can_enter, reason = self.order_matcher.check_entry_conditions(
                        signal_close=signal["close"],
                        signal_high=signal["high"],
                        next_open=current_opens[instrument_id]
                    )

                    if not can_enter:
                        continue

                    entry_price = self.order_matcher.calculate_entry_price(
                        current_opens[instrument_id], is_short=True
                    )

                    stop_loss = self.order_matcher.calculate_initial_stop_loss(
                        entry_price=entry_price,
                        signal_high=signal["high"],
                        atr=signal["atr"]
                    )

                    acceptable, risk_per_share = self.order_matcher.check_initial_risk(
                        entry_price=entry_price,
                        stop_loss=stop_loss
                    )

                    if not acceptable:
                        continue

                    equity = portfolio.current_equity(current_prices)
                    shares = self.order_matcher.calculate_position_size(
                        equity=equity,
                        risk_per_share=risk_per_share,
                        entry_price=entry_price
                    )

                    if shares <= 0:
                        continue

                    if not portfolio.can_open_position(
                        instrument_id=instrument_id,
                        entry_price=entry_price,
                        shares=shares,
                        current_prices=current_prices
                    ):
                        continue

                    tp1, tp2 = self.order_matcher.calculate_take_profits(
                        entry_price=entry_price,
                        signal_close=signal["close"],
                        signal_high=signal["high"],
                        signal_low=signal["low"],
                        atr=signal["atr"],
                        bottom_close=signal.get("bottom_close")
                    )

                    borrow_rate = borrow_rates.get(instrument_id, self.config.default_borrow_rate)

                    position = Position(
                        instrument_id=instrument_id,
                        signal_date=signal["signal_date"],
                        entry_date=current_date,
                        entry_price=entry_price,
                        shares=shares,
                        initial_risk_per_share=risk_per_share,
                        initial_stop_loss=stop_loss,
                        current_stop_loss=stop_loss,
                        take_profit_1=tp1,
                        take_profit_2=tp2,
                        shares_remaining=shares,
                        borrow_rate=borrow_rate
                    )

                    if portfolio.open_position(position):
                        all_signals.append({
                            "signal_date": signal["signal_date"],
                            "entry_date": current_date,
                            "instrument_id": instrument_id,
                            "entry_price": entry_price,
                            "shares": shares,
                            "stop_loss": stop_loss,
                            "tp1": tp1,
                            "tp2": tp2,
                        })

            # === Process Existing Positions (Exit) ===
            positions_to_close = []

            for instrument_id, position in list(portfolio.positions.items()):
                if instrument_id not in current_prices:
                    continue

                # Update trailing stop
                if i > 0:
                    prev_date = backtest_dates[i - 1]
                    date_to_idx = inst_date_idx.get(instrument_id, {})
                    if prev_date in date_to_idx:
                        prev_idx = date_to_idx[prev_date]
                        prev_high = data[instrument_id]["high"][prev_idx]
                        self.order_matcher.update_trailing_stop(
                            position=position,
                            current_low=current_lows[instrument_id],
                            prev_high=prev_high
                        )

                exit_info = self.order_matcher.check_exit_conditions(
                    position=position,
                    current_open=current_opens[instrument_id],
                    current_high=current_highs[instrument_id],
                    current_low=current_lows[instrument_id],
                    current_close=current_prices[instrument_id]
                )

                if exit_info["should_exit"]:
                    positions_to_close.append({
                        "instrument_id": instrument_id,
                        "exit_price": exit_info["exit_price"],
                        "exit_reason": exit_info["exit_reason"],
                        "exit_shares": exit_info["exit_shares"],
                    })

                    if exit_info["exit_reason"].value == "take_profit_1":
                        position.tp1_hit = True

            # Execute exits
            for close_info in positions_to_close:
                closed_pos = portfolio.close_position(
                    instrument_id=close_info["instrument_id"],
                    exit_date=current_date,
                    exit_price=close_info["exit_price"],
                    shares=close_info["exit_shares"],
                    reason=close_info["exit_reason"]
                )

                if closed_pos:
                    borrow_fees = ledger.get_total_borrow_fees(closed_pos.instrument_id)
                    dividends = ledger.get_total_dividends(closed_pos.instrument_id)

                    net_pnl = ledger.calculate_net_pnl(
                        entry_price=closed_pos.entry_price,
                        exit_price=closed_pos.exit_price,
                        shares=closed_pos.shares,
                        borrow_fees=borrow_fees,
                        dividends=dividends,
                        commission=self.config.commission_per_trade
                    )

                    all_trades.append({
                        "instrument_id": closed_pos.instrument_id,
                        "signal_date": closed_pos.signal_date,
                        "entry_date": closed_pos.entry_date,
                        "exit_date": closed_pos.exit_date,
                        "entry_price": closed_pos.entry_price,
                        "exit_price": closed_pos.exit_price,
                        "shares": closed_pos.shares,
                        "exit_reason": closed_pos.exit_reason.value,
                        "gross_pnl": (closed_pos.entry_price - closed_pos.exit_price) * closed_pos.shares,
                        "borrow_fees": borrow_fees,
                        "dividends": dividends,
                        "net_pnl": net_pnl,
                    })

            # === Accrue Borrow Fees ===
            ledger.accrue_borrow_fees(
                date=current_date,
                positions=portfolio.positions,
                borrow_rates=borrow_rates
            )

            # === Process Dividends ===
            for instrument_id, position in portfolio.positions.items():
                if instrument_id in dividend_data:
                    for div_event in dividend_data[instrument_id]:
                        if div_event["date"] == current_date:
                            ledger.record_dividend(
                                ex_dividend_date=current_date,
                                instrument_id=instrument_id,
                                shares=position.shares_remaining,
                                dividend_per_share=div_event["amount"]
                            )
                            position.dividends_paid += div_event["amount"] * position.shares_remaining

            # === Generate Signals for Next Day ===
            if i < len(backtest_dates) - 1:
                next_date = backtest_dates[i + 1]
                pending_signals[next_date] = []

                for instrument_id, inst_data in data.items():
                    date_to_idx = inst_date_idx[instrument_id]
                    if current_date not in date_to_idx:
                        continue

                    current_idx = date_to_idx[current_date]

                    # Need enough history for warmup
                    if current_idx + 1 < self.config.warmup_days:
                        continue

                    # Build history arrays up to current_date
                    hist_len = current_idx + 1
                    dates_hist = inst_data["dates"][:hist_len]
                    opens_hist = inst_data["open"][:hist_len]
                    highs_hist = inst_data["high"][:hist_len]
                    lows_hist = inst_data["low"][:hist_len]
                    closes_hist = inst_data["close"][:hist_len]
                    volumes_hist = inst_data["volume"][:hist_len]

                    signal_result = self.signal_generator.evaluate(
                        instrument_id=instrument_id,
                        dates=dates_hist,
                        open_prices=opens_hist,
                        high_prices=highs_hist,
                        low_prices=lows_hist,
                        close_prices=closes_hist,
                        volumes=volumes_hist,
                        signal_index=hist_len - 1
                    )

                    if signal_result.passed:
                        pending_signals[next_date].append({
                            "instrument_id": instrument_id,
                            "signal_date": current_date,
                            "close": closes_hist[-1],
                            "high": highs_hist[-1],
                            "low": lows_hist[-1],
                            "atr": signal_result.intermediate_values.get("atr_t"),
                            "bottom_close": signal_result.intermediate_values.get("bottom_launch_close"),
                        })

            # === Update Portfolio ===
            portfolio.update_daily(
                current_date=current_date,
                current_prices=current_prices,
                day_highs=current_highs,
                day_lows=current_lows
            )

        # === Calculate Performance ===
        equity_curve = portfolio.equity_history
        performance_metrics = self.performance_calculator.calculate_all_metrics(
            equity_curve=equity_curve,
            closed_positions=all_trades,
            start_date=self.config.start_date,
            end_date=self.config.end_date
        )

        open_positions = [
            {
                "instrument_id": pos.instrument_id,
                "signal_date": pos.signal_date,
                "entry_date": pos.entry_date,
                "entry_price": pos.entry_price,
                "shares_remaining": pos.shares_remaining,
                "unrealized_pnl": pos.unrealized_pnl,
            }
            for pos in portfolio.positions.values()
        ]

        data_info = {
            "instruments_count": len(data),
            "start_date": backtest_dates[0] if backtest_dates else None,
            "end_date": backtest_dates[-1] if backtest_dates else None,
            "trading_days": len(backtest_dates),
        }

        return {
            "performance": performance_metrics,
            "trades": all_trades,
            "open_positions": open_positions,
            "equity_curve": equity_curve,
            "signals": all_signals,
            "ledger": ledger.to_dict(),
            "data_info": data_info,
            "config": self.config.to_dict(),
        }

    def run_sensitivity_analysis(
        self,
        data: Dict[str, Dict[str, List[Any]]],
        borrow_rates: Optional[Dict[str, float]] = None,
        dividend_data: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> Dict[str, Any]:
        """Run parameter sensitivity analysis."""
        default_result = self.run(data, borrow_rates, dividend_data)

        parameter_grid = self.sensitivity_analyzer.generate_parameter_grid()

        sensitivity_results = []
        for variant in parameter_grid:
            variant_engine = VCRShortBacktestEngine(variant["config"])
            variant_result = variant_engine.run(data, borrow_rates, dividend_data)

            sensitivity_results.append({
                "parameter": variant["parameter"],
                "value": variant["value"],
                "metrics": variant_result["performance"],
            })

        formatted_results = self.sensitivity_analyzer.format_results(
            default_result["performance"],
            sensitivity_results
        )

        critical_params = self.sensitivity_analyzer.identify_critical_parameters(
            sensitivity_results
        )

        return {
            "default": default_result,
            "sensitivity": formatted_results,
            "critical_parameters": critical_params,
        }
