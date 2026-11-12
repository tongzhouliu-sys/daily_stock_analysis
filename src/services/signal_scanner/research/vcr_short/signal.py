# -*- coding: utf-8 -*-
"""Signal generation for VCR Short strategy.

Implements all signal conditions as specified in the requirements:
- Acceleration uptrend
- Volatility expansion
- High position
- Bottom launch volume
- Climax volume
- Optional rejection candle filter
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.indicators import (
    sma,
    atr_wilder,
    rolling_max,
    find_lowest_close_index,
)


@dataclass
class SignalCondition:
    """Result of a single signal condition check."""
    name: str
    passed: bool
    value: Optional[float] = None
    threshold: Optional[float] = None
    detail: Optional[str] = None


@dataclass
class SignalResult:
    """Complete signal evaluation result for a single bar."""
    signal_date: str
    instrument_id: str
    passed: bool
    conditions: List[SignalCondition]
    intermediate_values: Dict[str, Any]
    rejection_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "signal_date": self.signal_date,
            "instrument_id": self.instrument_id,
            "passed": self.passed,
            "conditions": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "value": c.value,
                    "threshold": c.threshold,
                    "detail": c.detail,
                }
                for c in self.conditions
            ],
            "intermediate_values": self.intermediate_values,
            "rejection_reasons": self.rejection_reasons,
        }


class SignalGenerator:
    """Generates VCR Short signals from OHLCV data."""

    def __init__(self, config: VCRShortConfig):
        self.config = config

    def evaluate(
        self,
        instrument_id: str,
        dates: List[str],
        open_prices: List[float],
        high_prices: List[float],
        low_prices: List[float],
        close_prices: List[float],
        volumes: List[float],
        signal_index: int
    ) -> SignalResult:
        """Evaluate signal conditions for a single bar.

        Args:
            instrument_id: Security identifier
            dates: List of trading dates
            open_prices: Open prices
            high_prices: High prices
            low_prices: Low prices
            close_prices: Close prices
            volumes: Volume values
            signal_index: Index of the signal day T

        Returns:
            SignalResult with all condition checks
        """
        T = signal_index
        cfg = self.config
        conditions = []
        intermediate = {}
        rejection_reasons = []

        # === Pre-compute indicators ===
        # SMA20 and SMA50
        sma20 = sma(close_prices, cfg.sma_short_period)
        sma50 = sma(close_prices, cfg.sma_long_period)

        # ATR14
        atr14 = atr_wilder(high_prices, low_prices, close_prices, cfg.atr_period)

        # Check if we have enough data
        if T < cfg.warmup_days:
            return SignalResult(
                signal_date=dates[T],
                instrument_id=instrument_id,
                passed=False,
                conditions=[],
                intermediate_values={"error": "insufficient_warmup"},
                rejection_reasons=["INSUFFICIENT_WARMUP"]
            )

        # === Condition 1: Acceleration Uptrend ===
        # C[T] > SMA20[T] > SMA50[T]
        c_t = close_prices[T]
        sma20_t = sma20[T]
        sma50_t = sma50[T]

        if sma20_t is None or sma50_t is None:
            return SignalResult(
                signal_date=dates[T],
                instrument_id=instrument_id,
                passed=False,
                conditions=[],
                intermediate_values={"error": "insufficient_sma_data"},
                rejection_reasons=["INSUFFICIENT_SMA_DATA"]
            )

        # Check C[T] > SMA20[T] > SMA50[T]
        cond1a = c_t > sma20_t > sma50_t
        conditions.append(SignalCondition(
            name="price_above_smas",
            passed=cond1a,
            value=c_t,
            threshold=f"{sma20_t:.2f}>{sma50_t:.2f}",
            detail=f"C={c_t:.2f}, SMA20={sma20_t:.2f}, SMA50={sma50_t:.2f}"
        ))
        if not cond1a:
            rejection_reasons.append("PRICE_NOT_ABOVE_SMAS")

        # C[T]/C[T-20] - 1 >= 30%
        if T >= 20:
            c_t_20 = close_prices[T - 20]
            if c_t_20 is not None and c_t_20 > 0:
                ret_20d = c_t / c_t_20 - 1
                cond1b = ret_20d >= cfg.acceleration_20d_threshold
                conditions.append(SignalCondition(
                    name="acceleration_20d",
                    passed=cond1b,
                    value=ret_20d,
                    threshold=cfg.acceleration_20d_threshold,
                    detail=f"20d return: {ret_20d:.2%}"
                ))
                intermediate["return_20d"] = ret_20d
                if not cond1b:
                    rejection_reasons.append("INSUFFICIENT_20D_ACCELERATION")
            else:
                rejection_reasons.append("MISSING_20D_AGO_CLOSE")
        else:
            rejection_reasons.append("INSUFFICIENT_20D_HISTORY")

        # C[T]/C[T-5] - 1 >= 10%
        if T >= 5:
            c_t_5 = close_prices[T - 5]
            if c_t_5 is not None and c_t_5 > 0:
                ret_5d = c_t / c_t_5 - 1
                cond1c = ret_5d >= cfg.acceleration_5d_threshold
                conditions.append(SignalCondition(
                    name="acceleration_5d",
                    passed=cond1c,
                    value=ret_5d,
                    threshold=cfg.acceleration_5d_threshold,
                    detail=f"5d return: {ret_5d:.2%}"
                ))
                intermediate["return_5d"] = ret_5d
                if not cond1c:
                    rejection_reasons.append("INSUFFICIENT_5D_ACCELERATION")
            else:
                rejection_reasons.append("MISSING_5D_AGO_CLOSE")
        else:
            rejection_reasons.append("INSUFFICIENT_5D_HISTORY")

        # C[T] = max(C[T-19...T])
        if T >= 19:
            window_20 = close_prices[T-19:T+1]
            if None not in window_20:
                max_20 = max(window_20)
                cond1d = c_t == max_20
                conditions.append(SignalCondition(
                    name="close_is_20d_high",
                    passed=cond1d,
                    value=c_t,
                    threshold=max_20,
                    detail=f"C[T]={c_t:.2f}, max(20d)={max_20:.2f}"
                ))
                intermediate["max_close_20d"] = max_20
                if not cond1d:
                    rejection_reasons.append("NOT_20D_HIGH")
            else:
                rejection_reasons.append("MISSING_20D_WINDOW")
        else:
            rejection_reasons.append("INSUFFICIENT_20D_DATA")

        # === Condition 2: Volatility Expansion ===
        # ATR14[T] / ATR14[T-20] >= 1.5
        atr_t = atr14[T]
        atr_t_20 = atr14[T - 20] if T >= 20 else None

        if atr_t is not None and atr_t > 0 and atr_t_20 is not None and atr_t_20 > 0:
            atr_ratio = atr_t / atr_t_20
            cond2 = atr_ratio >= cfg.atr_expansion_ratio
            conditions.append(SignalCondition(
                name="atr_expansion",
                passed=cond2,
                value=atr_ratio,
                threshold=cfg.atr_expansion_ratio,
                detail=f"ATR[T]={atr_t:.4f}, ATR[T-20]={atr_t_20:.4f}"
            ))
            intermediate["atr_t"] = atr_t
            intermediate["atr_t_20"] = atr_t_20
            intermediate["atr_ratio"] = atr_ratio
            if not cond2:
                rejection_reasons.append("INSUFFICIENT_ATR_EXPANSION")
        else:
            rejection_reasons.append("INVALID_ATR_VALUES")

        # === Condition 3: High Position ===
        # C[T] >= 0.95 * max(C[T-252...T])
        if T >= cfg.high_position_lookback:
            window_252 = close_prices[T - cfg.high_position_lookback:T + 1]
            if None not in window_252:
                max_252 = max(window_252)
                threshold_252 = cfg.high_position_threshold * max_252
                cond3 = c_t >= threshold_252
                conditions.append(SignalCondition(
                    name="high_position",
                    passed=cond3,
                    value=c_t,
                    threshold=threshold_252,
                    detail=f"C[T]={c_t:.2f}, 95% of 252d high={threshold_252:.2f}"
                ))
                intermediate["max_close_252d"] = max_252
                intermediate["high_position_threshold"] = threshold_252
                if not cond3:
                    rejection_reasons.append("NOT_HIGH_POSITION")
            else:
                rejection_reasons.append("MISSING_252D_WINDOW")
        else:
            rejection_reasons.append("INSUFFICIENT_252D_DATA")

        # === Condition 4: Bottom Launch Volume ===
        # Find L = index of lowest close in [T-120, T-5]
        if T >= cfg.bottom_lookback:
            L = find_lowest_close_index(
                close_prices,
                lookback_end=T - 5,
                lookback_period=cfg.bottom_lookback - 5  # T-120 to T-5
            )

            if L is not None:
                # Window W = [L-5, min(L+10, T-1)]
                w_start = max(0, L - cfg.bottom_window_offset_start)
                w_end = min(L + cfg.bottom_window_offset_end, T - 1)

                if w_start <= w_end:
                    window_v = volumes[w_start:w_end + 1]
                    if None not in window_v and len(window_v) > 0:
                        v_base_max = max(window_v)
                        v_t = volumes[T]

                        if v_t is not None and v_base_max > 0:
                            v_ratio = v_t / v_base_max
                            cond4 = v_ratio >= cfg.bottom_volume_multiplier
                            conditions.append(SignalCondition(
                                name="bottom_launch_volume",
                                passed=cond4,
                                value=v_ratio,
                                threshold=cfg.bottom_volume_multiplier,
                                detail=f"V[T]={v_t:.0f}, V_base_max={v_base_max:.0f}, L={dates[L]}"
                            ))
                            intermediate["bottom_launch_L"] = L
                            intermediate["bottom_launch_date"] = dates[L]
                            intermediate["v_base_max"] = v_base_max
                            intermediate["volume_ratio_bottom"] = v_ratio
                            if not cond4:
                                rejection_reasons.append("INSUFFICIENT_BOTTOM_LAUNCH_VOLUME")
                        else:
                            rejection_reasons.append("INVALID_VOLUME_VALUES")
                    else:
                        rejection_reasons.append("MISSING_VOLUME_WINDOW")
                else:
                    rejection_reasons.append("INVALID_BOTTOM_WINDOW")
            else:
                rejection_reasons.append("NO_VALID_BOTTOM_CLOSE")
        else:
            rejection_reasons.append("INSUFFICIENT_BOTTOM_LOOKBACK")

        # === Condition 5: Climax Volume ===
        # V[T] >= 2.5 * SMA20(V)[T] and V[T] = max(V[T-120...T])
        if T >= cfg.climax_volume_sma_period:
            # SMA20 of volume
            vol_sma20 = sma(volumes, cfg.climax_volume_sma_period)
            vol_sma20_t = vol_sma20[T]
            v_t = volumes[T]

            if vol_sma20_t is not None and vol_sma20_t > 0 and v_t is not None:
                vol_ratio = v_t / vol_sma20_t
                cond5a = vol_ratio >= cfg.climax_volume_multiplier

                conditions.append(SignalCondition(
                    name="climax_volume_vs_sma",
                    passed=cond5a,
                    value=vol_ratio,
                    threshold=cfg.climax_volume_multiplier,
                    detail=f"V[T]={v_t:.0f}, SMA20(V)={vol_sma20_t:.0f}"
                ))
                intermediate["volume_sma20_t"] = vol_sma20_t
                intermediate["volume_ratio_sma"] = vol_ratio

                if not cond5a:
                    rejection_reasons.append("INSUFFICIENT_CLIMAX_VOLUME_VS_SMA")
            else:
                rejection_reasons.append("INVALID_VOLUME_SMA")

            # V[T] = max(V[T-120...T])
            if T >= cfg.climax_volume_lookback:
                window_v_120 = volumes[T - cfg.climax_volume_lookback:T + 1]
                if None not in window_v_120:
                    max_v_120 = max(window_v_120)
                    cond5b = v_t == max_v_120
                    conditions.append(SignalCondition(
                        name="climax_volume_is_120d_high",
                        passed=cond5b,
                        value=v_t,
                        threshold=max_v_120,
                        detail=f"V[T]={v_t:.0f}, max(120d)={max_v_120:.0f}"
                    ))
                    intermediate["max_volume_120d"] = max_v_120
                    if not cond5b:
                        rejection_reasons.append("NOT_120D_VOLUME_HIGH")
                else:
                    rejection_reasons.append("MISSING_120D_VOLUME_WINDOW")
            else:
                rejection_reasons.append("INSUFFICIENT_120D_VOLUME_DATA")
        else:
            rejection_reasons.append("INSUFFICIENT_VOLUME_SMA_DATA")

        # === Condition 6: Optional Rejection Candle ===
        if cfg.require_rejection:
            h_t = high_prices[T]
            lo_t = low_prices[T]
            o_t = open_prices[T]

            if h_t is not None and lo_t is not None and h_t > lo_t:
                # (H[T] - C[T]) / (H[T] - L[T]) >= 0.33 or C[T] < O[T]
                upper_shadow_ratio = (h_t - c_t) / (h_t - lo_t)
                cond6 = upper_shadow_ratio >= cfg.rejection_threshold or c_t < o_t
                conditions.append(SignalCondition(
                    name="rejection_candle",
                    passed=cond6,
                    value=upper_shadow_ratio,
                    threshold=cfg.rejection_threshold,
                    detail=f"Upper shadow ratio: {upper_shadow_ratio:.2%}, C<O: {c_t < o_t}"
                ))
                intermediate["upper_shadow_ratio"] = upper_shadow_ratio
                if not cond6:
                    rejection_reasons.append("NO_REJECTION_CANDLE")
            elif h_t == lo_t:
                # Zero amplitude, only check C < O
                cond6 = c_t < o_t
                conditions.append(SignalCondition(
                    name="rejection_candle",
                    passed=cond6,
                    value=None,
                    threshold=None,
                    detail=f"Zero amplitude, C<O: {c_t < o_t}"
                ))
                if not cond6:
                    rejection_reasons.append("NO_REJECTION_CANDLE")
            else:
                rejection_reasons.append("INVALID_PRICE_DATA")

        # === Final Result ===
        passed = len(rejection_reasons) == 0

        return SignalResult(
            signal_date=dates[T],
            instrument_id=instrument_id,
            passed=passed,
            conditions=conditions,
            intermediate_values=intermediate,
            rejection_reasons=rejection_reasons
        )
