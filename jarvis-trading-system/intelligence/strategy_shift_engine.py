"""
Daily Capital Rotation Engine.

Called once per session (before market open) or immediately when the
regime classifier detects a state change.

Algorithm
---------
1. Filter strategies by supported_regimes ∩ {current_regime}.
2. Score each eligible strategy by its rolling Sharpe ratio.
3. Select top TOP_N strategies by Sharpe (to avoid dilution).
4. Allocate capital proportionally via softmax of positive Sharpes.
   — Strategies with Sharpe ≤ 0 get BOOTSTRAP_PCT of capital (enough
     to generate one minimum-size trade) so they accumulate history.
5. Cap any single strategy at MAX_STRATEGY_PCT of available capital.

Returns an AllocationResult consumed by the execution engine and
logged by IntentLogger.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from core.types import Regime

if TYPE_CHECKING:
    from strategies.base_strategy import BaseStrategy


@dataclass
class AllocationResult:
    allocations: dict[str, float]       # strategy_id → capital ₹
    ranked_strategies: list[str]        # all eligible, sorted Sharpe desc
    sharpe_scores: dict[str, float]     # strategy_id → Sharpe
    regime: Regime
    total_capital: float
    active_count: int
    excluded_count: int = 0             # strategies filtered out by regime


class StrategyShiftEngine:
    TOP_N: int = 8                      # max strategies with meaningful allocation
    MAX_STRATEGY_PCT: float = 0.25      # hard cap per strategy
    BOOTSTRAP_PCT: float = 0.02         # allocation for strategies with no edge yet
    MIN_SHARPE_THRESHOLD: float = -1.0  # strategies below this are excluded entirely

    def __init__(
        self,
        top_n: int = TOP_N,
        max_strategy_pct: float = MAX_STRATEGY_PCT,
        bootstrap_pct: float = BOOTSTRAP_PCT,
    ) -> None:
        self._top_n = top_n
        self._max_strategy_pct = max_strategy_pct
        self._bootstrap_pct = bootstrap_pct

    def compute_allocations(
        self,
        strategies: list["BaseStrategy"],
        regime: Regime,
        available_capital: float,
    ) -> AllocationResult:
        all_count = len(strategies)

        # 1. Filter by regime
        eligible = [s for s in strategies if s.is_active(regime)]
        excluded_count = all_count - len(eligible)

        if not eligible:
            return AllocationResult(
                allocations={},
                ranked_strategies=[],
                sharpe_scores={},
                regime=regime,
                total_capital=available_capital,
                active_count=0,
                excluded_count=excluded_count,
            )

        # 2. Score by Sharpe and remove deeply negative
        sharpes = {s.strategy_id: s.get_sharpe() for s in eligible}
        eligible = [
            s for s in eligible
            if sharpes[s.strategy_id] >= self.MIN_SHARPE_THRESHOLD
        ]

        # 3. Rank descending
        ranked = sorted(eligible, key=lambda s: sharpes[s.strategy_id], reverse=True)
        top = ranked[: self._top_n]

        max_alloc = available_capital * self._max_strategy_pct

        # 4. Compute allocations
        scores = np.array([max(sharpes[s.strategy_id], 0.0) for s in top])
        allocations: dict[str, float] = {}

        if scores.sum() < 1e-9:
            # All zeroes — bootstrap equal allocation
            per_strategy = min(available_capital * self._bootstrap_pct, max_alloc)
            for s in top:
                allocations[s.strategy_id] = round(per_strategy, 2)
        else:
            # Softmax (numerically stable)
            exp_scores = np.exp(scores - scores.max())
            weights = exp_scores / exp_scores.sum()
            for i, s in enumerate(top):
                alloc = min(float(weights[i]) * available_capital, max_alloc)
                if alloc < 1.0:
                    alloc = 0.0  # below ₹1 is noise
                allocations[s.strategy_id] = round(alloc, 2)

        return AllocationResult(
            allocations=allocations,
            ranked_strategies=[s.strategy_id for s in ranked],
            sharpe_scores={k: round(v, 4) for k, v in sharpes.items()},
            regime=regime,
            total_capital=available_capital,
            active_count=len(eligible),
            excluded_count=excluded_count,
        )

    def should_rotate(
        self,
        current_regime: Regime,
        new_regime: Regime,
        current_allocations: dict[str, float],
        strategies: list["BaseStrategy"],
    ) -> bool:
        """
        Returns True when a regime change warrants immediate re-allocation.
        Avoids churning on transient noise by requiring the new regime to
        activate a meaningfully different strategy set.
        """
        if current_regime == new_regime:
            return False

        current_active = {
            s.strategy_id for s in strategies if s.is_active(current_regime)
        }
        new_active = {
            s.strategy_id for s in strategies if s.is_active(new_regime)
        }
        overlap = current_active & new_active
        # Rotate if fewer than 50 % of strategies overlap
        union = current_active | new_active
        return len(overlap) / len(union) < 0.5 if union else False
