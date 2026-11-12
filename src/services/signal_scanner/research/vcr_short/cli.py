# -*- coding: utf-8 -*-
"""CLI entry point for VCR Short backtest.

Provides command-line interface for running backtests,
generating reports, and performing sensitivity analysis.
"""

import argparse
import sys
import json
import csv
from pathlib import Path
from typing import Dict, Any, List

from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.engine import VCRShortBacktestEngine
from src.services.signal_scanner.research.vcr_short.report import ReportGenerator
from src.services.signal_scanner.research.vcr_short.demo_data import DemoDataGenerator


def load_data_from_csv(csv_path: str) -> Dict[str, Dict[str, List[Any]]]:
    """Load historical data from CSV file.

    Expected CSV format:
    date,instrument_id,open,high,low,close,volume

    Returns:
        Dictionary of instrument_id -> {dates, open, high, low, close, volume}
    """
    # Group rows by instrument
    by_instrument: Dict[str, List[Dict]] = {}
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iid = row["instrument_id"]
            by_instrument.setdefault(iid, []).append(row)

    data = {}
    for iid, rows in by_instrument.items():
        # Sort by date
        rows.sort(key=lambda r: r["date"])
        data[iid] = {
            "dates": [r["date"] for r in rows],
            "open": [float(r["open"]) for r in rows],
            "high": [float(r["high"]) for r in rows],
            "low": [float(r["low"]) for r in rows],
            "close": [float(r["close"]) for r in rows],
            "volume": [int(float(r["volume"])) for r in rows],
        }

    return data


def load_borrow_rates(json_path: str) -> Dict[str, float]:
    """Load borrow rates from JSON file."""
    with open(json_path, "r") as f:
        return json.load(f)


def load_dividend_data(json_path: str) -> Dict[str, list]:
    """Load dividend data from JSON file."""
    with open(json_path, "r") as f:
        return json.load(f)


def cmd_run(args):
    """Run backtest command."""
    print(f"Loading data from {args.data}...")

    if args.data == "demo":
        print("Generating demo data...")
        generator = DemoDataGenerator()
        data = generator.generate(
            num_stocks=args.demo_stocks,
            start_date=args.start_date,
            end_date=args.end_date
        )
        print(f"Generated demo data for {len(data)} stocks")
    else:
        data = load_data_from_csv(args.data)
        print(f"Loaded data for {len(data)} instruments")

    borrow_rates = None
    if args.borrow_rates:
        borrow_rates = load_borrow_rates(args.borrow_rates)
        print(f"Loaded borrow rates for {len(borrow_rates)} instruments")

    dividend_data = None
    if args.dividends:
        dividend_data = load_dividend_data(args.dividends)
        print(f"Loaded dividend data for {len(dividend_data)} instruments")

    config = VCRShortConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_equity=args.equity,
        max_positions=args.max_positions,
        slippage_bps=args.slippage,
    )

    print(f"\nRunning backtest from {config.start_date} to {config.end_date}...")
    print(f"Initial equity: ${config.initial_equity:,.2f}")
    print(f"Max positions: {config.max_positions}")
    print(f"Slippage: {config.slippage_bps} bps")

    engine = VCRShortBacktestEngine(config)
    results = engine.run(data, borrow_rates, dividend_data)

    print("\nBacktest completed!")
    perf = results["performance"]
    print(f"Total trades: {perf.get('total_trades', 0)}")

    win_rate = perf.get("win_rate")
    print(f"Win rate: {win_rate:.2%}" if win_rate is not None else "Win rate: N/A")

    cagr = perf.get("cagr")
    print(f"CAGR: {cagr:.2%}" if cagr is not None else "CAGR: N/A")

    max_dd = perf.get("max_drawdown")
    print(f"Max drawdown: {max_dd:.2%}" if max_dd is not None else "Max drawdown: N/A")

    sharpe = perf.get("sharpe_ratio")
    print(f"Sharpe ratio: {sharpe:.2f}" if sharpe is not None else "Sharpe ratio: N/A")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_gen = ReportGenerator(str(output_dir))
    report_gen.save_all_reports(
        performance_metrics=results["performance"],
        config=results["config"],
        data_info=results["data_info"],
        all_trades=results["trades"],
        open_positions=results["open_positions"],
        equity_curve=results["equity_curve"],
    )

    print(f"\nReports saved to {output_dir}/")
    print("  - summary.json")
    print("  - trades.json")
    print("  - trades.csv")
    print("  - equity_curve.csv")
    print("  - REPORT.md")


def cmd_sensitivity(args):
    """Run sensitivity analysis command."""
    print(f"Loading data from {args.data}...")

    if args.data == "demo":
        print("Generating demo data...")
        generator = DemoDataGenerator()
        data = generator.generate(
            num_stocks=args.demo_stocks,
            start_date=args.start_date,
            end_date=args.end_date
        )
        print(f"Generated demo data for {len(data)} stocks")
    else:
        data = load_data_from_csv(args.data)
        print(f"Loaded data for {len(data)} instruments")

    config = VCRShortConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_equity=args.equity,
        max_positions=args.max_positions,
        slippage_bps=args.slippage,
    )

    print("\nRunning sensitivity analysis...")
    print("This will run multiple backtests with different parameter values.")

    engine = VCRShortBacktestEngine(config)
    results = engine.run_sensitivity_analysis(data)

    print("\nSensitivity analysis completed!")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_gen = ReportGenerator(str(output_dir))
    report_gen.save_all_reports(
        performance_metrics=results["default"]["performance"],
        config=results["default"]["config"],
        data_info=results["default"]["data_info"],
        all_trades=results["default"]["trades"],
        open_positions=results["default"]["open_positions"],
        equity_curve=results["default"]["equity_curve"],
        sensitivity_results=results["sensitivity"],
        critical_parameters=results["critical_parameters"],
    )

    print(f"\nReports saved to {output_dir}/")
    print("  - summary.json")
    print("  - trades.json")
    print("  - trades.csv")
    print("  - equity_curve.csv")
    print("  - sensitivity.json")
    print("  - REPORT.md")

    print("\nCritical Parameters (sorted by impact):")
    for i, param in enumerate(results["critical_parameters"][:5], 1):
        print(f"  {i}. {param['parameter']}")
        print(f"     Impact score: {param['impact_score']:.4f}")
        cagr_lo, cagr_hi = param["cagr_range"]
        sharpe_lo, sharpe_hi = param["sharpe_range"]
        print(f"     CAGR range: [{cagr_lo:.2%}, {cagr_hi:.2%}]")
        print(f"     Sharpe range: [{sharpe_lo:.2f}, {sharpe_hi:.2f}]")


def cmd_demo(args):
    """Generate demo data command."""
    print(f"Generating demo data for {args.stocks} stocks...")
    print(f"Period: {args.start_date} to {args.end_date}")

    generator = DemoDataGenerator()
    data = generator.generate(
        num_stocks=args.stocks,
        start_date=args.start_date,
        end_date=args.end_date
    )

    # Write as CSV without pandas
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_records = 0
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "instrument_id", "open", "high", "low", "close", "volume"])
        for instrument_id, inst_data in data.items():
            n = len(inst_data["dates"])
            for i in range(n):
                writer.writerow([
                    inst_data["dates"][i],
                    instrument_id,
                    inst_data["open"][i],
                    inst_data["high"][i],
                    inst_data["low"][i],
                    inst_data["close"][i],
                    inst_data["volume"][i],
                ])
                total_records += 1

    print(f"\nDemo data saved to {output_path}")
    print(f"Total records: {total_records}")
    print(f"Instruments: {len(data)}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Volume Climax Reversal Short Backtest Engine"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run backtest")
    run_parser.add_argument("--data", required=True, help="Path to CSV data file or 'demo'")
    run_parser.add_argument("--output", default="backtest_results/vcr_short", help="Output directory")
    run_parser.add_argument("--start-date", default="2010-01-01", help="Start date (YYYY-MM-DD)")
    run_parser.add_argument("--end-date", default="2025-12-31", help="End date (YYYY-MM-DD)")
    run_parser.add_argument("--equity", type=float, default=100000.0, help="Initial equity (USD)")
    run_parser.add_argument("--max-positions", type=int, default=10, help="Max concurrent positions")
    run_parser.add_argument("--slippage", type=float, default=10.0, help="Slippage (basis points)")
    run_parser.add_argument("--borrow-rates", help="Path to JSON with borrow rates")
    run_parser.add_argument("--dividends", help="Path to JSON with dividend data")
    run_parser.add_argument("--demo-stocks", type=int, default=50, help="Stocks for demo data")

    # Sensitivity command
    sens_parser = subparsers.add_parser("sensitivity", help="Run parameter sensitivity analysis")
    sens_parser.add_argument("--data", required=True, help="Path to CSV data file or 'demo'")
    sens_parser.add_argument("--output", default="backtest_results/vcr_short_sensitivity", help="Output directory")
    sens_parser.add_argument("--start-date", default="2010-01-01", help="Start date (YYYY-MM-DD)")
    sens_parser.add_argument("--end-date", default="2025-12-31", help="End date (YYYY-MM-DD)")
    sens_parser.add_argument("--equity", type=float, default=100000.0, help="Initial equity (USD)")
    sens_parser.add_argument("--max-positions", type=int, default=10, help="Max concurrent positions")
    sens_parser.add_argument("--slippage", type=float, default=10.0, help="Slippage (basis points)")
    sens_parser.add_argument("--demo-stocks", type=int, default=50, help="Stocks for demo data")

    # Demo command
    demo_parser = subparsers.add_parser("demo", help="Generate demo data")
    demo_parser.add_argument("--output", default="backtest_results/vcr_short_demo_data.csv", help="Output CSV path")
    demo_parser.add_argument("--stocks", type=int, default=50, help="Number of stocks")
    demo_parser.add_argument("--start-date", default="2010-01-01", help="Start date (YYYY-MM-DD)")
    demo_parser.add_argument("--end-date", default="2025-12-31", help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "sensitivity":
        cmd_sensitivity(args)
    elif args.command == "demo":
        cmd_demo(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
