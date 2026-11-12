#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 data10 文件夹加载真实美股数据进行回测
"""

import sys
import os
from pathlib import Path
import csv
from collections import defaultdict
from datetime import datetime

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.engine import VCRShortBacktestEngine
from src.services.signal_scanner.research.vcr_short.report import ReportGenerator


def load_data10(data_dir: str, start_date: str = None, end_date: str = None):
    """
    加载 data10 文件夹中的数据
    
    data10 文件夹结构：每个文件以日期命名（如 2016-05-31.csv）
    每个文件包含当天的所有股票数据
    
    返回格式：
    {
        'AAPL': {
            'dates': ['2016-05-31', '2016-06-01', ...],
            'open': [45.79, 46.0, ...],
            'high': [46.0, 46.5, ...],
            'low': [45.575, 45.8, ...],
            'close': [45.89, 46.2, ...],
            'volume': [2255765, 2500000, ...]
        },
        ...
    }
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        raise ValueError(f"数据目录不存在: {data_dir}")
    
    # 获取所有 CSV 文件并排序
    csv_files = sorted([f for f in data_path.glob("*.csv") if f.stem[0].isdigit()])
    
    if not csv_files:
        raise ValueError(f"在 {data_dir} 中未找到 CSV 文件")
    
    print(f"找到 {len(csv_files)} 个数据文件")
    
    # 过滤日期范围
    if start_date or end_date:
        filtered_files = []
        for f in csv_files:
            file_date = f.stem
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue
            filtered_files.append(f)
        csv_files = filtered_files
        print(f"日期过滤后剩余 {len(csv_files)} 个文件")
    
    # 收集所有股票数据
    # 结构：{ticker: {date: {open, high, low, close, volume}}}
    stock_data = defaultdict(dict)
    
    for i, csv_file in enumerate(csv_files):
        if (i + 1) % 100 == 0:
            print(f"处理文件 {i + 1}/{len(csv_files)}...")
        
        date_str = csv_file.stem  # 如 '2016-05-31'
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row['ticker']
                
                # 跳过无效数据
                try:
                    open_price = float(row['open'])
                    high_price = float(row['high'])
                    low_price = float(row['low'])
                    close_price = float(row['close'])
                    volume = int(row['volume'])
                    
                    # 跳过价格为 0 或负数的记录
                    if open_price <= 0 or high_price <= 0 or low_price <= 0 or close_price <= 0:
                        continue
                    
                    # 跳过成交量为 0 的记录
                    if volume <= 0:
                        continue
                    
                    stock_data[ticker][date_str] = {
                        'open': open_price,
                        'high': high_price,
                        'low': low_price,
                        'close': close_price,
                        'volume': volume
                    }
                except (ValueError, KeyError):
                    continue
    
    print(f"共加载 {len(stock_data)} 只股票的数据")
    
    # 转换为回测引擎需要的格式
    result = {}
    for ticker, date_data in stock_data.items():
        # 按日期排序
        sorted_dates = sorted(date_data.keys())
        
        if len(sorted_dates) < 252:  # 至少需要一年的数据
            continue
        
        result[ticker] = {
            'dates': sorted_dates,
            'open': [date_data[d]['open'] for d in sorted_dates],
            'high': [date_data[d]['high'] for d in sorted_dates],
            'low': [date_data[d]['low'] for d in sorted_dates],
            'close': [date_data[d]['close'] for d in sorted_dates],
            'volume': [date_data[d]['volume'] for d in sorted_dates]
        }
    
    print(f"过滤后剩余 {len(result)} 只股票（至少 252 个交易日）")
    
    return result


def run_backtest_with_real_data():
    """使用真实数据运行回测"""
    print("=" * 80)
    print("使用 data10 真实数据进行回测")
    print("=" * 80)
    
    # 加载数据（使用 2020-2022 年的数据）
    data_dir = str(project_root / "data10")
    
    print("\n[1/4] 加载数据...")
    data = load_data10(
        data_dir,
        start_date="2019-01-01",  # 提前一年用于预热
        end_date="2022-12-31"
    )
    
    if not data:
        print("错误：未加载到任何数据")
        return False
    
    # 选择一些流动性好的股票（价格 > $10，成交量较大）
    print("\n[2/4] 筛选流动性好的股票...")
    filtered_data = {}
    for ticker, stock in data.items():
        # 取最近一年的平均成交量
        recent_volumes = stock['volume'][-252:]
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        recent_prices = stock['close'][-252:]
        avg_price = sum(recent_prices) / len(recent_prices)
        
        # 筛选条件：平均成交量 > 100 万股，平均价格 > $10
        if avg_volume > 1_000_000 and avg_price > 10:
            filtered_data[ticker] = stock
    
    print(f"筛选后剩余 {len(filtered_data)} 只股票")
    
    # 限制股票数量以加快回测速度
    max_stocks = 100
    if len(filtered_data) > max_stocks:
        # 按平均成交量排序，选择前 100 只
        sorted_tickers = sorted(
            filtered_data.keys(),
            key=lambda t: sum(filtered_data[t]['volume'][-252:]) / 252,
            reverse=True
        )
        filtered_data = {t: filtered_data[t] for t in sorted_tickers[:max_stocks]}
        print(f"选择流动性最好的 {max_stocks} 只股票")
    
    # 配置回测参数
    print("\n[3/4] 配置回测参数...")
    config = VCRShortConfig(
        start_date="2020-01-01",  # 2020 年开始回测
        end_date="2022-12-31",    # 到 2022 年底
        initial_equity=100000.0,
        max_positions=10,
        slippage_bps=10.0,
        commission_per_trade=0.0,
        warmup_days=252
    )
    
    print(f"回测区间: {config.start_date} 至 {config.end_date}")
    print(f"初始资金: ${config.initial_equity:,.2f}")
    print(f"最大持仓: {config.max_positions}")
    
    # 运行回测
    print("\n[4/4] 运行回测...")
    engine = VCRShortBacktestEngine(config)
    results = engine.run(filtered_data)
    
    # 输出结果
    print("\n" + "=" * 80)
    print("回测结果")
    print("=" * 80)
    
    perf = results['performance']
    
    print(f"\n交易统计:")
    print(f"  总交易次数: {perf.get('total_trades', 0)}")
    print(f"  盈利交易: {perf.get('winning_trades', 0)}")
    print(f"  亏损交易: {perf.get('losing_trades', 0)}")
    
    win_rate = perf.get('win_rate')
    if win_rate is not None:
        print(f"  胜率: {win_rate:.2%}")
    else:
        print(f"  胜率: N/A")
    
    print(f"\n收益指标:")
    cagr = perf.get('cagr')
    if cagr is not None:
        print(f"  年化收益率 (CAGR): {cagr:.2%}")
    else:
        print(f"  年化收益率 (CAGR): N/A")
    
    max_dd = perf.get('max_drawdown')
    if max_dd is not None:
        print(f"  最大回撤: {max_dd:.2%}")
    else:
        print(f"  最大回撤: N/A")
    
    sharpe = perf.get('sharpe_ratio')
    if sharpe is not None:
        print(f"  Sharpe 比率: {sharpe:.2f}")
    else:
        print(f"  Sharpe 比率: N/A")
    
    sortino = perf.get('sortino_ratio')
    if sortino is not None:
        print(f"  Sortino 比率: {sortino:.2f}")
    else:
        print(f"  Sortino 比率: N/A")
    
    calmar = perf.get('calmar_ratio')
    if calmar is not None:
        print(f"  Calmar 比率: {calmar:.2f}")
    else:
        print(f"  Calmar 比率: N/A")
    
    print(f"\n持仓统计:")
    print(f"  信号数量: {len(results['signals'])}")
    print(f"  成交记录: {len(results['trades'])}")
    print(f"  期末未平仓: {len(results['open_positions'])}")
    
    # 保存报告
    print("\n[5/5] 生成报告...")
    output_dir = Path("backtest_results/real_data")
    report_gen = ReportGenerator(str(output_dir))
    
    report_gen.save_all_reports(
        performance_metrics=results["performance"],
        config=results["config"],
        data_info=results["data_info"],
        all_trades=results["trades"],
        open_positions=results["open_positions"],
        equity_curve=results["equity_curve"]
    )
    
    print(f"✓ 报告已生成到: {output_dir}/")
    
    # 输出前 10 笔交易
    if results['trades']:
        print("\n" + "=" * 80)
        print("前 10 笔交易")
        print("=" * 80)
        for i, trade in enumerate(results['trades'][:10]):
            print(f"\n交易 {i+1}:")
            print(f"  股票: {trade['instrument_id']}")
            print(f"  信号日期: {trade['signal_date']}")
            print(f"  入场日期: {trade['entry_date']}")
            print(f"  入场价格: ${trade['entry_price']:.2f}")
            print(f"  退出日期: {trade['exit_date']}")
            print(f"  退出价格: ${trade['exit_price']:.2f}")
            print(f"  退出原因: {trade['exit_reason']}")
            print(f"  净盈亏: ${trade['net_pnl']:.2f}")
    
    return True


if __name__ == "__main__":
    try:
        success = run_backtest_with_real_data()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
