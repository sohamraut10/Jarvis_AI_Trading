"""
Abstract broker interface.  All concrete brokers (paper, live Dhan) must
implement every method defined here.  Upper layers depend only on this
contract — never on a concrete implementation.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Domain enumerations ────────────────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"       # stop-loss limit
    SL_M = "SL_M"   # stop-loss market


class OrderStatus(str, Enum):
    PENDING = "PENDING"       # submitted, awaiting acknowledgement
    OPEN = "OPEN"             # live in order book
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"   # MIS — auto-squared at session end
    CNC = "CNC"             # Cash & Carry (delivery)


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"   # NSE F&O
    BFO = "BFO"   # BSE F&O


# ── Core data models ───────────────────────────────────────────────────────────

@dataclass
class Order:
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int
    product: ProductType
    exchange: Exchange = Exchange.NSE
    price: Optional[float] = None          # required for LIMIT / SL
    trigger_price: Optional[float] = None  # required for SL / SL_M
    strategy_id: Optional[str] = None
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = field(default=OrderStatus.PENDING)
    filled_qty: int = field(default=0)
    avg_fill_price: float = field(default=0.0)
    reject_reason: Optional[str] = field(default=None)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )


@dataclass
class Fill:
    """Single execution event produced when an order (partially) fills."""
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    strategy_id: Optional[str] = None
    filled_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Position:
    """Aggregated position for one symbol."""
    symbol: str
    qty: int            # positive = long, negative = short, 0 = flat
    avg_price: float    # volume-weighted average entry price
    product: ProductType
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    def update_unrealized(self, ltp: float) -> None:
        self.unrealized_pnl = (ltp - self.avg_price) * self.qty

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl


# ── Abstract broker ────────────────────────────────────────────────────────────

class BaseBroker(ABC):
    """
    Defines the full surface area that JARVIS's execution engine and
    risk manager talk to.  Keeps upper layers broker-agnostic.
    """

    # ── Order lifecycle ────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, order: Order) -> str:
        """Submit an order.  Returns order_id on success, raises on rejection."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.  Returns True if cancelled, False if not found."""

    @abstractmethod
    async def get_order(self, order_id: str) -> Optional[Order]:
        """Fetch current state of a single order."""

    @abstractmethod
    async def get_all_orders(self) -> list[Order]:
        """Return all orders placed in this session (any status)."""

    # ── Positions & capital ────────────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> dict[str, Position]:
        """Return {symbol: Position} for all open positions."""

    @abstractmethod
    async def get_available_capital(self) -> float:
        """Cash available for new orders (capital − margin used)."""

    @abstractmethod
    async def get_portfolio_value(self) -> float:
        """Total equity: available cash + mark-to-market of open positions."""

    # ── P&L ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_daily_pnl(self) -> float:
        """Realized + unrealized P&L since session open."""

    @abstractmethod
    async def get_fills(self) -> list[Fill]:
        """All fills executed this session, oldest first."""

    # ── Price feed integration ─────────────────────────────────────────────────

    @abstractmethod
    async def update_ltp(self, symbol: str, ltp: float) -> None:
        """
        Called by the data feed on every tick.
        Implementations should:
          1. Update unrealized P&L for all positions in that symbol.
          2. Attempt to fill any resting limit / SL orders.
        """

    # ── Utility ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def cancel_all_orders(self) -> int:
        """Cancel every open order.  Returns count of cancellations."""

    @abstractmethod
    async def square_off_all(self) -> list[Order]:
        """
        Market-sell (or buy-back) every open position.
        Used by the kill-switch.  Returns the closing orders placed.
        """
