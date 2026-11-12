# -*- coding: utf-8 -*-
"""Portfolio and position management for VCR Short strategy.

Manages position sizing, entry/exit logic, stop-loss/take-profit,
and trailing stops as specified in the requirements.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig


class ExitReason(Enum):
    """Reason for position exit."""
    STOP_LOSS = "stop_loss"
    BREAKEVEN_STOP = "breakeven_stop"
    TRAILING_STOP = "trailing_stop"
    TAKE_PROFIT_1 = "take_profit_1"
    TAKE_PROFIT_2 = "take_profit_2"
    TIME_EXIT = "time_exit"
    DELISTED = "delisted"
    END_OF_BACKTEST = "end_of_backtest"


@dataclass
class Position:
    """Represents a single short position."""
    instrument_id: str
    signal_date: str
    entry_date: str
    entry_price: float  # After slippage
    shares: int
    initial_risk_per_share: float  # R = SL - entry_price
    borrow_rate: float

    # Stop loss and take profit levels
    initial_stop_loss: float
    current_stop_loss: float
    take_profit_1: float
    take_profit_2: float

    # Position state
    shares_remaining: int

    # Trailing stop state
    tp1_hit: bool = False
    entry_day_index: int = 0  # Day 1 = entry day
    breakeven_activated: bool = False
    highest_close_since_entry: float = 0.0

    # Costs
    borrow_fee_accrued: float = 0.0
    dividends_paid: float = 0.0
    commission_paid: float = 0.0

    # Exit tracking
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    exit_shares: int = 0

    # P&L
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "instrument_id": self.instrument_id,
            "signal_date": self.signal_date,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "shares": self.shares,
            "initial_risk_per_share": self.initial_risk_per_share,
            "initial_stop_loss": self.initial_stop_loss,
            "current_stop_loss": self.current_stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "shares_remaining": self.shares_remaining,
            "tp1_hit": self.tp1_hit,
            "entry_day_index": self.entry_day_index,
            "breakeven_activated": self.breakeven_activated,
            "borrow_rate": self.borrow_rate,
            "borrow_fee_accrued": self.borrow_fee_accrued,
            "dividends_paid": self.dividends_paid,
            "commission_paid": self.commission_paid,
            "exit_date": self.exit_date,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
        }


@dataclass
class Portfolio:
    """Portfolio state and position management."""
    config: VCRShortConfig
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    closed_positions: List[Position] = field(default_factory=list)

    # Daily tracking
    current_date: Optional[str] = None
    equity_history: List[Dict[str, Any]] = field(default_factory=list)

    def current_equity(self, current_prices: Dict[str, float]) -> float:
        """Calculate current portfolio equity.

        Equity = Cash - Sum(short_position_market_value)
        """
        equity = self.cash

        for pos in self.positions.values():
            if pos.instrument_id in current_prices:
                current_price = current_prices[pos.instrument_id]
                # Short P&L = (entry_price - current_price) * shares_remaining
                unrealized = (pos.entry_price - current_price) * pos.shares_remaining
                equity += pos.entry_price * pos.shares_remaining + unrealized

        return equity

    def current_exposure(self, current_prices: Dict[str, float]) -> float:
        """Calculate current notional exposure."""
        exposure = 0.0
        for pos in self.positions.values():
            if pos.instrument_id in current_prices:
                exposure += current_prices[pos.instrument_id] * pos.shares_remaining
        return exposure

    def available_slots(self) -> int:
        """Return number of available position slots."""
        return max(0, self.config.max_positions - len(self.positions))

    def can_open_position(
        self,
        instrument_id: str,
        entry_price: float,
        shares: int,
        current_prices: Dict[str, float]
    ) -> bool:
        """Check if a new position can be opened.

        Checks:
        - Not already short this instrument
        - Available position slots
        - Exposure limits
        - Risk per trade limits
        """
        # Already short this instrument
        if instrument_id in self.positions:
            return False

        # No available slots
        if self.available_slots() == 0:
            return False

        # Calculate new exposure
        new_exposure = entry_price * shares
        current_exposure = self.current_exposure(current_prices)
        equity = self.current_equity(current_prices)

        # Check exposure limit
        if current_exposure + new_exposure > equity * self.config.max_exposure_pct:
            return False

        # Check equity is positive
        if equity <= 0:
            return False

        return True

    def open_position(self, position: Position) -> bool:
        """Open a new short position."""
        if position.instrument_id in self.positions:
            return False

        self.positions[position.instrument_id] = position
        # 开仓时收到卖空所得现金（卖出借入的股票）
        self.cash += position.entry_price * position.shares
        # 扣除开仓佣金
        self.cash -= position.commission_paid
        return True

    def close_position(
        self,
        instrument_id: str,
        exit_date: str,
        exit_price: float,
        shares: int,
        reason: ExitReason
    ) -> Optional[Position]:
        """Close (partially or fully) a position.

        Args:
            instrument_id: Security to close
            exit_date: Exit date
            exit_price: Exit price (after slippage)
            shares: Number of shares to close
            reason: Exit reason

        Returns:
            Closed position if fully closed, None otherwise
        """
        if instrument_id not in self.positions:
            return None

        pos = self.positions[instrument_id]

        # Calculate P&L for closed shares
        pnl = (pos.entry_price - exit_price) * shares

        # Subtract costs
        pnl -= pos.dividends_paid * (shares / pos.shares)
        pnl -= pos.commission_paid

        pos.realized_pnl += pnl
        pos.shares_remaining -= shares

        # Update cash: 平仓时支付买回成本
        self.cash -= exit_price * shares

        if pos.shares_remaining == 0:
            # Fully closed
            pos.exit_date = exit_date
            pos.exit_price = exit_price
            pos.exit_reason = reason
            pos.exit_shares = pos.shares
            pos.unrealized_pnl = 0.0

            del self.positions[instrument_id]
            self.closed_positions.append(pos)
            return pos
        else:
            # Partial close
            pos.unrealized_pnl = (pos.entry_price - exit_price) * pos.shares_remaining
            return None

    def update_daily(
        self,
        current_date: str,
        current_prices: Dict[str, float],
        day_highs: Dict[str, float],
        day_lows: Dict[str, float]
    ):
        """Update portfolio state for end of day.

        Updates:
        - Position day counters
        - Trailing stops
        - Unrealized P&L
        """
        self.current_date = current_date

        for pos in list(self.positions.values()):
            pos.entry_day_index += 1

            # Update highest close for trailing stop
            if pos.instrument_id in current_prices:
                current_price = current_prices[pos.instrument_id]
                pos.highest_close_since_entry = max(
                    pos.highest_close_since_entry,
                    current_price
                )

            # Update unrealized P&L
            if pos.instrument_id in current_prices:
                current_price = current_prices[pos.instrument_id]
                pos.unrealized_pnl = (pos.entry_price - current_price) * pos.shares_remaining

        # Record equity snapshot
        equity = self.current_equity(current_prices)
        self.equity_history.append({
            "date": current_date,
            "equity": equity,
            "cash": self.cash,
            "num_positions": len(self.positions),
            "exposure": self.current_exposure(current_prices),
        })
