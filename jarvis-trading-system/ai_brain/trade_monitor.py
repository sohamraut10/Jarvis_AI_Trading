"""
TradeMonitor — Layer 5 adaptive position watcher.

Monitors every AI-opened position on each price tick and fires an ExitSignal
when an exit condition is met.  The ActionExecutor acts on that signal.

Exit conditions (checked in priority order)
────────────────────────────────────────────
  1. STOP_HIT       — price crosses the hard stop-loss level
  2. TARGET_HIT     — price crosses the take-profit level
  3. TRAIL_STOP     — trailing stop activates once unrealised profit ≥ 50% of
                       target distance; stop trails at trail_high × (1 − sl_pct/2)
                       for longs, trail_low × (1 + sl_pct/2) for shorts
  4. TIME_EXIT      — position held longer than MAX_HOLD_MINUTES (default 240)
  5. REVERSAL       — caller signals a directional reversal for this symbol

Adaptive trailing logic
───────────────────────
  trail_activated when unrealised_pct ≥ TRAIL_TRIGGER_FRAC × take_profit_pct
  trail_stop_pct  = stop_loss_pct × TRAIL_TIGHTEN  (default 0.5× original SL)
  This locks in roughly half the target profit once the trade is going well.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_HOLD_MINUTES     = 240       # force-exit after 4 hours
TRAIL_TRIGGER_FRAC   = 0.50     # trail activates at 50% of target profit
TRAIL_TIGHTEN        = 0.50     # trailing SL = original SL × this factor

ExitReason = Literal["stop_hit", "target_hit", "trail_stop", "time_exit", "reversal"]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MonitoredPosition:
    symbol: str
    direction: Literal["long", "short"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    stop_loss_pct: float
    take_profit_pct: float
    size_pct: float
    opened_at: float = field(default_factory=time.time)

    # Trailing state
    trail_activated: bool  = False
    trail_extreme: float   = 0.0       # highest price seen (long) / lowest (short)
    trail_stop_price: float = 0.0      # dynamic trailing stop level

    # Live tracking
    current_price: float  = 0.0
    unrealised_pnl_pct: float = 0.0

    def __post_init__(self) -> None:
        self.trail_extreme    = self.entry_price
        self.trail_stop_price = self.stop_loss_price
        self.current_price    = self.entry_price

    @property
    def held_seconds(self) -> float:
        return time.time() - self.opened_at

    def update_price(self, ltp: float) -> None:
        self.current_price = ltp
        if self.direction == "long":
            self.unrealised_pnl_pct = (ltp - self.entry_price) / self.entry_price
            if ltp > self.trail_extreme:
                self.trail_extreme = ltp
        else:
            self.unrealised_pnl_pct = (self.entry_price - ltp) / self.entry_price
            if ltp < self.trail_extreme:
                self.trail_extreme = ltp

    def activate_trail(self) -> None:
        if self.trail_activated:
            return
        self.trail_activated = True
        trail_sl_pct = self.stop_loss_pct * TRAIL_TIGHTEN
        if self.direction == "long":
            self.trail_stop_price = self.trail_extreme * (1.0 - trail_sl_pct)
        else:
            self.trail_stop_price = self.trail_extreme * (1.0 + trail_sl_pct)
        logger.info(
            "TrailStop ACTIVATED  %s  extreme=%.4f  trail_stop=%.4f  (pnl=%.2f%%)",
            self.symbol, self.trail_extreme,
            self.trail_stop_price, self.unrealised_pnl_pct * 100,
        )

    def update_trail(self) -> None:
        if not self.trail_activated:
            return
        trail_sl_pct = self.stop_loss_pct * TRAIL_TIGHTEN
        if self.direction == "long":
            new_stop = self.trail_extreme * (1.0 - trail_sl_pct)
            if new_stop > self.trail_stop_price:
                self.trail_stop_price = new_stop
        else:
            new_stop = self.trail_extreme * (1.0 + trail_sl_pct)
            if new_stop < self.trail_stop_price:
                self.trail_stop_price = new_stop

    def to_dict(self) -> dict:
        return {
            "symbol":              self.symbol,
            "direction":           self.direction,
            "entry_price":         round(self.entry_price, 4),
            "current_price":       round(self.current_price, 4),
            "stop_loss_price":     round(self.stop_loss_price, 4),
            "take_profit_price":   round(self.take_profit_price, 4),
            "trail_activated":     self.trail_activated,
            "trail_stop_price":    round(self.trail_stop_price, 4) if self.trail_activated else None,
            "unrealised_pnl_pct":  round(self.unrealised_pnl_pct * 100, 3),
            "held_seconds":        round(self.held_seconds, 1),
            "size_pct":            round(self.size_pct, 4),
        }


@dataclass
class ExitSignal:
    symbol: str
    reason: ExitReason
    ltp: float
    entry_price: float
    unrealised_pnl_pct: float
    held_for_s: float
    direction: str
    stop_loss_price: float
    take_profit_price: float

    def to_dict(self) -> dict:
        return {
            "symbol":             self.symbol,
            "reason":             self.reason,
            "ltp":                round(self.ltp, 4),
            "entry_price":        round(self.entry_price, 4),
            "unrealised_pnl_pct": round(self.unrealised_pnl_pct * 100, 3),
            "held_for_s":         round(self.held_for_s, 1),
            "direction":          self.direction,
        }


# ── Monitor ───────────────────────────────────────────────────────────────────

class TradeMonitor:
    """
    Thread/task-safe position watcher.
    Call check() on every price tick for monitored symbols.
    Call add() when an AI trade is confirmed open.
    Call remove() after the exit order is sent.
    """

    def __init__(
        self,
        max_hold_minutes: float = MAX_HOLD_MINUTES,
        trail_trigger_frac: float = TRAIL_TRIGGER_FRAC,
    ) -> None:
        self._positions: Dict[str, MonitoredPosition] = {}
        self._max_hold_s        = max_hold_minutes * 60
        self._trail_trigger     = trail_trigger_frac

    # ── Public interface ──────────────────────────────────────────────────────

    def add(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        size_pct: float,
    ) -> MonitoredPosition:
        if direction == "long":
            sl_price = entry_price * (1.0 - stop_loss_pct)
            tp_price = entry_price * (1.0 + take_profit_pct)
        else:
            sl_price = entry_price * (1.0 + stop_loss_pct)
            tp_price = entry_price * (1.0 - take_profit_pct)

        pos = MonitoredPosition(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            size_pct=size_pct,
        )
        self._positions[symbol] = pos
        logger.info(
            "TradeMonitor ADD  %-8s  dir=%s  entry=%.4f  SL=%.4f  TP=%.4f",
            symbol, direction, entry_price, sl_price, tp_price,
        )
        return pos

    def remove(self, symbol: str) -> None:
        if symbol in self._positions:
            del self._positions[symbol]
            logger.info("TradeMonitor REMOVE  %s", symbol)

    def signal_reversal(self, symbol: str) -> Optional[ExitSignal]:
        """
        Call when a strong opposing signal fires for an open position.
        Returns an ExitSignal if a position exists; None otherwise.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        return self._make_exit(pos, "reversal", pos.current_price)

    def check(self, symbol: str, ltp: float) -> Optional[ExitSignal]:
        """
        Evaluate exit conditions for one symbol at the current price.
        Returns ExitSignal if an exit is triggered, None otherwise.
        Caller must call remove(symbol) after acting on the signal.
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        pos.update_price(ltp)

        # ── Check trail activation ─────────────────────────────────────────
        tp_dist = abs(pos.take_profit_price - pos.entry_price)
        profit  = abs(ltp - pos.entry_price) if pos.unrealised_pnl_pct > 0 else 0.0
        if (not pos.trail_activated
                and tp_dist > 0
                and profit >= self._trail_trigger * tp_dist):
            pos.activate_trail()

        # Update trail extreme and stop
        pos.update_trail()

        # ── Exit condition checks (priority order) ─────────────────────────

        # 1. Hard stop
        if self._stop_hit(pos, ltp):
            logger.warning(
                "STOP HIT  %s  ltp=%.4f  stop=%.4f  pnl=%.2f%%",
                symbol, ltp, pos.stop_loss_price, pos.unrealised_pnl_pct * 100,
            )
            return self._make_exit(pos, "stop_hit", ltp)

        # 2. Target
        if self._target_hit(pos, ltp):
            logger.info(
                "TARGET HIT  %s  ltp=%.4f  tp=%.4f  pnl=%.2f%%",
                symbol, ltp, pos.take_profit_price, pos.unrealised_pnl_pct * 100,
            )
            return self._make_exit(pos, "target_hit", ltp)

        # 3. Trailing stop
        if pos.trail_activated and self._trail_hit(pos, ltp):
            logger.info(
                "TRAIL STOP  %s  ltp=%.4f  trail_stop=%.4f  pnl=%.2f%%",
                symbol, ltp, pos.trail_stop_price, pos.unrealised_pnl_pct * 100,
            )
            return self._make_exit(pos, "trail_stop", ltp)

        # 4. Time exit
        if pos.held_seconds >= self._max_hold_s:
            logger.info(
                "TIME EXIT  %s  held=%.0f min  pnl=%.2f%%",
                symbol, pos.held_seconds / 60, pos.unrealised_pnl_pct * 100,
            )
            return self._make_exit(pos, "time_exit", ltp)

        return None

    def check_all(self, prices: dict[str, float]) -> list[ExitSignal]:
        """Batch-check all monitored symbols; return list of triggered exits."""
        exits: list[ExitSignal] = []
        for sym, ltp in prices.items():
            sig = self.check(sym, ltp)
            if sig:
                exits.append(sig)
        return exits

    def snapshot(self) -> dict:
        return {
            sym: pos.to_dict()
            for sym, pos in self._positions.items()
        }

    @property
    def monitored_symbols(self) -> set[str]:
        return set(self._positions)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _stop_hit(pos: MonitoredPosition, ltp: float) -> bool:
        if pos.direction == "long":
            return ltp <= pos.stop_loss_price
        return ltp >= pos.stop_loss_price

    @staticmethod
    def _target_hit(pos: MonitoredPosition, ltp: float) -> bool:
        if pos.direction == "long":
            return ltp >= pos.take_profit_price
        return ltp <= pos.take_profit_price

    @staticmethod
    def _trail_hit(pos: MonitoredPosition, ltp: float) -> bool:
        if pos.direction == "long":
            return ltp <= pos.trail_stop_price
        return ltp >= pos.trail_stop_price

    @staticmethod
    def _make_exit(pos: MonitoredPosition, reason: ExitReason, ltp: float) -> ExitSignal:
        return ExitSignal(
            symbol=pos.symbol,
            reason=reason,
            ltp=ltp,
            entry_price=pos.entry_price,
            unrealised_pnl_pct=pos.unrealised_pnl_pct,
            held_for_s=pos.held_seconds,
            direction=pos.direction,
            stop_loss_price=pos.stop_loss_price,
            take_profit_price=pos.take_profit_price,
        )
