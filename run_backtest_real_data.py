#!/usr/bin/env python3
"""下载真实美股历史数据并运行回测。"""

import sys
import json
import csv
from pathlib import Path
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("错误: 需要安装 yfinance")
    print("运行: pip install yfinance")
    sys.exit(1)

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


def download_real_data(output_dir: str, start_date: str = "2020-01-01", end_date: str = "2023-12-31"):
    """从 yfinance 下载真实美股数据。"""
    print("=" * 80)
    print("下载真实美股历史数据")
    print("=" * 80)
    print(f"区间: {start_date} 至 {end_date}")
    print()
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 标普500成分股样本（选择流动性好的大盘股）
    tickers = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "JPM", "V", "JNJ",
        "WMT", "PG", "MA", "UNH", "HD",
        "BAC", "XOM", "PFE", "CSCO", "KO",
    ]
    
    print(f"【步骤1/4】下载 {len(tickers)} 只股票数据...")
    
    # 下载所有股票数据
    all_data = {}
    for ticker in tickers:
        print(f"  下载 {ticker}...", end=" ")
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(start=start_date, end=end_date, auto_adjust=False)
            
            if df.empty:
                print("无数据")
                continue
            
            all_data[ticker] = {
                "date": [d.strftime("%Y-%m-%d") for d in df.index],
                "open": df["Open"].tolist(),
                "high": df["High"].tolist(),
                "low": df["Low"].tolist(),
                "close": df["Close"].tolist(),
                "volume": df["Volume"].tolist(),
            }
            print(f"✓ {len(df)} 天")
        except Exception as e:
            print(f"✗ {e}")
    
    # 下载 SPY 数据
    print(f"\n【步骤2/4】下载 SPY (标普500 ETF) 数据...")
    spy = yf.Ticker("SPY")
    spy_df = spy.history(start=start_date, end=end_date, auto_adjust=False)
    spy_data = {
        "date": [d.strftime("%Y-%m-%d") for d in spy_df.index],
        "open": spy_df["Open"].tolist(),
        "high": spy_df["High"].tolist(),
        "low": spy_df["Low"].tolist(),
        "close": spy_df["Close"].tolist(),
        "volume": spy_df["Volume"].tolist(),
    }
    print(f"  ✓ SPY: {len(spy_df)} 天")
    
    # 保存数据
    print(f"\n【步骤3/4】保存数据到 {output_dir}...")
    
    # 保存交易日历
    trading_dates = spy_data["date"]
    with open(output_path / "trading_calendar.json", "w") as f:
        json.dump({"trading_dates": trading_dates}, f, indent=2)
    print(f"  ✓ 交易日历: {len(trading_dates)} 天")
    
    # 保存股票池
    universe = list(all_data.keys())
    with open(output_path / "universe.json", "w") as f:
        json.dump({"securities": universe}, f, indent=2)
    print(f"  ✓ 股票池: {len(universe)} 只")
    
    # 保存市场数据
    with open(output_path / "market_data.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "security_id", "open", "high", "low", "close", "volume"])
        for ticker, data in all_data.items():
            for i in range(len(data["date"])):
                writer.writerow([
                    data["date"][i],
                    ticker,
                    data["open"][i],
                    data["high"][i],
                    data["low"][i],
                    data["close"][i],
                    data["volume"][i],
                ])
    print(f"  ✓ 市场数据: {len(all_data)} 只股票")
    
    # 保存 SPY 数据
    with open(output_path / "spy_data.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for i in range(len(spy_data["date"])):
            writer.writerow([
                spy_data["date"][i],
                spy_data["open"][i],
                spy_data["high"][i],
                spy_data["low"][i],
                spy_data["close"][i],
                spy_data["volume"][i],
            ])
    print(f"  ✓ SPY 数据")
    
    # 保存空的公司行动文件
    with open(output_path / "corporate_actions.json", "w") as f:
        json.dump([], f)
    print(f"  ✓ 公司行动: 0 个")
    
    print(f"\n【步骤4/4】数据下载完成！")
    print(f"  数据目录: {output_path.absolute()}")
    
    return str(output_path)


def run_backtest_with_real_data(data_dir: str, output_dir: str):
    """使用真实数据运行回测。"""
    print("\n" + "=" * 80)
    print("运行回测")
    print("=" * 80)
    
    # 加载数据
    print("\n【步骤1/3】加载数据...")
    trading_dates = load_trading_calendar(data_dir)
    universe = load_universe(data_dir)
    spy_data = load_spy_data(data_dir)
    market_data = load_market_data(data_dir)
    corporate_actions = load_corporate_actions(data_dir)
    
    print(f"  ✓ 交易日历: {len(trading_dates)} 天")
    print(f"  ✓ 股票池: {len(universe)} 只")
    print(f"  ✓ 市场数据: {len(market_data)} 只")
    
    # 验证数据
    print("\n【步骤2/3】验证数据一致性...")
    validate_data_consistency(market_data, spy_data, trading_dates, universe)
    
    # 配置回测
    print("\n【步骤3/3】配置并运行回测...")
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
    
    print(f"  ✓ 回测区间: {config.start_date} 至 {config.end_date}")
    print(f"  ✓ 初始资金: ${config.initial_equity:,.2f}")
    
    # 运行回测
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
    
    return results, config


def print_detailed_results(results, config):
    """打印详细结果。"""
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
    
    if results['trades']:
        print("\n【交易明细（前10笔）】")
        print(f"  {'股票':<8} {'入场日期':<12} {'出场日期':<12} {'盈亏':>12} {'收益率':>10} {'退出原因':<15}")
        print("  " + "-" * 75)
        for trade in results['trades'][:10]:
            print(f"  {trade['security_id']:<8} {trade['entry_date']:<12} {trade['exit_date']:<12} "
                  f"${trade['pnl']:>10,.2f} {trade['return_pct']:>9.2f}% {trade['exit_reason']:<15}")


def main():
    """主函数。"""
    data_dir = "real_data"
    output_dir = "real_backtest_output"
    
    # 下载真实数据
    download_real_data(data_dir, start_date="2020-01-01", end_date="2023-12-31")
    
    # 运行回测
    results, config = run_backtest_with_real_data(data_dir, output_dir)
    
    # 打印结果
    print_detailed_results(results, config)
    
    print("\n" + "=" * 80)
    print("报告文件")
    print("=" * 80)
    output_path = Path(output_dir)
    for file in sorted(output_path.glob("*")):
        size = file.stat().st_size
        print(f"  ✓ {file.name:<30} ({size:>8,} bytes)")
    
    print(f"\n✓ 回测完成！详细报告已保存至: {output_path.absolute()}")


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 回测失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
