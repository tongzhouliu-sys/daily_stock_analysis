# -*- coding: utf-8 -*-
"""Configuration for Volume Climax Reversal Short strategy.

This module defines all strategy parameters, filters, and constraints
as specified in the requirements document.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class VCRShortConfig:
    """Main configuration for the VCR Short backtest.

    All parameters have defaults as specified in the requirements document.
    """

    # === Backtest Period ===
    start_date: str = "2010-01-01"
    end_date: str = "2025-12-31"

    # === Capital & Portfolio ===
    initial_equity: float = 100_000.0  # USD
    max_positions: int = 10  # Maximum concurrent short positions
    max_exposure_pct: float = 1.0  # Max 100% of equity in notional exposure
    max_risk_per_trade_pct: float = 0.01  # Max 1% of equity risk per trade

    # === Price & Liquidity Filters ===
    min_close_price: float = 5.0  # Strict > $5
    min_adv20: float = 25_000_000.0  # Strict > $25M average dollar volume

    # === Borrowing Costs ===
    max_borrow_rate: float = 0.20  # Max 20% annualized borrow fee
    default_borrow_rate: float = 0.10  # Default 10% when unknown
    require_gc_collateral: bool = True  # Prefer General Collateral source

    # === Execution ===
    slippage_bps: float = 10.0  # 10 basis points one-way
    commission_per_trade: float = 0.0  # USD per trade, can set to $1

    # === Signal Parameters ===
    # Acceleration uptrend
    sma_short_period: int = 20
    sma_long_period: int = 50
    acceleration_20d_threshold: float = 0.30  # 30% over 20 days
    acceleration_5d_threshold: float = 0.10  # 10% over 5 days

    # Volatility expansion
    atr_period: int = 14
    atr_expansion_ratio: float = 1.5  # ATR[T] / ATR[T-20] >= 1.5

    # High position
    high_position_lookback: int = 252  # 252 trading days
    high_position_threshold: float = 0.95  # >= 95% of 252-day high

    # Bottom launch volume
    bottom_lookback: int = 120  # Lookback for lowest close
    bottom_window_offset_start: int = 5  # L-5
    bottom_window_offset_end: int = 10  # L+10
    bottom_volume_multiplier: float = 1.5  # V[T] >= 1.5 * V_base_max

    # Climax volume
    climax_volume_sma_period: int = 20
    climax_volume_multiplier: float = 2.5  # V[T] >= 2.5 * SMA20(V)
    climax_volume_lookback: int = 120  # 120-day max volume

    # Optional filters
    require_rejection: bool = False  # High-low rejection candle filter
    rejection_threshold: float = 0.33  # (H-C)/(H-Lo) >= 0.33
    require_earnings_filter: bool = False  # Earnings date filter

    # === Entry Rules ===
    entry_gap_up_threshold: float = 0.02  # O_next > H[T] * 1.02 -> skip
    entry_gap_down_threshold: float = 0.05  # O_next < C[T] * 0.95 -> skip

    # === Stop Loss ===
    initial_sl_atr_multiplier: float = 1.5  # SL = max(S + 1.5*A, ...)
    initial_sl_high_multiplier: float = 1.005  # SL = max(..., H[T] * 1.005, ...)
    initial_sl_atr_base: float = 0.8  # SL = max(..., S + 0.8*A)
    max_initial_risk_pct: float = 0.12  # R/S > 12% -> skip

    # === Take Profit ===
    tp1_atr_multiplier: float = 0.5  # TP1 = min(TP1_candidate, S - 0.5*A)
    tp2_atr_offset: float = 0.5  # TP2 = min(TP1 - 0.5*A, TP2_raw)
    tp1_close_pct: float = 0.50  # Close 50% at TP1

    # === Time Exit ===
    time_exit_days: int = 20  # Exit at day 20 close

    # === Trailing Stop ===
    breakeven_trigger_r: float = 1.0  # Move SL to S when price hits S-R
    trailing_stop_buffer: float = 0.005  # min(SL, prev_high * 1.005)

    # === Warmup ===
    warmup_days: int = 252  # Minimum warmup before backtest start

    # === Evaluation Clock ===
    evaluation_minutes_after_close: int = 60  # Signal computed 60 min after close

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VCRShortConfig":
        """Create config from dictionary."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


# Default configuration instance
DefaultConfig = VCRShortConfig()


@dataclass
class SensitivityConfig:
    """Configuration for parameter sensitivity analysis.

    Defines parameter ranges to scan while keeping other parameters fixed.
    """

    # Acceleration 20d threshold: 20% / 30% / 40%
    acceleration_20d_values: tuple = (0.20, 0.30, 0.40)

    # Bottom volume multiplier: 1.2 / 1.5 / 2.0
    bottom_volume_multiplier_values: tuple = (1.2, 1.5, 2.0)

    # Initial ATR stop multiplier: 1.0 / 1.5 / 2.0
    initial_sl_atr_multiplier_values: tuple = (1.0, 1.5, 2.0)

    # Slippage bps: 5 / 10 / 20
    slippage_bps_values: tuple = (5.0, 10.0, 20.0)

    # Default borrow rate: 5% / 10% / 20%
    default_borrow_rate_values: tuple = (0.05, 0.10, 0.20)

    def generate_variants(self, base_config: VCRShortConfig) -> list:
        """Generate all sensitivity test configurations.

        Returns a list of (param_name, param_value, config) tuples.
        """
        variants = []

        # Acceleration 20d threshold
        for val in self.acceleration_20d_values:
            cfg = VCRShortConfig(**base_config.to_dict())
            cfg.acceleration_20d_threshold = val
            variants.append(("acceleration_20d_threshold", val, cfg))

        # Bottom volume multiplier
        for val in self.bottom_volume_multiplier_values:
            cfg = VCRShortConfig(**base_config.to_dict())
            cfg.bottom_volume_multiplier = val
            variants.append(("bottom_volume_multiplier", val, cfg))

        # Initial ATR stop multiplier
        for val in self.initial_sl_atr_multiplier_values:
            cfg = VCRShortConfig(**base_config.to_dict())
            cfg.initial_sl_atr_multiplier = val
            variants.append(("initial_sl_atr_multiplier", val, cfg))

        # Slippage bps
        for val in self.slippage_bps_values:
            cfg = VCRShortConfig(**base_config.to_dict())
            cfg.slippage_bps = val
            variants.append(("slippage_bps", val, cfg))

        # Default borrow rate
        for val in self.default_borrow_rate_values:
            cfg = VCRShortConfig(**base_config.to_dict())
            cfg.default_borrow_rate = val
            variants.append(("default_borrow_rate", val, cfg))

        return variants
