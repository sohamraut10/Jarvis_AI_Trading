"""
4-state HMM Regime Classifier.

Feature vector (7 dimensions) per observation:
    [0] bar_return      — single-bar log return
    [1] price_vs_sma    — (close − SMA20) / SMA20   (trend direction)
    [2] trend_strength  — |log(close / close_20_bars_ago)|  (magnitude)
    [3] realized_vol    — rolling 20-bar std of log returns
    [4] vix_normalized  — India VIX / 20.0  (1.0 = historical avg)
    [5] pcr             — put-call ratio (1.0 neutral, >1.2 bearish)
    [6] oi_change_pct   — aggregate OI % change (positive = buildup)

State labeling (post-fit, from learned emission means):
    highest (vol + VIX)             → HIGH_VOL
    most positive bar_return        → TRENDING_UP
    most negative bar_return        → TRENDING_DOWN
    remaining                       → SIDEWAYS

Falls back to rule-based classification when hmmlearn is not installed
or insufficient data is available for training.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from core.types import Regime

logger = logging.getLogger(__name__)

try:
    from hmmlearn.hmm import GaussianHMM
    _HMM_AVAILABLE = True
except ImportError:
    _HMM_AVAILABLE = False
    logger.warning("hmmlearn not installed — RegimeClassifier will use rule-based fallback")


# ── Feature constants ──────────────────────────────────────────────────────────
N_FEATURES = 7
IDX_RETURN, IDX_SMA, IDX_TREND, IDX_VOL, IDX_VIX, IDX_PCR, IDX_OI = range(7)

# Rule-based thresholds
_HIGH_VOL_THRESHOLD = 0.015     # realized vol (log-return std)
_HIGH_VIX_THRESHOLD = 1.5      # normalized VIX
_TREND_RETURN_THRESHOLD = 0.003 # |log return| to call it trending


class RegimeClassifier:
    def __init__(self, n_components: int = 4) -> None:
        self._n_components = n_components
        self._model: Optional[object] = None
        self._state_map: dict[int, Regime] = {}
        self._fitted: bool = False
        self._last_features: Optional[np.ndarray] = None

    # ── Feature extraction ─────────────────────────────────────────────────────

    @staticmethod
    def extract_features(
        closes: np.ndarray,
        vix: float = 20.0,
        pcr: float = 1.0,
        oi_change_pct: float = 0.0,
    ) -> Optional[np.ndarray]:
        """
        Compute the 7-dim feature vector from a 1-D close-price array.
        Requires at least 21 prices (20 for SMA + 1 current).
        Returns None when insufficient data.
        """
        if len(closes) < 21:
            return None

        log_ret = np.diff(np.log(closes))
        bar_return = float(log_ret[-1])
        sma20 = float(closes[-21:-1].mean())
        price_vs_sma = (closes[-1] - sma20) / sma20 if sma20 > 0 else 0.0
        trend_strength = abs(float(np.log(closes[-1] / closes[-21])))
        realized_vol = float(np.std(log_ret[-20:])) if len(log_ret) >= 20 else 0.01
        vix_normalized = vix / 20.0

        return np.array([
            bar_return, price_vs_sma, trend_strength,
            realized_vol, vix_normalized, pcr, oi_change_pct,
        ], dtype=float)

    # ── HMM training ──────────────────────────────────────────────────────────

    def fit(self, feature_matrix: np.ndarray) -> "RegimeClassifier":
        """
        Train GaussianHMM on (T, 7) feature matrix.
        No-op when hmmlearn is unavailable or data is insufficient.
        """
        min_obs = self._n_components * 20
        if not _HMM_AVAILABLE:
            return self

        if len(feature_matrix) < min_obs:
            logger.warning(
                "HMM training skipped: %d rows < minimum %d", len(feature_matrix), min_obs
            )
            return self

        try:
            model = GaussianHMM(
                n_components=self._n_components,
                covariance_type="full",
                n_iter=150,
                tol=1e-4,
                random_state=42,
            )
            model.fit(feature_matrix)
            self._model = model
            self._state_map = self._label_states(model.means_)
            self._fitted = True
            logger.info(
                "HMM trained: %d obs, state_map=%s",
                len(feature_matrix),
                {v: k for k, v in self._state_map.items()},
            )
        except Exception as exc:
            logger.warning("HMM training failed (%s); falling back to rule-based", exc)
            self._fitted = False
        return self

    @staticmethod
    def _label_states(means: np.ndarray) -> dict[int, Regime]:
        """
        Map state indices → Regime from learned emission means.
        Uses greedy assignment: HIGH_VOL first, then TRENDING_UP/DOWN, then SIDEWAYS.
        """
        n = means.shape[0]
        remaining = list(range(n))
        mapping: dict[int, Regime] = {}

        # HIGH_VOL: combined vol + VIX signal highest
        vol_score = means[:, IDX_VOL] + means[:, IDX_VIX] * 0.5
        hv = int(np.argmax(vol_score))
        mapping[hv] = Regime.HIGH_VOL
        remaining.remove(hv)

        # TRENDING_UP: most positive bar return among remaining
        tu = max(remaining, key=lambda s: means[s, IDX_RETURN])
        mapping[tu] = Regime.TRENDING_UP
        remaining.remove(tu)

        # TRENDING_DOWN: most negative bar return
        td = min(remaining, key=lambda s: means[s, IDX_RETURN])
        mapping[td] = Regime.TRENDING_DOWN
        remaining.remove(td)

        # SIDEWAYS: last one
        mapping[remaining[0]] = Regime.SIDEWAYS
        return mapping

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, features: np.ndarray) -> Regime:
        """Classify a single 7-dim feature vector into a Regime."""
        self._last_features = features
        if self._fitted and self._model is not None:
            try:
                state = int(self._model.predict(features.reshape(1, -1))[0])
                return self._state_map.get(state, Regime.UNKNOWN)
            except Exception as exc:
                logger.warning("HMM predict failed (%s); using rule-based", exc)
        return self._classify_rule_based(features)

    def predict_from_closes(
        self,
        closes: np.ndarray,
        vix: float = 20.0,
        pcr: float = 1.0,
        oi_change_pct: float = 0.0,
    ) -> Regime:
        """Convenience wrapper: extract features then classify."""
        features = self.extract_features(closes, vix, pcr, oi_change_pct)
        if features is None:
            return Regime.UNKNOWN
        return self.predict(features)

    def feature_dict(self) -> dict:
        """Return the last computed features as a human-readable dict (for intent logger)."""
        if self._last_features is None:
            return {}
        f = self._last_features
        return {
            "bar_return": round(float(f[IDX_RETURN]), 6),
            "price_vs_sma": round(float(f[IDX_SMA]), 4),
            "trend_strength": round(float(f[IDX_TREND]), 4),
            "realized_vol": round(float(f[IDX_VOL]), 4),
            "vix_normalized": round(float(f[IDX_VIX]), 3),
            "pcr": round(float(f[IDX_PCR]), 3),
            "oi_change_pct": round(float(f[IDX_OI]), 4),
        }

    # ── Rule-based fallback ────────────────────────────────────────────────────

    @staticmethod
    def _classify_rule_based(features: np.ndarray) -> Regime:
        bar_return = features[IDX_RETURN]
        realized_vol = features[IDX_VOL]
        vix_norm = features[IDX_VIX]

        if realized_vol > _HIGH_VOL_THRESHOLD or vix_norm > _HIGH_VIX_THRESHOLD:
            return Regime.HIGH_VOL

        if bar_return > _TREND_RETURN_THRESHOLD and realized_vol < _HIGH_VOL_THRESHOLD:
            return Regime.TRENDING_UP

        if bar_return < -_TREND_RETURN_THRESHOLD and realized_vol < _HIGH_VOL_THRESHOLD:
            return Regime.TRENDING_DOWN

        return Regime.SIDEWAYS

    @property
    def is_fitted(self) -> bool:
        return self._fitted
