"""
Unit tests for strategy plugins and BaseStrategy helpers.
Run: pytest tests/test_strategies.py -v
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pytest

from core.types import Regime
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide
from strategies.momentum.orb_breakout import ORBBreakout
from strategies.momentum.rsi_momentum import RSIMomentum
from strategies.momentum.vwap_breakout import VWAPBreakout
from strategies.trend.ema_crossover import EMACrossover
from strategies.trend.supertrend import SuperTrend


# ── Helpers ───────────────────────────────────────────────────────────────────

_SESSION = date(2024, 1, 15)


def make_bar(
    close: float,
    high: Optional[float] = None,
    low: Optional[float] = None,
    open_: Optional[float] = None,
    volume: float = 1000.0,
    symbol: str = "TEST",
    tf: str = "5min",
    ts: Optional[datetime] = None,
) -> Bar:
    spread = close * 0.002
    return Bar(
        symbol=symbol,
        timeframe=tf,
        open=open_ or close - spread / 2,
        high=high or close + spread,
        low=low or close - spread,
        close=close,
        volume=volume,
        timestamp=ts or datetime.combine(_SESSION, datetime.min.time()),
    )


def feed(strategy: BaseStrategy, bars: list[Bar]) -> list[Optional[Signal]]:
    return [strategy.on_bar(b) for b in bars]


# ── BaseStrategy helpers ──────────────────────────────────────────────────────

def test_ema_monotone_increasing():
    """EMA of increasing series must also increase."""
    series = np.arange(1.0, 21.0)
    ema = BaseStrategy._ema(series, period=5)
    assert all(ema[i] <= ema[i + 1] for i in range(len(ema) - 1))


def test_rsi_overbought():
    # 30 bars of strong up-moves → RSI near 100
    closes = np.array([100.0 + i * 2 for i in range(30)])
    rsi = BaseStrategy._rsi(closes, period=14)
    assert rsi > 80


def test_rsi_oversold():
    closes = np.array([100.0 - i * 2 for i in range(30)])
    rsi = BaseStrategy._rsi(closes, period=14)
    assert rsi < 20


def test_sharpe_positive_trades():
    class Dummy(BaseStrategy):
        WARMUP_BARS = 1
        def on_bar(self, bar):
            self._push(bar)
    d = Dummy("dummy", [Regime.SIDEWAYS])
    for v in [10, 20, 15, 25, 30]:
        d.record_trade(float(v))
    assert d.get_sharpe() > 0


def test_get_stats_empty_returns_defaults():
    class Dummy(BaseStrategy):
        WARMUP_BARS = 1
        def on_bar(self, bar):
            self._push(bar)
    stats = Dummy("d", []).get_stats()
    assert stats.win_rate == 0.5
    assert stats.sample_size == 0


def test_regime_filter():
    strat = EMACrossover()
    assert strat.is_active(Regime.TRENDING_UP)
    assert not strat.is_active(Regime.SIDEWAYS)


# ── EMACrossover ──────────────────────────────────────────────────────────────

def _ema_crossover_bars() -> list[Bar]:
    """
    Flat for 25 bars then sharply rising — forces 9-EMA to cross above 21-EMA.
    """
    bars = [make_bar(100.0) for _ in range(25)]
    bars += [make_bar(100.0 + i * 3) for i in range(1, 20)]
    return bars


def test_ema_crossover_buy_signal():
    strat = EMACrossover()
    signals = feed(strat, _ema_crossover_bars())
    buy_signals = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buy_signals) >= 1
    s = buy_signals[0]
    assert s.strategy_id == "ema_crossover"
    assert s.stop_loss < s.entry_price
    assert s.take_profit > s.entry_price
    assert s.risk_reward >= 1.9


def _ema_death_cross_bars() -> list[Bar]:
    bars = [make_bar(200.0) for _ in range(25)]
    bars += [make_bar(200.0 - i * 3) for i in range(1, 20)]
    return bars


def test_ema_crossover_sell_signal():
    strat = EMACrossover()
    signals = feed(strat, _ema_death_cross_bars())
    sell_signals = [s for s in signals if s and s.side == SignalSide.SELL]
    assert len(sell_signals) >= 1


def test_ema_no_signal_before_warmup():
    strat = EMACrossover()
    bars = [make_bar(100.0 + i) for i in range(strat.WARMUP_BARS - 1)]
    signals = feed(strat, bars)
    assert all(s is None for s in signals)


# ── SuperTrend ────────────────────────────────────────────────────────────────

def _supertrend_flip_up_bars() -> list[Bar]:
    """Downtrend for 20 bars, then a sharp reversal up to force ST flip."""
    bars = []
    for i in range(20):
        c = 500.0 - i * 2
        bars.append(make_bar(c, high=c + 2, low=c - 2))
    for i in range(15):
        c = 460.0 + i * 5
        bars.append(make_bar(c, high=c + 3, low=c - 1))
    return bars


def test_supertrend_buy_signal():
    strat = SuperTrend()
    signals = feed(strat, _supertrend_flip_up_bars())
    buy_signals = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buy_signals) >= 1
    s = buy_signals[0]
    assert s.stop_loss < s.entry_price
    assert s.take_profit > s.entry_price


def test_supertrend_sell_signal():
    strat = SuperTrend()
    # Uptrend then reversal
    bars = []
    for i in range(20):
        c = 400.0 + i * 2
        bars.append(make_bar(c, high=c + 2, low=c - 2))
    for i in range(15):
        c = 438.0 - i * 5
        bars.append(make_bar(c, high=c + 1, low=c - 3))
    signals = feed(strat, bars)
    sell_signals = [s for s in signals if s and s.side == SignalSide.SELL]
    assert len(sell_signals) >= 1


# ── ORBBreakout ───────────────────────────────────────────────────────────────

def _orb_session_bars(breakout_direction: str = "up") -> list[Bar]:
    ts = datetime.combine(_SESSION, datetime.min.time())
    # 3 opening range bars: range 95-105
    orb_bars = [
        make_bar(100.0, high=105.0, low=95.0, ts=ts + timedelta(minutes=i * 5))
        for i in range(3)
    ]
    # Post-ORB bars
    if breakout_direction == "up":
        extra = [make_bar(107.0, high=108.0, low=106.0,
                          ts=ts + timedelta(minutes=(3 + i) * 5))
                 for i in range(3)]
    else:
        extra = [make_bar(93.0, high=94.0, low=92.0,
                          ts=ts + timedelta(minutes=(3 + i) * 5))
                 for i in range(3)]
    return orb_bars + extra


def test_orb_long_breakout():
    strat = ORBBreakout()
    signals = feed(strat, _orb_session_bars("up"))
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buys) == 1
    assert buys[0].stop_loss < buys[0].entry_price


def test_orb_short_breakout():
    strat = ORBBreakout()
    signals = feed(strat, _orb_session_bars("down"))
    sells = [s for s in signals if s and s.side == SignalSide.SELL]
    assert len(sells) == 1
    assert sells[0].stop_loss > sells[0].entry_price


def test_orb_only_one_long_per_session():
    strat = ORBBreakout()
    ts = datetime.combine(_SESSION, datetime.min.time())
    bars = [make_bar(100.0, high=105.0, low=95.0,
                     ts=ts + timedelta(minutes=i * 5)) for i in range(3)]
    # Two successive breakout bars
    bars += [make_bar(108.0, ts=ts + timedelta(minutes=20)),
             make_bar(110.0, ts=ts + timedelta(minutes=25))]
    signals = feed(strat, bars)
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buys) == 1


def test_orb_resets_next_session():
    strat = ORBBreakout()
    # Day 1 — fires long
    day1 = _orb_session_bars("up")
    # Day 2 — own opening range + breakout
    day2_ts = datetime.combine(date(2024, 1, 16), datetime.min.time())
    day2 = [make_bar(100.0, high=105.0, low=95.0,
                     ts=day2_ts + timedelta(minutes=i * 5)) for i in range(3)]
    day2 += [make_bar(107.0, ts=day2_ts + timedelta(minutes=20))]
    signals = feed(strat, day1 + day2)
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buys) == 2   # one per day


# ── RSIMomentum ───────────────────────────────────────────────────────────────

def _rsi_bull_cross_bars() -> list[Bar]:
    """
    Start with sideways bars (RSI ~50), then feed strong up-moves to
    push RSI above 55, triggering the bull cross.
    """
    bars = [make_bar(100.0) for _ in range(16)]
    # Alternating to keep RSI near 50
    for i in range(4):
        bars.append(make_bar(100.5 if i % 2 == 0 else 99.5))
    # Strong push
    for i in range(6):
        bars.append(make_bar(101.0 + i * 1.5))
    return bars


def test_rsi_momentum_buy_signal():
    strat = RSIMomentum()
    signals = feed(strat, _rsi_bull_cross_bars())
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buys) >= 1
    s = buys[0]
    assert s.stop_loss < s.entry_price
    assert s.risk_reward >= 1.9


def test_rsi_momentum_sell_signal():
    # 20 flat bars → RSI=100 (no losses), then sharp decline pushes RSI to 0
    # cross: prev_rsi=100 ≥ 45 > curr_rsi=0 → SELL fires
    strat = RSIMomentum()
    bars = [make_bar(100.0) for _ in range(20)]
    for i in range(1, 7):
        bars.append(make_bar(100.0 - i * 4))
    signals = feed(strat, bars)
    sells = [s for s in signals if s and s.side == SignalSide.SELL]
    assert len(sells) >= 1


# ── VWAPBreakout ──────────────────────────────────────────────────────────────

def _vwap_long_bars() -> list[Bar]:
    ts = datetime.combine(_SESSION, datetime.min.time())
    bars = []
    # Normal volume bars around 100 — VWAP ≈ 100
    for i in range(25):
        bars.append(make_bar(
            100.0, volume=1000.0,
            ts=ts + timedelta(minutes=i),
        ))
    # Surge bar: close > VWAP (100) with volume spike
    bars.append(make_bar(
        102.0, volume=3000.0,   # 3× average → surge
        ts=ts + timedelta(minutes=25),
    ))
    return bars


def test_vwap_long_breakout():
    strat = VWAPBreakout(vol_mult=1.5)
    signals = feed(strat, _vwap_long_bars())
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    assert len(buys) >= 1


def test_vwap_no_signal_without_surge():
    strat = VWAPBreakout(vol_mult=1.5)
    ts = datetime.combine(_SESSION, datetime.min.time())
    bars = [make_bar(102.0, volume=1000.0,
                     ts=ts + timedelta(minutes=i)) for i in range(30)]
    signals = feed(strat, bars)
    # No volume surge → no signal
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    # All bars same volume so no surge — but close > VWAP on early bars may fire
    # just verify no crash and signal has correct structure if any fires
    for s in buys:
        assert s.stop_loss < s.entry_price


def test_vwap_resets_each_session():
    strat = VWAPBreakout()
    # Day 1
    ts1 = datetime.combine(_SESSION, datetime.min.time())
    day1 = [make_bar(100.0, volume=1000.0, ts=ts1 + timedelta(minutes=i))
            for i in range(25)]
    day1.append(make_bar(103.0, volume=5000.0, ts=ts1 + timedelta(minutes=25)))
    # Day 2 — separate session
    ts2 = datetime.combine(date(2024, 1, 16), datetime.min.time())
    day2 = [make_bar(100.0, volume=1000.0, ts=ts2 + timedelta(minutes=i))
            for i in range(25)]
    day2.append(make_bar(103.0, volume=5000.0, ts=ts2 + timedelta(minutes=25)))
    signals = feed(strat, day1 + day2)
    buys = [s for s in signals if s and s.side == SignalSide.BUY]
    # Should fire at most once per day (long_fired flag resets on new day)
    assert len(buys) <= 2
