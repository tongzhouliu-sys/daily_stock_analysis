#!/usr/bin/env python3
"""将 data10 目录的真实数据转换为回测系统格式。"""

import csv
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict


def convert_data10_to_backtest_format(data10_dir: str, output_dir: str):
    """转换 data10 数据为回测格式。"""
    print("=" * 80)
    print("转换 data10 真实数据为回测格式")
    print("=" * 80)
    
    data10_path = Path(data10_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 收集所有日期文件
    date_files = sorted([f for f in data10_path.glob("*.csv") if f.stem[0].isdigit()])
    print(f"\n找到 {len(date_files)} 个数据文件")
    print(f"时间范围: {date_files[0].stem} 至 {date_files[-1].stem}")
    
    # 选择回测区间（使用最近4年数据，包含2020年疫情和2022年熊市）
    start_date = "2020-01-01"
    end_date = "2023-12-31"
    
    # 过滤日期范围
    date_files = [f for f in date_files if start_date <= f.stem <= end_date]
    print(f"\n回测区间: {start_date} 至 {end_date}")
    print(f"实际交易日: {len(date_files)} 天")
    
    # 读取完整标普500成分股池（2020-2023期间数据完整的股票）
    import json
    universe_file = Path("sp500_universe_2020_2023.json")
    if universe_file.exists():
        with open(universe_file, 'r') as f:
            universe_data = json.load(f)
            target_tickers = set(universe_data["universe"])
        print(f"✓ 从 {universe_file} 加载 {len(target_tickers)} 只股票")
    else:
        # 如果文件不存在，使用默认的大盘股列表
        target_tickers = {
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
            "JPM", "V", "JNJ", "WMT", "PG", "MA", "UNH", "HD",
            "BAC", "XOM", "PFE", "CSCO", "KO", "SPY"
        }
        print(f"⚠ 未找到股票池文件，使用默认的 {len(target_tickers)} 只股票")
    
    print(f"\n【步骤1/4】读取目标股票数据...")
    
    # 按股票组织数据
    stock_data = defaultdict(lambda: {
        "date": [], "open": [], "high": [], "low": [], "close": [], "volume": []
    })
    
    trading_dates = []
    
    for date_file in date_files:
        date_str = date_file.stem
        trading_dates.append(date_str)
        
        with open(date_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row['ticker']
                if ticker in target_tickers:
                    try:
                        stock_data[ticker]["date"].append(date_str)
                        stock_data[ticker]["open"].append(float(row['open']))
                        stock_data[ticker]["high"].append(float(row['high']))
                        stock_data[ticker]["low"].append(float(row['low']))
                        stock_data[ticker]["close"].append(float(row['close']))
                        stock_data[ticker]["volume"].append(int(row['volume']))
                    except (ValueError, KeyError):
                        # 跳过无效数据
                        stock_data[ticker]["date"].pop()
    
    print(f"  ✓ 读取完成")
    
    # 分离 SPY 数据
    print(f"\n【步骤2/4】分离 SPY 数据...")
    spy_data = stock_data.pop("SPY")
    print(f"  ✓ SPY: {len(spy_data['date'])} 天")
    
    # 过滤掉数据不完整的股票（必须完全匹配交易日数量）
    print(f"\n【步骤3/4】过滤数据不完整的股票...")
    required_days = len(trading_dates)
    universe = []
    skipped_count = 0
    for ticker, data in stock_data.items():
        if len(data["date"]) == required_days:
            universe.append(ticker)
        else:
            skipped_count += 1
            if skipped_count <= 10:  # 只显示前10个
                print(f"  ✗ {ticker}: {len(data['date'])}/{required_days} 天数据，跳过")
    
    if skipped_count > 10:
        print(f"  ... 还有 {skipped_count - 10} 只股票数据不完整")
    
    universe.sort()
    print(f"  ✓ 保留 {len(universe)} 只股票（数据完整）")
    
    # 保存数据
    print(f"\n【步骤4/4】保存转换后的数据...")
    
    # 保存交易日历
    with open(output_path / "trading_calendar.json", "w") as f:
        json.dump({"trading_dates": trading_dates}, f, indent=2)
    print(f"  ✓ 交易日历: {len(trading_dates)} 天")
    
    # 保存股票池
    with open(output_path / "universe.json", "w") as f:
        json.dump({"securities": universe}, f, indent=2)
    print(f"  ✓ 股票池: {len(universe)} 只")
    
    # 保存市场数据
    with open(output_path / "market_data.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "security_id", "open", "high", "low", "close", "volume"])
        for ticker in universe:
            data = stock_data[ticker]
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
    print(f"  ✓ 市场数据: {len(universe)} 只股票")
    
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
    
    print(f"\n✓ 数据转换完成！")
    print(f"  输出目录: {output_path.absolute()}")
    
    return str(output_path)


if __name__ == "__main__":
    import sys
    
    data10_dir = "data10"
    output_dir = "real_data_converted"
    
    try:
        convert_data10_to_backtest_format(data10_dir, output_dir)
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 转换失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
