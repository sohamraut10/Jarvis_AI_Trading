"""
Paper broker — full in-memory simulation of order execution.

Design:
  • Market orders fill immediately at LTP ± configurable slippage tick.
  • Limit orders rest in the book; filled when LTP crosses the limit price.
  • SL_M orders trigger when LTP touches trigger_price, then fill at market.
  • SL (limit) orders trigger when LTP touches trigger_price, then rest as LIMIT.
  • Positions use VWAP entry; avg_price updates on partial fills.
  • Daily P&L = Σ realized_pnl (all closed trades) + Σ unrealized_pnl (open).
  • Thread-safe via asyncio.Lock — safe for concurrent strategy coroutines.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from .base_broker import (
    BaseBroker,
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
)

logger = logging.getLogger(__name__)

# Slippage: 1 tick per side (0.05 for equity; overridable)
_DEFAULT_SLIPPAGE_PCT = 0.0005   # 0.05 %


class KillSwitchError(RuntimeError):
    """Raised when an order would violate the daily drawdown limit."""


class PaperBroker(BaseBroker):
    """
    Fully in-process paper broker.  All state lives in plain Python dicts;
    no external calls are made.  Feed prices arrive via update_ltp().
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        kill_switch_amount: float = 300.0,
        slippage_pct: float = _DEFAULT_SLIPPAGE_PCT,
    ) -> None:
        self._initial_capital = initial_capital
        self._kill_switch_amount = kill_switch_amount  # hard daily loss limit
        self._slippage_pct = slippage_pct
        self._killed = False                       # True after kill-switch fires

        self._orders: dict[str, Order] = {}        # order_id → Order
        self._positions: dict[str, Position] = {}  # symbol → Position
        self._fills: list[Fill] = []
        self._ltp: dict[str, float] = {}           # symbol → last traded price

        # Resting orders bucketed by symbol for O(1) fill checks
        self._resting: dict[str, list[Order]] = defaultdict(list)

        self._lock = asyncio.Lock()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _slipped_price(self, side: OrderSide, ltp: float) -> float:
        """Apply one-sided slippage: buy fills slightly higher, sell lower."""
        factor = 1 + self._slippage_pct if side == OrderSide.BUY else 1 - self._slippage_pct
        return round(ltp * factor, 2)

    def _margin_required(self, order: Order, fill_price: float) -> float:
        """Rough margin: full notional for paper (no leverage model yet)."""
        return fill_price * order.qty

    def _apply_fill(self, order: Order, fill_price: float, qty: int) -> Fill:
        """
        Update order, position, capital, and fills list for one execution.
        Caller must hold self._lock.
        """
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            price=fill_price,
            strategy_id=order.strategy_id,
        )
        self._fills.append(fill)

        # ── Update order state ─────────────────────────────────────────────────
        prev_filled = order.filled_qty
        order.filled_qty += qty
        order.avg_fill_price = (
            (order.avg_fill_price * prev_filled + fill_price * qty) / order.filled_qty
        )
        order.status = (
            OrderStatus.FILLED if order.filled_qty >= order.qty
            else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at = datetime.utcnow()

        # ── Update position ────────────────────────────────────────────────────
        pos = self._positions.get(order.symbol)
        signed_qty = qty if order.side == OrderSide.BUY else -qty

        if pos is None:
            pos = Position(
                symbol=order.symbol,
                qty=signed_qty,
                avg_price=fill_price,
                product=order.product,
            )
            self._positions[order.symbol] = pos
        else:
            opening = (pos.qty == 0) or (pos.qty > 0 and order.side == OrderSide.BUY) or \
                      (pos.qty < 0 and order.side == OrderSide.SELL)

            if opening:
                # Adding to existing direction → VWAP avg_price
                new_qty = pos.qty + signed_qty
                pos.avg_price = (
                    (pos.avg_price * abs(pos.qty) + fill_price * qty) / abs(new_qty)
                )
                pos.qty = new_qty
            else:
                # Closing or reversing
                close_qty = min(abs(pos.qty), qty)
                if order.side == OrderSide.SELL:
                    realized = (fill_price - pos.avg_price) * close_qty
                else:
                    realized = (pos.avg_price - fill_price) * close_qty
                pos.realized_pnl += realized
                pos.qty += signed_qty

                if pos.qty == 0:
                    pos.avg_price = 0.0
                    pos.unrealized_pnl = 0.0
                elif (pos.qty > 0 and order.side == OrderSide.SELL and qty > abs(pos.qty - signed_qty)) or \
                     (pos.qty < 0 and order.side == OrderSide.BUY):
                    # Reversed — reset avg to fill price
                    pos.avg_price = fill_price

        # ── Update cash ────────────────────────────────────────────────────────
        # For intraday paper trading we do NOT deduct/credit full notional;
        # that would make capital fluctuate with every fill and go negative for
        # futures/options positions whose notional dwarfs the account.
        # Instead, realized P&L flows through pos.realized_pnl and available
        # capital = initial_capital + cumulative_realized_pnl.

        logger.info(
            "FILL symbol=%s side=%s qty=%d price=%.2f order_id=%s",
            order.symbol, order.side.value, qty, fill_price, order.order_id,
        )
        return fill

    def _try_fill_resting(self, symbol: str, ltp: float) -> None:
        """
        Scan resting orders for symbol and fill those whose conditions are met.
        Caller must hold self._lock.
        """
        remaining: list[Order] = []
        for order in self._resting.get(symbol, []):
            if order.is_terminal():
                continue

            filled = False

            if order.order_type == OrderType.LIMIT:
                # BUY limit: fill if LTP <= limit price
                # SELL limit: fill if LTP >= limit price
                if order.side == OrderSide.BUY and ltp <= order.price:
                    self._apply_fill(order, order.price, order.qty - order.filled_qty)
                    filled = True
                elif order.side == OrderSide.SELL and ltp >= order.price:
                    self._apply_fill(order, order.price, order.qty - order.filled_qty)
                    filled = True

            elif order.order_type == OrderType.SL_M:
                # Trigger reached → fill at market (slipped LTP)
                if order.side == OrderSide.BUY and ltp >= order.trigger_price:
                    self._apply_fill(order, self._slipped_price(order.side, ltp),
                                     order.qty - order.filled_qty)
                    filled = True
                elif order.side == OrderSide.SELL and ltp <= order.trigger_price:
                    self._apply_fill(order, self._slipped_price(order.side, ltp),
                                     order.qty - order.filled_qty)
                    filled = True

            elif order.order_type == OrderType.SL:
                # Trigger reached → convert to limit
                if order.side == OrderSide.BUY and ltp >= order.trigger_price:
                    order.order_type = OrderType.LIMIT
                    remaining.append(order)
                    continue
                elif order.side == OrderSide.SELL and ltp <= order.trigger_price:
                    order.order_type = OrderType.LIMIT
                    remaining.append(order)
                    continue

            if not filled:
                remaining.append(order)

        self._resting[symbol] = remaining

    def _check_kill_switch(self) -> None:
        """Raise KillSwitchError if daily loss ≥ kill_switch_amount."""
        if self._killed:
            raise KillSwitchError("Kill-switch already active — all trading halted.")
        daily_pnl = self._compute_daily_pnl()
        if daily_pnl <= -self._kill_switch_amount:
            self._killed = True
            logger.critical(
                "KILL-SWITCH TRIGGERED daily_pnl=%.2f threshold=%.2f",
                daily_pnl, -self._kill_switch_amount,
            )
            raise KillSwitchError(
                f"Daily loss ₹{abs(daily_pnl):.2f} exceeds kill-switch "
                f"threshold ₹{self._kill_switch_amount:.2f}."
            )

    def _compute_daily_pnl(self) -> float:
        realized = sum(p.realized_pnl for p in self._positions.values())
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return realized + unrealized

    # ── BaseBroker implementation ──────────────────────────────────────────────

    async def place_order(self, order: Order) -> str:
        async with self._lock:
            self._check_kill_switch()

            ltp = self._ltp.get(order.symbol)

            # Validate basic preconditions
            if order.qty <= 0:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "qty must be > 0"
                self._orders[order.order_id] = order
                raise ValueError(order.reject_reason)

            if order.order_type in (OrderType.LIMIT, OrderType.SL) and order.price is None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "price required for LIMIT/SL orders"
                self._orders[order.order_id] = order
                raise ValueError(order.reject_reason)

            if order.order_type in (OrderType.SL, OrderType.SL_M) and order.trigger_price is None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = "trigger_price required for SL orders"
                self._orders[order.order_id] = order
                raise ValueError(order.reject_reason)

            fill_price = self._slipped_price(order.side, ltp) if ltp else order.price

            if order.order_type == OrderType.MARKET:
                if ltp is None:
                    # No LTP yet — queue as resting; will fill on next tick
                    order.status = OrderStatus.OPEN
                    self._orders[order.order_id] = order
                    self._resting[order.symbol].append(order)
                    logger.warning("No LTP for %s — MARKET order queued.", order.symbol)
                else:
                    order.status = OrderStatus.OPEN
                    self._orders[order.order_id] = order
                    self._apply_fill(order, fill_price, order.qty)
            else:
                # Resting limit / SL order
                order.status = OrderStatus.OPEN
                self._orders[order.order_id] = order
                self._resting[order.symbol].append(order)
                # Immediately check if already fillable
                if ltp is not None:
                    self._try_fill_resting(order.symbol, ltp)

            return order.order_id

    async def cancel_order(self, order_id: str) -> bool:
        async with self._lock:
            order = self._orders.get(order_id)
            if order is None or order.is_terminal():
                return False
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.utcnow()
            # Remove from resting bucket
            self._resting[order.symbol] = [
                o for o in self._resting[order.symbol] if o.order_id != order_id
            ]
            logger.info("CANCEL order_id=%s symbol=%s", order_id, order.symbol)
            return True

    async def cancel_all_orders(self) -> int:
        async with self._lock:
            count = 0
            for order in list(self._orders.values()):
                if not order.is_terminal():
                    order.status = OrderStatus.CANCELLED
                    order.updated_at = datetime.utcnow()
                    count += 1
            for symbol in self._resting:
                self._resting[symbol] = []
            logger.info("CANCEL_ALL count=%d", count)
            return count

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    async def get_all_orders(self) -> list[Order]:
        return list(self._orders.values())

    async def get_positions(self) -> dict[str, Position]:
        return {s: p for s, p in self._positions.items() if p.qty != 0}

    async def get_available_capital(self) -> float:
        # Base capital + all realized gains/losses so far
        realized = sum(p.realized_pnl for p in self._positions.values())
        return max(self._initial_capital + realized, 0.0)

    async def get_portfolio_value(self) -> float:
        # What the account is actually worth right now
        return round(self._initial_capital + self._compute_daily_pnl(), 2)

    async def get_daily_pnl(self) -> float:
        async with self._lock:
            return self._compute_daily_pnl()

    async def get_fills(self) -> list[Fill]:
        return list(self._fills)

    async def update_ltp(self, symbol: str, ltp: float) -> None:
        async with self._lock:
            self._ltp[symbol] = ltp

            # Update unrealized P&L for the affected position
            pos = self._positions.get(symbol)
            if pos and pos.qty != 0:
                pos.update_unrealized(ltp)

            # Attempt to fill any resting orders
            self._try_fill_resting(symbol, ltp)

            # Check kill-switch passively (does not raise here — only on new orders)
            daily_pnl = self._compute_daily_pnl()
            if not self._killed and daily_pnl <= -self._kill_switch_amount:
                self._killed = True
                logger.critical(
                    "KILL-SWITCH TRIGGERED daily_pnl=%.2f threshold=%.2f — "
                    "squaring off all positions.",
                    daily_pnl, -self._kill_switch_amount,
                )
                # Fire-and-forget square-off (runs after lock releases)
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.ensure_future(self._square_off_unlocked())
                )

    async def _square_off_unlocked(self) -> list[Order]:
        """Internal square-off that acquires its own lock."""
        async with self._lock:
            closing_orders: list[Order] = []
            for symbol, pos in list(self._positions.items()):
                if pos.qty == 0:
                    continue
                side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
                order = Order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    qty=abs(pos.qty),
                    product=pos.product,
                    strategy_id="KILL_SWITCH",
                )
                order.status = OrderStatus.OPEN
                self._orders[order.order_id] = order
                ltp = self._ltp.get(symbol, pos.avg_price)
                self._apply_fill(order, self._slipped_price(side, ltp), abs(pos.qty))
                closing_orders.append(order)
            return closing_orders

    async def square_off_all(self) -> list[Order]:
        return await self._square_off_unlocked()

    # ── Diagnostic helpers (not part of abstract interface) ───────────────────

    def is_killed(self) -> bool:
        return self._killed

    def get_ltp(self, symbol: str) -> Optional[float]:
        return self._ltp.get(symbol)

    def snapshot(self) -> dict:
        """Return a JSON-serialisable summary for the WebSocket feed."""
        realized   = sum(p.realized_pnl   for p in self._positions.values())
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        daily_pnl  = realized + unrealized
        return {
            "capital":        round(self._initial_capital, 2),   # stable base
            "realized_pnl":   round(realized, 2),
            "portfolio_value": round(self._initial_capital + daily_pnl, 2),
            "daily_pnl":      round(daily_pnl, 2),
            "kill_switch_active": self._killed,
            "open_positions": {
                s: {
                    "qty": p.qty,
                    "avg_price": round(p.avg_price, 2),
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                    "realized_pnl": round(p.realized_pnl, 2),
                }
                for s, p in self._positions.items()
                if p.qty != 0
            },
        }
