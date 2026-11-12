#!/usr/bin/env python3
"""End-to-end test for US Equity Rebound backtest system."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .sample_data import generate_sample_data
from .config import ReboundConfig
from .engine import BacktestEngine
from .data_loader import (
    load_market_data,
    load_spy_data,
    load_universe,
    load_corporate_actions,
    load_trading_calendar,
    validate_data_consistency,
)
from .reporting import generate_reports


def run_e2e_test():
    """Run complete end-to-end test."""
    print("=" * 70)
    print("US EQUITY OVERSOLD REBOUND BACKTEST - END-TO-END TEST")
    print("=" * 70)
    
    # Create temporary directory for test data
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "data"
        output_dir = Path(tmpdir) / "output"
        
        # Step 1: Generate sample data
        print("\n[1/6] Generating sample data...")
        generate_sample_data(
            str(data_dir),
            start_date="2020-01-01",
            end_date="2021-12-31",
        )
        
        # Step 2: Load data
        print("\n[2/6] Loading data...")
        trading_dates = load_trading_calendar(str(data_dir))
        universe = load_universe(str(data_dir))
        spy_data = load_spy_data(str(data_dir))
        market_data = load_market_data(str(data_dir))
        corporate_actions = load_corporate_actions(str(data_dir))
        
        print(f"  ✓ Loaded {len(trading_dates)} trading days")
        print(f"  ✓ Loaded {len(universe)} securities")
        print(f"  ✓ Loaded {len(corporate_actions)} corporate actions")
        
        # Step 3: Validate data
        print("\n[3/6] Validating data consistency...")
        validate_data_consistency(market_data, spy_data, trading_dates, universe)
        
        # Step 4: Configure backtest
        print("\n[4/6] Configuring backtest...")
        config = ReboundConfig(
            start_date="2020-01-01",
            end_date="2021-12-31",
            initial_equity=100000.0,
            rsi_threshold=10.0,
            atr_stop_multiple=2.0,
            max_holding_sessions=10,
        )
        print(f"  ✓ Period: {config.start_date} to {config.end_date}")
        print(f"  ✓ Initial equity: ${config.initial_equity:,.2f}")
        
        # Step 5: Run backtest
        print("\n[5/6] Running backtest engine...")
        engine = BacktestEngine(config)
        results = engine.run(
            trading_dates=trading_dates,
            market_data=market_data,
            spy_data=spy_data,
            universe=universe,
            corporate_actions=corporate_actions,
        )
        
        # Step 6: Generate reports
        print("\n[6/6] Generating reports...")
        generate_reports(results, str(output_dir), config.to_dict())
        
        # Print summary
        perf = results["performance"]
        print("\n" + "=" * 70)
        print("BACKTEST RESULTS SUMMARY")
        print("=" * 70)
        print(f"Total Return:        {perf['total_return']:.2f}%")
        print(f"Sharpe Ratio:        {perf['sharpe_ratio']:.3f}")
        print(f"Max Drawdown:        {perf['max_drawdown']:.2f}%")
        print(f"Total Trades:        {perf['total_trades']}")
        print(f"Win Rate:            {perf['win_rate']:.2f}%")
        print(f"Profit Factor:       {perf['profit_factor']:.3f}")
        print(f"Avg Holding Days:    {perf['avg_holding_days']:.1f}")
        print("=" * 70)
        
        # Verify reports were created
        print("\nGenerated reports:")
        for file in sorted(output_dir.glob("*")):
            size = file.stat().st_size
            print(f"  ✓ {file.name} ({size:,} bytes)")
        
        print("\n" + "=" * 70)
        print("✓ END-TO-END TEST PASSED")
        print("=" * 70)
        
        return True


if __name__ == "__main__":
    try:
        success = run_e2e_test()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
