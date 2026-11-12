"""Backtest configuration and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict


@dataclass(frozen=True)
class ReboundConfig:
    """Immutable backtest configuration.

    All financial rates are expressed as decimals (e.g. 0.00005 = 0.005%).
    """

    start_date: str = "2000-01-01"
    end_date: str = "2025-12-31"
    initial_equity: float = 100_000.0
    decision_delay_minutes: int = 60

    # Transaction costs
    commission_rate: float = 0.00005   # 0.005% per side
    slippage_rate: float = 0.0005      # 0.05% per side

    # Liquidity filters
    liquidity_fraction: float = 0.30
    min_avg_volume: int = 500_000

    # Position sizing
    risk_fraction: float = 0.01        # 1% of equity per trade
    position_cap: float = 0.15         # 15% of equity per position

    # Portfolio constraints
    max_positions: int = 10
    max_per_industry: int = 2

    # Exit parameters
    atr_stop_multiple: float = 2.0
    max_holding_sessions: int = 10

    # Entry signal thresholds
    rsi_threshold: float = 10.0
    volume_multiple: float = 2.0

    @classmethod
    def from_dict(cls, overrides: Dict[str, Any] | None = None) -> "ReboundConfig":
        if not overrides:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in overrides.items() if k in known}
        return cls(**filtered)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def with_overrides(self, **kwargs: Any) -> "ReboundConfig":
        base = self.to_dict()
        base.update(kwargs)
        return ReboundConfig.from_dict(base)


# The 81-combination sweep grid
SWEEP_GRID = {
    "atr_stop_multiple": [1.5, 2.0, 2.5],
    "max_holding_sessions": [7, 10, 15],
    "rsi_threshold": [5, 10, 15],
    "volume_multiple": [1.5, 2.0, 2.5],
}

DEFAULT_CONFIG = ReboundConfig()
