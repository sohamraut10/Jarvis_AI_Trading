"""
Pre-trade risk gate.

Every order passes through RiskManager.check() before reaching the broker.
The gate runs a waterfall of checks; the first failure short-circuits and
returns a rejected RiskDecision with a structured reason string.

Checks (in order)
-----------------
1. Kill-switch gate      — broker already halted → hard reject
2. Daily P&L gate        — live loss already at/past threshold → hard reject
3. Price sanity          — ltp must be > 0
4. Capital sufficiency   — must afford at least 1 share
5. Single-trade cap      — trade value ≤ MAX_TRADE_PCT of portfolio
6. Symbol concentration  — existing + new position ≤ MAX_SYMBOL_PCT of portfolio
7. Strategy cap          — total strategy exposure ≤ MAX_STRATEGY_PCT of portfolio
8. Open-position count   — total open symbols ≤ MAX_OPEN_POSITIONS

All thresholds are configurable at construction time with sensible defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from core.broker.base_broker import BaseBroker, Order, OrderSide

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    adjusted_qty: int = 0   # may be < requested qty after concentration trim

    def __bool__(self) -> bool:
        return self.approved


class RiskManager:
    # ── Default thresholds ────────────────────────────────────────────────────
    MAX_TRADE_PCT: float = 0.20         # single trade ≤ 20 % of portfolio
    MAX_SYMBOL_PCT: float = 0.30        # single symbol ≤ 30 % of portfolio
    MAX_STRATEGY_PCT: float = 0.25      # one strategy ≤ 25 % of portfolio
    MAX_OPEN_POSITIONS: int = 10        # concurrent open symbols

    def __init__(
        self,
        broker: BaseBroker,
        kill_switch_amount: float = 300.0,
        max_trade_pct: float = MAX_TRADE_PCT,
        max_symbol_pct: float = MAX_SYMBOL_PCT,
        max_strategy_pct: float = MAX_STRATEGY_PCT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
    ) -> None:
        self._broker = broker
        self._kill_switch_amount = kill_switch_amount
        self._max_trade_pct = max_trade_pct
        self._max_symbol_pct = max_symbol_pct
        self._max_strategy_pct = max_strategy_pct
        self._max_open_positions = max_open_positions

    # ── Public API ─────────────────────────────────────────────────────────────

    async def check(
        self,
        order: Order,
        ltp: float,
        strategy_id: Optional[str] = None,
    ) -> RiskDecision:
        """
        Run all pre-trade checks for the given order.

        Parameters
        ----------
        order       : the Order about to be placed (qty already sized by KellySizer)
        ltp         : last traded price used for notional calculations
        strategy_id : override order.strategy_id if needed

        Returns a RiskDecision.  Caller must check `.approved` before placing.
        """
        sid = strategy_id or order.strategy_id or "unknown"

        # ── 1. Kill-switch gate ────────────────────────────────────────────────
        from core.broker.paper_broker import PaperBroker  # avoid circular import
        if isinstance(self._broker, PaperBroker) and self._broker.is_killed():
            return self._reject("kill_switch_active", sid, order)

        # ── 2. Daily P&L gate ──────────────────────────────────────────────────
        daily_pnl = await self._broker.get_daily_pnl()
        if daily_pnl <= -self._kill_switch_amount:
            return self._reject(
                f"daily_pnl ₹{daily_pnl:.2f} ≤ threshold -₹{self._kill_switch_amount:.2f}",
                sid, order,
            )

        # ── 3. Price sanity ────────────────────────────────────────────────────
        if ltp <= 0:
            return self._reject(f"invalid ltp={ltp}", sid, order)

        # ── 4. Capital sufficiency ────────────────────────────────────────────
        available = await self._broker.get_available_capital()
        if available < ltp:
            return self._reject(
                f"insufficient capital ₹{available:.2f} < ltp ₹{ltp:.2f}",
                sid, order,
            )

        portfolio_value = await self._broker.get_portfolio_value()
        if portfolio_value <= 0:
            return self._reject("portfolio_value ≤ 0", sid, order)

        trade_value = ltp * order.qty

        # ── 5. Single-trade cap ───────────────────────────────────────────────
        max_trade_value = portfolio_value * self._max_trade_pct
        if trade_value > max_trade_value:
            trimmed_qty = int(max_trade_value / ltp)
            if trimmed_qty <= 0:
                # High-priced stock: allow the minimum 1-share trade as long
                # as capital is sufficient (already verified in check #4).
                trimmed_qty = 1
            logger.warning(
                "risk_trim strategy=%s symbol=%s qty %d→%d (single-trade cap)",
                sid, order.symbol, order.qty, trimmed_qty,
            )
            order.qty = trimmed_qty
            trade_value = ltp * trimmed_qty

        # ── 6. Symbol concentration ───────────────────────────────────────────
        positions = await self._broker.get_positions()
        existing_pos = positions.get(order.symbol)
        existing_value = abs(existing_pos.qty) * ltp if existing_pos else 0.0
        max_symbol_value = portfolio_value * self._max_symbol_pct

        if order.side == OrderSide.BUY:
            projected_symbol_value = existing_value + trade_value
            if projected_symbol_value > max_symbol_value:
                headroom = max_symbol_value - existing_value
                trimmed_qty = int(headroom / ltp)
                if trimmed_qty <= 0:
                    return self._reject(
                        f"symbol concentration: {order.symbol} would be "
                        f"₹{projected_symbol_value:.2f} "
                        f"({projected_symbol_value/portfolio_value*100:.1f}% of portfolio) "
                        f"— max {self._max_symbol_pct*100:.0f}%",
                        sid, order,
                    )
                logger.warning(
                    "risk_trim strategy=%s symbol=%s qty %d→%d (symbol concentration)",
                    sid, order.symbol, order.qty, trimmed_qty,
                )
                order.qty = trimmed_qty
                trade_value = ltp * trimmed_qty

        # ── 7. Strategy exposure cap ──────────────────────────────────────────
        if sid != "unknown":
            all_orders = await self._broker.get_all_orders()
            strategy_exposure = self._compute_strategy_exposure(
                all_orders, positions, sid, ltp
            )
            max_strategy_value = portfolio_value * self._max_strategy_pct
            if order.side == OrderSide.BUY:
                projected = strategy_exposure + (ltp * order.qty)
                if projected > max_strategy_value:
                    headroom = max_strategy_value - strategy_exposure
                    trimmed_qty = int(headroom / ltp)
                    if trimmed_qty <= 0:
                        return self._reject(
                            f"strategy cap: {sid} exposure ₹{projected:.2f} "
                            f"({projected/portfolio_value*100:.1f}%) "
                            f"— max {self._max_strategy_pct*100:.0f}%",
                            sid, order,
                        )
                    logger.warning(
                        "risk_trim strategy=%s symbol=%s qty %d→%d (strategy cap)",
                        sid, order.symbol, order.qty, trimmed_qty,
                    )
                    order.qty = trimmed_qty

        # ── 8. Open-position count ─────────────────────────────────────────────
        open_symbols = {s for s, p in positions.items() if p.qty != 0}
        is_new_symbol = order.symbol not in open_symbols and order.side == OrderSide.BUY
        if is_new_symbol and len(open_symbols) >= self._max_open_positions:
            return self._reject(
                f"max open positions ({self._max_open_positions}) reached",
                sid, order,
            )

        # ── All checks passed ──────────────────────────────────────────────────
        decision = RiskDecision(approved=True, reason="all_checks_passed", adjusted_qty=order.qty)
        logger.info(
            "risk_approved strategy=%s symbol=%s side=%s qty=%d ltp=%.2f",
            sid, order.symbol, order.side.value, order.qty, ltp,
        )
        return decision

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _reject(reason: str, strategy_id: str, order: Order) -> RiskDecision:
        logger.warning(
            "risk_rejected strategy=%s symbol=%s side=%s qty=%d reason=%s",
            strategy_id, order.symbol, order.side.value, order.qty, reason,
        )
        return RiskDecision(approved=False, reason=reason, adjusted_qty=0)

    @staticmethod
    def _compute_strategy_exposure(
        all_orders,
        positions: dict,
        strategy_id: str,
        ltp: float,
    ) -> float:
        """
        Sum the current mark-to-market value of all open positions that were
        opened by this strategy.  Uses order.strategy_id to attribute positions.
        """
        attributed_symbols: set[str] = set()
        for order in all_orders:
            if order.strategy_id == strategy_id and order.filled_qty > 0:
                attributed_symbols.add(order.symbol)

        exposure = 0.0
        for symbol in attributed_symbols:
            pos = positions.get(symbol)
            if pos and pos.qty != 0:
                exposure += abs(pos.qty) * ltp
        return exposure
