"""
ActionExecutor — Layer 5 final piece: Decision → broker order.

Translates a DecisionEngine Decision or a TradeMonitor ExitSignal into a
concrete broker order and registers / deregisters positions with TradeMonitor.

Open flow
─────────
  1. Validate decision is actionable (direction != flat, conviction ≥ 72).
  2. Fetch current LTP and available capital from the broker.
  3. Compute qty = floor(capital × size_pct / ltp / lot_size) × lot_size.
  4. Place MARKET order via broker.place_order().
  5. Register position with TradeMonitor using confirmed fill price.
  6. Return ActionResult.

Close flow (from ExitSignal)
────────────────────────────
  1. Fetch current open position qty from broker.
  2. Place opposing MARKET order for that qty.
  3. Deregister from TradeMonitor.
  4. Return ActionResult.

Lot sizes
─────────
  Currency futures : 1 000 units per lot
  MCX CRUDEOIL     : 100 barrels
  MCX GOLD         : 100 grams
  MCX SILVER       : 30 kg
  MCX NATURALGAS   : 1 250 mmBtu
  MCX COPPER       : 2 500 kg
  Equity / default : 1 (fractional qty allowed; rounds to nearest 1)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from core.broker.base_broker import (
    Exchange,
    Order,
    OrderSide,
    OrderType,
    ProductType,
)
from ai_brain.decision_engine import Decision, CONVICTION_THRESHOLD
from ai_brain.trade_monitor import ExitSignal, TradeMonitor

logger = logging.getLogger(__name__)

# ── Lot size table ────────────────────────────────────────────────────────────

_LOT_SIZES: dict[str, int] = {
    # Currency futures (NSE)
    "USDINR": 1000, "EURINR": 1000, "GBPINR": 1000, "JPYINR": 1000,
    # MCX commodities
    "CRUDEOIL":    100,
    "GOLD":        100,
    "SILVER":       30,
    "NATURALGAS": 1250,
    "COPPER":     2500,
}

# Exchange mapping (for order routing)
_EXCHANGE: dict[str, Exchange] = {
    "USDINR": Exchange.NFO,  "EURINR": Exchange.NFO,
    "GBPINR": Exchange.NFO,  "JPYINR": Exchange.NFO,
    "CRUDEOIL":  Exchange.NSE, "GOLD":   Exchange.NSE,
    "SILVER":    Exchange.NSE, "NATURALGAS": Exchange.NSE,
    "COPPER":    Exchange.NSE,
}

_STRATEGY_ID = "ai_brain_layer5"


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ActionResult:
    symbol: str
    action: Literal["open_long", "open_short", "close_long", "close_short", "skip"]
    success: bool
    reason: str
    order_id: Optional[str]     = None
    qty: int                    = 0
    price: float                = 0.0
    cost_usd: float             = 0.0
    latency_ms: float           = 0.0
    source: str                 = "ai"
    ts: float                   = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol":     self.symbol,
            "action":     self.action,
            "success":    self.success,
            "reason":     self.reason,
            "order_id":   self.order_id,
            "qty":        self.qty,
            "price":      round(self.price, 4),
            "cost_usd":   round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 1),
            "source":     self.source,
            "ts":         self.ts,
        }


# ── ActionExecutor ────────────────────────────────────────────────────────────

class ActionExecutor:

    def __init__(self, monitor: TradeMonitor) -> None:
        self._monitor = monitor

    # ── Open a new AI position ────────────────────────────────────────────────

    async def execute(self, decision: Decision, broker) -> ActionResult:
        """
        Translate a Decision into a broker order.  Returns a skip ActionResult
        if the decision is not actionable or sizing produces qty=0.
        """
        sym = decision.symbol
        t0  = time.perf_counter()

        # ── Guard: must be actionable ─────────────────────────────────────────
        if not decision.is_actionable:
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason=(
                    f"not actionable: direction={decision.direction} "
                    f"conviction={decision.conviction}"
                ),
                source=decision.source,
            )

        # ── Fetch LTP and capital ─────────────────────────────────────────────
        ltp = broker.get_ltp(sym)
        if ltp is None or ltp <= 0:
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason=f"no LTP for {sym}",
                source=decision.source,
            )

        capital = await broker.get_available_capital()
        if capital <= 0:
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason="no available capital",
                source=decision.source,
            )

        # ── Already monitored — skip (monitor handles it) ─────────────────────
        if sym in self._monitor.monitored_symbols:
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason="position already monitored",
                source=decision.source,
            )

        # ── Compute qty ───────────────────────────────────────────────────────
        lot  = _LOT_SIZES.get(sym, 1)
        alloc = capital * decision.size_pct
        raw_qty = alloc / ltp
        qty  = max(lot, int(math.floor(raw_qty / lot) * lot))

        if qty <= 0:
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason=(
                    f"qty=0: alloc=₹{alloc:.0f}  ltp={ltp:.2f}  lot={lot}"
                ),
                source=decision.source,
            )

        # ── Build and place order ─────────────────────────────────────────────
        side   = OrderSide.BUY if decision.direction == "long" else OrderSide.SELL
        action = "open_long" if decision.direction == "long" else "open_short"

        order = Order(
            symbol=sym,
            side=side,
            order_type=OrderType.MARKET,
            qty=qty,
            product=ProductType.INTRADAY,
            exchange=_EXCHANGE.get(sym, Exchange.NSE),
            strategy_id=_STRATEGY_ID,
        )

        try:
            order_id = await broker.place_order(order)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.error("ActionExecutor: place_order failed for %s: %s", sym, exc)
            return ActionResult(
                symbol=sym, action=action, success=False,
                reason=f"place_order error: {exc}",
                latency_ms=elapsed, source=decision.source,
            )

        # ── Register with TradeMonitor ────────────────────────────────────────
        fill_price = broker.get_ltp(sym) or ltp   # best estimate post-fill
        self._monitor.add(
            symbol=sym,
            direction=decision.direction,  # type: ignore[arg-type]
            entry_price=fill_price,
            stop_loss_pct=decision.stop_loss_pct,
            take_profit_pct=decision.take_profit_pct,
            size_pct=decision.size_pct,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "ActionExecutor OPEN  %-8s  %s  qty=%d  ltp=%.4f  sl=%.2f%%  tp=%.2f%%  "
            "src=%s  conv=%d  order=%s",
            sym, action, qty, fill_price,
            decision.stop_loss_pct * 100, decision.take_profit_pct * 100,
            decision.source, decision.conviction, order_id,
        )

        return ActionResult(
            symbol=sym, action=action, success=True,
            reason=decision.reasoning,
            order_id=order_id, qty=qty, price=fill_price,
            cost_usd=decision.cost_usd,
            latency_ms=elapsed_ms,
            source=decision.source,
        )

    # ── Close a position from ExitSignal ─────────────────────────────────────

    async def close(self, exit_signal: ExitSignal, broker) -> ActionResult:
        """
        Close the position described by exit_signal.
        Deregisters from TradeMonitor regardless of broker result.
        """
        sym = exit_signal.symbol
        t0  = time.perf_counter()

        # ── Fetch current open qty ────────────────────────────────────────────
        positions = await broker.get_positions()
        pos       = positions.get(sym)
        if pos is None or pos.qty == 0:
            self._monitor.remove(sym)
            return ActionResult(
                symbol=sym, action="skip", success=False,
                reason="no open position to close",
            )

        qty  = abs(pos.qty)
        side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
        act  = "close_long" if pos.qty > 0 else "close_short"

        order = Order(
            symbol=sym,
            side=side,
            order_type=OrderType.MARKET,
            qty=qty,
            product=ProductType.INTRADAY,
            exchange=_EXCHANGE.get(sym, Exchange.NSE),
            strategy_id=_STRATEGY_ID,
        )

        try:
            order_id   = await broker.place_order(order)
            fill_price = broker.get_ltp(sym) or exit_signal.ltp
            success    = True
            reason     = f"exit:{exit_signal.reason}"
        except Exception as exc:
            logger.error("ActionExecutor: close order failed for %s: %s", sym, exc)
            order_id   = None
            fill_price = exit_signal.ltp
            success    = False
            reason     = f"close error: {exc}"

        # Always deregister — even on broker error the monitor should stop tracking
        self._monitor.remove(sym)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        pnl_pct    = exit_signal.unrealised_pnl_pct * 100
        logger.info(
            "ActionExecutor CLOSE %-8s  %s  qty=%d  ltp=%.4f  pnl=%.2f%%  "
            "reason=%s  held=%.0fs",
            sym, act, qty, fill_price, pnl_pct,
            exit_signal.reason, exit_signal.held_for_s,
        )

        return ActionResult(
            symbol=sym, action=act, success=success,
            reason=reason, order_id=order_id, qty=qty,
            price=fill_price, latency_ms=elapsed_ms,
        )

    # ── Batch: process all pending exits ─────────────────────────────────────

    async def process_exits(
        self,
        prices: dict[str, float],
        broker,
    ) -> list[ActionResult]:
        """
        Run TradeMonitor.check_all(), then close every triggered position.
        Returns list of ActionResults for all exit orders placed.
        """
        exits   = self._monitor.check_all(prices)
        results = []
        for sig in exits:
            result = await self.close(sig, broker)
            results.append(result)
        return results
