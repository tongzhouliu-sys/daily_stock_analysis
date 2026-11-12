"""Report generation - CSV and JSON output."""

from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime


def generate_reports(
    results: Dict[str, Any],
    output_dir: str,
    config: Dict[str, Any],
):
    """Generate all report files.
    
    Args:
        results: Backtest results from engine
        output_dir: Output directory path
        config: Backtest configuration
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Generate all reports
    _write_summary_json(results, output_path, config)
    _write_report_md(results, output_path, config)
    _write_trades_csv(results["trades"], output_path)
    _write_equity_csv(results["daily_records"], output_path)
    _write_annual_returns_csv(results["performance"]["annual_returns"], output_path)
    _write_exit_breakdown_csv(results["performance"]["exit_breakdown"], output_path)
    
    # Write run manifest
    _write_run_manifest(output_path, config, results)


def _write_summary_json(
    results: Dict[str, Any],
    output_path: Path,
    config: Dict[str, Any],
):
    """Write summary.json with key metrics."""
    summary = {
        "backtest_period": f"{config['start_date']} to {config['end_date']}",
        "initial_equity": config["initial_equity"],
        "final_equity": results["performance"]["final_equity"],
        "total_return_pct": results["performance"]["total_return_pct"],
        "cagr_pct": results["performance"]["cagr_pct"],
        "sharpe_ratio": results["performance"]["sharpe_ratio"],
        "max_drawdown": results["performance"]["max_drawdown"],
        "total_trades": results["performance"]["total_trades"],
        "win_rate": results["performance"]["win_rate"],
        "profit_factor": results["performance"]["win_rate"]["profit_factor"],
        "avg_holding_days": (
            sum(t["holding_days"] for t in results["trades"]) / len(results["trades"])
            if results["trades"] else 0
        ),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    
    with open(output_path / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)


def _write_report_md(
    results: Dict[str, Any],
    output_path: Path,
    config: Dict[str, Any],
):
    """Write report.md with human-readable summary."""
    perf = results["performance"]
    
    lines = [
        "# 标普 500 超跌反弹策略回测报告",
        "",
        f"**回测区间**: {config['start_date']} 至 {config['end_date']}",
        f"**初始资金**: ${config['initial_equity']:,.2f}",
        f"**最终权益**: ${perf['final_equity']:,.2f}",
        "",
        "## 核心指标",
        "",
        f"- **总收益率**: {perf['total_return_pct']:.2f}%",
        f"- **年化收益率 (CAGR)**: {perf['cagr_pct']:.2f}%",
        f"- **夏普比率**: {perf['sharpe_ratio']:.3f}",
        f"- **最大回撤**: {perf['max_drawdown']['max_drawdown_pct']:.2f}%",
        f"  - 峰值日期: {perf['max_drawdown']['peak_date']}",
        f"  - 谷值日期: {perf['max_drawdown']['valley_date']}",
        f"  - 恢复日期: {perf['max_drawdown']['recovery_date'] or '未恢复'}",
        "",
        "## 交易统计",
        "",
        f"- **总交易次数**: {perf['total_trades']}",
        f"- **胜率**: {perf['win_rate']['win_rate_pct']:.2f}%",
        f"- **盈利交易**: {perf['win_rate']['winning_trades']}",
        f"- **亏损交易**: {perf['win_rate']['losing_trades']}",
        f"- **平均盈利**: ${perf['win_rate']['avg_win']:,.2f}",
        f"- **平均亏损**: ${perf['win_rate']['avg_loss']:,.2f}",
        f"- **盈亏比**: {perf['win_rate']['profit_factor']:.3f}",
        "",
        "## 退出原因分布",
        "",
        "| 退出原因 | 交易次数 | 总盈亏 | 平均盈亏 |",
        "|---------|---------|--------|---------|",
    ]
    
    for reason, stats in perf["exit_breakdown"].items():
        lines.append(
            f"| {reason} | {stats['count']} | ${stats['total_pnl']:,.2f} | ${stats['avg_pnl']:,.2f} |"
        )
    
    lines.extend([
        "",
        "## 年度收益",
        "",
        "| 年份 | 起始权益 | 结束权益 | 收益率 |",
        "|------|---------|---------|--------|",
    ])
    
    for year_data in perf["annual_returns"]:
        lines.append(
            f"| {year_data['year']} | ${year_data['start_equity']:,.2f} | "
            f"${year_data['end_equity']:,.2f} | {year_data['return_pct']:.2f}% |"
        )
    
    lines.extend([
        "",
        "## 参数配置",
        "",
        "```json",
        json.dumps(config, indent=2),
        "```",
        "",
        f"*报告生成时间: {datetime.utcnow().isoformat()}Z*",
    ])
    
    with open(output_path / "report.md", "w") as f:
        f.write("\n".join(lines))


def _write_trades_csv(trades: List[Dict[str, Any]], output_path: Path):
    """Write trades.csv with all completed trades."""
    if not trades:
        return
    
    fieldnames = [
        "security_id",
        "entry_date",
        "exit_date",
        "shares",
        "entry_price",
        "exit_price",
        "pnl",
        "return_pct",
        "exit_reason",
        "holding_days",
    ]
    
    with open(output_path / "trades.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)


def _write_equity_csv(daily_records: List[Dict[str, Any]], output_path: Path):
    """Write equity.csv with daily portfolio state."""
    if not daily_records:
        return
    
    fieldnames = [
        "date",
        "equity",
        "cash",
        "positions_value",
        "num_positions",
        "pending_buys",
        "pending_sells",
    ]
    
    with open(output_path / "equity.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in daily_records:
            writer.writerow(record)


def _write_annual_returns_csv(
    annual_returns: List[Dict[str, Any]],
    output_path: Path,
):
    """Write annual_returns.csv."""
    if not annual_returns:
        return
    
    fieldnames = ["year", "start_equity", "end_equity", "return_pct"]
    
    with open(output_path / "annual_returns.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for year_data in annual_returns:
            writer.writerow(year_data)


def _write_exit_breakdown_csv(
    exit_breakdown: Dict[str, Dict[str, Any]],
    output_path: Path,
):
    """Write exit_breakdown.csv."""
    if not exit_breakdown:
        return
    
    rows = [
        {"exit_reason": reason, **stats}
        for reason, stats in exit_breakdown.items()
    ]
    
    fieldnames = ["exit_reason", "count", "total_pnl", "avg_pnl"]
    
    with open(output_path / "exit_breakdown.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_run_manifest(
    output_path: Path,
    config: Dict[str, Any],
    results: Dict[str, Any],
):
    """Write run_manifest.json with reproducibility info."""
    manifest = {
        "config": config,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "results_summary": {
            "total_trades": len(results["trades"]),
            "final_equity": results["performance"]["final_equity"],
            "total_return_pct": results["performance"]["total_return_pct"],
        },
    }
    
    with open(output_path / "run_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
