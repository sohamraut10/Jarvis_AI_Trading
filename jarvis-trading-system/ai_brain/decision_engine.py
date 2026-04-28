"""
DecisionEngine — Layer 5 conviction gate and LLM orchestrator.

Responsibilities
────────────────
1. Check CostThrottle.can_call() — route to rules-only fallback if exhausted.
2. Hash the AnalystPayload prompt and check the 45-second response cache.
3. Call AIRouter (primary / fallback / consensus) with the full context message.
4. Parse and strictly validate the JSON response from the LLM.
5. Enforce the conviction threshold (≥ 72 → trade, < 72 → flat override).
6. Clamp all numeric fields to safe ranges; guarantee R:R ≥ 2:1.
7. Return a Decision dataclass; log cost and source.

Decision sources
────────────────
  "ai"       — live LLM call (Claude or GPT-4o)
  "cache"    — served from CostThrottle's 45-second cache
  "rules"    — budget exhausted or parse failure; derived from signal scanner

Rules-only conviction proxy
───────────────────────────
  conviction_proxy = int(composite_confidence × 100)
  Trades only when proxy ≥ CONVICTION_THRESHOLD.
  Uses conservative defaults: size_pct=0.05, SL=1%, TP=2%.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from ai_brain.ai_router import AIRouter
from ai_brain.cost_throttle import CostThrottle
from ai_brain.analyst import AnalystPayload

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CONVICTION_THRESHOLD = 72

_SIZE_MIN   = 0.05
_SIZE_MAX   = 0.25
_SL_MIN     = 0.003
_SL_MAX     = 0.020
_TP_FACTOR  = 2.0          # take_profit ≥ TP_FACTOR × stop_loss
_TP_MAX     = 0.040

_RULES_SIZE_PCT = 0.05
_RULES_SL_PCT   = 0.010
_RULES_TP_PCT   = 0.020

Source = Literal["ai", "cache", "rules"]


# ── Decision dataclass ────────────────────────────────────────────────────────

@dataclass
class Decision:
    symbol: str
    direction: Literal["long", "short", "flat"]
    conviction: int                  # 0–100
    size_pct: float                  # fraction of available capital
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str
    risk_notes: str
    source: Source                   # "ai" | "cache" | "rules"
    model_used: str
    latency_ms: float
    cost_usd: float
    consensus: bool
    ts: float = field(default_factory=time.time)

    # Derived helpers
    @property
    def is_actionable(self) -> bool:
        return self.direction in ("long", "short") and self.conviction >= CONVICTION_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "direction":       self.direction,
            "conviction":      self.conviction,
            "size_pct":        round(self.size_pct, 4),
            "stop_loss_pct":   round(self.stop_loss_pct, 4),
            "take_profit_pct": round(self.take_profit_pct, 4),
            "reasoning":       self.reasoning,
            "risk_notes":      self.risk_notes,
            "source":          self.source,
            "model_used":      self.model_used,
            "latency_ms":      round(self.latency_ms, 1),
            "cost_usd":        round(self.cost_usd, 6),
            "consensus":       self.consensus,
            "is_actionable":   self.is_actionable,
            "ts":              self.ts,
        }


# ── DecisionEngine ────────────────────────────────────────────────────────────

class DecisionEngine:

    def __init__(
        self,
        router: AIRouter,
        throttle: CostThrottle,
        call_mode: Literal["primary", "fallback", "consensus"] = "primary",
    ) -> None:
        self._router   = router
        self._throttle = throttle
        self._mode     = call_mode

    # ── Public ────────────────────────────────────────────────────────────────

    async def decide(self, payload: AnalystPayload) -> Decision:
        """
        Make a trade decision for the given AnalystPayload.
        Always returns a Decision; never raises (falls back to rules on error).
        """
        sym = payload.symbol
        t0  = time.perf_counter()

        # ── Rules-only mode (budget exhausted) ───────────────────────────────
        if not self._throttle.can_call():
            logger.info("DecisionEngine: rules-only mode for %s (budget limit)", sym)
            return self._rules_decision(payload, latency_ms=0.0)

        # ── Cache check ───────────────────────────────────────────────────────
        prompt_text = payload.full_message()
        cache_key   = CostThrottle.make_key(prompt_text)
        cached      = self._throttle.get_cached(cache_key)
        if cached is not None:
            logger.debug("DecisionEngine: cache HIT for %s", sym)
            return self._parse_to_decision(
                cached.response, sym,
                model_used=cached.model_used,
                latency_ms=0.0,
                cost_usd=0.0,
                consensus=False,
                source="cache",
                payload=payload,
            )

        # ── Live LLM call ─────────────────────────────────────────────────────
        try:
            resp = await self._router.call(
                prompt=payload.full_message(),
                context={},          # context already embedded in prompt string
                mode=self._mode,
            )
            latency_ms = resp.latency_ms

            # Record spend + cache result
            await self._throttle.record(
                cache_key,
                resp.response,
                resp.direction,
                resp.model_used,
                resp.cost_usd,
            )

            return self._parse_to_decision(
                resp.response, sym,
                model_used=resp.model_used,
                latency_ms=latency_ms,
                cost_usd=resp.cost_usd,
                consensus=resp.consensus,
                source="ai",
                payload=payload,
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.warning(
                "DecisionEngine: LLM call failed for %s (%s) — falling back to rules",
                sym, exc,
            )
            return self._rules_decision(payload, latency_ms=elapsed_ms)

    # ── Parse + validate LLM response ────────────────────────────────────────

    def _parse_to_decision(
        self,
        raw_text: str,
        symbol: str,
        *,
        model_used: str,
        latency_ms: float,
        cost_usd: float,
        consensus: bool,
        source: Source,
        payload: AnalystPayload,
    ) -> Decision:
        try:
            data = self._extract_json(raw_text)
            return self._validated_decision(
                data, symbol,
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                consensus=consensus,
                source=source,
            )
        except Exception as exc:
            logger.warning(
                "DecisionEngine: JSON parse/validation failed for %s (%s) — rules fallback",
                symbol, exc,
            )
            return self._rules_decision(payload, latency_ms=latency_ms, cost_usd=cost_usd)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from raw LLM output, stripping markdown fences if present."""
        stripped = text.strip()
        # Strip ```json ... ``` or ``` ... ```
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            inner = "\n".join(
                l for l in lines
                if not l.strip().startswith("```")
            )
            stripped = inner.strip()
        return json.loads(stripped)

    @staticmethod
    def _validated_decision(
        data: dict[str, Any],
        symbol: str,
        *,
        model_used: str,
        latency_ms: float,
        cost_usd: float,
        consensus: bool,
        source: Source,
    ) -> Decision:
        # ── direction ─────────────────────────────────────────────────────────
        raw_dir = str(data.get("direction", "flat")).lower().strip()
        if raw_dir not in ("long", "short", "flat"):
            logger.warning("Invalid direction %r — defaulting to flat", raw_dir)
            raw_dir = "flat"

        # ── conviction ────────────────────────────────────────────────────────
        try:
            conviction = int(float(data.get("conviction", 0)))
        except (TypeError, ValueError):
            conviction = 0
        conviction = max(0, min(100, conviction))

        # Apply conviction gate
        if conviction < CONVICTION_THRESHOLD:
            logger.info(
                "conviction %d < threshold %d for %s — overriding to flat",
                conviction, CONVICTION_THRESHOLD, symbol,
            )
            raw_dir    = "flat"
            size_pct   = 0.0
            sl_pct     = _SL_MIN
            tp_pct     = _SL_MIN * _TP_FACTOR
        else:
            # ── size_pct ──────────────────────────────────────────────────────
            try:
                size_pct = float(data.get("size_pct", _SIZE_MIN))
            except (TypeError, ValueError):
                size_pct = _SIZE_MIN
            if raw_dir == "flat":
                size_pct = 0.0
            else:
                size_pct = max(_SIZE_MIN, min(_SIZE_MAX, size_pct))

            # ── stop_loss_pct ─────────────────────────────────────────────────
            try:
                sl_pct = float(data.get("stop_loss_pct", _SL_MIN))
            except (TypeError, ValueError):
                sl_pct = _SL_MIN
            sl_pct = max(_SL_MIN, min(_SL_MAX, sl_pct))

            # ── take_profit_pct ───────────────────────────────────────────────
            try:
                tp_pct = float(data.get("take_profit_pct", sl_pct * _TP_FACTOR))
            except (TypeError, ValueError):
                tp_pct = sl_pct * _TP_FACTOR
            tp_pct = max(sl_pct * _TP_FACTOR, min(_TP_MAX, tp_pct))

        reasoning  = str(data.get("reasoning",  ""))[:200]
        risk_notes = str(data.get("risk_notes", "N/A"))[:200]

        dec = Decision(
            symbol=symbol,
            direction=raw_dir,  # type: ignore[arg-type]
            conviction=conviction,
            size_pct=size_pct,
            stop_loss_pct=sl_pct,
            take_profit_pct=tp_pct,
            reasoning=reasoning,
            risk_notes=risk_notes,
            source=source,
            model_used=model_used,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            consensus=consensus,
        )

        logger.info(
            "Decision %-8s  dir=%-5s  conv=%3d  size=%.0f%%  sl=%.2f%%  tp=%.2f%%  "
            "src=%-5s  model=%s  cost=$%.5f",
            symbol, dec.direction, dec.conviction,
            dec.size_pct * 100, dec.stop_loss_pct * 100, dec.take_profit_pct * 100,
            dec.source, dec.model_used, dec.cost_usd,
        )
        return dec

    # ── Rules-only fallback ───────────────────────────────────────────────────

    @staticmethod
    def _rules_decision(
        payload: AnalystPayload,
        latency_ms: float,
        cost_usd: float = 0.0,
    ) -> Decision:
        scan        = payload.entry_ref.scan
        proxy       = int(scan.composite_confidence * 100)
        direction   = scan.composite_direction if proxy >= CONVICTION_THRESHOLD else "flat"
        size_pct    = _RULES_SIZE_PCT if direction != "flat" else 0.0

        logger.info(
            "Rules decision %s  proxy=%d  dir=%s",
            payload.symbol, proxy, direction,
        )

        return Decision(
            symbol=payload.symbol,
            direction=direction,  # type: ignore[arg-type]
            conviction=proxy,
            size_pct=size_pct,
            stop_loss_pct=_RULES_SL_PCT,
            take_profit_pct=_RULES_TP_PCT,
            reasoning=(
                f"Rules-only: {scan.reasoning}"
            ),
            risk_notes="Budget-limited — conservative sizing applied",
            source="rules",
            model_used="rules",
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            consensus=False,
        )
