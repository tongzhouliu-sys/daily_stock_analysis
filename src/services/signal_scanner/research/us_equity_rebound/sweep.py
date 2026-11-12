"""Parameter sweep for sensitivity analysis."""

from __future__ import annotations

from typing import List, Dict, Any
from itertools import product
from .config import ReboundConfig, SWEEP_GRID
from .engine import BacktestEngine


def generate_parameter_combinations() -> List[Dict[str, Any]]:
    """Generate all 81 parameter combinations for sweep.
    
    Returns:
        List of parameter override dicts
    """
    keys = list(SWEEP_GRID.keys())
    values = list(SWEEP_GRID.values())
    
    combinations = []
    for combo in product(*values):
        override = dict(zip(keys, combo))
        combinations.append(override)
    
    return combinations


def run_sweep(
    trading_dates: List[str],
    market_data: Dict[str, Dict[str, List[float]]],
    spy_data: Dict[str, List[float]],
    universe: List[str],
    base_config: ReboundConfig,
    corporate_actions: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run parameter sweep across all combinations.
    
    Args:
        trading_dates: List of trading dates
        market_data: Market data dict
        spy_data: SPY data dict
        universe: List of security IDs
        base_config: Base configuration
        corporate_actions: Corporate actions list
    
    Returns:
        Sweep results with all combinations
    """
    if corporate_actions is None:
        corporate_actions = []
    
    combinations = generate_parameter_combinations()
    results = []
    
    for idx, override in enumerate(combinations, 1):
        # Create config with overrides
        config = base_config.with_overrides(**override)
        
        # Run backtest
        engine = BacktestEngine(config)
        backtest_results = engine.run(
            trading_dates=trading_dates,
            market_data=market_data,
            spy_data=spy_data,
            universe=universe,
            corporate_actions=corporate_actions,
        )
        
        # Extract key metrics
        result = {
            "combination_id": idx,
            "parameters": override,
            "total_return_pct": backtest_results["performance"]["total_return_pct"],
            "cagr_pct": backtest_results["performance"]["cagr_pct"],
            "sharpe_ratio": backtest_results["performance"]["sharpe_ratio"],
            "max_drawdown_pct": backtest_results["performance"]["max_drawdown"]["max_drawdown_pct"],
            "total_trades": backtest_results["performance"]["total_trades"],
            "win_rate_pct": backtest_results["performance"]["win_rate"]["win_rate_pct"],
            "profit_factor": backtest_results["performance"]["win_rate"]["profit_factor"],
        }
        
        results.append(result)
    
    # Find best combination by Sharpe ratio
    best = max(results, key=lambda r: r["sharpe_ratio"])
    
    return {
        "total_combinations": len(results),
        "results": results,
        "best_combination": best,
        "sweep_grid": SWEEP_GRID,
    }


def generate_sensitivity_report(sweep_results: Dict[str, Any], output_path: str):
    """Generate sensitivity analysis report.
    
    Args:
        sweep_results: Results from run_sweep
        output_path: Output directory path
    """
    import json
    import csv
    from pathlib import Path
    
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    
    # Write sweep summary
    summary = {
        "total_combinations": sweep_results["total_combinations"],
        "best_combination": sweep_results["best_combination"],
        "sweep_grid": sweep_results["sweep_grid"],
    }
    
    with open(output / "sweep_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    # Write all results to CSV
    if sweep_results["results"]:
        fieldnames = [
            "combination_id",
            "atr_stop_multiple",
            "max_holding_sessions",
            "rsi_threshold",
            "volume_multiple",
            "total_return_pct",
            "cagr_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "total_trades",
            "win_rate_pct",
            "profit_factor",
        ]
        
        rows = []
        for r in sweep_results["results"]:
            row = {
                "combination_id": r["combination_id"],
                **r["parameters"],
                "total_return_pct": r["total_return_pct"],
                "cagr_pct": r["cagr_pct"],
                "sharpe_ratio": r["sharpe_ratio"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "total_trades": r["total_trades"],
                "win_rate_pct": r["win_rate_pct"],
                "profit_factor": r["profit_factor"],
            }
            rows.append(row)
        
        with open(output / "sensitivity.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    
    # Generate markdown report
    lines = [
        "# 参数敏感性分析报告",
        "",
        f"**扫描组合数**: {sweep_results['total_combinations']}",
        "",
        "## 扫描参数范围",
        "",
        "```json",
        json.dumps(sweep_results["sweep_grid"], indent=2),
        "```",
        "",
        "## 最优参数组合",
        "",
        "```json",
        json.dumps(sweep_results["best_combination"], indent=2),
        "```",
        "",
        "## 统计摘要",
        "",
    ]
    
    # Calculate statistics
    returns = [r["total_return_pct"] for r in sweep_results["results"]]
    sharpes = [r["sharpe_ratio"] for r in sweep_results["results"]]
    drawdowns = [r["max_drawdown_pct"] for r in sweep_results["results"]]
    
    lines.extend([
        f"- **收益率范围**: {min(returns):.2f}% 至 {max(returns):.2f}%",
        f"- **平均收益率**: {sum(returns)/len(returns):.2f}%",
        f"- **夏普比率范围**: {min(sharpes):.3f} 至 {max(sharpes):.3f}",
        f"- **平均夏普比率**: {sum(sharpes)/len(sharpes):.3f}",
        f"- **最大回撤范围**: {min(drawdowns):.2f}% 至 {max(drawdowns):.2f}%",
        "",
        f"*完整结果见 [sensitivity.csv](sensitivity.csv)*",
    ])
    
    with open(output / "sensitivity_report.md", "w") as f:
        f.write("\n".join(lines))
