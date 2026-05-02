"""
StrategySelector — pure-logic (no LLM) strategy picker.

Scores each registered strategy per symbol using a weighted formula:
  score = 0.40 × sharpe_norm + 0.30 × regime_compat + 0.30 × sentiment_align

The winner is updated after each MarketSentinel cycle.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.types import Regime

logger = logging.getLogger(__name__)

# ── Strategy → compatible regimes map ────────────────────────────────────────

_STRATEGY_REGIMES: dict[str, set[str]] = {
    "ema_crossover":   {Regime.TRENDING_UP, Regime.TRENDING_DOWN},
    "supertrend":      {Regime.TRENDING_UP, Regime.TRENDING_DOWN},
    "orb_breakout":    {Regime.TRENDING_UP, Regime.HIGH_VOL},
    "rsi_momentum":    {Regime.TRENDING_UP, Regime.TRENDING_DOWN, Regime.SIDEWAYS},
    "vwap_breakout":   {Regime.TRENDING_UP, Regime.HIGH_VOL},
    "atm_straddle":    {Regime.HIGH_VOL},
    "iron_condor":     {Regime.SIDEWAYS},
}

_DIRECTIONAL_STRATEGIES = {
    "ema_crossover", "supertrend", "orb_breakout",
    "rsi_momentum", "vwap_breakout",
}


@dataclass
class StrategyScore:
    strategy_id: str
    score: float
    sharpe_norm: float
    regime_compat: float
    sentiment_align: float
    win_rate: float
    regime: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "strategy_id":    self.strategy_id,
            "score":          round(self.score, 4),
            "sharpe_norm":    round(self.sharpe_norm, 4),
            "regime_compat":  round(self.regime_compat, 2),
            "sentiment_align": round(self.sentiment_align, 2),
            "win_rate":       round(self.win_rate, 4),
            "regime":         self.regime,
            "ts":             self.ts,
        }


@dataclass
class SelectionResult:
    symbol: str
    best_strategy: str
    scores: List[StrategyScore]
    overall_sentiment: str
    regime: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "best_strategy":    self.best_strategy,
            "scores":           [s.to_dict() for s in self.scores],
            "overall_sentiment": self.overall_sentiment,
            "regime":           self.regime,
            "ts":               self.ts,
        }


class StrategySelector:
    """
    Selects the best strategy per symbol.

    strategy_stats: {strategy_id: {"sharpe": float, "win_rate": float}}
    Strategies not in stats default to sharpe=0, win_rate=0.5.
    """

    def __init__(self, strategy_ids: List[str], window_days: int = 20) -> None:
        self._strategy_ids = strategy_ids
        self._window_days  = window_days
        self._stats: Dict[str, dict] = {}
        self._selections: Dict[str, SelectionResult] = {}

    def update_stats(self, stats: Dict[str, dict]) -> None:
        """Update rolling performance stats: {strategy_id: {sharpe, win_rate}}."""
        self._stats = stats

    def select(
        self,
        symbol: str,
        regime: Regime,
        overall_sentiment: str,
        candidate_symbols: List[str],
    ) -> SelectionResult:
        """
        Pick the best strategy for `symbol`.

        overall_sentiment: "bullish" | "bearish" | "neutral"
        candidate_symbols: symbols flagged as candidates by MarketSentinel;
                           used to boost sentiment_align for directional strategies.
        """
        sharpe_values = [
            self._stats.get(sid, {}).get("sharpe", 0.0)
            for sid in self._strategy_ids
        ]
        sharpe_max  = max(abs(v) for v in sharpe_values) or 1.0

        scores: List[StrategyScore] = []
        for sid in self._strategy_ids:
            stat    = self._stats.get(sid, {})
            sharpe  = stat.get("sharpe", 0.0)
            wr      = stat.get("win_rate", 0.50)

            sharpe_norm   = self._norm_sharpe(sharpe, sharpe_max)
            regime_compat = self._regime_compat(sid, regime)
            sent_align    = self._sentiment_align(sid, overall_sentiment, symbol in candidate_symbols)

            total = 0.40 * sharpe_norm + 0.30 * regime_compat + 0.30 * sent_align
            scores.append(StrategyScore(
                strategy_id=sid,
                score=total,
                sharpe_norm=sharpe_norm,
                regime_compat=regime_compat,
                sentiment_align=sent_align,
                win_rate=wr,
                regime=regime.value,
            ))

        scores.sort(key=lambda s: s.score, reverse=True)
        best = scores[0].strategy_id if scores else (self._strategy_ids[0] if self._strategy_ids else "unknown")

        result = SelectionResult(
            symbol=symbol,
            best_strategy=best,
            scores=scores,
            overall_sentiment=overall_sentiment,
            regime=regime.value,
        )
        self._selections[symbol] = result
        logger.info(
            "StrategySelector %s → %s  (score=%.3f  regime=%s  sent=%s)",
            symbol, best, scores[0].score if scores else 0, regime.value, overall_sentiment,
        )
        return result

    def get_selection(self, symbol: str) -> Optional[SelectionResult]:
        return self._selections.get(symbol)

    def all_selections(self) -> Dict[str, dict]:
        return {sym: sel.to_dict() for sym, sel in self._selections.items()}

    # ── Scoring helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _norm_sharpe(sharpe: float, max_abs: float) -> float:
        """Normalize to [0, 1]; negative Sharpe maps below 0.5."""
        if max_abs == 0:
            return 0.5
        return max(0.0, min(1.0, (sharpe / max_abs + 1) / 2))

    @staticmethod
    def _regime_compat(strategy_id: str, regime: Regime) -> float:
        compatible = _STRATEGY_REGIMES.get(strategy_id, set())
        return 1.0 if regime in compatible else 0.2

    @staticmethod
    def _sentiment_align(strategy_id: str, sentiment: str, is_candidate: bool) -> float:
        if strategy_id == "atm_straddle":
            return 0.8 if sentiment in ("bullish", "bearish") else 1.0
        if strategy_id == "iron_condor":
            return 1.0 if sentiment == "neutral" else 0.4
        if strategy_id in _DIRECTIONAL_STRATEGIES:
            if is_candidate:
                return 1.0 if sentiment in ("bullish", "bearish") else 0.6
            return 0.7 if sentiment == "neutral" else 0.5
        return 0.5
