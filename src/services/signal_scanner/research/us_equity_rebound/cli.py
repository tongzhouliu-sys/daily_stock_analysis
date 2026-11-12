"""Command-line interface for US Equity Rebound backtest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

from .config import ReboundConfig
from .engine import BacktestEngine
from .sweep import run_sweep, generate_sensitivity_report
from .reporting import generate_reports
from .manifest import load_manifest, validate_manifest, DataManifest
from .data_loader import (
    load_market_data,
    load_spy_data,
    load_universe,
    load_corporate_actions,
    load_trading_calendar,
    validate_data_consistency,
)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="US Equity Oversold Rebound Backtest System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate input data
  python -m src.services.signal_scanner.research.us_equity_rebound validate --data-dir ./data
  
  # Validate with manifest
  python -m src.services.signal_scanner.research.us_equity_rebound validate --manifest ./data/manifest.json
  
  # Run single backtest with default parameters (output to backtest_results/)
  python -m src.services.signal_scanner.research.us_equity_rebound run --data-dir ./data
  
  # Run with manifest
  python -m src.services.signal_scanner.research.us_equity_rebound run --manifest ./data/manifest.json
  
  # Run with custom config
  python -m src.services.signal_scanner.research.us_equity_rebound run --data-dir ./data --config config.json
  
  # Run parameter sweep (81 combinations)
  python -m src.services.signal_scanner.research.us_equity_rebound sweep --data-dir ./data
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to run")
    
    # Common arguments for data source
    def add_data_source_args(subparser):
        """Add data source arguments to subparser."""
        data_group = subparser.add_mutually_exclusive_group(required=True)
        data_group.add_argument("--data-dir", help="Input data directory")
        data_group.add_argument("--manifest", help="Path to manifest JSON file")
    
    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate input data")
    add_data_source_args(validate_parser)
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run single backtest")
    add_data_source_args(run_parser)
    run_parser.add_argument("--output", default="backtest_results/us_equity_rebound", 
                           help="Output directory (default: backtest_results/us_equity_rebound)")
    run_parser.add_argument("--config", help="Custom config JSON file")
    
    # Sweep command
    sweep_parser = subparsers.add_parser("sweep", help="Run parameter sweep")
    add_data_source_args(sweep_parser)
    sweep_parser.add_argument("--output", default="backtest_results/us_equity_rebound_sweep",
                             help="Output directory (default: backtest_results/us_equity_rebound_sweep)")
    sweep_parser.add_argument("--config", help="Base config JSON file")
    
    args = parser.parse_args()
    
    try:
        if args.command == "validate":
            return cmd_validate(args)
        elif args.command == "run":
            return cmd_run(args)
        elif args.command == "sweep":
            return cmd_sweep(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    return 0


def cmd_validate(args) -> int:
    """Validate input data."""
    print("Validating input data...")
    
    data_dir = args.data_dir
    
    # Load all data
    print("  Loading trading calendar...")
    trading_dates = load_trading_calendar(data_dir)
    print(f"    ✓ {len(trading_dates)} trading days")
    
    print("  Loading universe...")
    universe = load_universe(data_dir)
    print(f"    ✓ {len(universe)} securities")
    
    print("  Loading SPY data...")
    spy_data = load_spy_data(data_dir)
    print(f"    ✓ {len(spy_data['date'])} days")
    
    print("  Loading market data...")
    market_data = load_market_data(data_dir)
    print(f"    ✓ {len(market_data)} securities")
    
    print("  Loading corporate actions...")
    corporate_actions = load_corporate_actions(data_dir)
    print(f"    ✓ {len(corporate_actions)} events")
    
    # Validate consistency
    print("  Validating data consistency...")
    validate_data_consistency(market_data, spy_data, trading_dates, universe)
    
    print("\n✓ All validation checks passed")
    return 0


def cmd_run(args) -> int:
    """Run single backtest."""
    print("Running backtest...")
    
    # Load config
    if args.config:
        with open(args.config, "r") as f:
            config_dict = json.load(f)
        config = ReboundConfig.from_dict(config_dict)
    else:
        config = ReboundConfig()
    
    print(f"  Config: {config.start_date} to {config.end_date}")
    print(f"  Initial equity: ${config.initial_equity:,.2f}")
    
    # Load data
    print("  Loading data...")
    trading_dates = load_trading_calendar(args.data_dir)
    universe = load_universe(args.data_dir)
    spy_data = load_spy_data(args.data_dir)
    market_data = load_market_data(args.data_dir)
    corporate_actions = load_corporate_actions(args.data_dir)
    
    # Validate
    validate_data_consistency(market_data, spy_data, trading_dates, universe)
    
    # Run backtest
    print("  Running engine...")
    engine = BacktestEngine(config)
    results = engine.run(
        trading_dates=trading_dates,
        market_data=market_data,
        spy_data=spy_data,
        universe=universe,
        corporate_actions=corporate_actions,
    )
    
    # Generate reports
    print(f"  Generating reports in {args.output}...")
    generate_reports(results, args.output, config.to_dict())
    
    # Print summary
    perf = results["performance"]
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Total Return:      {perf['total_return_pct']:>10.2f}%")
    print(f"CAGR:              {perf['cagr_pct']:>10.2f}%")
    print(f"Sharpe Ratio:      {perf['sharpe_ratio']:>10.3f}")
    print(f"Max Drawdown:      {perf['max_drawdown']['max_drawdown_pct']:>10.2f}%")
    print(f"Total Trades:      {perf['total_trades']:>10d}")
    print(f"Win Rate:          {perf['win_rate']['win_rate_pct']:>10.2f}%")
    print(f"Profit Factor:     {perf['win_rate']['profit_factor']:>10.3f}")
    print("=" * 60)
    print(f"\n✓ Reports generated in {args.output}")
    
    return 0


def cmd_sweep(args) -> int:
    """Run parameter sweep."""
    print("Running parameter sweep (81 combinations)...")
    
    # Load base config
    if args.config:
        with open(args.config, "r") as f:
            config_dict = json.load(f)
        base_config = ReboundConfig.from_dict(config_dict)
    else:
        base_config = ReboundConfig()
    
    # Load data
    print("  Loading data...")
    trading_dates = load_trading_calendar(args.data_dir)
    universe = load_universe(args.data_dir)
    spy_data = load_spy_data(args.data_dir)
    market_data = load_market_data(args.data_dir)
    corporate_actions = load_corporate_actions(args.data_dir)
    
    # Validate
    validate_data_consistency(market_data, spy_data, trading_dates, universe)
    
    # Run sweep
    print("  Running 81 parameter combinations...")
    sweep_results = run_sweep(
        trading_dates=trading_dates,
        market_data=market_data,
        spy_data=spy_data,
        universe=universe,
        base_config=base_config,
        corporate_actions=corporate_actions,
    )
    
    # Generate sensitivity report
    print(f"  Generating sensitivity report in {args.output}...")
    generate_sensitivity_report(sweep_results, args.output)
    
    # Print best result
    best = sweep_results["best_combination"]
    print("\n" + "=" * 60)
    print("BEST PARAMETER COMBINATION")
    print("=" * 60)
    print(f"ATR Stop Multiple:    {best['parameters']['atr_stop_multiple']}")
    print(f"Max Holding Sessions: {best['parameters']['max_holding_sessions']}")
    print(f"RSI Threshold:        {best['parameters']['rsi_threshold']}")
    print(f"Volume Multiple:      {best['parameters']['volume_multiple']}")
    print("-" * 60)
    print(f"Total Return:         {best['total_return_pct']:>10.2f}%")
    print(f"CAGR:                 {best['cagr_pct']:>10.2f}%")
    print(f"Sharpe Ratio:         {best['sharpe_ratio']:>10.3f}")
    print(f"Max Drawdown:         {best['max_drawdown_pct']:>10.2f}%")
    print(f"Total Trades:         {best['total_trades']:>10d}")
    print(f"Win Rate:             {best['win_rate_pct']:>10.2f}%")
    print("=" * 60)
    print(f"\n✓ Sensitivity report generated in {args.output}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
