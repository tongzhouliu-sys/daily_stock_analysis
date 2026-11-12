#!/usr/bin/env python3
"""识别2020-2023期间的标普500成分股（基于数据完整性筛选）。"""

import csv
from pathlib import Path
from collections import defaultdict

def identify_universe(data10_dir: str, start_date: str = "2020-01-01", end_date: str = "2023-12-31"):
    """识别在回测区间有完整数据的股票。"""
    print("=" * 80)
    print("识别标普500成分股（基于数据完整性）")
    print("=" * 80)
    
    data10_path = Path(data10_dir)
    
    # 收集回测区间的日期文件
    date_files = sorted([
        f for f in data10_path.glob("*.csv") 
        if f.stem[0].isdigit() and start_date <= f.stem <= end_date
    ])
    
    print(f"\n回测区间: {start_date} 至 {end_date}")
    print(f"交易日数量: {len(date_files)} 天")
    
    # 统计每只股票的出现次数
    ticker_counts = defaultdict(int)
    
    print(f"\n扫描数据文件...")
    for i, date_file in enumerate(date_files):
        if (i + 1) % 200 == 0:
            print(f"  已处理 {i + 1}/{len(date_files)} 个文件")
        
        with open(date_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row['ticker']
                ticker_counts[ticker] += 1
    
    print(f"  ✓ 扫描完成，共 {len(ticker_counts)} 只股票")
    
    # 筛选：至少90%交易日有数据（作为标普500成分股的代理）
    min_days = int(len(date_files) * 0.9)
    print(f"\n筛选条件: 至少 {min_days} 个交易日有数据（{len(date_files)}天的90%）")
    
    qualified_tickers = sorted([
        ticker for ticker, count in ticker_counts.items() 
        if count >= min_days
    ])
    
    print(f"  ✓ 符合条件的股票: {len(qualified_tickers)} 只")
    
    # 检查SPY
    if "SPY" in qualified_tickers:
        print(f"  ✓ SPY 包含在内")
    else:
        print(f"  ⚠ SPY 不在合格列表中（天数: {ticker_counts.get('SPY', 0)}）")
    
    # 显示一些示例
    print(f"\n示例股票（前20只）:")
    for ticker in qualified_tickers[:20]:
        print(f"  {ticker}: {ticker_counts[ticker]} 天")
    
    # 保存到文件
    output_file = Path("sp500_universe_2020_2023.json")
    import json
    with open(output_file, 'w') as f:
        json.dump({
            "universe": qualified_tickers,
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": len(date_files),
            "min_days_required": min_days,
            "total_tickers": len(qualified_tickers)
        }, f, indent=2)
    
    print(f"\n✓ 股票池已保存至: {output_file}")
    print(f"  总计: {len(qualified_tickers)} 只股票")
    
    return qualified_tickers


if __name__ == "__main__":
    import sys
    
    try:
        universe = identify_universe("data10")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 识别失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
