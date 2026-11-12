# -*- coding: utf-8 -*-
"""Volume Climax Reversal Short backtest engine.

This package implements a portfolio-level short-selling backtest for US equities
based on volume climax reversal patterns. The engine is designed as an offline
research tool that operates on frozen historical data snapshots.

Key components:
- config: Strategy parameters and configuration
- indicators: Technical indicators (ATR, SMA, etc.)
- signal: Signal generation with all filter conditions
- portfolio: Position and portfolio management
- ledger: Accounting for borrow fees, dividends, P&L
- matching: Order matching and execution simulation
- performance: Performance metrics calculation
- sensitivity: Parameter sensitivity analysis
- report: Report generation
"""

from src.services.signal_scanner.research.vcr_short.config import (
    VCRShortConfig,
    DefaultConfig,
    SensitivityConfig,
)

__version__ = "1.0.0"
__all__ = [
    "VCRShortConfig",
    "DefaultConfig",
    "SensitivityConfig",
]
