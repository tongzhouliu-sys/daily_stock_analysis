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

    # R23: the human-readable report must be derived from the written artifacts,
    # not from a hand-composed narrative that can drift from summary/trades.
    render_backtest_report(output_path)


def _write_summary_json(
    results: Dict[str, Any],
    output_path: Path,
    config: Dict[str, Any],
):
    """Write summary.json with key metrics."""
    summary = {
        "backtest_period": f"{config['start_date']} to {config['end_date']}",
        "universe": (results.get("data_info") or {}).get("universe_name"),
        "universe_size": (results.get("data_info") or {}).get("universe_size"),
        "max_positions_configured": config.get("max_positions"),
        "max_positions_observed": (results.get("data_info") or {}).get("max_open_positions_observed"),
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
    data_info = results.get("data_info") or {}
    universe_name = data_info.get("universe_name") or "美国上市股票（流动性筛选池）"

    lines = [
        f"# {universe_name} 超跌反弹策略回测报告",
        "",
        f"**回测区间**: {config['start_date']} 至 {config['end_date']}",
        f"**初始资金**: ${config['initial_equity']:,.2f}",
        f"**最终权益**: ${perf['final_equity']:,.2f}",
        "",
        "## 股票池与数据口径",
        "",
        f"- **股票池定义**: {universe_name}",
        f"- **股票池规模**: {data_info.get('universe_size', 'n/a')} 个代码，"
        f"{data_info.get('securities_loaded', 'n/a')} 个具备可用行情",
        f"- **上市/退市处理**: {data_info.get('universe_note') or '允许中途上市与退市，不使用期末存活名单'}",
        f"- **复权方式**: {data_info.get('adjustment') or '未复权'}",
        f"- **成交假设**: {data_info.get('execution') or 'T 日收盘生成信号，T+1 开盘成交'}",
        f"- **预热处理**: 回测起点之前 {data_info.get('warmup_sessions_before_start', 0)} 个交易日仅用于指标预热",
        f"- **最大持仓数（配置/实际）**: {data_info.get('max_positions', config.get('max_positions'))} / "
        f"{data_info.get('max_open_positions_observed', 0)}",
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
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
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
        "data_info": results.get("data_info"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "results_summary": {
            "total_trades": len(results["trades"]),
            "final_equity": results["performance"]["final_equity"],
            "total_return_pct": results["performance"]["total_return_pct"],
        },
    }
    
    with open(output_path / "run_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _max_consecutive_losses(trades: List[Dict[str, Any]]) -> int:
    streak = 0
    longest = 0
    for trade in sorted(trades, key=lambda item: str(item.get("exit_date") or "")):
        if float(trade.get("pnl") or 0.0) < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


def _universe_etf_section(data_info: Dict[str, Any]) -> List[str]:
    count = data_info.get("etf_count")
    if count is None:
        return ["- **ETF 纳入情况**: 数据源未提供证券类型字段，本报告无法给出 ETF 精确数量。"]
    source = data_info.get("etf_source") or "universe metadata"
    note = data_info.get("etf_note") or "ETF 与个股使用同一套流动性与信号规则，未单独剔除。"
    return [
        f"- **ETF 纳入情况**: 股票池包含 ETF，共 {count} 只（来源：{source}）。",
        f"- **ETF 处理口径**: {note}",
    ]


def render_backtest_report(
    output_dir: str,
    *,
    universe_metadata: Dict[str, Any] | None = None,
) -> Path:
    """Render ``report.md`` from the artifact files in ``output_dir``.

    Every number in the report is recomputed from ``summary.json``,
    ``trades.csv``, ``equity.csv``, ``annual_returns.csv``, ``exit_breakdown.csv``
    and ``run_manifest.json``. Callers that already have universe metadata can
    pass ``etf_count`` / ``etf_source`` there; otherwise the values recorded in
    the manifest are used.
    """
    output_path = Path(output_dir)
    manifest = _read_json(output_path / "run_manifest.json", {})
    summary = _read_json(output_path / "summary.json", {})
    config = manifest.get("config") or {}
    data_info = dict(manifest.get("data_info") or {})
    if universe_metadata:
        for key in ("etf_count", "etf_source", "etf_note", "universe_name"):
            if key in universe_metadata:
                data_info[key] = universe_metadata[key]
    trades = [
        {
            **row,
            "pnl": float(row.get("pnl") or 0.0),
            "return_pct": float(row.get("return_pct") or 0.0),
            "holding_days": int(float(row.get("holding_days") or 0)),
        }
        for row in _read_csv_rows(output_path / "trades.csv")
    ]
    equity_rows = _read_csv_rows(output_path / "equity.csv")
    annual_returns = _read_csv_rows(output_path / "annual_returns.csv")
    exit_breakdown = _read_csv_rows(output_path / "exit_breakdown.csv")

    final_equity = float(summary.get("final_equity") or 0.0)
    if equity_rows:
        final_equity = float(equity_rows[-1].get("equity") or final_equity)
    win_rate = summary.get("win_rate") or {}
    total_trades = len(trades) or int(summary.get("total_trades") or 0)
    winning = [trade for trade in trades if trade["pnl"] > 0]
    losing = [trade for trade in trades if trade["pnl"] < 0]
    avg_loss = (sum(trade["pnl"] for trade in losing) / len(losing)) if losing else 0.0
    max_loss_by_pct = min(trades, key=lambda item: item["return_pct"]) if trades else None
    max_loss_by_pnl = min(trades, key=lambda item: item["pnl"]) if trades else None
    max_consecutive_losses = _max_consecutive_losses(trades)
    max_drawdown = summary.get("max_drawdown") or {}

    lines = [
        f"# {data_info.get('universe_name') or '美股超跌反弹策略'}回测报告",
        "",
        "## 1. 回测概述",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        f"| 回测区间 | {config.get('start_date', 'n/a')} 至 {config.get('end_date', 'n/a')} |",
        f"| 初始资金 | ${float(config.get('initial_equity') or 0.0):,.2f} |",
        f"| 最终净值 | ${final_equity:,.2f} |",
        f"| 总收益率 | {float(summary.get('total_return_pct') or 0.0):.2f}% |",
        f"| 年化收益率 (CAGR) | {float(summary.get('cagr_pct') or 0.0):.2f}% |",
        f"| 夏普比率 | {float(summary.get('sharpe_ratio') or 0.0):.3f} |",
        f"| 最大回撤 | {float(max_drawdown.get('max_drawdown_pct') or 0.0):.2f}% |",
        f"| 交易笔数 | {total_trades} 笔完整往返 |",
        f"| 持仓上限 | {config.get('max_positions', 'n/a')} 只 |",
        "",
        "## 2. 股票池与数据口径",
        "",
        f"- **股票池定义**: {data_info.get('universe_name') or '未命名股票池'}",
        f"- **股票池规模**: {data_info.get('universe_size', 'n/a')} 个代码，"
        f"{data_info.get('securities_loaded', 'n/a')} 个具备可用行情",
        f"- **上市/退市处理**: {data_info.get('universe_note') or '允许中途上市与退市'}",
        f"- **复权方式**: {data_info.get('adjustment') or '未复权'}",
        f"- **成交假设**: {data_info.get('execution') or '未声明'}",
        *_universe_etf_section(data_info),
        "",
        "## 3. 策略逻辑",
        "",
        f"- **市场开关**: SPY 收盘价高于 SMA200 时允许开新仓。",
        f"- **入场**: RSI2 < {config.get('rsi_threshold', 'n/a')}（或 3 日跌幅 ≤ −8% / 1 日跌幅 ≤ −5%），"
        f"成交量 ≥ {config.get('volume_multiple', 'n/a')} × 20 日均量，且出现止跌 K 线。",
        f"- **出场**: ATR 止损（{config.get('atr_stop_multiple', 'n/a')} × ATR）、"
        f"最长 {config.get('max_holding_sessions', 'n/a')} 个交易日、或收盘回到 SMA20。",
        f"- **仓位**: 单笔风险 {float(config.get('risk_fraction') or 0.0) * 100:.2f}% 权益，"
        f"单票上限 {float(config.get('position_cap') or 0.0) * 100:.2f}%，最多 {config.get('max_positions', 'n/a')} 只。",
        f"- **交易成本**: 佣金 {float(config.get('commission_rate') or 0.0) * 10000:.2f} bp，"
        f"滑点 {float(config.get('slippage_rate') or 0.0) * 10000:.2f} bp。",
        "",
        "## 4. 绩效指标",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 总收益率 | {float(summary.get('total_return_pct') or 0.0):.2f}% |",
        f"| 年化收益率 | {float(summary.get('cagr_pct') or 0.0):.2f}% |",
        f"| 夏普比率 | {float(summary.get('sharpe_ratio') or 0.0):.3f} |",
        f"| 最大回撤 | {float(max_drawdown.get('max_drawdown_pct') or 0.0):.2f}% |",
        f"| 胜率 | {float(win_rate.get('win_rate_pct') or 0.0):.2f}% |",
        f"| 盈利 / 亏损笔数 | {int(win_rate.get('winning_trades') or len(winning))} / "
        f"{int(win_rate.get('losing_trades') or len(losing))} |",
        f"| 平均盈利 | ${float(win_rate.get('avg_win') or 0.0):,.2f} |",
        f"| 平均亏损 | ${avg_loss:,.2f} |",
        f"| 盈亏比 | {float(summary.get('profit_factor') or 0.0):.3f} |",
        f"| 平均持仓天数 | {float(summary.get('avg_holding_days') or 0.0):.2f} |",
        "",
        "## 5. 交易明细",
        "",
        "| 代码 | 入场 | 出场 | 股数 | 入场价 | 出场价 | 盈亏 | 收益率 | 退出原因 | 持仓天数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for trade in trades:
        lines.append(
            f"| {trade.get('security_id')} | {trade.get('entry_date')} | {trade.get('exit_date')} | "
            f"{trade.get('shares')} | {float(trade.get('entry_price') or 0.0):.4f} | "
            f"{float(trade.get('exit_price') or 0.0):.4f} | ${trade['pnl']:,.2f} | "
            f"{trade['return_pct']:.2f}% | {trade.get('exit_reason')} | {trade['holding_days']} |"
        )

    lines.extend([
        "",
        "## 6. 风险分析",
        "",
        f"- **最大回撤**: {float(max_drawdown.get('max_drawdown_pct') or 0.0):.2f}%"
        f"（峰值 {max_drawdown.get('peak_date') or 'n/a'}，谷值 {max_drawdown.get('valley_date') or 'n/a'}，"
        f"恢复 {max_drawdown.get('recovery_date') or '未恢复'}）",
        f"- **单笔最大亏损（按收益率）**: "
        + (
            f"{max_loss_by_pct.get('security_id')} {max_loss_by_pct['return_pct']:.2f}%"
            if max_loss_by_pct
            else "n/a"
        ),
        f"- **单笔最大亏损（按金额）**: "
        + (
            f"{max_loss_by_pnl.get('security_id')} ${max_loss_by_pnl['pnl']:,.2f}"
            if max_loss_by_pnl
            else "n/a"
        ),
        f"- **最长连续亏损**: {max_consecutive_losses} 笔",
        "",
        "## 7. 年度收益",
        "",
        "| 年份 | 起始权益 | 结束权益 | 收益率 |",
        "| --- | --- | --- | --- |",
    ])
    for row in annual_returns:
        lines.append(
            f"| {row.get('year')} | ${float(row.get('start_equity') or 0.0):,.2f} | "
            f"${float(row.get('end_equity') or 0.0):,.2f} | {float(row.get('return_pct') or 0.0):.2f}% |"
        )

    lines.extend([
        "",
        "## 8. 退出原因分布",
        "",
        "| 退出原因 | 交易次数 | 总盈亏 | 平均盈亏 |",
        "| --- | --- | --- | --- |",
    ])
    for row in exit_breakdown:
        lines.append(
            f"| {row.get('exit_reason')} | {row.get('count')} | "
            f"${float(row.get('total_pnl') or 0.0):,.2f} | ${float(row.get('avg_pnl') or 0.0):,.2f} |"
        )

    lines.extend([
        "",
        "## 9. 结论与建议",
        "",
        "- 以上数字全部由本次运行的 `summary.json`、`trades.csv`、`equity.csv` 与 `run_manifest.json` 计算，"
        "不包含手工修正值。",
        "- 本报告只描述历史回测结果，不构成任何投资建议；ETF 与个股使用同一套规则，"
        "未对 ETF 单独做流动性或借券约束建模。",
        "",
        f"*报告生成时间: {datetime.utcnow().isoformat()}Z*",
    ])

    report_path = output_path / "report.md"
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return report_path
