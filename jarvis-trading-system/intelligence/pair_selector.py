"""
PairSelector — scores live instruments and recommends the best pairs to trade.

Scoring factors (each 0–1, weighted composite → 0–100):
  volatility    30%  — recent ATR% relative to peers; more = more opportunity
  trend         25%  — directional consistency in last 20 closes
  tick_quality  20%  — ticks per broadcast cycle (liquidity proxy)
  signal_conf   15%  — average confidence of recent signals for this symbol
  regime_fit    10%  — does current market regime suit this instrument type?
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.types import Regime


@dataclass
class InstrumentScore:
    symbol: str
    score: float
    rank: int = 0
    components: dict = field(default_factory=dict)
    reasoning: str = ""
    recommended: bool = False


class PairSelector:
    TOP_N    = 10
    SELECT_N = 3

    W_VOLATILITY  = 0.30
    W_TREND       = 0.25
    W_TICKS       = 0.20
    W_SIGNAL_CONF = 0.15
    W_REGIME_FIT  = 0.10

    def score_all(
        self,
        scanner: dict,
        close_history: dict[str, deque],
        recent_signals: list[dict],
        regime: Regime,
    ) -> list[InstrumentScore]:
        live_syms = [s for s, d in scanner.items() if d.get("status") == "live"]
        if not live_syms:
            return []

        sig_conf: dict[str, list[float]] = {}
        for sig in recent_signals:
            sym = sig.get("symbol", "")
            c   = float(sig.get("confidence") or 0.0)
            if sym in live_syms:
                sig_conf.setdefault(sym, []).append(c)

        tick_counts = {s: scanner[s].get("ticks", 0) or 0 for s in live_syms}
        max_ticks   = max(tick_counts.values(), default=1) or 1

        results: list[InstrumentScore] = []
        for sym in live_syms:
            closes   = list(close_history.get(sym, []))
            is_curr  = scanner[sym].get("is_currency", False)

            vol_score    = self._volatility(closes)
            trend_score  = self._trend_clarity(closes)
            tick_score   = tick_counts[sym] / max_ticks
            confs        = sig_conf.get(sym, [])
            sig_score    = float(np.mean(confs)) if confs else 0.5
            regime_score = self._regime_fit(is_curr, regime)

            composite = (
                self.W_VOLATILITY  * vol_score   +
                self.W_TREND       * trend_score  +
                self.W_TICKS       * tick_score   +
                self.W_SIGNAL_CONF * sig_score    +
                self.W_REGIME_FIT  * regime_score
            )
            score_100 = round(composite * 100, 1)

            results.append(InstrumentScore(
                symbol=sym,
                score=score_100,
                components={
                    "volatility":  round(vol_score,    3),
                    "trend":       round(trend_score,  3),
                    "ticks":       round(tick_score,   3),
                    "signal_conf": round(sig_score,    3),
                    "regime_fit":  round(regime_score, 3),
                },
                reasoning=self._make_reasoning(
                    score_100, vol_score, trend_score, tick_score, sig_score, regime
                ),
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:self.TOP_N]
        for i, r in enumerate(top):
            r.rank = i + 1
            r.recommended = (i < self.SELECT_N)
        return top

    def recommended_symbols(
        self,
        scanner: dict,
        close_history: dict[str, deque],
        recent_signals: list[dict],
        regime: Regime,
    ) -> set[str]:
        scores = self.score_all(scanner, close_history, recent_signals, regime)
        return {r.symbol for r in scores if r.recommended}

    # ── Component scorers ──────────────────────────────────────────────────────

    @staticmethod
    def _volatility(closes: list[float]) -> float:
        if len(closes) < 5:
            return 0.5
        arr = np.array(closes[-50:], dtype=float)
        avg = float(arr.mean())
        if avg == 0:
            return 0.0
        rets = np.abs(np.diff(arr) / arr[:-1])
        atr_pct = float(rets.mean())
        # normalise: 0% → 0, 1% → 1 (capped)
        return min(atr_pct / 0.01, 1.0)

    @staticmethod
    def _trend_clarity(closes: list[float]) -> float:
        if len(closes) < 10:
            return 0.5
        arr  = np.array(closes[-20:], dtype=float)
        diff = np.sign(np.diff(arr))
        if diff.size == 0:
            return 0.5
        ups   = float((diff > 0).mean())
        downs = float((diff < 0).mean())
        return max(ups, downs)

    @staticmethod
    def _regime_fit(is_currency: bool, regime: Regime) -> float:
        if regime == Regime.HIGH_VOL:
            return 0.9 if is_currency else 0.7
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            return 0.85 if is_currency else 0.75
        if regime == Regime.SIDEWAYS:
            return 0.4 if is_currency else 0.6
        return 0.5

    @staticmethod
    def _make_reasoning(
        score: float,
        vol: float,
        trend: float,
        ticks: float,
        conf: float,
        regime: Regime,
    ) -> str:
        parts: list[str] = []
        if vol > 0.65:
            parts.append("high volatility")
        elif vol < 0.25:
            parts.append("low volatility")
        if trend > 0.70:
            parts.append("strong directional trend")
        elif trend < 0.40:
            parts.append("choppy price action")
        if ticks > 0.85:
            parts.append("highest liquidity")
        elif ticks > 0.5:
            parts.append("good liquidity")
        if conf > 0.65:
            parts.append("strong signal history")
        regime_str = str(regime).replace("Regime.", "").replace("_", " ").lower()
        parts.append(f"{regime_str} regime")
        return f"{score:.0f}/100 — " + ("; ".join(parts) or "neutral")
