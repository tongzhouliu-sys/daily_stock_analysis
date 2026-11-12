# -*- coding: utf-8 -*-
"""Report generation for VCR Short backtest.

Generates comprehensive reports including performance metrics,
trade details, and parameter sensitivity analysis.
"""

from typing import Dict, Any, List
from datetime import datetime
import json
import csv
from pathlib import Path


class ReportGenerator:
    """Generates backtest reports in various formats."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_summary_report(
        self,
        performance_metrics: Dict[str, Any],
        config: Dict[str, Any],
        data_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate summary report.

        Args:
            performance_metrics: Performance calculation results
            config: Strategy configuration
            data_info: Data coverage information

        Returns:
            Summary report dictionary
        """
        report = {
            "report_type": "VCR_SHORT_BACKTEST_SUMMARY",
            "generated_at": datetime.now().isoformat(),
            "configuration": config,
            "data_coverage": data_info,
            "performance": performance_metrics,
        }

        return report

    def generate_trade_report(
        self,
        all_trades: List[Dict[str, Any]],
        open_positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Generate detailed trade report.

        Args:
            all_trades: List of all completed trades
            open_positions: List of positions still open at end

        Returns:
            Trade report dictionary
        """
        report = {
            "report_type": "VCR_SHORT_TRADE_DETAILS",
            "generated_at": datetime.now().isoformat(),
            "total_trades": len(all_trades),
            "open_positions": len(open_positions),
            "trades": all_trades,
            "open_positions_detail": open_positions,
        }

        return report

    def generate_sensitivity_report(
        self,
        sensitivity_results: Dict[str, Any],
        critical_parameters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Generate parameter sensitivity report.

        Args:
            sensitivity_results: Sensitivity analysis results
            critical_parameters: Ranked critical parameters

        Returns:
            Sensitivity report dictionary
        """
        report = {
            "report_type": "VCR_SHORT_SENSITIVITY_ANALYSIS",
            "generated_at": datetime.now().isoformat(),
            "sensitivity_results": sensitivity_results,
            "critical_parameters": critical_parameters,
        }

        return report

    def save_json(self, data: Dict[str, Any], filename: str):
        """Save data as JSON file.

        Args:
            data: Data to save
            filename: Output filename
        """
        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def save_csv(self, data: List[Dict[str, Any]], filename: str):
        """Save data as CSV file.

        Args:
            data: List of dictionaries to save
            filename: Output filename
        """
        if not data:
            return

        filepath = self.output_dir / filename
        fieldnames = data[0].keys()

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

    def save_equity_curve_csv(
        self,
        equity_curve: List[Dict[str, Any]],
        filename: str = "equity_curve.csv"
    ):
        """Save equity curve to CSV.

        Args:
            equity_curve: List of {date, equity} dictionaries
            filename: Output filename
        """
        self.save_csv(equity_curve, filename)

    def save_trades_csv(
        self,
        trades: List[Dict[str, Any]],
        filename: str = "trades.csv"
    ):
        """Save trades to CSV.

        Args:
            trades: List of trade dictionaries
            filename: Output filename
        """
        self.save_csv(trades, filename)

    def generate_markdown_summary(
        self,
        performance_metrics: Dict[str, Any],
        config: Dict[str, Any]
    ) -> str:
        """Generate markdown summary report.

        Args:
            performance_metrics: Performance metrics
            config: Strategy configuration

        Returns:
            Markdown formatted summary
        """
        lines = [
            "# Volume Climax Reversal Short Backtest Report",
            "",
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Configuration",
            "",
            f"- Backtest Period: {config.get('start_date')} to {config.get('end_date')}",
            f"- Initial Equity: ${config.get('initial_equity', 100000):,.2f}",
            f"- Max Positions: {config.get('max_positions', 10)}",
            f"- Slippage: {config.get('slippage_bps', 10)} bps",
            "",
            "## Performance Summary",
            "",
        ]

        # Format key metrics
        cagr = performance_metrics.get("cagr")
        if cagr is not None:
            lines.append(f"- **CAGR**: {cagr:.2%}")

        max_dd = performance_metrics.get("max_drawdown")
        if max_dd is not None:
            lines.append(f"- **Max Drawdown**: {max_dd:.2%}")

        sharpe = performance_metrics.get("sharpe_ratio")
        if sharpe is not None:
            lines.append(f"- **Sharpe Ratio**: {sharpe:.2f}")

        sortino = performance_metrics.get("sortino_ratio")
        if sortino is not None:
            lines.append(f"- **Sortino Ratio**: {sortino:.2f}")

        calmar = performance_metrics.get("calmar_ratio")
        if calmar is not None:
            lines.append(f"- **Calmar Ratio**: {calmar:.2f}")

        lines.extend([
            "",
            "## Trade Statistics",
            "",
        ])

        total_trades = performance_metrics.get("total_trades", 0)
        lines.append(f"- **Total Trades**: {total_trades}")

        win_rate = performance_metrics.get("win_rate")
        if win_rate is not None:
            lines.append(f"- **Win Rate**: {win_rate:.2%}")

        avg_win = performance_metrics.get("avg_win")
        if avg_win is not None:
            lines.append(f"- **Average Win**: ${avg_win:,.2f}")

        avg_loss = performance_metrics.get("avg_loss")
        if avg_loss is not None:
            lines.append(f"- **Average Loss**: ${avg_loss:,.2f}")

        profit_factor = performance_metrics.get("profit_factor")
        if profit_factor is not None:
            lines.append(f"- **Profit Factor**: {profit_factor:.2f}")

        expectancy = performance_metrics.get("expectancy")
        if expectancy is not None:
            lines.append(f"- **Expectancy**: ${expectancy:,.2f}")

        max_consec = performance_metrics.get("max_consecutive_losses", 0)
        lines.append(f"- **Max Consecutive Losses**: {max_consec}")

        lines.extend([
            "",
            "## Files Generated",
            "",
            "- `summary.json` - Complete summary report",
            "- `trades.json` - Detailed trade records",
            "- `trades.csv` - Trade data in CSV format",
            "- `equity_curve.csv` - Daily equity curve",
            "- `sensitivity.json` - Parameter sensitivity analysis",
            "- `REPORT.md` - This summary report",
            "",
        ])

        return "\n".join(lines)

    def save_all_reports(
        self,
        performance_metrics: Dict[str, Any],
        config: Dict[str, Any],
        data_info: Dict[str, Any],
        all_trades: List[Dict[str, Any]],
        open_positions: List[Dict[str, Any]],
        equity_curve: List[Dict[str, Any]],
        sensitivity_results: Dict[str, Any] = None,
        critical_parameters: List[Dict[str, Any]] = None,
        exit_breakdown: Dict[str, Any] = None,
        benchmark_data: Dict[str, Any] = None,
    ):
        """Save all report files.

        Args:
            performance_metrics: Performance metrics
            config: Strategy configuration
            data_info: Data coverage information
            all_trades: All completed trades
            open_positions: Open positions at end
            equity_curve: Daily equity curve
            sensitivity_results: Sensitivity analysis results (optional)
            critical_parameters: Critical parameters (optional)
            exit_breakdown: Exit reason breakdown (optional)
            benchmark_data: SPY benchmark comparison data (optional)
        """
        # Generate and save summary report
        summary = self.generate_summary_report(performance_metrics, config, data_info)
        self.save_json(summary, "summary.json")

        # Generate and save trade report
        trade_report = self.generate_trade_report(all_trades, open_positions)
        self.save_json(trade_report, "trades.json")
        self.save_trades_csv(all_trades, "trades.csv")

        # Save equity curve
        self.save_equity_curve_csv(equity_curve, "equity_curve.csv")

        # Save open positions CSV
        if open_positions:
            self.save_csv(open_positions, "open_positions.csv")

        # Save exit breakdown CSV
        if exit_breakdown:
            rows = [
                {"exit_reason": reason, **stats}
                for reason, stats in exit_breakdown.items()
            ]
            self.save_csv(rows, "exit_breakdown.csv")

        # Save benchmark comparison
        if benchmark_data:
            self.save_json(benchmark_data, "benchmark.json")
            if "daily_comparison" in benchmark_data:
                self.save_csv(benchmark_data["daily_comparison"], "benchmark_daily.csv")

        # Generate and save sensitivity report if available
        if sensitivity_results and critical_parameters:
            sensitivity_report = self.generate_sensitivity_report(
                sensitivity_results, critical_parameters
            )
            self.save_json(sensitivity_report, "sensitivity.json")

        # Write run manifest for reproducibility
        self._write_run_manifest(config, data_info, performance_metrics)

        # Generate markdown summary
        md_summary = self.generate_markdown_summary(performance_metrics, config)
        md_path = self.output_dir / "REPORT.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_summary)

    def _write_run_manifest(
        self,
        config: Dict[str, Any],
        data_info: Dict[str, Any],
        performance_metrics: Dict[str, Any],
    ):
        """Write run_manifest.json for reproducibility."""
        import hashlib
        manifest = {
            "strategy": "VCR_SHORT",
            "config": config,
            "data_info": data_info,
            "performance_summary": {
                k: v for k, v in performance_metrics.items()
                if k in ("total_trades", "cagr", "max_drawdown", "sharpe_ratio")
            },
            "generated_at": datetime.now().isoformat(),
        }
        self.save_json(manifest, "run_manifest.json")
