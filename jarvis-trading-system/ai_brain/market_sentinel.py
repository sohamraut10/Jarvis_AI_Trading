"""
MarketSentinel — AI-powered market intelligence layer.

Runs on a configurable cycle (default 5 min) and uses the sentinel LLM step
(gemini-2.5-flash primary) to analyse:
  • Price momentum and regime across all watched symbols
  • Recent signal strength from the signal scanner
  • VIX / volatility context

Outputs a SentinelResult with:
  • top_candidates: up to 3 symbols with the strongest setup
  • overall_sentiment: bullish | bearish | neutral
  • themes: list of short market narrative strings
  • risk_flags: list of caution strings
  • regime_commentary: one-liner on current regime

The sentinel result is consumed by StrategySelector and fed into the
dashboard SentinelPanel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ai_brain.ai_router import AIRouter
from ai_brain.cost_throttle import CostThrottle

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are JARVIS Market Sentinel, a quantitative market intelligence AI. "
    "Analyse the provided market data snapshot and respond ONLY in valid JSON. "
    "Be concise, data-driven, and decisive."
)

_SENTINEL_PROMPT = """
Analyse the current market state and identify actionable opportunities.

Respond with JSON matching this exact schema:
{
  "top_candidates": [
    {"symbol": "...", "direction": "long|short", "conviction": 0-100, "reason": "..."}
  ],
  "overall_sentiment": "bullish|bearish|neutral",
  "themes": ["theme1", "theme2"],
  "risk_flags": ["flag1", "flag2"],
  "regime_commentary": "one-liner on current regime"
}

Rules:
- top_candidates: exactly 0–3 symbols; include only if conviction ≥ 60
- overall_sentiment: based on regime + momentum weight of all symbols
- themes: up to 3 key market narratives (e.g. "momentum continuation", "pre-RBI caution")
- risk_flags: up to 3 caution flags (e.g. "high VIX", "OI buildup", "earnings risk")
- regime_commentary: ≤ 20 words
"""


@dataclass
class Candidate:
    symbol: str
    direction: str          # "long" | "short"
    conviction: int         # 0–100
    reason: str

    def to_dict(self) -> dict:
        return {
            "symbol":    self.symbol,
            "direction": self.direction,
            "conviction": self.conviction,
            "reason":    self.reason,
        }


@dataclass
class SentinelResult:
    top_candidates: List[Candidate]
    overall_sentiment: str          # "bullish" | "bearish" | "neutral"
    themes: List[str]
    risk_flags: List[str]
    regime_commentary: str
    model_used: str
    latency_ms: float
    cost_usd: float
    ts: float = field(default_factory=time.time)

    @property
    def candidate_symbols(self) -> List[str]:
        return [c.symbol for c in self.top_candidates]

    def to_dict(self) -> dict:
        return {
            "top_candidates":    [c.to_dict() for c in self.top_candidates],
            "overall_sentiment": self.overall_sentiment,
            "themes":            self.themes,
            "risk_flags":        self.risk_flags,
            "regime_commentary": self.regime_commentary,
            "model_used":        self.model_used,
            "latency_ms":        round(self.latency_ms, 1),
            "cost_usd":          round(self.cost_usd, 6),
            "ts":                self.ts,
        }


class MarketSentinel:
    """
    Periodically scans the entire watchlist and produces a SentinelResult.
    Safe to call concurrently — uses asyncio.Lock around result write.
    """

    def __init__(
        self,
        router: AIRouter,
        throttle: CostThrottle,
        *,
        cycle_seconds: int = 300,
    ) -> None:
        self._router       = router
        self._throttle     = throttle
        self._cycle        = cycle_seconds
        self._lock         = asyncio.Lock()
        self._last_result: Optional[SentinelResult] = None
        self._running      = False

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def last_result(self) -> Optional[SentinelResult]:
        return self._last_result

    async def run_once(self, market_snapshot: dict) -> SentinelResult:
        """Run a single sentinel scan; returns SentinelResult (rules fallback on error)."""
        t0 = time.perf_counter()
        if not self._throttle.can_call():
            logger.info("MarketSentinel: budget limit → rules fallback")
            return self._rules_fallback(market_snapshot, latency_ms=0.0)

        try:
            resp = await self._router.call(
                prompt=_SENTINEL_PROMPT,
                context=self._build_context(market_snapshot),
                mode="primary",
                step="sentinel",
            )
            result = self._parse(resp.response, resp.model_used, resp.latency_ms, resp.cost_usd)
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("MarketSentinel: LLM call failed (%s) — rules fallback", exc)
            result = self._rules_fallback(market_snapshot, latency_ms=latency_ms)

        async with self._lock:
            self._last_result = result

        logger.info(
            "MarketSentinel: sentiment=%s  candidates=%s  model=%s  cost=$%.5f",
            result.overall_sentiment,
            [c.symbol for c in result.top_candidates],
            result.model_used,
            result.cost_usd,
        )
        return result

    async def start_loop(self, get_snapshot_fn) -> None:
        """Background loop — calls get_snapshot_fn() each cycle."""
        self._running = True
        while self._running:
            try:
                snap = get_snapshot_fn()
                await self.run_once(snap)
            except Exception as exc:
                logger.error("MarketSentinel loop error: %s", exc)
            await asyncio.sleep(self._cycle)

    def stop(self) -> None:
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_context(snap: dict) -> dict:
        """Distil the market snapshot to what the LLM needs."""
        scanner = snap.get("scanner", {})
        regime  = snap.get("regime", "UNKNOWN")
        broker  = snap.get("broker", {})

        symbols_data = []
        for sym, data in scanner.items():
            symbols_data.append({
                "symbol":     sym,
                "direction":  data.get("composite_direction", "flat"),
                "confidence": round(data.get("composite_confidence", 0), 3),
                "signals":    data.get("signal_count", 0),
                "agree":      data.get("agreeing_count", 0),
                "ltp":        data.get("ltp"),
            })

        return {
            "regime":          regime,
            "symbols":         symbols_data,
            "daily_pnl":       broker.get("daily_pnl", 0),
            "kill_active":     broker.get("kill_switch_active", False),
            "open_positions":  list((broker.get("open_positions") or {}).keys()),
        }

    @staticmethod
    def _parse(
        raw: str, model_used: str, latency_ms: float, cost_usd: float
    ) -> SentinelResult:
        try:
            # Strip markdown fences
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
            data = json.loads(text)

            candidates: List[Candidate] = []
            for c in (data.get("top_candidates") or [])[:3]:
                conv = int(float(c.get("conviction", 0)))
                if conv >= 60:
                    candidates.append(Candidate(
                        symbol=str(c.get("symbol", "")),
                        direction=str(c.get("direction", "long")).lower(),
                        conviction=max(0, min(100, conv)),
                        reason=str(c.get("reason", ""))[:200],
                    ))

            sentiment = str(data.get("overall_sentiment", "neutral")).lower()
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"

            themes   = [str(t)[:100] for t in (data.get("themes") or [])[:3]]
            r_flags  = [str(f)[:100] for f in (data.get("risk_flags") or [])[:3]]
            comment  = str(data.get("regime_commentary", ""))[:120]

            return SentinelResult(
                top_candidates=candidates,
                overall_sentiment=sentiment,
                themes=themes,
                risk_flags=r_flags,
                regime_commentary=comment,
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )
        except Exception as exc:
            logger.warning("MarketSentinel: parse error (%s) — empty result", exc)
            return SentinelResult(
                top_candidates=[],
                overall_sentiment="neutral",
                themes=[],
                risk_flags=[f"parse_error: {exc}"],
                regime_commentary="",
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )

    @staticmethod
    def _rules_fallback(snap: dict, latency_ms: float) -> SentinelResult:
        """Pure-signal-based fallback when LLM is unavailable."""
        scanner = snap.get("scanner", {})
        regime  = snap.get("regime", "UNKNOWN")

        candidates: List[Candidate] = []
        for sym, data in scanner.items():
            conf = data.get("composite_confidence", 0.0)
            dirn = data.get("composite_direction", "flat")
            if dirn != "flat" and conf >= 0.65:
                candidates.append(Candidate(
                    symbol=sym,
                    direction=dirn,
                    conviction=int(conf * 100),
                    reason="Rules: high composite confidence",
                ))

        candidates.sort(key=lambda c: c.conviction, reverse=True)
        candidates = candidates[:3]

        n_long  = sum(1 for c in candidates if c.direction == "long")
        n_short = sum(1 for c in candidates if c.direction == "short")
        if n_long > n_short:
            sentiment = "bullish"
        elif n_short > n_long:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        return SentinelResult(
            top_candidates=candidates,
            overall_sentiment=sentiment,
            themes=["rules-based scan"],
            risk_flags=["LLM unavailable — budget limit"],
            regime_commentary=f"Regime: {regime} (rules fallback)",
            model_used="rules",
            latency_ms=latency_ms,
            cost_usd=0.0,
        )
