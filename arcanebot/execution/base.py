"""Shared execution interface.

The signal engine talks to *this* interface only. The backtest loop (Phase 4)
and the MetaApi demo adapter (Phase 7) both implement it, so identical signal
code drives simulated and real orders. Defined early to lock the contract; the
concrete backtest broker fills it in during Phase 4.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


@dataclass(frozen=True)
class OrderRequest:
    """A single trade instruction produced by the signal engine.

    Prices are raw strategy levels; the broker implementation is responsible
    for applying spread/slippage on fill (backtest) or letting the venue do it
    (live). ``reason`` carries the human-readable rule trail for logging.
    """
    side: Side
    order_type: OrderType
    entry_price: float
    stop_loss: float
    take_profit: float
    lots: float
    created_at: pd.Timestamp
    expires_at: pd.Timestamp | None
    reason: str


@dataclass(frozen=True)
class Position:
    side: Side
    entry_price: float      # actual fill, cost-adjusted
    stop_loss: float
    take_profit: float
    lots: float
    opened_at: pd.Timestamp
    reason: str


class ExecutionBackend(abc.ABC):
    """Abstract broker. Backtest and live adapters both implement this."""

    @abc.abstractmethod
    def submit(self, order: OrderRequest) -> None:
        """Register a (possibly pending limit) order."""

    @abc.abstractmethod
    def cancel_pending(self, reason: str) -> None:
        """Cancel any unfilled pending order (e.g. invalidation / killzone end)."""

    @property
    @abc.abstractmethod
    def open_position(self) -> Position | None:
        """The single currently-open position, or None."""

    @abc.abstractmethod
    def equity(self) -> float:
        """Current account equity in USD."""
