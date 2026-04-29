"""
VWAP Breakout — 1-min momentum.

VWAP acts as intraday fair value.  A close above VWAP with a volume
surge signals institutional accumulation → BUY.  Below VWAP with surge
→ SELL.

VWAP resets each session.
Volume surge: current volume > vol_mult × rolling average volume.
One signal per direction per session to avoid churn.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide


class VWAPBreakout(BaseStrategy):
    WARMUP_BARS = 10

    def __init__(
        self,
        strategy_id: str = "vwap_breakout",
        vol_lookback: int = 20,
        vol_mult: float = 1.5,
    ) -> None:
        super().__init__(
            strategy_id=strategy_id,
            supported_regimes=[Regime.TRENDING_UP, Regime.HIGH_VOL, Regime.SIDEWAYS],
        )
        self._vol_lookback = vol_lookback
        self._vol_mult = vol_mult

        # Session state
        self._session_date: Optional[date] = None
        self._cum_tp_vol: float = 0.0   # Σ(hlc3 × volume)
        self._cum_vol: float = 0.0      # Σ(volume)
        self._long_fired: bool = False
        self._short_fired: bool = False

    def _reset_session(self, new_date: date) -> None:
        self._session_date = new_date
        self._cum_tp_vol = 0.0
        self._cum_vol = 0.0
        self._long_fired = False
        self._short_fired = False

    @property
    def _vwap(self) -> Optional[float]:
        if self._cum_vol < 1e-9:
            return None
        return self._cum_tp_vol / self._cum_vol

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push(bar)
        bar_date = bar.timestamp.date()

        if bar_date != self._session_date:
            self._reset_session(bar_date)

        # Accumulate VWAP
        self._cum_tp_vol += bar.hlc3 * bar.volume
        self._cum_vol += bar.volume

        if not self._ready():
            return None

        vwap = self._vwap
        if vwap is None:
            return None

        # Volume surge check
        recent_vols = self._volumes()[-self._vol_lookback:]
        avg_vol = float(recent_vols.mean()) if len(recent_vols) > 0 else 0.0
        surge = avg_vol > 0 and bar.volume > self._vol_mult * avg_vol

        if not surge:
            return None

        atr_approx = float(np.mean(
            self._highs()[-10:] - self._lows()[-10:]
        ))

        # VWAP long breakout
        if not self._long_fired and bar.close > vwap:
            self._long_fired = True
            sl = round(vwap - atr_approx, 2)
            tp = round(bar.close + 2.0 * abs(bar.close - sl), 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.BUY,
                confidence=0.60,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"vwap_long close={bar.close:.2f} > VWAP={vwap:.2f} "
                    f"vol={bar.volume:.0f} avg={avg_vol:.0f} ({self._vol_mult}×)"
                ),
            )

        # VWAP short breakout
        if not self._short_fired and bar.close < vwap:
            self._short_fired = True
            sl = round(vwap + atr_approx, 2)
            tp = round(bar.close - 2.0 * abs(sl - bar.close), 2)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=SignalSide.SELL,
                confidence=0.60,
                entry_price=bar.close,
                stop_loss=sl,
                take_profit=tp,
                timeframe=bar.timeframe,
                reason=(
                    f"vwap_short close={bar.close:.2f} < VWAP={vwap:.2f} "
                    f"vol={bar.volume:.0f} avg={avg_vol:.0f} ({self._vol_mult}×)"
                ),
            )

        return None
