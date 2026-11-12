"""Portfolio ledger and position tracking."""

from __future__ import annotations

from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from decimal import Decimal
from .config import ReboundConfig


@dataclass
class Position:
    """Represents an open position."""
    
    security_id: str
    entry_date: str
    entry_index: int
    shares: int
    entry_price: float
    stop_price: float
    atr_at_entry: float
    entry_commission: float
    industry: str
    holding_sessions: int = 0
    
    def market_value(self, current_price: float) -> float:
        return self.shares * current_price
    
    def unrealized_pnl(self, current_price: float) -> float:
        return self.market_value(current_price) - (self.shares * self.entry_price)


@dataclass
class Trade:
    """Represents a completed round-trip trade."""
    
    security_id: str
    entry_date: str
    exit_date: str
    entry_index: int
    exit_index: int
    shares: int
    entry_price: float
    exit_price: float
    entry_commission: float
    exit_commission: float
    exit_reason: str
    pnl: float
    return_pct: float
    holding_sessions: int
    industry: str


@dataclass
class DailySnapshot:
    """Daily portfolio state snapshot."""
    
    date: str
    cash: float
    positions_value: float
    equity: float
    num_positions: int
    drawdown: float
    daily_return: float


class Portfolio:
    """Portfolio ledger tracking cash, positions, and trades."""
    
    def __init__(self, config: ReboundConfig):
        self.config = config
        self.initial_equity = config.initial_equity
        self.cash = config.initial_equity
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[Trade] = []
        self.daily_snapshots: List[DailySnapshot] = []
        self.pending_sell_orders: List[Dict[str, Any]] = []
        self.peak_equity = config.initial_equity
        
    def equity(self, prices: Dict[str, float]) -> float:
        """Calculate total equity (cash + positions market value)."""
        positions_value = sum(
            pos.market_value(prices.get(pos.security_id, pos.entry_price))
            for pos in self.positions.values()
        )
        return self.cash + positions_value
    
    def available_cash(self) -> float:
        """Return available cash for new positions."""
        return self.cash
    
    def num_positions(self) -> int:
        """Return number of open positions."""
        return len(self.positions)
    
    def industry_count(self, industry: str) -> int:
        """Return number of positions in given industry."""
        return sum(1 for pos in self.positions.values() if pos.industry == industry)
    
    def has_position(self, security_id: str) -> bool:
        """Check if security has open position."""
        return security_id in self.positions
    
    def execute_buy(
        self,
        security_id: str,
        date: str,
        index: int,
        open_price: float,
        atr: float,
        industry: str,
        equity: float,
    ) -> bool:
        """Execute buy order with position sizing.
        
        Returns True if successful, False if rejected.
        """
        # Check position limits
        if self.has_position(security_id):
            return False
        
        if self.num_positions() >= self.config.max_positions:
            return False
        
        if self.industry_count(industry) >= self.config.max_per_industry:
            return False
        
        # Calculate position size
        fill_price = open_price * (1 + self.config.slippage_rate)
        stop_price = open_price - self.config.atr_stop_multiple * atr
        price_risk_per_share = open_price - stop_price
        
        if price_risk_per_share <= 0:
            return False
        
        # Risk-based sizing
        risk_shares = int(equity * self.config.risk_fraction / price_risk_per_share)
        
        # Cap-based sizing
        cap_shares = int(equity * self.config.position_cap / fill_price)
        
        # Cash-based sizing
        available = self.available_cash()
        cash_shares = int(available / (fill_price * (1 + self.config.commission_rate)))
        
        # Take minimum
        shares = min(risk_shares, cap_shares, cash_shares)
        
        if shares <= 0:
            return False
        
        # Calculate commission
        commission = shares * fill_price * self.config.commission_rate
        total_cost = shares * fill_price + commission
        
        if total_cost > available:
            return False
        
        # Execute
        self.cash -= total_cost
        self.positions[security_id] = Position(
            security_id=security_id,
            entry_date=date,
            entry_index=index,
            shares=shares,
            entry_price=fill_price,
            stop_price=stop_price,
            atr_at_entry=atr,
            entry_commission=commission,
            industry=industry,
            holding_sessions=0,
        )
        
        return True
    
    def execute_sell(
        self,
        security_id: str,
        date: str,
        index: int,
        close_price: float,
        reason: str,
    ) -> Optional[Trade]:
        """Execute sell order and close position.
        
        Returns Trade object if successful, None if no position.
        """
        if security_id not in self.positions:
            return None
        
        pos = self.positions[security_id]
        fill_price = close_price * (1 - self.config.slippage_rate)
        commission = pos.shares * fill_price * self.config.commission_rate
        
        proceeds = pos.shares * fill_price - commission
        self.cash += proceeds
        
        pnl = proceeds - (pos.shares * pos.entry_price + pos.entry_commission)
        return_pct = (fill_price / pos.entry_price - 1) * 100
        
        trade = Trade(
            security_id=security_id,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_index=pos.entry_index,
            exit_index=index,
            shares=pos.shares,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            entry_commission=pos.entry_commission,
            exit_commission=commission,
            exit_reason=reason,
            pnl=pnl,
            return_pct=return_pct,
            holding_sessions=pos.holding_sessions,
            industry=pos.industry,
        )
        
        self.closed_trades.append(trade)
        del self.positions[security_id]
        
        return trade
    
    def update_holding_periods(self):
        """Increment holding session count for all positions."""
        for pos in self.positions.values():
            pos.holding_sessions += 1
    
    def record_snapshot(self, date: str, prices: Dict[str, float]):
        """Record daily portfolio snapshot."""
        equity = self.equity(prices)
        positions_value = sum(
            pos.market_value(prices.get(pos.security_id, pos.entry_price))
            for pos in self.positions.values()
        )
        
        # Update peak
        if equity > self.peak_equity:
            self.peak_equity = equity
        
        # Calculate drawdown
        drawdown = (equity / self.peak_equity - 1) * 100 if self.peak_equity > 0 else 0.0
        
        # Calculate daily return
        if self.daily_snapshots:
            prev_equity = self.daily_snapshots[-1].equity
            daily_return = (equity / prev_equity - 1) * 100 if prev_equity > 0 else 0.0
        else:
            daily_return = 0.0
        
        self.daily_snapshots.append(DailySnapshot(
            date=date,
            cash=self.cash,
            positions_value=positions_value,
            equity=equity,
            num_positions=len(self.positions),
            drawdown=drawdown,
            daily_return=daily_return,
        ))
