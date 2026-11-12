# -*- coding: utf-8 -*-
"""Order matching and execution simulation for VCR Short strategy.

Handles entry/exit execution with slippage, gap filters, and
stop-loss/take-profit logic as specified in the requirements.
"""

from typing import Optional, Tuple, Dict, Any
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig
from src.services.signal_scanner.research.vcr_short.portfolio import Position, ExitReason


class OrderMatcher:
    """Simulates order execution with realistic constraints."""

    def __init__(self, config: VCRShortConfig):
        self.config = config

    def calculate_entry_price(
        self,
        open_price: float,
        is_short: bool = True
    ) -> float:
        """Calculate execution price with slippage.

        For short sales: entry_price = open_price * (1 - slippage)
        For cover (buy back): price = open_price * (1 + slippage)

        Args:
            open_price: Opening price
            is_short: True for short entry, False for cover

        Returns:
            Execution price after slippage
        """
        slippage_rate = self.config.slippage_bps / 10000.0

        if is_short:
            # Short sale: sell at slightly lower price
            return open_price * (1 - slippage_rate)
        else:
            # Cover: buy at slightly higher price
            return open_price * (1 + slippage_rate)

    def check_entry_conditions(
        self,
        signal_close: float,
        signal_high: float,
        next_open: float
    ) -> Tuple[bool, Optional[str]]:
        """Check if entry conditions are met.

        Skip entry if:
        - O_next > H[T] * 1.02 (gap up too much)
        - O_next < C[T] * 0.95 (gap down too much)

        Args:
            signal_close: Close price on signal day T
            signal_high: High price on signal day T
            next_open: Opening price on next trading day

        Returns:
            (can_enter, reason_if_not)
        """
        cfg = self.config

        # Check gap up: O_next > H[T] * 1.02
        if next_open > signal_high * (1 + cfg.entry_gap_up_threshold):
            return False, "GAP_UP_TOO_LARGE"

        # Check gap down: O_next < C[T] * 0.95
        if next_open < signal_close * (1 - cfg.entry_gap_down_threshold):
            return False, "GAP_DOWN_TOO_LARGE"

        return True, None

    def calculate_initial_stop_loss(
        self,
        entry_price: float,
        signal_high: float,
        atr: float
    ) -> float:
        """Calculate initial stop loss level.

        SL = max(entry_price + 1.5*ATR, signal_high * 1.005, entry_price + 0.8*ATR)

        Args:
            entry_price: Short entry price
            signal_high: High price on signal day
            atr: ATR value on signal day

        Returns:
            Initial stop loss price
        """
        cfg = self.config

        sl1 = entry_price + cfg.initial_sl_atr_multiplier * atr
        sl2 = signal_high * cfg.initial_sl_high_multiplier
        sl3 = entry_price + cfg.initial_sl_atr_base * atr

        return max(sl1, sl2, sl3)

    def calculate_take_profits(
        self,
        entry_price: float,
        signal_close: float,
        signal_high: float,
        signal_low: float,
        atr: float,
        bottom_close: Optional[float] = None
    ) -> Tuple[float, float]:
        """Calculate take profit levels.

        TP1 calculation:
        - TP1_candidate = signal_close if entry_price >= signal_close * 1.005
        - Otherwise TP1_candidate = entry_price - ATR
        - TP1 = min(TP1_candidate, entry_price - 0.5*ATR)

        TP2 calculation:
        - TP2_raw = max(bottom_close, signal_high - 0.5*(signal_high - bottom_low))
        - TP2 = min(TP1 - 0.5*ATR, TP2_raw)
        - If TP2 >= TP1, TP2 = TP1 - ATR

        Args:
            entry_price: Short entry price
            signal_close: Close on signal day
            signal_high: High on signal day
            signal_low: Low on signal day
            atr: ATR on signal day
            bottom_close: Close price at bottom launch point L

        Returns:
            (TP1, TP2) prices
        """
        cfg = self.config

        # TP1
        if entry_price >= signal_close * 1.005:
            tp1_candidate = signal_close
        else:
            tp1_candidate = entry_price - atr

        tp1 = min(tp1_candidate, entry_price - cfg.tp1_atr_multiplier * atr)

        # TP2
        if bottom_close is not None:
            tp2_raw = max(
                bottom_close,
                signal_high - 0.5 * (signal_high - signal_low)
            )
        else:
            tp2_raw = signal_high - 0.5 * (signal_high - signal_low)

        tp2 = min(tp1 - cfg.tp2_atr_offset * atr, tp2_raw)

        # Ensure TP2 < TP1
        if tp2 >= tp1:
            tp2 = tp1 - atr

        return tp1, tp2

    def check_initial_risk(
        self,
        entry_price: float,
        stop_loss: float
    ) -> Tuple[bool, float]:
        """Check if initial risk per share is acceptable.

        Skip if R/entry_price > 12%

        Args:
            entry_price: Short entry price
            stop_loss: Initial stop loss price

        Returns:
            (acceptable, risk_per_share)
        """
        risk_per_share = stop_loss - entry_price
        risk_pct = risk_per_share / entry_price

        return risk_pct <= self.config.max_initial_risk_pct, risk_per_share

    def calculate_position_size(
        self,
        equity: float,
        risk_per_share: float,
        entry_price: float,
        available_shares: Optional[int] = None
    ) -> int:
        """Calculate position size based on risk budget.

        shares = floor(equity * 1% / risk_per_share)
        Then constrain by:
        - Available shares to borrow
        - Remaining exposure capacity

        Args:
            equity: Current portfolio equity
            risk_per_share: Initial risk per share (SL - entry)
            entry_price: Entry price
            available_shares: Maximum shares available to borrow

        Returns:
            Number of shares to short
        """
        if risk_per_share <= 0:
            return 0

        # Risk-based sizing
        risk_budget = equity * self.config.max_risk_per_trade_pct
        shares = int(risk_budget / risk_per_share)

        # Constrain by available shares
        if available_shares is not None:
            shares = min(shares, available_shares)

        return max(0, shares)

    def check_exit_conditions(
        self,
        position: Position,
        current_open: float,
        current_high: float,
        current_low: float,
        current_close: float
    ) -> Dict[str, Any]:
        """Check all exit conditions for a position.

        Priority:
        1. Stop loss (including breakeven and trailing)
        2. Take profit
        3. Time exit (day 20)

        Args:
            position: Current position
            current_open: Today's open
            current_high: Today's high
            current_low: Today's low
            current_close: Today's close

        Returns:
            Dictionary with exit information if triggered
        """
        result = {
            "should_exit": False,
            "exit_price": None,
            "exit_reason": None,
            "exit_shares": 0,
        }

        sl = position.current_stop_loss
        tp1 = position.take_profit_1
        tp2 = position.take_profit_2

        # === Stop Loss Check ===
        # If open >= SL, exit at open
        if current_open >= sl:
            result["should_exit"] = True
            result["exit_price"] = self.calculate_entry_price(current_open, is_short=False)
            result["exit_reason"] = ExitReason.STOP_LOSS
            result["exit_shares"] = position.shares_remaining
            return result

        # If high >= SL, exit at SL
        if current_high >= sl:
            result["should_exit"] = True
            result["exit_price"] = self.calculate_entry_price(sl, is_short=False)
            result["exit_reason"] = ExitReason.STOP_LOSS
            result["exit_shares"] = position.shares_remaining
            return result

        # === Take Profit Check ===
        # TP1 (only if not already hit)
        if not position.tp1_hit and position.shares_remaining > 1:
            # If open <= TP1, exit at open
            if current_open <= tp1:
                result["should_exit"] = True
                result["exit_price"] = self.calculate_entry_price(current_open, is_short=False)
                result["exit_reason"] = ExitReason.TAKE_PROFIT_1
                result["exit_shares"] = int(position.shares * self.config.tp1_close_pct)
                return result

            # If low <= TP1, exit at TP1
            if current_low <= tp1:
                result["should_exit"] = True
                result["exit_price"] = self.calculate_entry_price(tp1, is_short=False)
                result["exit_reason"] = ExitReason.TAKE_PROFIT_1
                result["exit_shares"] = int(position.shares * self.config.tp1_close_pct)
                return result

        # TP2
        if position.tp1_hit or position.shares_remaining == 1:
            # If open <= TP2, exit at open
            if current_open <= tp2:
                result["should_exit"] = True
                result["exit_price"] = self.calculate_entry_price(current_open, is_short=False)
                result["exit_reason"] = ExitReason.TAKE_PROFIT_2
                result["exit_shares"] = position.shares_remaining
                return result

            # If low <= TP2, exit at TP2
            if current_low <= tp2:
                result["should_exit"] = True
                result["exit_price"] = self.calculate_entry_price(tp2, is_short=False)
                result["exit_reason"] = ExitReason.TAKE_PROFIT_2
                result["exit_shares"] = position.shares_remaining
                return result

        # === Time Exit (Day 20) ===
        if position.entry_day_index >= self.config.time_exit_days:
            result["should_exit"] = True
            result["exit_price"] = self.calculate_entry_price(current_close, is_short=False)
            result["exit_reason"] = ExitReason.TIME_EXIT
            result["exit_shares"] = position.shares_remaining
            return result

        return result

    def update_trailing_stop(
        self,
        position: Position,
        current_low: float,
        prev_high: float
    ):
        """Update trailing stop loss.

        Rules:
        1. When price hits S-R (entry - risk), move SL to breakeven (S)
        2. From next day, update SL = min(current_SL, prev_high * 1.005)

        Args:
            position: Position to update
            current_low: Today's low price
            prev_high: Previous day's high price
        """
        # Check if we should activate breakeven stop
        if not position.breakeven_activated:
            # If low touches S-R, activate breakeven
            if current_low <= position.entry_price - position.initial_risk_per_share:
                position.breakeven_activated = True
                position.current_stop_loss = position.entry_price

        # Update trailing stop if breakeven is active
        if position.breakeven_activated and prev_high > 0:
            trailing_level = prev_high * (1 + self.config.trailing_stop_buffer)
            position.current_stop_loss = min(
                position.current_stop_loss,
                trailing_level
            )
