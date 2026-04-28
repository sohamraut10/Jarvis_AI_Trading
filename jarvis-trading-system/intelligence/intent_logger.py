"""
Intent Logger — append-only JSONL structured reasoning log.

Captures the WHY behind every JARVIS decision, not just the WHAT.
Each line is a self-contained JSON object with a timestamp and an
event_type that determines the remaining fields.

Usage:
    logger = IntentLogger("logs/intent.jsonl")
    await logger.log_signal(signal, regime="TRENDING_UP", kelly_explain={...})
    await logger.log_regime_change("SIDEWAYS", "TRENDING_UP", features)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class EventType:
    SIGNAL = "signal_generated"
    ORDER_PLACED = "order_placed"
    ORDER_REJECTED = "order_rejected"
    ORDER_FILLED = "order_filled"
    REGIME_CHANGE = "regime_change"
    KILL_SWITCH = "kill_switch_triggered"
    ALLOCATION = "capital_allocated"
    ALPHA_DECAY = "alpha_decay_detected"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class IntentLogger:
    def __init__(self, log_path: str = "logs/intent.jsonl") -> None:
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ── Core writer ────────────────────────────────────────────────────────────

    async def _write(self, entry: dict[str, Any]) -> None:
        entry.setdefault("timestamp", datetime.utcnow().isoformat())
        line = json.dumps(entry, default=str)
        async with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ── Typed log methods ──────────────────────────────────────────────────────

    async def log_signal(
        self,
        signal,                             # Signal from base_strategy
        regime: str,
        kelly_explain: Optional[dict] = None,
    ) -> None:
        await self._write({
            "event_type": EventType.SIGNAL,
            "strategy_id": signal.strategy_id,
            "symbol": signal.symbol,
            "side": str(signal.side),
            "confidence": round(signal.confidence, 3),
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "risk_reward": round(signal.risk_reward, 2),
            "timeframe": signal.timeframe,
            "regime": regime,
            "reason": signal.reason,
            "kelly": kelly_explain,
        })

    async def log_order(
        self,
        order,                              # Order from base_broker
        decision,                           # RiskDecision
        regime: str,
    ) -> None:
        approved = decision.approved
        await self._write({
            "event_type": EventType.ORDER_PLACED if approved else EventType.ORDER_REJECTED,
            "order_id": order.order_id,
            "strategy_id": order.strategy_id,
            "symbol": order.symbol,
            "side": str(order.side),
            "qty": order.qty,
            "order_type": str(order.order_type),
            "regime": regime,
            "approved": approved,
            "risk_reason": decision.reason,
            "adjusted_qty": decision.adjusted_qty,
        })

    async def log_fill(
        self,
        fill,                               # Fill from base_broker
        strategy_id: Optional[str] = None,
    ) -> None:
        await self._write({
            "event_type": EventType.ORDER_FILLED,
            "order_id": fill.order_id,
            "strategy_id": strategy_id or fill.strategy_id,
            "symbol": fill.symbol,
            "side": str(fill.side),
            "qty": fill.qty,
            "price": fill.price,
        })

    async def log_regime_change(
        self,
        old_regime: str,
        new_regime: str,
        features: dict,
    ) -> None:
        await self._write({
            "event_type": EventType.REGIME_CHANGE,
            "old_regime": old_regime,
            "new_regime": new_regime,
            "features": features,
        })

    async def log_kill_switch(self, daily_pnl: float, threshold: float) -> None:
        await self._write({
            "event_type": EventType.KILL_SWITCH,
            "daily_pnl": round(daily_pnl, 2),
            "threshold": round(threshold, 2),
            "severity": "HARD_STOP",
            "message": (
                f"Daily loss ₹{abs(daily_pnl):.2f} exceeded kill-switch "
                f"threshold ₹{abs(threshold):.2f}. All trading halted."
            ),
        })

    async def log_allocation(self, result) -> None:      # AllocationResult
        await self._write({
            "event_type": EventType.ALLOCATION,
            "regime": str(result.regime),
            "total_capital": result.total_capital,
            "active_strategies": result.active_count,
            "excluded_by_regime": result.excluded_count,
            "top_ranked": result.ranked_strategies[:8],
            "allocations": result.allocations,
            "sharpe_scores": result.sharpe_scores,
        })

    async def log_alpha_decay(self, status) -> None:     # DecayStatus
        await self._write({
            "event_type": EventType.ALPHA_DECAY,
            "strategy_id": status.strategy_id,
            "severity": str(status.severity),
            "short_sharpe": status.short_sharpe,
            "long_sharpe": status.long_sharpe,
            "win_rate_short": status.win_rate_short,
            "win_rate_long": status.win_rate_long,
            "consecutive_losses": status.consecutive_losses,
            "reason": status.reason,
        })

    async def log_session_start(self, capital: float, regime: str) -> None:
        await self._write({
            "event_type": EventType.SESSION_START,
            "capital": capital,
            "regime": regime,
        })

    async def log_session_end(self, summary: dict) -> None:
        await self._write({
            "event_type": EventType.SESSION_END,
            **summary,
        })

    # ── Utility ────────────────────────────────────────────────────────────────

    async def tail(self, n: int = 20) -> list[dict]:
        """Return the last n log entries (for dashboard / debugging)."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            return [json.loads(l) for l in lines[-n:] if l.strip()]
        except FileNotFoundError:
            return []
