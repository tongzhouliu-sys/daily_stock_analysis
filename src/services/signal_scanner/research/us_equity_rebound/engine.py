"""Core backtest engine - main replay loop."""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

from .config import ReboundConfig
from .portfolio import Portfolio
from .signals import generate_entry_signal
from .exit_logic import determine_exit_reason
from .market_regime import check_market_on
from .liquidity import calculate_liquidity_metrics, filter_by_liquidity, rank_candidates
from .corporate_actions import process_corporate_actions, get_corporate_actions_for_date
from .performance import generate_performance_report
from .indicators import rsi2_wilder, atr14_wilder


class BacktestEngine:
    """Main backtest engine implementing the replay loop."""
    
    def __init__(self, config: ReboundConfig):
        self.config = config
        self.portfolio = Portfolio(config)
        
        # State tracking per security
        self.rsi_state: Dict[str, tuple] = {}  # security_id -> (avg_gain, avg_loss)
        self.atr_state: Dict[str, float] = {}  # security_id -> prev_atr
        
        # Pending orders
        self.pending_buy_orders: List[Dict[str, Any]] = []
        self.pending_sell_orders: List[Dict[str, Any]] = []
        
        # Trade log
        self.trades: List[Dict[str, Any]] = []
        
        # Daily records
        self.daily_records: List[Dict[str, Any]] = []
    
    def run(
        self,
        trading_dates: List[str],
        market_data: Dict[str, Dict[str, List[float]]],
        spy_data: Dict[str, List[float]],
        universe: List[str],
        corporate_actions: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run backtest over trading dates.
        
        Args:
            trading_dates: List of trading dates (YYYY-MM-DD)
            market_data: Dict of security_id -> {open, high, low, close, volume}
            spy_data: Dict with SPY {open, high, low, close, volume}
            universe: List of security IDs in universe
            corporate_actions: List of corporate action events
        
        Returns:
            Complete backtest results
        """
        if corporate_actions is None:
            corporate_actions = []
        
        # Initialize indicator states
        for security_id in universe:
            self.rsi_state[security_id] = (None, None)
            self.atr_state[security_id] = None
        
        # Main replay loop
        for date_idx, current_date in enumerate(trading_dates):
            self._process_day(
                date_idx=date_idx,
                current_date=current_date,
                trading_dates=trading_dates,
                market_data=market_data,
                spy_data=spy_data,
                universe=universe,
                corporate_actions=corporate_actions,
            )
        
        # Generate final report
        return self._generate_results()
    
    def _process_day(
        self,
        date_idx: int,
        current_date: str,
        trading_dates: List[str],
        market_data: Dict[str, Dict[str, List[float]]],
        spy_data: Dict[str, List[float]],
        universe: List[str],
        corporate_actions: List[Dict[str, Any]],
    ):
        """Process single trading day."""
        
        # Phase 1: Pre-market - Process corporate actions
        self.portfolio.cash, self.portfolio.positions = process_corporate_actions(
            self.portfolio.cash,
            self.portfolio.positions,
            corporate_actions,
            current_date,
        )
        
        # Phase 2: Market open - Execute pending sell orders
        self._execute_pending_sells(date_idx, current_date, market_data)
        
        # Phase 3: Market open - Execute pending buy orders
        self._execute_pending_buys(date_idx, current_date, market_data)
        
        # Phase 4: Update indicator states for all securities
        self._update_indicators(date_idx, market_data, universe)
        
        # Phase 5: Post-market - Check exit conditions
        self._check_exits(date_idx, current_date, market_data)
        
        # Phase 6: Post-market - Generate new signals
        self._generate_signals(date_idx, current_date, market_data, spy_data, universe)
        
        # Phase 7: Record daily snapshot
        self._record_daily_state(date_idx, current_date, market_data)
    
    def _update_indicators(
        self,
        date_idx: int,
        market_data: Dict[str, Dict[str, List[float]]],
        universe: List[str],
    ):
        """Update RSI and ATR states for all securities."""
        for security_id in universe:
            if security_id not in market_data:
                continue
            
            data = market_data[security_id]
            closes = data["close"][:date_idx + 1]
            highs = data["high"][:date_idx + 1]
            lows = data["low"][:date_idx + 1]
            
            if len(closes) < 3:
                continue
            
            # Update RSI
            rsi, avg_gain, avg_loss = rsi2_wilder(
                closes, date_idx,
                self.rsi_state[security_id][0],
                self.rsi_state[security_id][1],
            )
            self.rsi_state[security_id] = (avg_gain, avg_loss)
            
            # Update ATR
            atr, _ = atr14_wilder(
                highs, lows, closes, date_idx,
                self.atr_state[security_id],
            )
            self.atr_state[security_id] = atr
    
    def _check_exits(
        self,
        date_idx: int,
        current_date: str,
        market_data: Dict[str, Dict[str, List[float]]],
    ):
        """Check exit conditions for all positions."""
        positions_to_exit = []
        
        for security_id, position in list(self.portfolio.positions.items()):
            if security_id not in market_data:
                continue
            
            data = market_data[security_id]
            close_price = data["close"][date_idx]
            closes = data["close"][:date_idx + 1]
            
            # Increment holding period
            position.holding_sessions += 1
            
            # Check exit conditions
            exit_reason = determine_exit_reason(
                close_price=close_price,
                stop_price=position.stop_price,
                holding_sessions=position.holding_sessions,
                closes=closes,
                index=date_idx,
                config=self.config,
            )
            
            if exit_reason:
                positions_to_exit.append({
                    "security_id": security_id,
                    "exit_date": current_date,
                    "exit_date_idx": date_idx,
                    "exit_price": close_price,
                    "exit_reason": exit_reason,
                })
        
        # Queue exit orders for next day's open
        for exit_order in positions_to_exit:
            self.pending_sell_orders.append(exit_order)
    
    def _generate_signals(
        self,
        date_idx: int,
        current_date: str,
        market_data: Dict[str, Dict[str, List[float]]],
        spy_data: Dict[str, List[float]],
        universe: List[str],
    ):
        """Generate entry signals for next day."""
        # Check market regime
        spy_closes = spy_data["close"][:date_idx + 1]
        market_on = check_market_on(spy_closes, date_idx)
        
        if not market_on:
            # Market closed, no new signals
            return
        
        # Calculate liquidity for all securities
        candidates = []
        for security_id in universe:
            if security_id not in market_data:
                continue
            
            # Skip if already holding
            if self.portfolio.has_position(security_id):
                continue
            
            data = market_data[security_id]
            closes = data["close"][:date_idx + 1]
            volumes = data["volume"][:date_idx + 1]
            opens = data["open"][:date_idx + 1]
            highs = data["high"][:date_idx + 1]
            lows = data["low"][:date_idx + 1]
            
            if len(closes) < 200:
                # Need 200-day warmup
                continue
            
            # Calculate liquidity
            liquidity = calculate_liquidity_metrics(
                security_id, closes, volumes, date_idx
            )
            
            # Generate signal
            signal = generate_entry_signal(
                security_id=security_id,
                date=current_date,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
                index=date_idx,
                rsi_state=self.rsi_state[security_id],
                atr_state=self.atr_state[security_id],
                config=self.config,
            )
            
            if signal:
                candidates.append({
                    "security_id": security_id,
                    "signal": signal,
                    "liquidity": liquidity,
                })
        
        # Filter by liquidity
        filtered = filter_by_liquidity(candidates, self.config)
        
        # Rank candidates
        ranked = rank_candidates(filtered)
        
        # Queue buy orders for top candidates
        for candidate in ranked:
            if self.portfolio.num_positions() >= self.config.max_positions:
                break
            
            self.pending_buy_orders.append({
                "security_id": candidate["security_id"],
                "signal_date": current_date,
                "signal_date_idx": date_idx,
                "signal": candidate["signal"],
                "rank": candidate["rank"],
            })
    
    def _execute_pending_sells(
        self,
        date_idx: int,
        current_date: str,
        market_data: Dict[str, Dict[str, List[float]]],
    ):
        """Execute pending sell orders at market open."""
        executed_orders = []
        
        for order in self.pending_sell_orders:
            security_id = order["security_id"]
            
            if security_id not in market_data:
                continue
            
            data = market_data[security_id]
            open_price = data["open"][date_idx]
            
            # Execute sell (use open_price as the reference price)
            trade = self.portfolio.execute_sell(
                security_id=security_id,
                date=current_date,
                index=date_idx,
                close_price=open_price,
                reason=order["exit_reason"],
            )
            
            if trade:
                self.trades.append({
                    "security_id": security_id,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "shares": trade.shares,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "pnl": trade.pnl,
                    "return_pct": trade.return_pct,
                    "exit_reason": trade.exit_reason,
                    "holding_days": trade.holding_sessions,
                })
            
            executed_orders.append(order)
        
        # Remove executed orders
        for order in executed_orders:
            self.pending_sell_orders.remove(order)
    
    def _execute_pending_buys(
        self,
        date_idx: int,
        current_date: str,
        market_data: Dict[str, Dict[str, List[float]]],
    ):
        """Execute pending buy orders at market open."""
        executed_orders = []
        
        for order in self.pending_buy_orders:
            security_id = order["security_id"]
            
            if security_id not in market_data:
                continue
            
            data = market_data[security_id]
            open_price = data["open"][date_idx]
            
            # Get industry (simplified - would need actual data)
            industry = "UNKNOWN"
            
            # Calculate current equity
            current_equity = self.portfolio.equity(
                {sid: market_data[sid]["close"][date_idx] 
                 for sid in self.portfolio.positions.keys()
                 if sid in market_data}
            )
            
            # Execute buy
            success = self.portfolio.execute_buy(
                security_id=security_id,
                date=current_date,
                index=date_idx,
                open_price=open_price,
                atr=order["signal"]["atr"],
                industry=industry,
                equity=current_equity,
            )
            
            if success:
                executed_orders.append(order)
        
        # Remove executed orders
        for order in executed_orders:
            self.pending_buy_orders.remove(order)
    
    def _record_daily_state(
        self,
        date_idx: int,
        current_date: str,
        market_data: Dict[str, Dict[str, List[float]]],
    ):
        """Record daily portfolio state."""
        # Get current prices
        current_prices = {}
        for security_id in self.portfolio.positions.keys():
            if security_id in market_data:
                current_prices[security_id] = market_data[security_id]["close"][date_idx]
        
        # Record snapshot
        self.portfolio.record_snapshot(current_date, current_prices)
        
        # Record daily detail
        self.daily_records.append({
            "date": current_date,
            "equity": self.portfolio.equity(current_prices),
            "cash": self.portfolio.cash,
            "positions_value": sum(
                pos.market_value(current_prices.get(pos.security_id, pos.entry_price))
                for pos in self.portfolio.positions.values()
            ),
            "num_positions": self.portfolio.num_positions(),
            "pending_buys": len(self.pending_buy_orders),
            "pending_sells": len(self.pending_sell_orders),
        })
    
    def _generate_results(self) -> Dict[str, Any]:
        """Generate complete backtest results."""
        performance = generate_performance_report(self.portfolio, self.config)
        
        return {
            "config": self.config.to_dict(),
            "performance": performance,
            "trades": self.trades,
            "daily_records": self.daily_records,
            "final_portfolio": {
                "cash": self.portfolio.cash,
                "positions": {
                    sid: {
                        "shares": pos.shares,
                        "entry_price": pos.entry_price,
                        "entry_date": pos.entry_date,
                        "stop_price": pos.stop_price,
                        "holding_sessions": pos.holding_sessions,
                    }
                    for sid, pos in self.portfolio.positions.items()
                },
            },
        }
