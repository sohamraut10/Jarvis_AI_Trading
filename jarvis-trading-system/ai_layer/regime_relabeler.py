"""
Post-hoc regime label correction.

After market close, examines the P&L distribution of trades executed under
each HMM-assigned regime label. When the distribution signature does not
match the label (e.g. mean-reverting gains during a period labelled
TRENDING_UP), the relabeler proposes a corrected label that can be fed
back into the next HMM refit cycle.

Correction is purely advisory — the classifier retains full authority.
Requires at least 5 closed trades per regime bucket before suggesting a change.
"""
import logging
from collections import defaultdict

import numpy as np

from core.types import Regime

logger = logging.getLogger(__name__)

_MIN_SAMPLE = 5           # minimum closed trades before offering a suggestion
_CV_HIGH_VOL = 2.0        # coefficient-of-variation threshold for HIGH_VOL
_WIN_TRENDING_UP = 0.55   # win-rate floor to suggest TRENDING_UP
_WIN_TRENDING_DOWN = 0.45  # win-rate ceiling to suggest TRENDING_DOWN


class RegimeRelabeler:
    def __init__(self, correction_threshold: float = 0.6) -> None:
        """
        correction_threshold: fraction of trades that must agree before we
        consider the current label *correct* (unused in current heuristic but
        available for callers that want a confidence gate).
        """
        self._threshold = correction_threshold
        self._corrections: dict[str, str] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def analyse(self, experiences: list[dict]) -> dict[str, str]:
        """
        Group closed trades by their recorded regime label and suggest
        corrections for any mislabelled buckets.

        Returns {original_label: suggested_label} for mismatches only.
        """
        closed = [e for e in experiences if e.get("outcome") == "closed"]
        if not closed:
            return {}

        by_regime: dict[str, list[float]] = defaultdict(list)
        for exp in closed:
            by_regime[exp.get("regime", Regime.UNKNOWN.value)].append(
                float(exp.get("pnl", 0.0))
            )

        corrections: dict[str, str] = {}
        for label, pnls in by_regime.items():
            if len(pnls) < _MIN_SAMPLE:
                continue
            suggested = self._suggest(np.array(pnls, dtype=np.float64))
            if suggested is not None and suggested != label:
                logger.info(
                    "RegimeRelabeler: %s → %s  (n=%d, μ=%.2f, σ=%.2f)",
                    label, suggested, len(pnls),
                    float(np.mean(pnls)), float(np.std(pnls)),
                )
                corrections[label] = suggested

        self._corrections = corrections
        return corrections

    def relabel(self, experiences: list[dict]) -> list[dict]:
        """
        Return a new list of experiences with corrected regime labels applied.
        Adds 'regime_corrected=True' flag to each modified record.
        """
        if not self._corrections:
            return experiences
        out = []
        for exp in experiences:
            e = dict(exp)
            orig = e.get("regime", "")
            if orig in self._corrections:
                e["regime"] = self._corrections[orig]
                e["regime_corrected"] = True
            out.append(e)
        return out

    def get_corrections(self) -> dict[str, str]:
        return dict(self._corrections)

    # ── heuristic classifier ──────────────────────────────────────────────────

    @staticmethod
    def _suggest(pnls: np.ndarray) -> str | None:
        mean = float(np.mean(pnls))
        std = float(np.std(pnls))
        win_rate = float(np.mean(pnls > 0))
        cv = std / (abs(mean) + 1e-9)

        if cv > _CV_HIGH_VOL:
            return Regime.HIGH_VOL.value
        if mean > 0 and win_rate >= _WIN_TRENDING_UP:
            return Regime.TRENDING_UP.value
        if mean < 0 and win_rate <= _WIN_TRENDING_DOWN:
            return Regime.TRENDING_DOWN.value
        return Regime.SIDEWAYS.value
