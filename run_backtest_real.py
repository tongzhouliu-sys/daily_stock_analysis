#!/usr/bin/env python3
"""使用 data10 真实数据运行回测。"""

import sys
import json
from pathlib import Path
from datetime import datetime

from src.services.signal_scanner.research.us_equity_rebound.config import ReboundConfig
from src.services.signal_scanner.research.us_equity_rebound.engine import BacktestEngine
from src.services.signal_scanner.research.us_equity_rebound.data_loader import (
    load_market_data,
    load_spy_data,
    load_universe,
    load_corporate_actions,
    load_trading_calendar,
    validate_data_consistency,
)
from src.services.signal_scanner.research.us_equity_rebound.reporting import generate_reports


def run_backtest_with_real_data():
    """使用真实数据运行回测。"""
    print("=" * 80)
    print("标普500超跌反弹策略 - 真实数据回测")
    print("=" * 80)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    data_dir = "real_data_converted"
    output_dir = "backtest_results_real"
    
    # 加载数据
    print("【步骤1/4】加载真实市场数据...")
    trading_dates = load_trading_calendar(data_dir)
    universe = load_universe(data_dir)
    spy_data = load_spy_data(data_dir)
    market_data = load_market_data(data_dir)
    corporate_actions = load_corporate_actions(data_dir)
    
    print(f"  ✓ 交易日历: {len(trading_dates)} 个交易日")
    print(f"  ✓ 股票池: {len(universe)} 只股票")
    print(f"  ✓ SPY数据: {len(spy_data['date'])} 天")
    print(f"  ✓ 市场数据: {len(market_data)} 只证券")
    print(f"  ✓ 回测区间: {trading_dates[0]} 至 {trading_dates[-1]}")
    
    # 数据验证
    print("\n【步骤2/4】验证数据一致性...")
    validate_data_consistency(market_data, spy_data, trading_dates, universe)
    
    # 配置回测参数
    print("\n【步骤3/4】配置回测参数...")
    config = ReboundConfig(
        start_date=trading_dates[0],
        end_date=trading_dates[-1],
        initial_equity=100000.0,
        rsi_threshold=10.0,
        volume_multiple=2.0,
        atr_stop_multiple=2.0,
        max_holding_sessions=10,
        max_positions=10,
        max_per_industry=2,
        risk_fraction=0.01,
        position_cap=0.15,
        commission_rate=0.00005,
        slippage_rate=0.0005,
    )
    
    print(f"  ✓ 初始资金: ${config.initial_equity:,.2f}")
    print(f"  ✓ RSI阈值: {config.rsi_threshold}")
    print(f"  ✓ 成交量倍数: {config.volume_multiple}")
    print(f"  ✓ ATR止损倍数: {config.atr_stop_multiple}")
    print(f"  ✓ 最大持仓天数: {config.max_holding_sessions}")
    print(f"  ✓ 最大持仓数: {config.max_positions}")
    print(f"  ✓ 佣金率: {config.commission_rate*10000}bp")
    print(f"  ✓ 滑点率: {config.slippage_rate*10000}bp")
    
    # 运行回测
    print("\n【步骤4/4】运行回测引擎...")
    engine = BacktestEngine(config)
    results = engine.run(
        trading_dates=trading_dates,
        market_data=market_data,
        spy_data=spy_data,
        universe=universe,
        corporate_actions=corporate_actions,
    )
    
    # 生成报告
    generate_reports(results, output_dir, config.to_dict())
    
    # 输出详细结果
    perf = results["performance"]
    
    print("\n" + "=" * 80)
    print("回测结果摘要")
    print("=" * 80)
    
    print("\n【收益指标】")
    print(f"  总收益率:          {perf['total_return_pct']:>10.2f}%")
    print(f"  年化收益率(CAGR):  {perf['cagr_pct']:>10.2f}%")
    print(f"  夏普比率:          {perf['sharpe_ratio']:>10.3f}")
    print(f"  最终权益:          ${perf['final_equity']:>12,.2f}")
    
    print("\n【风险指标】")
    print(f"  最大回撤:          {perf['max_drawdown']['max_drawdown_pct']:>10.2f}%")
    print(f"  回撤峰值日期:      {perf['max_drawdown']['peak_date']}")
    print(f"  回撤谷值日期:      {perf['max_drawdown']['valley_date']}")
    print(f"  回撤恢复日期:      {perf['max_drawdown']['recovery_date'] or '未恢复'}")
    
    print("\n【交易统计】")
    print(f"  总交易次数:        {perf['total_trades']:>10d}")
    print(f"  盈利交易:          {perf['win_rate']['winning_trades']:>10d}")
    print(f"  亏损交易:          {perf['win_rate']['losing_trades']:>10d}")
    print(f"  胜率:              {perf['win_rate']['win_rate_pct']:>10.2f}%")
    print(f"  平均盈利:          ${perf['win_rate']['avg_win']:>12,.2f}")
    print(f"  平均亏损:          ${perf['win_rate']['avg_loss']:>12,.2f}")
    print(f"  盈亏比:            {perf['win_rate']['profit_factor']:>10.3f}")
    
    print("\n【退出原因分布】")
    print(f"  {'退出原因':<20} {'次数':>8} {'总盈亏':>15} {'平均盈亏':>15}")
    print("  " + "-" * 60)
    for reason, stats in perf['exit_breakdown'].items():
        print(f"  {reason:<20} {stats['count']:>8d} ${stats['total_pnl']:>13,.2f} ${stats['avg_pnl']:>13,.2f}")
    
    print("\n【年度收益】")
    print(f"  {'年份':<8} {'起始权益':>15} {'结束权益':>15} {'收益率':>10}")
    print("  " + "-" * 50)
    for year_data in perf['annual_returns']:
        print(f"  {year_data['year']:<8} ${year_data['start_equity']:>13,.2f} ${year_data['end_equity']:>13,.2f} {year_data['return_pct']:>9.2f}%")
    
    # 显示交易明细
    if results['trades']:
        print("\n【交易明细（前20笔）】")
        print(f"  {'股票':<8} {'入场日期':<12} {'出场日期':<12} {'盈亏':>12} {'收益率':>10} {'退出原因':<15}")
        print("  " + "-" * 75)
        for trade in results['trades'][:20]:
            print(f"  {trade['security_id']:<8} {trade['entry_date']:<12} {trade['exit_date']:<12} "
                  f"${trade['pnl']:>10,.2f} {trade['return_pct']:>9.2f}% {trade['exit_reason']:<15}")
    
    print("\n" + "=" * 80)
    print("生成的报告文件")
    print("=" * 80)
    output_path = Path(output_dir)
    for file in sorted(output_path.glob("*")):
        size = file.stat().st_size
        print(f"  ✓ {file.name:<30} ({size:>8,} bytes)")
    
    print(f"\n✓ 回测完成！详细报告已保存至: {output_path.absolute()}")
    
    return results


if __name__ == "__main__":
    try:
        results = run_backtest_with_real_data()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 回测失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
