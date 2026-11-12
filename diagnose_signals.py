#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号诊断脚本
检查演示数据质量和信号生成条件
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.services.signal_scanner.research.vcr_short.demo_data import DemoDataGenerator
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.signal import SignalGenerator


def diagnose_signals():
    """诊断信号生成"""
    print("=" * 80)
    print("信号诊断")
    print("=" * 80)
    
    # 生成演示数据
    print("\n[1/4] 生成演示数据...")
    generator = DemoDataGenerator()
    data = generator.generate(
        num_stocks=5,
        start_date="2021-01-01",
        end_date="2022-12-31"
    )
    print(f"✓ 生成了 {len(data)} 只股票的数据")
    
    # 配置
    config = VCRShortConfig(
        start_date="2022-01-01",
        end_date="2022-12-31"
    )
    
    signal_gen = SignalGenerator(config)
    
    # 统计
    total_checks = 0
    passed_checks = 0
    rejection_reasons_count = {}
    
    print("\n[2/4] 检查信号生成条件...")
    
    for symbol, stock_data in data.items():
        print(f"\n股票 {symbol}:")
        
        dates = stock_data['dates']
        closes = stock_data['close']
        opens = stock_data['open']
        highs = stock_data['high']
        lows = stock_data['low']
        volumes = stock_data['volume']
        
        # 找到2022年开始的位置
        dates_2022 = [d for d in dates if d >= "2022-01-01"]
        if not dates_2022:
            print("  无2022年数据")
            continue
        
        start_idx = dates.index(dates_2022[0])
        
        # 检查几个时间点
        check_indices = [
            start_idx + 50,   # 2022年3月左右
            start_idx + 100,  # 2022年5月左右
            start_idx + 150,  # 2022年8月左右
            start_idx + 200,  # 2022年10月左右
        ]
        
        for idx in check_indices:
            if idx >= len(dates):
                continue
            
            date = dates[idx]
            total_checks += 1
            
            # 生成信号
            signal_result = signal_gen.evaluate(
                instrument_id=symbol,
                dates=dates[:idx+1],
                open_prices=opens[:idx+1],
                high_prices=highs[:idx+1],
                low_prices=lows[:idx+1],
                close_prices=closes[:idx+1],
                volumes=volumes[:idx+1],
                signal_index=idx
            )
            
            if signal_result.passed:
                passed_checks += 1
                print(f"  ✓ {date}: 生成信号")
            else:
                # 统计拒绝原因
                for reason in signal_result.rejection_reasons:
                    rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
                
                # 显示前3个拒绝原因
                reasons_str = ", ".join(signal_result.rejection_reasons[:3])
                print(f"  ✗ {date}: 未生成信号 - {reasons_str}")
    
    print("\n[3/4] 统计结果:")
    print(f"  总检查次数: {total_checks}")
    print(f"  通过次数: {passed_checks}")
    print(f"  通过率: {100*passed_checks/total_checks:.2f}%" if total_checks > 0 else "  通过率: N/A")
    
    print("\n[4/4] 拒绝原因统计:")
    if rejection_reasons_count:
        for reason, count in sorted(rejection_reasons_count.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason}: {count}")
    else:
        print("  无拒绝原因（所有检查都通过了）")


def check_data_quality():
    """检查数据质量"""
    print("\n" + "=" * 80)
    print("数据质量检查")
    print("=" * 80)
    
    # 生成演示数据
    generator = DemoDataGenerator()
    data = generator.generate(
        num_stocks=5,
        start_date="2021-01-01",
        end_date="2022-12-31"
    )
    
    config = VCRShortConfig(
        start_date="2022-01-01",
        end_date="2022-12-31"
    )
    
    for symbol, stock_data in data.items():
        print(f"\n股票 {symbol}:")
        
        dates = stock_data['dates']
        closes = stock_data['close']
        volumes = stock_data['volume']
        
        # 找到2022年开始的位置
        dates_2022 = [d for d in dates if d >= "2022-01-01"]
        if not dates_2022:
            print("  无2022年数据")
            continue
        
        start_idx = dates.index(dates_2022[0])
        closes_2022 = closes[start_idx:]
        volumes_2022 = volumes[start_idx:]
        
        print(f"  2022年数据点数: {len(closes_2022)}")
        print(f"  价格范围: ${min(closes_2022):.2f} - ${max(closes_2022):.2f}")
        print(f"  平均价格: ${sum(closes_2022)/len(closes_2022):.2f}")
        
        # 检查价格过滤
        prices_above_5 = sum(1 for c in closes_2022 if c > 5.0)
        print(f"  价格 > $5 的天数: {prices_above_5}/{len(closes_2022)} ({100*prices_above_5/len(closes_2022):.1f}%)")
        
        # 检查 ADV20
        adv20_values = []
        for i in range(19, len(closes_2022)):
            avg_dollar_volume = sum(closes_2022[j] * volumes_2022[j] for j in range(i-19, i+1)) / 20
            adv20_values.append(avg_dollar_volume)
        
        if adv20_values:
            adv20_above_25m = sum(1 for v in adv20_values if v > 25_000_000)
            print(f"  ADV20 > $25M 的天数: {adv20_above_25m}/{len(adv20_values)} ({100*adv20_above_25m/len(adv20_values):.1f}%)")
            print(f"  ADV20 范围: ${min(adv20_values)/1e6:.2f}M - ${max(adv20_values)/1e6:.2f}M")
            print(f"  平均 ADV20: ${sum(adv20_values)/len(adv20_values)/1e6:.2f}M")


if __name__ == "__main__":
    try:
        check_data_quality()
        diagnose_signals()
        print("\n" + "=" * 80)
        print("诊断完成")
        print("=" * 80)
    except Exception as e:
        print(f"\n✗ 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
