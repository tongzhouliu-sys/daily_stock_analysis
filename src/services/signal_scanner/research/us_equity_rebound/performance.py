"""Performance metrics calculation."""

from __future__ import annotations

from typing import List, Dict, Any
from decimal import Decimal
from .portfolio import Portfolio, Trade


def calculate_total_return(portfolio: Portfolio) -> float:
    """Calculate total return as (final_equity / initial_equity) - 1."""
    if not portfolio.daily_snapshots:
        return 0.0
    final_equity = portfolio.daily_snapshots[-1].equity
    return (final_equity / portfolio.initial_equity - 1) * 100


def calculate_cagr(portfolio: Portfolio, start_date: str, end_date: str) -> float:
    """Calculate Compound Annual Growth Rate.
    
    CAGR = (final_equity / initial_equity) ^ (365.2425 / days) - 1
    """
    if not portfolio.daily_snapshots:
        return 0.0
    
    from datetime import date
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days = (end - start).days
    
    if days <= 0:
        return 0.0
    
    final_equity = portfolio.daily_snapshots[-1].equity
    ratio = final_equity / portfolio.initial_equity
    power = 365.2425 / days
    cagr = ratio ** power - 1
    
    return cagr * 100


def calculate_sharpe_ratio(portfolio: Portfolio, risk_free_rate: float = 0.0) -> float:
    """Calculate Sharpe ratio.
    
    Sharpe = sqrt(252) * mean(daily_excess_return) / stdev(daily_excess_return)
    """
    if not portfolio.daily_snapshots:
        return 0.0
    
    daily_returns = [s.daily_return / 100 for s in portfolio.daily_snapshots]
    daily_rf = risk_free_rate / 252
    
    excess_returns = [r - daily_rf for r in daily_returns]
    
    if len(excess_returns) < 2:
        return 0.0
    
    mean_excess = sum(excess_returns) / len(excess_returns)
    variance = sum((r - mean_excess) ** 2 for r in excess_returns) / (len(excess_returns) - 1)
    std_excess = variance ** 0.5
    
    if std_excess == 0:
        return 0.0
    
    sharpe = (252 ** 0.5) * mean_excess / std_excess
    return sharpe


def calculate_max_drawdown(portfolio: Portfolio) -> Dict[str, Any]:
    """Calculate maximum drawdown with peak/valley dates."""
    if not portfolio.daily_snapshots:
        return {
            "max_drawdown_pct": 0.0,
            "peak_date": None,
            "valley_date": None,
            "recovery_date": None,
        }
    
    max_dd = 0.0
    peak_idx = 0
    valley_idx = 0
    current_peak_idx = 0
    
    for i, snapshot in enumerate(portfolio.daily_snapshots):
        if snapshot.equity > portfolio.daily_snapshots[current_peak_idx].equity:
            current_peak_idx = i
        
        dd = snapshot.drawdown
        if dd < max_dd:
            max_dd = dd
            peak_idx = current_peak_idx
            valley_idx = i
    
    # Find recovery date
    recovery_date = None
    if max_dd < 0:
        peak_equity = portfolio.daily_snapshots[peak_idx].equity
        for i in range(valley_idx, len(portfolio.daily_snapshots)):
            if portfolio.daily_snapshots[i].equity >= peak_equity:
                recovery_date = portfolio.daily_snapshots[i].date
                break
    
    return {
        "max_drawdown_pct": max_dd,
        "peak_date": portfolio.daily_snapshots[peak_idx].date,
        "valley_date": portfolio.daily_snapshots[valley_idx].date,
        "recovery_date": recovery_date,
    }


def calculate_win_rate(trades: List[Trade]) -> Dict[str, Any]:
    """Calculate win rate and profit/loss statistics."""
    if not trades:
        return {
            "win_rate_pct": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
        }
    
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0
    
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    
    total_wins = sum(t.pnl for t in wins)
    total_losses = abs(sum(t.pnl for t in losses))
    profit_factor = total_wins / total_losses if total_losses > 0 else 0.0
    
    return {
        "win_rate_pct": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
    }


def calculate_exit_breakdown(trades: List[Trade]) -> Dict[str, Any]:
    """Break down trades by exit reason."""
    breakdown = {}
    
    for trade in trades:
        reason = trade.exit_reason
        if reason not in breakdown:
            breakdown[reason] = {
                "count": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
            }
        
        breakdown[reason]["count"] += 1
        breakdown[reason]["total_pnl"] += trade.pnl
    
    for reason in breakdown:
        count = breakdown[reason]["count"]
        if count > 0:
            breakdown[reason]["avg_pnl"] = breakdown[reason]["total_pnl"] / count
    
    return breakdown


def calculate_annual_returns(portfolio: Portfolio) -> List[Dict[str, Any]]:
    """Calculate returns by year."""
    if not portfolio.daily_snapshots:
        return []
    
    annual = {}
    
    for snapshot in portfolio.daily_snapshots:
        year = snapshot.date[:4]
        if year not in annual:
            annual[year] = {
                "year": year,
                "start_equity": snapshot.equity,
                "end_equity": snapshot.equity,
            }
        annual[year]["end_equity"] = snapshot.equity
    
    results = []
    prev_year = None
    
    for year in sorted(annual.keys()):
        data = annual[year]
        
        if prev_year is None:
            # First year
            start_equity = portfolio.initial_equity
        else:
            start_equity = annual[prev_year]["end_equity"]
        
        end_equity = data["end_equity"]
        return_pct = (end_equity / start_equity - 1) * 100 if start_equity > 0 else 0.0
        
        results.append({
            "year": year,
            "start_equity": start_equity,
            "end_equity": end_equity,
            "return_pct": return_pct,
        })
        
        prev_year = year
    
    return results


def generate_performance_report(portfolio: Portfolio, config: ReboundConfig) -> Dict[str, Any]:
    """Generate complete performance report."""
    from .config import ReboundConfig
    
    trades = portfolio.closed_trades
    
    return {
        "total_return_pct": calculate_total_return(portfolio),
        "cagr_pct": calculate_cagr(portfolio, config.start_date, config.end_date),
        "sharpe_ratio": calculate_sharpe_ratio(portfolio),
        "max_drawdown": calculate_max_drawdown(portfolio),
        "win_rate": calculate_win_rate(trades),
        "exit_breakdown": calculate_exit_breakdown(trades),
        "annual_returns": calculate_annual_returns(portfolio),
        "total_trades": len(trades),
        "open_positions": len(portfolio.positions),
        "final_equity": portfolio.daily_snapshots[-1].equity if portfolio.daily_snapshots else config.initial_equity,
    }
