"""
Alpha Decay Monitor.

Detects when a strategy's statistical edge is eroding by comparing
a short rolling window against a long rolling window (similar to a
Sharpe-based MACD).  Also flags consecutive loss streaks.

Decay severity tiers:
    NO_DATA   — fewer than SHORT_WINDOW trades recorded
    HEALTHY   — short Sharpe ≥ WARNING_RATIO × long Sharpe
    WARNING   — short Sharpe between DEGRADED_RATIO and WARNING_RATIO
    DEGRADED  — short Sharpe < DEGRADED_RATIO × long Sharpe OR
                consecutive losses ≥ MAX_CONSEC_LOSSES
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np


class DecaySeverity(str, Enum):
    NO_DATA = "NO_DATA"
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"


@dataclass
class DecayStatus:
    strategy_id: str
    severity: DecaySeverity
    short_sharpe: float
    long_sharpe: float
    win_rate_short: float
    win_rate_long: float
    consecutive_losses: int
    reason: str

    @property
    def is_decaying(self) -> bool:
        return self.severity in (DecaySeverity.WARNING, DecaySeverity.DEGRADED)


class AlphaDecayMonitor:
    SHORT_WINDOW: int = 10
    LONG_WINDOW: int = 50
    WARNING_RATIO: float = 0.70     # short/long Sharpe below this → WARNING
    DEGRADED_RATIO: float = 0.30    # short/long Sharpe below this → DEGRADED
    MAX_CONSEC_LOSSES: int = 5      # streak ≥ this → DEGRADED regardless

    def __init__(self) -> None:
        self._trades: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.LONG_WINDOW)
        )
        self._consecutive_losses: dict[str, int] = defaultdict(int)

    # ── Public API ─────────────────────────────────────────────────────────────

    def update(self, strategy_id: str, pnl: float) -> None:
        """Record a closed trade P&L for a strategy."""
        self._trades[strategy_id].append(pnl)
        if pnl < 0:
            self._consecutive_losses[strategy_id] += 1
        else:
            self._consecutive_losses[strategy_id] = 0

    def check_decay(self, strategy_id: str) -> DecayStatus:
        """Return current decay status for a strategy."""
        trades = list(self._trades.get(strategy_id, []))
        n = len(trades)
        consec = self._consecutive_losses.get(strategy_id, 0)

        if n < self.SHORT_WINDOW:
            return DecayStatus(
                strategy_id=strategy_id,
                severity=DecaySeverity.NO_DATA,
                short_sharpe=0.0,
                long_sharpe=0.0,
                win_rate_short=0.0,
                win_rate_long=0.0,
                consecutive_losses=consec,
                reason=f"insufficient_data ({n}/{self.SHORT_WINDOW} trades)",
            )

        short_trades = trades[-self.SHORT_WINDOW:]
        long_trades = trades  # up to LONG_WINDOW

        short_sharpe = self._sharpe(short_trades)
        long_sharpe = self._sharpe(long_trades)
        short_wr = sum(1 for t in short_trades if t > 0) / len(short_trades)
        long_wr = sum(1 for t in long_trades if t > 0) / len(long_trades)

        # Consecutive loss streak → immediate DEGRADED
        if consec >= self.MAX_CONSEC_LOSSES:
            return DecayStatus(
                strategy_id=strategy_id,
                severity=DecaySeverity.DEGRADED,
                short_sharpe=short_sharpe,
                long_sharpe=long_sharpe,
                win_rate_short=short_wr,
                win_rate_long=long_wr,
                consecutive_losses=consec,
                reason=f"consecutive_loss_streak={consec}",
            )

        # Compare short vs long Sharpe
        if long_sharpe > 1e-9 and short_sharpe > 1e-9:
            ratio = short_sharpe / long_sharpe
            if ratio < self.DEGRADED_RATIO:
                severity = DecaySeverity.DEGRADED
                reason = f"sharpe_ratio={ratio:.2f} < degraded_threshold={self.DEGRADED_RATIO}"
            elif ratio < self.WARNING_RATIO:
                severity = DecaySeverity.WARNING
                reason = f"sharpe_ratio={ratio:.2f} < warning_threshold={self.WARNING_RATIO}"
            else:
                severity = DecaySeverity.HEALTHY
                reason = f"sharpe_ratio={ratio:.2f} ≥ {self.WARNING_RATIO}"
        elif short_sharpe < 0 < long_sharpe:
            severity = DecaySeverity.DEGRADED
            reason = f"short_sharpe={short_sharpe:.3f} negative while long_sharpe={long_sharpe:.3f} positive"
        elif short_sharpe < long_sharpe:
            severity = DecaySeverity.WARNING
            reason = f"short_sharpe={short_sharpe:.3f} < long_sharpe={long_sharpe:.3f}"
        else:
            severity = DecaySeverity.HEALTHY
            reason = "stable_or_improving"

        return DecayStatus(
            strategy_id=strategy_id,
            severity=severity,
            short_sharpe=round(short_sharpe, 4),
            long_sharpe=round(long_sharpe, 4),
            win_rate_short=round(short_wr, 3),
            win_rate_long=round(long_wr, 3),
            consecutive_losses=consec,
            reason=reason,
        )

    def get_all_statuses(self) -> dict[str, DecayStatus]:
        """Return current decay status for every tracked strategy."""
        return {sid: self.check_decay(sid) for sid in self._trades}

    def reset(self, strategy_id: str) -> None:
        """Clear history for a strategy (e.g. after parameter re-fit)."""
        self._trades.pop(strategy_id, None)
        self._consecutive_losses.pop(strategy_id, None)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _sharpe(trades: list[float]) -> float:
        if len(trades) < 2:
            return 0.0
        arr = np.array(trades, dtype=float)
        std = arr.std()
        if std < 1e-9:
            return 0.0
        return float(arr.mean() / std)
