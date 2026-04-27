"""
Half-Kelly position sizer.

Formula
-------
  b  = avg_win / avg_loss          (reward-to-risk ratio)
  f* = (p * b - q) / b             (full Kelly fraction)
  f  = f* * kelly_fraction         (scaled — default 0.5 for Half-Kelly)

Where p = win_rate, q = 1 - p.

If f* ≤ 0 the edge is zero or negative: return qty=0, do not trade.

Usage
-----
  sizer = KellySizer(kelly_fraction=0.5)
  stats = StrategyStats(win_rate=0.55, avg_win=120.0, avg_loss=80.0)
  qty   = sizer.size(stats, ltp=500.0, available_capital=8000.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyStats:
    """Rolling performance metrics for one strategy, supplied by alpha_decay_monitor."""
    win_rate: float      # fraction of trades that close in profit  (0 < p < 1)
    avg_win: float       # average profit per winning trade in ₹/share (> 0)
    avg_loss: float      # average loss per losing trade in ₹/share  (> 0)
    sample_size: int = 0 # number of trades in the rolling window (0 = bootstrap)


class KellySizer:
    """
    Stateless position sizer.  All inputs are per-call so it is trivially
    thread-safe and easy to unit-test.
    """

    # Hard cap: even if Kelly says 80 % of capital, we never risk more than
    # this fraction on a single trade.  Protects against small-sample delusion.
    MAX_TRADE_FRACTION: float = 0.20   # 20 % of available capital per trade
    MIN_SAMPLE_SIZE: int = 10          # below this, fall back to min_bet_fraction
    MIN_BET_FRACTION: float = 0.02     # 2 % bootstrap allocation

    def __init__(self, kelly_fraction: float = 0.5) -> None:
        if not 0 < kelly_fraction <= 1:
            raise ValueError(f"kelly_fraction must be in (0, 1], got {kelly_fraction}")
        self.kelly_fraction = kelly_fraction

    # ── Core formula ──────────────────────────────────────────────────────────

    def raw_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Return the uncapped, unscaled Kelly fraction f*.
        Negative values mean negative edge — caller should not trade.
        """
        if avg_loss <= 0:
            raise ValueError("avg_loss must be > 0")
        if not 0 < win_rate < 1:
            raise ValueError("win_rate must be in (0, 1)")

        b = avg_win / avg_loss          # reward-to-risk
        q = 1.0 - win_rate
        return (win_rate * b - q) / b   # = win_rate - q/b

    def scaled_fraction(self, stats: StrategyStats) -> float:
        """
        Return Half-Kelly fraction clamped to [0, MAX_TRADE_FRACTION].
        Returns MIN_BET_FRACTION when sample_size < MIN_SAMPLE_SIZE.
        Returns 0.0 when edge is zero or negative.
        """
        if stats.sample_size > 0 and stats.sample_size < self.MIN_SAMPLE_SIZE:
            return self.MIN_BET_FRACTION

        fstar = self.raw_kelly(stats.win_rate, stats.avg_win, stats.avg_loss)
        if fstar <= 0:
            return 0.0

        scaled = fstar * self.kelly_fraction
        return min(scaled, self.MAX_TRADE_FRACTION)

    # ── Quantity computation ──────────────────────────────────────────────────

    def size(
        self,
        stats: StrategyStats,
        ltp: float,
        available_capital: float,
        lot_size: int = 1,
    ) -> int:
        """
        Return the number of shares (or lots) to trade.

        Parameters
        ----------
        stats             : rolling strategy performance metrics
        ltp               : last traded price of the instrument
        available_capital : cash available in the broker account
        lot_size          : minimum tradeable unit (1 for equity, N for F&O)

        Returns 0 if edge is negative, capital is insufficient, or ltp <= 0.
        """
        if ltp <= 0 or available_capital <= 0:
            return 0

        fraction = self.scaled_fraction(stats)
        if fraction <= 0:
            return 0

        capital_for_trade = available_capital * fraction
        raw_qty = capital_for_trade / ltp

        # Round down to nearest whole lot
        qty = math.floor(raw_qty / lot_size) * lot_size
        return max(qty, 0)

    # ── Introspection helper ──────────────────────────────────────────────────

    def explain(self, stats: StrategyStats, ltp: float, available_capital: float) -> dict:
        """Return a reasoning dict suitable for the intent logger."""
        fstar = self.raw_kelly(stats.win_rate, stats.avg_win, stats.avg_loss)
        fraction = self.scaled_fraction(stats)
        capital_at_risk = available_capital * fraction
        qty = self.size(stats, ltp, available_capital)
        return {
            "win_rate": stats.win_rate,
            "avg_win": stats.avg_win,
            "avg_loss": stats.avg_loss,
            "reward_to_risk": round(stats.avg_win / stats.avg_loss, 4),
            "raw_kelly_f": round(fstar, 4),
            "half_kelly_f": round(fraction, 4),
            "capital_at_risk": round(capital_at_risk, 2),
            "ltp": ltp,
            "qty": qty,
        }
