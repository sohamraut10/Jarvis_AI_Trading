"""
RSI Momentum — 5-min.

Enters on RSI crossing the momentum threshold (default 55/45).
Crossing above 55 = bulls gaining control → BUY.
Crossing below 45 = bears gaining control → SELL.

Stop:   recent swing low/high (lowest low / highest high of last 5 bars)
Target: 2:1 R:R
"""

from __future__ import annotations

from typing import Optional

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide


class RSIMomentum(BaseStrategy):
    WARMUP_BARS = 20   # RSI period + small look-back

    def __init__(
        self,
        strategy_id: str = "rsi_momentum",
        rsi_period: int = 14,
        bull_threshold: float = 55.0,
        bear_threshold: float = 45.0,
        swing_lookback: int = 5,
    ) -> None:
        super().__init__(
            strategy_id=strategy_id,
            supported_regimes=[Regime.TRENDING_UP, Regime.TRENDING_DOWN],
        )
        self._rsi_period = rsi_period
        self._bull_threshold = bull_threshold
        self._bear_threshold = bear_threshold
        self._swing_lookback = swing_lookback
        self._prev_rsi: Optional[float] = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push(bar)
        if not self._ready():
            return None

        closes = self._closes()
        curr_rsi = self._rsi(closes, self._rsi_period)

        if self._prev_rsi is None:
            self._prev_rsi = curr_rsi
            return None

        prev_rsi = self._prev_rsi
        self._prev_rsi = curr_rsi

        recent_lows = self._lows()[-self._swing_lookback:]
        recent_highs = self._highs()[-self._swing_lookback:]

        # RSI crosses above bull threshold → BUY
        if prev_rsi <= self._bull_threshold < curr_rsi:
            sl = round(float(recent_lows.min()), 2)
            risk = bar.close - sl
            if risk <= 0:
                return None
            tp = round(bar.close + 2.0 * risk, 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.BUY,
                confidence=0.62,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"rsi_cross_bull RSI {prev_rsi:.1f}→{curr_rsi:.1f} "
                    f"above threshold={self._bull_threshold}"
                ),
            )

        # RSI crosses below bear threshold → SELL
        if prev_rsi >= self._bear_threshold > curr_rsi:
            sl = round(float(recent_highs.max()), 2)
            risk = sl - bar.close
            if risk <= 0:
                return None
            tp = round(bar.close - 2.0 * risk, 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.SELL,
                confidence=0.62,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"rsi_cross_bear RSI {prev_rsi:.1f}→{curr_rsi:.1f} "
                    f"below threshold={self._bear_threshold}"
                ),
            )

        return None
