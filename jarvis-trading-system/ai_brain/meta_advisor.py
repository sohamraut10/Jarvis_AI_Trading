"""
MetaAdvisor — AI self-improvement layer.

Runs every 30 minutes and reviews the last 20 decisions plus strategy win rates.
Uses the meta_advisor LLM step (claude-sonnet-4-6 primary) to produce
ParameterTweak suggestions that the operator can one-click accept.

Each ParameterTweak carries:
  parameter  — the settings key to change (e.g. "kelly_fraction")
  current    — current value
  suggested  — proposed new value
  rationale  — ≤ 150 char explanation
  confidence — 0–100 (only present suggestions with ≥ 55)

The latest batch of suggestions is stored and exposed via last_suggestions.
Accepted tweaks are applied via the provided apply_tweak_fn callback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai_brain.ai_router import AIRouter
from ai_brain.cost_throttle import CostThrottle

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 55

_SYSTEM_PROMPT = (
    "You are JARVIS MetaAdvisor, a quantitative trading system improvement AI. "
    "Analyse performance data and suggest concrete, measurable parameter improvements. "
    "Respond ONLY in valid JSON. Be specific and conservative — do not suggest extreme changes."
)

_META_PROMPT = """
Review the trading performance data and suggest parameter improvements.

Respond with JSON matching this exact schema:
{
  "suggestions": [
    {
      "parameter": "kelly_fraction",
      "current": 0.5,
      "suggested": 0.4,
      "rationale": "Win rate below 50%% — reduce Kelly to limit drawdown exposure",
      "confidence": 72
    }
  ],
  "performance_summary": "...",
  "next_review_focus": "..."
}

Rules:
- suggestions: up to 5 items; only include if confidence ≥ 55
- parameter must be one of: kelly_fraction, kill_switch_pct, sharpe_rank_window_days,
  regime_lookback_bars, hmm_states, guardian_auto_execute
- suggested values must stay within safe bounds:
    kelly_fraction: 0.10 – 1.00
    kill_switch_pct: 0.01 – 0.10
    sharpe_rank_window_days: 5 – 60
    regime_lookback_bars: 50 – 500
    hmm_states: 2 – 8
    guardian_auto_execute: true | false
- rationale: ≤ 150 chars; cite specific metrics
- performance_summary: ≤ 200 chars overview
- next_review_focus: ≤ 100 chars — what to watch next cycle
"""

_SAFE_BOUNDS: dict[str, tuple] = {
    "kelly_fraction":           (0.10, 1.00),
    "kill_switch_pct":          (0.01, 0.10),
    "sharpe_rank_window_days":  (5,    60),
    "regime_lookback_bars":     (50,   500),
    "hmm_states":               (2,    8),
}


@dataclass
class ParameterTweak:
    parameter: str
    current: Any
    suggested: Any
    rationale: str
    confidence: int
    accepted: bool = False
    accepted_at: Optional[float] = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = {
            "parameter":  self.parameter,
            "current":    self.current,
            "suggested":  self.suggested,
            "rationale":  self.rationale,
            "confidence": self.confidence,
            "accepted":   self.accepted,
            "ts":         self.ts,
        }
        if self.accepted_at is not None:
            d["accepted_at"] = self.accepted_at
        return d


@dataclass
class AdvisorResult:
    suggestions: List[ParameterTweak]
    performance_summary: str
    next_review_focus: str
    model_used: str
    latency_ms: float
    cost_usd: float
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "suggestions":        [s.to_dict() for s in self.suggestions],
            "performance_summary": self.performance_summary,
            "next_review_focus":  self.next_review_focus,
            "model_used":         self.model_used,
            "latency_ms":         round(self.latency_ms, 1),
            "cost_usd":           round(self.cost_usd, 6),
            "ts":                 self.ts,
        }


ApplyTweakFn = Callable[[str, Any], Awaitable[None]]


class MetaAdvisor:
    """
    Runs the self-improvement cycle every `cycle_seconds` (default 1800 = 30 min).

    apply_tweak_fn: async callback(parameter, value) to persist accepted tweaks.
    """

    def __init__(
        self,
        router: AIRouter,
        throttle: CostThrottle,
        *,
        apply_tweak_fn: Optional[ApplyTweakFn] = None,
        cycle_seconds: int = 1800,
    ) -> None:
        self._router        = router
        self._throttle      = throttle
        self._apply_fn      = apply_tweak_fn
        self._cycle         = cycle_seconds
        self._lock          = asyncio.Lock()
        self._last_result: Optional[AdvisorResult] = None
        self._running       = False

    # ── Public ────────────────────────────────────────────────────────────────

    @property
    def last_result(self) -> Optional[AdvisorResult]:
        return self._last_result

    def last_suggestions(self) -> List[dict]:
        if self._last_result is None:
            return []
        return [s.to_dict() for s in self._last_result.suggestions]

    async def run_once(self, performance_data: dict) -> AdvisorResult:
        """Run a single advisor cycle with the given performance data."""
        t0 = time.perf_counter()
        if not self._throttle.can_call():
            logger.info("MetaAdvisor: budget limit — skipping cycle")
            return AdvisorResult(
                suggestions=[], performance_summary="Budget limit — skipped",
                next_review_focus="Check budget status",
                model_used="skipped", latency_ms=0.0, cost_usd=0.0,
            )

        try:
            resp = await self._router.call(
                prompt=_META_PROMPT,
                context=self._build_context(performance_data),
                mode="primary",
                step="meta_advisor",
            )
            result = self._parse(resp.response, resp.model_used, resp.latency_ms, resp.cost_usd)
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("MetaAdvisor: LLM failed (%s) — empty result", exc)
            result = AdvisorResult(
                suggestions=[],
                performance_summary=f"LLM error: {exc}",
                next_review_focus="Resolve LLM connectivity",
                model_used="rules", latency_ms=latency_ms, cost_usd=0.0,
            )

        async with self._lock:
            self._last_result = result

        logger.info(
            "MetaAdvisor: %d suggestions  model=%s  cost=$%.5f",
            len(result.suggestions), result.model_used, result.cost_usd,
        )
        return result

    async def accept_suggestion(self, parameter: str) -> bool:
        """Mark a suggestion as accepted and call apply_tweak_fn."""
        async with self._lock:
            if self._last_result is None:
                return False
            for tweak in self._last_result.suggestions:
                if tweak.parameter == parameter and not tweak.accepted:
                    tweak.accepted = True
                    tweak.accepted_at = time.time()
                    if self._apply_fn is not None:
                        try:
                            await self._apply_fn(parameter, tweak.suggested)
                            logger.info(
                                "MetaAdvisor: accepted %s → %s",
                                parameter, tweak.suggested,
                            )
                        except Exception as exc:
                            logger.error("MetaAdvisor: apply_tweak failed: %s", exc)
                    return True
        return False

    async def start_loop(self, get_performance_fn) -> None:
        """Background loop — runs every cycle_seconds."""
        self._running = True
        while self._running:
            try:
                perf = get_performance_fn()
                await self.run_once(perf)
            except Exception as exc:
                logger.error("MetaAdvisor loop error: %s", exc)
            await asyncio.sleep(self._cycle)

    def stop(self) -> None:
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_context(perf: dict) -> dict:
        decisions = perf.get("recent_decisions", [])[-20:]
        condensed = [
            {
                "symbol":    d.get("symbol"),
                "direction": d.get("direction"),
                "conviction": d.get("conviction"),
                "source":    d.get("source"),
                "is_actionable": d.get("is_actionable"),
            }
            for d in decisions
        ]
        return {
            "recent_decisions":  condensed,
            "strategy_win_rates": perf.get("strategy_win_rates", {}),
            "daily_pnl":         perf.get("daily_pnl", 0),
            "total_trades":      perf.get("total_trades", 0),
            "win_rate_overall":  perf.get("win_rate_overall", 0.5),
            "current_settings": {
                "kelly_fraction":          perf.get("kelly_fraction", 0.5),
                "kill_switch_pct":         perf.get("kill_switch_pct", 0.03),
                "sharpe_rank_window_days": perf.get("sharpe_rank_window_days", 20),
                "regime_lookback_bars":    perf.get("regime_lookback_bars", 200),
                "hmm_states":              perf.get("hmm_states", 4),
                "guardian_auto_execute":   perf.get("guardian_auto_execute", False),
            },
            "regime":  perf.get("regime", "UNKNOWN"),
        }

    @staticmethod
    def _parse(
        raw: str, model_used: str, latency_ms: float, cost_usd: float
    ) -> AdvisorResult:
        try:
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
            data = json.loads(text)

            tweaks: List[ParameterTweak] = []
            allowed = set(_SAFE_BOUNDS.keys()) | {"guardian_auto_execute"}
            for item in (data.get("suggestions") or [])[:5]:
                param = str(item.get("parameter", ""))
                if param not in allowed:
                    continue
                try:
                    conf = int(float(item.get("confidence", 0)))
                except (TypeError, ValueError):
                    conf = 0
                if conf < _MIN_CONFIDENCE:
                    continue

                current   = item.get("current")
                suggested = item.get("suggested")

                # Clamp numeric params
                if param in _SAFE_BOUNDS:
                    lo, hi = _SAFE_BOUNDS[param]
                    try:
                        suggested = type(lo)(suggested)
                        suggested = max(lo, min(hi, suggested))
                    except (TypeError, ValueError):
                        continue
                elif param == "guardian_auto_execute":
                    suggested = bool(suggested)

                tweaks.append(ParameterTweak(
                    parameter=param,
                    current=current,
                    suggested=suggested,
                    rationale=str(item.get("rationale", ""))[:200],
                    confidence=max(0, min(100, conf)),
                ))

            return AdvisorResult(
                suggestions=tweaks,
                performance_summary=str(data.get("performance_summary", ""))[:300],
                next_review_focus=str(data.get("next_review_focus", ""))[:150],
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )
        except Exception as exc:
            logger.warning("MetaAdvisor: parse error (%s) — empty suggestions", exc)
            return AdvisorResult(
                suggestions=[],
                performance_summary=f"Parse error: {exc}",
                next_review_focus="Fix LLM response format",
                model_used=model_used,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            )
