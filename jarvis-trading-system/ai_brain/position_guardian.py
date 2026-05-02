"""
PositionGuardian — AI position risk reviewer.

Runs every 60 seconds and reviews each open position using the guardian LLM
step (gemini-2.5-flash primary).  For each position it outputs a GuardianReview
with an action recommendation:
  HOLD             — no change needed
  TIGHTEN_STOP     — move stop-loss closer to current price
  PARTIAL_EXIT     — reduce position size by 50%
  FULL_EXIT        — close immediately

When guardian_auto_execute=True the guardian will automatically fire a FULL_EXIT
action via the provided close_position_fn callback.  TIGHTEN_STOP and
PARTIAL_EXIT are always advisory (dashboard alert) unless explicitly wired.

Safety: guardian never executes if the kill switch is already active.
Cost guard: throttle_override=True in yaml means guardian calls bypass the
daily budget check — position safety overrides cost.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from ai_brain.ai_router import AIRouter
from ai_brain.cost_throttle import CostThrottle

logger = logging.getLogger(__name__)

Action = Literal["HOLD", "TIGHTEN_STOP", "PARTIAL_EXIT", "FULL_EXIT"]

_SYSTEM_PROMPT = (
    "You are JARVIS Position Guardian, a real-time risk management AI for an "
    "Indian equity and options trading system. Respond ONLY in valid JSON. "
    "Be conservative — when in doubt, protect capital."
)

_GUARDIAN_PROMPT = """
Review the open position below and determine the appropriate risk action.

Respond with JSON matching this exact schema:
{
  "action": "HOLD|TIGHTEN_STOP|PARTIAL_EXIT|FULL_EXIT",
  "urgency": "low|medium|high",
  "reasoning": "...",
  "new_stop_pct": 0.005
}

Rules:
- action: HOLD if unrealized loss < 30% of stop distance; TIGHTEN_STOP if trending
  against position with momentum; PARTIAL_EXIT if regime turned hostile; FULL_EXIT if
  stop has been hit or momentum is strongly adverse.
- urgency: high if action is FULL_EXIT or PARTIAL_EXIT
- reasoning: ≤ 150 chars
- new_stop_pct: only required for TIGHTEN_STOP; omit for other actions
"""


@dataclass
class GuardianReview:
    symbol: str
    action: Action
    urgency: str                  # "low" | "medium" | "high"
    reasoning: str
    new_stop_pct: Optional[float]
    model_used: str
    latency_ms: float
    cost_usd: float
    auto_executed: bool = False
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = {
            "symbol":        self.symbol,
            "action":        self.action,
            "urgency":       self.urgency,
            "reasoning":     self.reasoning,
            "model_used":    self.model_used,
            "latency_ms":    round(self.latency_ms, 1),
            "cost_usd":      round(self.cost_usd, 6),
            "auto_executed": self.auto_executed,
            "ts":            self.ts,
        }
        if self.new_stop_pct is not None:
            d["new_stop_pct"] = round(self.new_stop_pct, 5)
        return d


ClosePositionFn = Callable[[str], Awaitable[None]]


class PositionGuardian:
    """
    Periodically reviews each open position and produces GuardianReview objects.

    close_position_fn: async callback(symbol) to close a position.
                       Called automatically when action=FULL_EXIT and
                       auto_execute=True.
    """

    def __init__(
        self,
        router: AIRouter,
        throttle: CostThrottle,
        *,
        close_position_fn: Optional[ClosePositionFn] = None,
        cycle_seconds: int = 60,
        auto_execute: bool = False,
    ) -> None:
        self._router            = router
        self._throttle          = throttle
        self._close_fn          = close_position_fn
        self._cycle             = cycle_seconds
        self._auto_execute      = auto_execute
        self._lock              = asyncio.Lock()
        self._recent_reviews: List[GuardianReview] = []
        self._running           = False

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def auto_execute(self) -> bool:
        return self._auto_execute

    @auto_execute.setter
    def auto_execute(self, val: bool) -> None:
        self._auto_execute = val

    def recent_reviews(self, n: int = 20) -> List[dict]:
        return [r.to_dict() for r in self._recent_reviews[-n:]]

    async def review_position(
        self,
        symbol: str,
        position: dict,
        market_data: dict,
        sentinel_sentiment: str = "neutral",
    ) -> GuardianReview:
        """
        Review a single position.  position dict should contain:
          direction, entry_price, stop_loss_price, take_profit_price,
          avg_price (or entry_price), qty, unrealized_pnl, opened_at
        """
        t0 = time.perf_counter()
        try:
            resp = await self._router.call(
                prompt=_GUARDIAN_PROMPT,
                context=self._build_context(symbol, position, market_data, sentinel_sentiment),
                mode="primary",
                step="guardian",
            )
            review = self._parse(symbol, resp.response, resp.model_used,
                                 resp.latency_ms, resp.cost_usd)
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("PositionGuardian: LLM failed for %s (%s) — HOLD fallback", symbol, exc)
            review = GuardianReview(
                symbol=symbol, action="HOLD", urgency="low",
                reasoning=f"LLM error — defaulting to HOLD ({exc})",
                new_stop_pct=None,
                model_used="rules", latency_ms=latency_ms, cost_usd=0.0,
            )

        # Auto-execute full exit if configured and safe
        if (
            review.action == "FULL_EXIT"
            and self._auto_execute
            and self._close_fn is not None
        ):
            try:
                await self._close_fn(symbol)
                review.auto_executed = True
                logger.warning("PositionGuardian: AUTO-CLOSED %s — %s", symbol, review.reasoning)
            except Exception as exc:
                logger.error("PositionGuardian: auto-close failed for %s: %s", symbol, exc)

        async with self._lock:
            self._recent_reviews.append(review)
            if len(self._recent_reviews) > 200:
                self._recent_reviews = self._recent_reviews[-200:]

        logger.info(
            "Guardian %s → %s  urgency=%s  model=%s  cost=$%.5f%s",
            symbol, review.action, review.urgency, review.model_used, review.cost_usd,
            "  [AUTO-EXECUTED]" if review.auto_executed else "",
        )
        return review

    async def review_all(
        self,
        open_positions: Dict[str, dict],
        market_data: dict,
        sentinel_sentiment: str = "neutral",
        kill_active: bool = False,
    ) -> List[GuardianReview]:
        if kill_active or not open_positions:
            return []

        tasks = [
            self.review_position(sym, pos, market_data, sentinel_sentiment)
            for sym, pos in open_positions.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, GuardianReview)]

    async def start_loop(self, get_snapshot_fn) -> None:
        """Background loop — reviews all open positions every cycle."""
        self._running = True
        while self._running:
            try:
                snap = get_snapshot_fn()
                positions  = (snap.get("broker") or {}).get("open_positions") or {}
                kill_active = (snap.get("broker") or {}).get("kill_switch_active", False)
                sentiment  = (snap.get("sentinel") or {}).get("overall_sentiment", "neutral")
                await self.review_all(positions, snap, sentiment, kill_active)
            except Exception as exc:
                logger.error("PositionGuardian loop error: %s", exc)
            await asyncio.sleep(self._cycle)

    def stop(self) -> None:
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_context(
        symbol: str,
        pos: dict,
        market_data: dict,
        sentiment: str,
    ) -> dict:
        scanner = (market_data.get("scanner") or {}).get(symbol, {})
        ltp     = scanner.get("ltp") or pos.get("avg_price", 0)
        entry   = pos.get("entry_price") or pos.get("avg_price", 0)
        unr_pnl = pos.get("unrealized_pnl", 0)

        pnl_pct = ((ltp - entry) / entry) if entry else 0.0
        if pos.get("direction") == "short":
            pnl_pct = -pnl_pct

        hold_minutes = (time.time() - pos.get("opened_at", time.time())) / 60

        return {
            "symbol":           symbol,
            "direction":        pos.get("direction", "long"),
            "entry_price":      entry,
            "current_price":    ltp,
            "stop_loss_price":  pos.get("stop_loss_price"),
            "take_profit_price": pos.get("take_profit_price"),
            "unrealized_pnl":   unr_pnl,
            "pnl_pct":          round(pnl_pct, 4),
            "held_minutes":     round(hold_minutes, 1),
            "qty":              pos.get("qty", 0),
            "scanner_direction": scanner.get("composite_direction", "flat"),
            "scanner_confidence": scanner.get("composite_confidence", 0),
            "market_sentiment": sentiment,
            "regime":           market_data.get("regime", "UNKNOWN"),
        }

    @staticmethod
    def _parse(
        symbol: str, raw: str,
        model_used: str, latency_ms: float, cost_usd: float,
    ) -> GuardianReview:
        try:
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
            data = json.loads(text)

            action = str(data.get("action", "HOLD")).upper()
            if action not in ("HOLD", "TIGHTEN_STOP", "PARTIAL_EXIT", "FULL_EXIT"):
                action = "HOLD"

            urgency = str(data.get("urgency", "low")).lower()
            if urgency not in ("low", "medium", "high"):
                urgency = "low"

            reasoning = str(data.get("reasoning", ""))[:200]

            new_stop = None
            if action == "TIGHTEN_STOP" and "new_stop_pct" in data:
                try:
                    new_stop = max(0.001, min(0.02, float(data["new_stop_pct"])))
                except (TypeError, ValueError):
                    pass

            return GuardianReview(
                symbol=symbol,
                action=action,  # type: ignore[arg-type]
                urgency=urgency,
                reasoning=reasoning,
                new_stop_pct=new_stop,
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )
        except Exception as exc:
            logger.warning("PositionGuardian: parse error for %s (%s) — HOLD", symbol, exc)
            return GuardianReview(
                symbol=symbol, action="HOLD", urgency="low",
                reasoning=f"Parse error — HOLD ({exc})",
                new_stop_pct=None,
                model_used=model_used, latency_ms=latency_ms, cost_usd=cost_usd,
            )
