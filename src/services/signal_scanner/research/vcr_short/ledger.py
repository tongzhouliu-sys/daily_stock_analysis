# -*- coding: utf-8 -*-
"""Accounting ledger for VCR Short strategy.

Handles borrow fees, dividend payments, and P&L calculations
as specified in the requirements.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig


@dataclass
class BorrowFeeRecord:
    """Record of borrow fee accrual."""
    date: str
    instrument_id: str
    shares: int
    entry_price: float
    borrow_rate: float
    daily_fee: float
    cumulative_fee: float


@dataclass
class DividendRecord:
    """Record of dividend payment by short seller."""
    date: str
    instrument_id: str
    shares: int
    dividend_per_share: float
    total_payment: float


@dataclass
class Ledger:
    """Accounting ledger for short positions.

    Tracks:
    - Borrow fees (daily accrual)
    - Dividend payments
    - Realized and unrealized P&L
    """

    config: VCRShortConfig
    borrow_fee_records: List[BorrowFeeRecord] = field(default_factory=list)
    dividend_records: List[DividendRecord] = field(default_factory=list)

    # Cumulative tracking per position
    position_borrow_fees: Dict[str, float] = field(default_factory=dict)
    position_dividends: Dict[str, float] = field(default_factory=dict)

    def accrue_borrow_fees(
        self,
        date: str,
        positions: Dict[str, Any],
        borrow_rates: Dict[str, float]
    ):
        """Accrue daily borrow fees for all open positions.

        Borrow fee = shares * entry_price * annual_rate / 365

        Args:
            date: Current date
            positions: Dictionary of instrument_id -> Position
            borrow_rates: Dictionary of instrument_id -> annual borrow rate
        """
        for instrument_id, pos in positions.items():
            if pos.shares_remaining <= 0:
                continue

            # Get borrow rate (use default if not available)
            rate = borrow_rates.get(instrument_id, self.config.default_borrow_rate)

            # Calculate daily fee
            daily_fee = (
                pos.shares_remaining *
                pos.entry_price *
                rate / 365.0
            )

            # Update cumulative
            cumulative = self.position_borrow_fees.get(instrument_id, 0.0) + daily_fee
            self.position_borrow_fees[instrument_id] = cumulative

            pos.borrow_fee_accrued = cumulative

            # Record
            self.borrow_fee_records.append(BorrowFeeRecord(
                date=date,
                instrument_id=instrument_id,
                shares=pos.shares_remaining,
                entry_price=pos.entry_price,
                borrow_rate=rate,
                daily_fee=daily_fee,
                cumulative_fee=cumulative
            ))

    def record_dividend(
        self,
        ex_dividend_date: str,
        instrument_id: str,
        shares: int,
        dividend_per_share: float
    ):
        """Record dividend payment by short seller.

        Short seller must pay dividend to lender on ex-dividend date.

        Args:
            ex_dividend_date: Ex-dividend date
            instrument_id: Security identifier
            shares: Number of shares short
            dividend_per_share: Dividend per share
        """
        if shares <= 0:
            return

        total_payment = shares * dividend_per_share

        # Update cumulative
        cumulative = self.position_dividends.get(instrument_id, 0.0) + total_payment
        self.position_dividends[instrument_id] = cumulative

        # Record
        self.dividend_records.append(DividendRecord(
            date=ex_dividend_date,
            instrument_id=instrument_id,
            shares=shares,
            dividend_per_share=dividend_per_share,
            total_payment=total_payment
        ))

        return total_payment

    def get_total_borrow_fees(self, instrument_id: str) -> float:
        """Get total borrow fees for a position."""
        return self.position_borrow_fees.get(instrument_id, 0.0)

    def get_total_dividends(self, instrument_id: str) -> float:
        """Get total dividend payments for a position."""
        return self.position_dividends.get(instrument_id, 0.0)

    def calculate_net_pnl(
        self,
        entry_price: float,
        exit_price: float,
        shares: int,
        borrow_fees: float,
        dividends: float,
        commission: float = 0.0
    ) -> float:
        """Calculate net P&L for a completed trade.

        Net P&L = (entry_price - exit_price) * shares - borrow_fees - dividends - commission

        Args:
            entry_price: Short entry price
            exit_price: Cover price
            shares: Number of shares
            borrow_fees: Total borrow fees
            dividends: Total dividend payments
            commission: Commission fees

        Returns:
            Net profit or loss
        """
        gross_pnl = (entry_price - exit_price) * shares
        net_pnl = gross_pnl - borrow_fees - dividends - commission
        return net_pnl

    def to_dict(self) -> Dict[str, Any]:
        """Convert ledger to dictionary for reporting."""
        return {
            "total_borrow_fees": sum(self.position_borrow_fees.values()),
            "total_dividends": sum(self.position_dividends.values()),
            "borrow_fee_records_count": len(self.borrow_fee_records),
            "dividend_records_count": len(self.dividend_records),
            "position_borrow_fees": dict(self.position_borrow_fees),
            "position_dividends": dict(self.position_dividends),
        }
