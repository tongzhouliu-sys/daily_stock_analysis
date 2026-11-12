# Volume Climax Reversal Short Strategy Backtest

美股成交量高潮反转做空策略回测引擎。

## 概述

本模块实现了基于成交量高潮反转模式的做空策略回测系统，用于离线研究和策略验证。

### 策略逻辑

该策略识别以下技术形态组合：
1. **加速上涨**：价格在 20 日和 50 日均线上方，且短期涨幅显著
2. **波动放大**：ATR 相对 20 天前扩张 1.5 倍以上
3. **高位运行**：价格处于 252 日高点的 95% 以内
4. **底部启动量**：相对底部区域成交量放大 1.5 倍
5. **天量**：成交量达到 20 日均量的 2.5 倍，且为 120 日最高

当所有条件满足时，在下一交易日开盘做空，并设置止损和止盈。

### 风险管理

- **仓位控制**：单笔交易风险不超过权益的 1%
- **组合限制**：最多同时持有 10 个空头头寸
- **止损机制**：基于 ATR 的初始止损，支持保本止损和跟踪止损
- **止盈策略**：分两阶段止盈（TP1 平仓 50%，TP2 平仓剩余）
- **时间退出**：持仓 20 个交易日后强制平仓

## 安装依赖

```bash
pip install pandas numpy
```

## 快速开始

### 1. 生成演示数据

```bash
python -m src.services.signal_scanner.research.vcr_short.cli demo \
    --output demo_data.csv \
    --stocks 50 \
    --start-date 2020-01-01 \
    --end-date 2023-12-31
```

### 2. 运行回测

```bash
python -m src.services.signal_scanner.research.vcr_short.cli run \
    --data demo \
    --output results/ \
    --start-date 2020-01-01 \
    --end-date 2023-12-31 \
    --equity 100000 \
    --max-positions 10 \
    --slippage 10
```

### 3. 参数敏感性分析

```bash
python -m src.services.signal_scanner.research.vcr_short.cli sensitivity \
    --data demo \
    --output sensitivity_results/ \
    --start-date 2020-01-01 \
    --end-date 2023-12-31
```

## 使用真实数据

### 数据格式要求

准备 CSV 文件，包含以下列：
```
date,instrument_id,open,high,low,close,volume
2020-01-02,AAPL,74.06,75.15,73.80,75.09,135480400
```

### 运行回测

```bash
python -m src.services.signal_scanner.research.vcr_short.cli run \
    --data your_data.csv \
    --output results/ \
    --borrow-rates borrow_rates.json \
    --dividends dividends.json
```

### 可选数据格式

**借券费率** (`borrow_rates.json`)：
```json
{
    "AAPL": 0.05,
    "MSFT": 0.08,
    "TSLA": 0.15
}
```

**分红数据** (`dividends.json`)：
```json
{
    "AAPL": [
        {"date": "2020-02-07", "amount": 0.77},
        {"date": "2020-05-08", "amount": 0.82}
    ]
}
```

## 输出文件

回测完成后，输出目录包含：

- `summary.json` - 完整绩效报告
- `trades.json` - 详细交易记录
- `trades.csv` - 交易数据（CSV 格式）
- `equity_curve.csv` - 每日权益曲线
- `sensitivity.json` - 参数敏感性分析（仅 sensitivity 命令）
- `REPORT.md` - 可读性报告摘要

## 配置参数

### 核心参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `start_date` | 2010-01-01 | 回测开始日期 |
| `end_date` | 2025-12-31 | 回测结束日期 |
| `initial_equity` | 100000 | 初始权益（美元） |
| `max_positions` | 10 | 最大持仓数量 |
| `slippage_bps` | 10 | 滑点（基点） |

### 信号参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `acceleration_20d_threshold` | 0.30 | 20 日加速阈值 |
| `acceleration_5d_threshold` | 0.10 | 5 日加速阈值 |
| `atr_expansion_ratio` | 1.5 | ATR 扩张比率 |
| `high_position_threshold` | 0.95 | 高位阈值 |
| `bottom_volume_multiplier` | 1.5 | 底部启动量倍数 |
| `climax_volume_multiplier` | 2.5 | 天量倍数 |

### 风控参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_risk_per_trade_pct` | 0.01 | 单笔交易最大风险（1%） |
| `initial_sl_atr_multiplier` | 1.5 | 初始止损 ATR 倍数 |
| `tp1_close_pct` | 0.50 | TP1 平仓比例（50%） |
| `time_exit_days` | 20 | 时间退出天数 |

## 绩效指标

- **CAGR**：复合年增长率
- **最大回撤**：峰值到谷底的最大跌幅
- **Sharpe Ratio**：风险调整后收益
- **Sortino Ratio**：下行风险调整后收益
- **Calmar Ratio**：CAGR 与最大回撤的比率
- **胜率**：盈利交易占比
- **盈亏比**：平均盈利 / 平均亏损
- **利润因子**：总盈利 / 总亏损

## 模块结构

```
vcr_short/
├── __init__.py          # 包初始化
├── config.py            # 配置参数
├── indicators.py        # 技术指标计算
├── signal.py            # 信号生成
├── portfolio.py         # 组合管理
├── matching.py          # 订单撮合
├── ledger.py            # 账本管理
├── performance.py       # 绩效计算
├── sensitivity.py       # 敏感性分析
├── report.py            # 报告生成
├── engine.py            # 主引擎
├── demo_data.py         # 演示数据生成
└── cli.py               # 命令行接口
```

## 注意事项

1. **数据质量**：确保输入数据包含完整的 OHLCV 信息，无缺失值
2. **前视偏差**：引擎严格使用时点数据，避免前视偏差
3. **借券成本**：默认借券费率为 10%，实际费率可能因标的和时间而异
4. **分红调整**：做空需支付分红，确保提供分红数据
5. **流动性假设**：默认假设所有信号均可执行，实际交易可能受流动性限制

## 局限性

- 日线数据无法精确判断盘中成交顺序
- 未考虑卖空价格限制（uptick rule）
- 未考虑借券召回风险
- 演示数据为合成数据，不代表真实市场表现

## 开发计划

- [ ] 集成真实美股历史数据
- [ ] 添加更多可选过滤器（如财报日期过滤）
- [ ] 支持多策略对比
- [ ] 添加可视化报告
- [ ] 支持实时信号扫描

## 许可证

本项目遵循主项目许可证。
