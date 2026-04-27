"""
EMA Crossover — 5-min trend-following.

Entry:  Golden cross  (fast EMA crosses above slow EMA) → BUY
        Death cross   (fast EMA crosses below slow EMA) → SELL
Stop:   1.5 × bar range beyond entry candle
Target: 2:1 reward-to-risk from stop distance
"""

from __future__ import annotations

from typing import Optional

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide


class EMACrossover(BaseStrategy):
    WARMUP_BARS = 22   # slow_period (21) + 1 buffer

    def __init__(
        self,
        strategy_id: str = "ema_crossover",
        fast_period: int = 9,
        slow_period: int = 21,
    ) -> None:
        super().__init__(
            strategy_id=strategy_id,
            supported_regimes=[Regime.TRENDING_UP, Regime.TRENDING_DOWN],
        )
        self.fast_period = fast_period
        self.slow_period = slow_period

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push(bar)
        if not self._ready() or len(self._bars) < 2:
            return None

        closes = self._closes()
        fast = self._ema(closes, self.fast_period)
        slow = self._ema(closes, self.slow_period)

        cf, cs = fast[-1], slow[-1]   # current
        pf, ps = fast[-2], slow[-2]   # previous

        bar_range = max(bar.high - bar.low, 1e-4)

        # Golden cross → BUY
        if pf <= ps and cf > cs:
            sl = bar.low - 1.5 * bar_range
            tp = bar.close + 2.0 * abs(bar.close - sl)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.BUY,
                confidence=0.65,
                entry_price=bar.close,
                stop_loss=round(sl, 2),
                take_profit=round(tp, 2),
                timeframe=bar.timeframe,
                reason=(
                    f"golden_cross EMA{self.fast_period}={cf:.2f} "
                    f"crossed above EMA{self.slow_period}={cs:.2f}"
                ),
            )

        # Death cross → SELL
        if pf >= ps and cf < cs:
            sl = bar.high + 1.5 * bar_range
            tp = bar.close - 2.0 * abs(sl - bar.close)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.SELL,
                confidence=0.65,
                entry_price=bar.close,
                stop_loss=round(sl, 2),
                take_profit=round(tp, 2),
                timeframe=bar.timeframe,
                reason=(
                    f"death_cross EMA{self.fast_period}={cf:.2f} "
                    f"crossed below EMA{self.slow_period}={cs:.2f}"
                ),
            )

        return None
