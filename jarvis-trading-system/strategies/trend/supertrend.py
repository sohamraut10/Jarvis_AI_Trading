"""
SuperTrend — 15-min trend-following.

Computes ATR-based dynamic support/resistance bands.
A trend flip (bearish→bullish or bullish→bearish) generates an entry signal.

Stop:   the current SuperTrend line (it trails the price)
Target: 3 × ATR projected from entry
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide


class SuperTrend(BaseStrategy):
    WARMUP_BARS = 15   # ATR period + buffer

    def __init__(
        self,
        strategy_id: str = "supertrend",
        period: int = 10,
        multiplier: float = 3.0,
    ) -> None:
        super().__init__(
            strategy_id=strategy_id,
            supported_regimes=[Regime.TRENDING_UP, Regime.TRENDING_DOWN, Regime.SIDEWAYS],
        )
        self.period = period
        self.multiplier = multiplier

    # ── SuperTrend calculation ─────────────────────────────────────────────────

    def _compute(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (supertrend_values, trend_direction) arrays (+1=up, -1=down)."""
        highs = self._highs()
        lows = self._lows()
        closes = self._closes()
        n = len(closes)

        atr = self._atr(highs, lows, closes, self.period)
        hl2 = (highs + lows) / 2.0
        basic_up = hl2 + self.multiplier * atr
        basic_dn = hl2 - self.multiplier * atr

        final_up = basic_up.copy()
        final_dn = basic_dn.copy()
        st = np.empty(n)
        trend = np.ones(n, dtype=int)   # 1=up, -1=down

        st[0] = basic_dn[0]
        trend[0] = 1

        for i in range(1, n):
            # Upper band: only tighten (ratchet down), reset if previous close broke above
            if closes[i - 1] > final_up[i - 1]:
                final_up[i] = basic_up[i]
            else:
                final_up[i] = min(basic_up[i], final_up[i - 1])

            # Lower band: only tighten (ratchet up), reset if previous close broke below
            if closes[i - 1] < final_dn[i - 1]:
                final_dn[i] = basic_dn[i]
            else:
                final_dn[i] = max(basic_dn[i], final_dn[i - 1])

            # Trend direction
            if st[i - 1] == final_up[i - 1]:   # was bearish
                if closes[i] > final_up[i]:
                    st[i] = final_dn[i]
                    trend[i] = 1
                else:
                    st[i] = final_up[i]
                    trend[i] = -1
            else:                               # was bullish
                if closes[i] < final_dn[i]:
                    st[i] = final_up[i]
                    trend[i] = -1
                else:
                    st[i] = final_dn[i]
                    trend[i] = 1

        return st, trend

    # ── BaseStrategy impl ──────────────────────────────────────────────────────

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push(bar)
        if not self._ready() or len(self._bars) < 2:
            return None

        st, trend = self._compute()
        cur_trend = trend[-1]
        prev_trend = trend[-2]

        if cur_trend == prev_trend:
            return None

        atr_val = self._atr(self._highs(), self._lows(), self._closes(), self.period)[-1]

        if cur_trend == 1:  # flip to bullish
            sl = round(st[-1], 2)
            tp = round(bar.close + 3.0 * atr_val, 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.BUY,
                confidence=0.70,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=f"supertrend_flip_bullish ST={st[-1]:.2f} ATR={atr_val:.2f}",
            )

        # flip to bearish
        sl = round(st[-1], 2)
        tp = round(bar.close - 3.0 * atr_val, 2)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=SignalSide.SELL,
            confidence=0.70,
            entry_price=bar.close,
            stop_loss=sl,
            take_profit=tp,
            timeframe=bar.timeframe,
            reason=f"supertrend_flip_bearish ST={st[-1]:.2f} ATR={atr_val:.2f}",
        )
