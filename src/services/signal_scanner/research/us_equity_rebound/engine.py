"""Core backtest engine - main replay loop.

Fixes from the T37 audit (E1, E2, E4, E5, E7, E9):

- Unknown industries no longer collide with the per-industry cap (E1).
- Buy orders are only valid for the open of ``signal_date + 1`` and are
  discarded otherwise instead of silently filling on stale signals (E2).
- The universe may contain securities with partial histories; bar lookups are
  per security instead of assuming every series covers the full calendar (E4).
- The 200-session warmup reads history before ``config.start_date`` and the
  replay window only starts at the configured start date (E5).
- Trade records expose ``net_pnl`` and ``realized_pnl`` under both names (E7).
- Signals only use bars up to the signal date, and the universe is a
  point-in-time liquidity screen rather than a period-end survivor list (E9).
"""

from __future__ import annotations

import math
from typing import List, Dict, Any, Optional

import numpy as np

from .config import ReboundConfig
from .portfolio import Portfolio
from .exit_logic import determine_exit_reason
from .market_regime import check_market_on
from .liquidity import filter_by_liquidity, rank_candidates
from .corporate_actions import process_corporate_actions
from .performance import generate_performance_report
from .indicators import check_kline_stop
from .precompute import SecuritySeries, build_universe_series


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class BacktestEngine:
    """Main backtest engine implementing the replay loop."""

    def __init__(self, config: ReboundConfig):
        self.config = config
        self.portfolio = Portfolio(config)
        self.series: Dict[str, SecuritySeries] = {}
        self.industry_map: Dict[str, str] = {}
        self.universe_name: Optional[str] = None

        # Pending orders. ``execution_idx`` is the only session an order may fill.
        self.pending_buy_orders: List[Dict[str, Any]] = []
        self.pending_sell_orders: List[Dict[str, Any]] = []

        self.trades: List[Dict[str, Any]] = []
        self.daily_records: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ run
    def run(
        self,
        trading_dates: List[str],
        market_data: Dict[str, Dict[str, List[float]]],
        spy_data: Dict[str, List[float]],
        universe: List[str],
        corporate_actions: List[Dict[str, Any]] = None,
        industry_map: Optional[Dict[str, str]] = None,
        universe_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the backtest over the configured date range.

        Args:
            trading_dates: Full trading calendar, ascending (YYYY-MM-DD).
            market_data: security_id -> OHLCV lists aligned with that
                security's own dates. Pre-start history is used for warmup.
            spy_data: SPY OHLCV dictionary aligned with ``trading_dates``.
            universe: Candidate security ids (point-in-time liquidity screen).
            corporate_actions: Split/dividend events effective on a session.
            industry_map: security_id -> industry label, when available.
            universe_name: Human-readable universe definition for reports.
        """
        if corporate_actions is None:
            corporate_actions = []
        self.industry_map = {
            str(key).upper(): str(value)
            for key, value in (industry_map or {}).items()
            if value
        }
        self.universe_name = universe_name

        if not trading_dates:
            raise ValueError("trading_dates is empty")

        calendar = list(trading_dates)
        self.series = build_universe_series(universe, market_data)

        start_idx = self._index_of_first_on_or_after(calendar, self.config.start_date)
        end_idx = self._index_of_last_on_or_before(calendar, self.config.end_date)
        if start_idx is None or end_idx is None or start_idx > end_idx:
            raise ValueError(
                f"No trading sessions between {self.config.start_date} and "
                f"{self.config.end_date}"
            )

        # Bars before ``start_idx`` only feed indicators (warmup); they produce
        # no orders, no snapshots and no performance records.
        for date_idx in range(start_idx, end_idx + 1):
            current_date = calendar[date_idx]
            self._process_day(
                date_idx=date_idx,
                current_date=current_date,
                calendar=calendar,
                spy_data=spy_data,
                corporate_actions=corporate_actions,
                last_session=date_idx == end_idx,
            )

        results = self._generate_results()
        results["data_info"] = {
            "universe_name": self.universe_name,
            "universe_note": (
                "包含回测区间内上市与退市的代码，不使用期末存活名单；"
                "每日按信号日之前 20 个交易日的成交额排名和成交量下限筛选"
            ),
            "universe_size": len(universe),
            "securities_loaded": len(self.series),
            "start_date": calendar[start_idx],
            "end_date": calendar[end_idx],
            "warmup_sessions_before_start": start_idx,
            "trading_days": end_idx - start_idx + 1,
            "adjustment": "split-adjusted prices and share volume (data10 splits.csv)",
            "execution": "signal at close t, fill at regular open of t+1",
            "max_positions": self.config.max_positions,
            "max_open_positions_observed": max(
                (record["num_positions"] for record in self.daily_records), default=0
            ),
        }
        return results

    @staticmethod
    def _index_of_first_on_or_after(calendar: List[str], day: str) -> Optional[int]:
        for index, session in enumerate(calendar):
            if session >= day:
                return index
        return None

    @staticmethod
    def _index_of_last_on_or_before(calendar: List[str], day: str) -> Optional[int]:
        for index in range(len(calendar) - 1, -1, -1):
            if calendar[index] <= day:
                return index
        return None

    # --------------------------------------------------------------- replay
    def _process_day(
        self,
        date_idx: int,
        current_date: str,
        calendar: List[str],
        spy_data: Dict[str, List[float]],
        corporate_actions: List[Dict[str, Any]],
        last_session: bool,
    ) -> None:
        """Process a single trading session."""
        # Phase 1: pre-market corporate actions.
        self.portfolio.cash, self.portfolio.positions = process_corporate_actions(
            self.portfolio.cash,
            self.portfolio.positions,
            corporate_actions,
            current_date,
        )

        # Phase 2/3: open - sells first (they free cash and slots), then buys.
        self._execute_pending_sells(date_idx, current_date)
        self._execute_pending_buys(date_idx, current_date)

        # Phase 5: close - exits and then new signals for the next session.
        self._check_exits(date_idx, current_date)
        if not last_session:
            self._generate_signals(date_idx, current_date, calendar, spy_data)

        # Phase 7: daily snapshot.
        self._record_daily_state(date_idx, current_date)

    def _position_price(self, security_id: str) -> Optional[float]:
        position = self.portfolio.positions.get(security_id)
        if position is None:
            return None
        return position.last_price or position.entry_price

    def _valuation_prices(self) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for security_id in self.portfolio.positions:
            price = self._position_price(security_id)
            if price is not None:
                prices[security_id] = price
        return prices

    # --------------------------------------------------------------- exits
    def _check_exits(self, date_idx: int, current_date: str) -> None:
        """Check close-based exit conditions and queue next-open sells.

        Holding sessions advance on every trading session, including days
        without a usable bar, so the time stop follows the market calendar.
        Bar-dependent checks (hard stop, SMA20) are skipped on missing bars.
        """
        pending_sell_ids = {order["security_id"] for order in self.pending_sell_orders}

        for security_id, position in list(self.portfolio.positions.items()):
            position.holding_sessions += 1

            series = self.series.get(security_id)
            if series is None or security_id in pending_sell_ids:
                continue
            pos = series.pos(current_date)
            if pos is None:
                # No bar today: keep the position, value it at last price.
                continue

            close_price = float(series.closes[pos])
            position.last_price = close_price

            exit_reason = determine_exit_reason(
                close_price=close_price,
                stop_price=position.stop_price,
                holding_sessions=position.holding_sessions,
                closes=series.closes,
                index=pos,
                config=self.config,
            )
            if exit_reason:
                self.pending_sell_orders.append(
                    {
                        "security_id": security_id,
                        "signal_date": current_date,
                        "execution_idx": date_idx + 1,
                        "exit_reason": exit_reason,
                    }
                )

    def _execute_pending_sells(self, date_idx: int, current_date: str) -> None:
        """Execute sell orders at the regular open of their designated session."""
        orders = list(self.pending_sell_orders)
        self.pending_sell_orders = []

        for order in orders:
            if order["execution_idx"] != date_idx:
                # Expired: a sell order only ever fills on its next session.
                continue

            security_id = order["security_id"]
            series = self.series.get(security_id)
            if series is None:
                continue
            pos = series.pos(current_date)
            if pos is None:
                continue

            open_price = float(series.opens[pos])
            trade = self.portfolio.execute_sell(
                security_id=security_id,
                date=current_date,
                index=pos,
                close_price=open_price,
                reason=order["exit_reason"],
            )
            if trade:
                self.trades.append(
                    {
                        "security_id": security_id,
                        "entry_date": trade.entry_date,
                        "exit_date": trade.exit_date,
                        "shares": trade.shares,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "pnl": trade.pnl,
                        "net_pnl": trade.pnl,
                        "realized_pnl": trade.pnl,
                        "return_pct": trade.return_pct,
                        "exit_reason": trade.exit_reason,
                        "holding_days": trade.holding_sessions,
                        "industry": trade.industry,
                    }
                )

    # ---------------------------------------------------------------- buys
    def _industry_for(self, security_id: str) -> str:
        return self.industry_map.get(security_id.upper(), "UNKNOWN")

    def _execute_pending_buys(self, date_idx: int, current_date: str) -> None:
        """Execute buy orders at the regular open of their designated session.

        Every order is valid for exactly one session. Orders that cannot fill
        (missing bar, rejected by limits) are discarded rather than retried on
        a later day (E2).
        """
        orders = list(self.pending_buy_orders)
        self.pending_buy_orders = []

        for order in orders:
            if order["execution_idx"] != date_idx:
                continue

            security_id = order["security_id"]
            series = self.series.get(security_id)
            if series is None:
                continue
            pos = series.pos(current_date)
            if pos is None:
                continue

            open_price = float(series.opens[pos])
            atr = order["signal"]["atr"]
            if not _finite(open_price) or open_price <= 0 or not _finite(atr):
                continue

            current_equity = self.portfolio.equity(self._valuation_prices())
            success = self.portfolio.execute_buy(
                security_id=security_id,
                date=current_date,
                index=pos,
                open_price=open_price,
                atr=atr,
                industry=self._industry_for(security_id),
                equity=current_equity,
            )
            if success:
                position = self.portfolio.positions.get(security_id)
                if position is not None:
                    position.last_price = open_price

    # ------------------------------------------------------------- signals
    def _evaluate_signal(
        self,
        series: SecuritySeries,
        pos: int,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate the four entry conditions at ``pos`` using precomputed data."""
        if pos < 200:
            return None

        config = self.config
        close = float(series.closes[pos])
        sma200 = series.sma200[pos]
        rsi = series.rsi[pos]
        atr = series.atr[pos]
        if not _finite(sma200) or not _finite(rsi) or not _finite(atr) or atr <= 0:
            return None

        trend_ok = close > float(sma200)
        if not trend_ok:
            return None

        crash_ok = rsi < config.rsi_threshold
        if not crash_ok and pos >= 3:
            crash_ok = close / float(series.closes[pos - 3]) - 1 <= -0.08
        if not crash_ok and pos >= 1:
            crash_ok = close / float(series.closes[pos - 1]) - 1 <= -0.05
        if not crash_ok:
            return None

        volume = float(series.volumes[pos])
        mean_prev = series.vol_mean_prev20[pos]
        std_prev = series.vol_std_prev20[pos]
        if not _finite(mean_prev) or mean_prev <= 0:
            return None
        volume_ok = volume >= config.volume_multiple * float(mean_prev)
        if not volume_ok and _finite(std_prev) and float(std_prev) > 0:
            z_score = (volume - float(mean_prev)) / float(std_prev)
            volume_ok = z_score > 2.0
        if not volume_ok:
            return None

        if not check_kline_stop(
            float(series.opens[pos]),
            float(series.highs[pos]),
            float(series.lows[pos]),
            close,
            float(atr),
        ):
            return None

        return {
            "security_id": series.security_id,
            "signal_date": series.dates[pos],
            "signal_index": pos,
            "rsi": float(rsi),
            "atr": float(atr),
            "sma200": float(sma200),
            "close": close,
            "volume": volume,
        }

    def _generate_signals(
        self,
        date_idx: int,
        current_date: str,
        calendar: List[str],
        spy_data: Dict[str, List[float]],
    ) -> None:
        """Generate buy orders for the next session's open."""
        spy_closes = spy_data["close"]
        if date_idx >= len(spy_closes) or not check_market_on(spy_closes, date_idx):
            return

        candidates: List[Dict[str, Any]] = []
        open_slots = self.config.max_positions - self.portfolio.num_positions()
        if open_slots <= 0:
            return

        for security_id, series in self.series.items():
            if self.portfolio.has_position(security_id):
                continue
            pos = series.pos(current_date)
            if pos is None:
                continue

            avg_volume = series.avg_volume20[pos]
            avg_dollar = series.avg_dollar_volume20[pos]
            if not _finite(avg_volume) or not _finite(avg_dollar):
                continue

            signal = self._evaluate_signal(series, pos)
            if signal is None:
                continue

            candidates.append(
                {
                    "security_id": security_id,
                    "signal": signal,
                    "liquidity": {
                        "avg_volume": float(avg_volume),
                        "avg_dollar_volume": float(avg_dollar),
                        "is_qualified": True,
                    },
                }
            )

        filtered = filter_by_liquidity(candidates, self.config)
        if not filtered:
            return
        ranked = rank_candidates(filtered)

        for candidate in ranked:
            if len(self.pending_buy_orders) >= open_slots:
                break
            self.pending_buy_orders.append(
                {
                    "security_id": candidate["security_id"],
                    "signal_date": current_date,
                    "execution_idx": date_idx + 1,
                    "signal": candidate["signal"],
                    "rank": candidate["rank"],
                }
            )

    # ----------------------------------------------------------- snapshots
    def _record_daily_state(self, date_idx: int, current_date: str) -> None:
        """Record daily portfolio state at the close."""
        for security_id in self.portfolio.positions:
            series = self.series.get(security_id)
            if series is None:
                continue
            pos = series.pos(current_date)
            if pos is not None:
                self.portfolio.positions[security_id].last_price = float(series.closes[pos])

        current_prices = self._valuation_prices()
        self.portfolio.record_snapshot(current_date, current_prices)

        self.daily_records.append(
            {
                "date": current_date,
                "equity": self.portfolio.equity(current_prices),
                "cash": self.portfolio.cash,
                "positions_value": sum(
                    pos.market_value(current_prices.get(pos.security_id, pos.last_price or pos.entry_price))
                    for pos in self.portfolio.positions.values()
                ),
                "num_positions": self.portfolio.num_positions(),
                "pending_buys": len(self.pending_buy_orders),
                "pending_sells": len(self.pending_sell_orders),
            }
        )

    # ------------------------------------------------------------- results
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
                        "industry": pos.industry,
                    }
                    for sid, pos in self.portfolio.positions.items()
                },
            },
        }
