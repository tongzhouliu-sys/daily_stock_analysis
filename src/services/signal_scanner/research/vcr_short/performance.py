# -*- coding: utf-8 -*-
"""Performance metrics calculation for VCR Short strategy.

Calculates returns, Sharpe ratio, Sortino ratio, Calmar ratio,
maximum drawdown, and other performance metrics.
"""

from typing import List, Dict, Any, Optional
import math
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig


class PerformanceCalculator:
    """Calculates performance metrics from equity curve and trades."""

    def __init__(self, config: VCRShortConfig):
        self.config = config

    def calculate_returns(self, equity_curve: List[Dict[str, Any]]) -> List[float]:
        """Calculate daily returns from equity curve.

        Args:
            equity_curve: List of {date, equity} dictionaries

        Returns:
            List of daily returns (percentage change)
        """
        if len(equity_curve) < 2:
            return []

        returns = []
        for i in range(1, len(equity_curve)):
            prev_equity = equity_curve[i-1]["equity"]
            curr_equity = equity_curve[i]["equity"]

            if prev_equity > 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)
            else:
                returns.append(0.0)

        return returns

    def calculate_cagr(
        self,
        start_equity: float,
        end_equity: float,
        start_date: str,
        end_date: str
    ) -> Optional[float]:
        """Calculate Compound Annual Growth Rate.

        CAGR = (end_equity / start_equity) ^ (365.25 / days) - 1

        Args:
            start_equity: Initial equity
            end_equity: Final equity
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            CAGR as decimal, or None if invalid
        """
        from datetime import datetime

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            days = (end - start).days

            if days <= 0 or start_equity <= 0 or end_equity <= 0:
                return None

            cagr = (end_equity / start_equity) ** (365.25 / days) - 1
            return cagr
        except Exception:
            return None

    def calculate_max_drawdown(self, equity_curve: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate maximum drawdown from peak.

        Args:
            equity_curve: List of {date, equity} dictionaries

        Returns:
            Dictionary with max_drawdown, peak_date, trough_date
        """
        if not equity_curve:
            return {
                "max_drawdown": 0.0,
                "peak_date": None,
                "trough_date": None,
                "peak_equity": None,
                "trough_equity": None,
            }

        peak = equity_curve[0]["equity"]
        peak_date = equity_curve[0]["date"]
        max_dd = 0.0
        max_dd_peak_date = peak_date
        max_dd_trough_date = peak_date

        for item in equity_curve:
            equity = item["equity"]
            date = item["date"]

            if equity > peak:
                peak = equity
                peak_date = date

            if peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
                    max_dd_peak_date = peak_date
                    max_dd_trough_date = date

        return {
            "max_drawdown": max_dd,
            "peak_date": max_dd_peak_date,
            "trough_date": max_dd_trough_date,
            "peak_equity": peak,
            "trough_equity": equity_curve[-1]["equity"] if equity_curve else None,
        }

    def calculate_sharpe_ratio(
        self,
        returns: List[float],
        risk_free_rate: float = 0.0
    ) -> Optional[float]:
        """Calculate Sharpe Ratio (annualized).

        Sharpe = (mean_return - risk_free_rate) / std_return * sqrt(252)

        Args:
            returns: List of daily returns
            risk_free_rate: Annual risk-free rate (default 0)

        Returns:
            Annualized Sharpe ratio, or None if insufficient data
        """
        if len(returns) < 2:
            return None

        # Convert annual risk-free rate to daily
        daily_rf = (1 + risk_free_rate) ** (1/252) - 1 if risk_free_rate > 0 else 0.0

        # Calculate excess returns
        excess_returns = [r - daily_rf for r in returns]

        # Calculate mean and std
        mean_excess = sum(excess_returns) / len(excess_returns)
        variance = sum((r - mean_excess) ** 2 for r in excess_returns) / (len(excess_returns) - 1)
        std_excess = math.sqrt(variance) if variance > 0 else 0.0

        if std_excess == 0:
            return None

        # Annualize
        sharpe = (mean_excess / std_excess) * math.sqrt(252)
        return sharpe

    def calculate_sortino_ratio(
        self,
        returns: List[float],
        target_return: float = 0.0
    ) -> Optional[float]:
        """Calculate Sortino Ratio (annualized).

        Sortino = (mean_return - target) / downside_std * sqrt(252)

        Args:
            returns: List of daily returns
            target_return: Target return (default 0)

        Returns:
            Annualized Sortino ratio, or None if insufficient data
        """
        if len(returns) < 2:
            return None

        # Convert annual target to daily
        daily_target = (1 + target_return) ** (1/252) - 1 if target_return > 0 else 0.0

        # Calculate downside deviation
        downside_returns = [min(0, r - daily_target) for r in returns]
        downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_std = math.sqrt(downside_variance) if downside_variance > 0 else 0.0

        if downside_std == 0:
            return None

        # Calculate mean return
        mean_return = sum(returns) / len(returns)

        # Annualize
        sortino = ((mean_return - daily_target) / downside_std) * math.sqrt(252)
        return sortino

    def calculate_calmar_ratio(
        self,
        cagr: Optional[float],
        max_drawdown: float
    ) -> Optional[float]:
        """Calculate Calmar Ratio.

        Calmar = CAGR / max_drawdown

        Args:
            cagr: Compound Annual Growth Rate
            max_drawdown: Maximum drawdown (as positive decimal)

        Returns:
            Calmar ratio, or None if invalid
        """
        if cagr is None or max_drawdown == 0:
            return None

        return cagr / max_drawdown

    def calculate_trade_statistics(
        self,
        closed_positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calculate trade-level statistics.

        Args:
            closed_positions: List of closed position dictionaries

        Returns:
            Dictionary with win rate, avg win, avg loss, profit factor, etc.
        """
        if not closed_positions:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": None,
                "avg_win": None,
                "avg_loss": None,
                "profit_factor": None,
                "expectancy": None,
                "max_consecutive_losses": 0,
            }

        # Separate wins and losses
        wins = [p for p in closed_positions if p.get("realized_pnl", 0) > 0]
        losses = [p for p in closed_positions if p.get("realized_pnl", 0) < 0]

        total_trades = len(closed_positions)
        winning_trades = len(wins)
        losing_trades = len(losses)

        win_rate = winning_trades / total_trades if total_trades > 0 else None

        # Average win/loss
        avg_win = sum(p["realized_pnl"] for p in wins) / len(wins) if wins else None
        avg_loss = abs(sum(p["realized_pnl"] for p in losses) / len(losses)) if losses else None

        # Profit factor
        gross_profit = sum(p["realized_pnl"] for p in wins) if wins else 0
        gross_loss = abs(sum(p["realized_pnl"] for p in losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        # Expectancy
        total_pnl = sum(p.get("realized_pnl", 0) for p in closed_positions)
        expectancy = total_pnl / total_trades if total_trades > 0 else None

        # Max consecutive losses
        max_consec_losses = 0
        current_consec = 0
        for p in closed_positions:
            if p.get("realized_pnl", 0) < 0:
                current_consec += 1
                max_consec_losses = max(max_consec_losses, current_consec)
            else:
                current_consec = 0

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "max_consecutive_losses": max_consec_losses,
            "total_pnl": total_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        }

    def calculate_all_metrics(
        self,
        equity_curve: List[Dict[str, Any]],
        closed_positions: List[Dict[str, Any]],
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """Calculate all performance metrics.

        Args:
            equity_curve: List of {date, equity} dictionaries
            closed_positions: List of closed position dictionaries
            start_date: Backtest start date
            end_date: Backtest end date

        Returns:
            Dictionary with all performance metrics
        """
        if not equity_curve:
            return {}

        # Calculate returns
        returns = self.calculate_returns(equity_curve)

        # Calculate CAGR
        start_equity = equity_curve[0]["equity"]
        end_equity = equity_curve[-1]["equity"]
        cagr = self.calculate_cagr(start_equity, end_equity, start_date, end_date)

        # Calculate max drawdown
        drawdown_info = self.calculate_max_drawdown(equity_curve)

        # Calculate ratios
        sharpe = self.calculate_sharpe_ratio(returns)
        sortino = self.calculate_sortino_ratio(returns)
        calmar = self.calculate_calmar_ratio(cagr, drawdown_info["max_drawdown"])

        # Calculate trade statistics
        trade_stats = self.calculate_trade_statistics(closed_positions)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "start_equity": start_equity,
            "end_equity": end_equity,
            "cagr": cagr,
            "max_drawdown": drawdown_info["max_drawdown"],
            "drawdown_peak_date": drawdown_info["peak_date"],
            "drawdown_trough_date": drawdown_info["trough_date"],
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            **trade_stats,
        }
