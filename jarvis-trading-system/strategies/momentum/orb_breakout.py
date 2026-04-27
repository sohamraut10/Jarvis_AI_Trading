"""
Opening Range Breakout (ORB) — 5-min momentum.

Opening range = first ORB_BARS candles of the session (default 3 = 15 min).
Entry:   BUY  when close breaks above ORB high + buffer
         SELL when close breaks below ORB low  − buffer
Stop:    opposite edge of the opening range
Target:  range_size × 2 projected from entry
One signal per direction per session.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide


class ORBBreakout(BaseStrategy):
    WARMUP_BARS = 3   # we need the opening range before we can signal

    def __init__(
        self,
        strategy_id: str = "orb_breakout",
        orb_bars: int = 3,
        buffer_pct: float = 0.001,   # 0.1 % above/below range
    ) -> None:
        super().__init__(
            strategy_id=strategy_id,
            supported_regimes=[Regime.TRENDING_UP, Regime.TRENDING_DOWN, Regime.HIGH_VOL],
        )
        self._orb_bars = orb_bars
        self._buffer_pct = buffer_pct

        # Session state — reset each new trading day
        self._session_date: Optional[date] = None
        self._session_highs: list[float] = []
        self._session_lows: list[float] = []
        self._orb_high: Optional[float] = None
        self._orb_low: Optional[float] = None
        self._long_fired: bool = False
        self._short_fired: bool = False

    def _reset_session(self, new_date: date) -> None:
        self._session_date = new_date
        self._session_highs = []
        self._session_lows = []
        self._orb_high = None
        self._orb_low = None
        self._long_fired = False
        self._short_fired = False

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push(bar)
        bar_date = bar.timestamp.date()

        # New session detected → reset state
        if bar_date != self._session_date:
            self._reset_session(bar_date)

        # Accumulate opening range bars
        if self._orb_high is None:
            self._session_highs.append(bar.high)
            self._session_lows.append(bar.low)

            if len(self._session_highs) >= self._orb_bars:
                self._orb_high = max(self._session_highs)
                self._orb_low = min(self._session_lows)
            return None  # still building the range

        # Opening range established — check for breakout
        orb_range = self._orb_high - self._orb_low
        buf = bar.close * self._buffer_pct

        # Long breakout
        if not self._long_fired and bar.close > self._orb_high + buf:
            self._long_fired = True
            sl = round(self._orb_low, 2)
            tp = round(bar.close + 2.0 * orb_range, 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.BUY,
                confidence=0.68,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"orb_long_breakout close={bar.close:.2f} "
                    f"above orb_high={self._orb_high:.2f} range={orb_range:.2f}"
                ),
            )

        # Short breakout
        if not self._short_fired and bar.close < self._orb_low - buf:
            self._short_fired = True
            sl = round(self._orb_high, 2)
            tp = round(bar.close - 2.0 * orb_range, 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.SELL,
                confidence=0.68,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"orb_short_breakout close={bar.close:.2f} "
                    f"below orb_low={self._orb_low:.2f} range={orb_range:.2f}"
                ),
            )

        return None
