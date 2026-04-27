"""
BaseStrategy — the plugin contract every strategy must satisfy.

Lifecycle (driven by execution engine):
    bar loop  →  on_bar(bar) → Signal | None
    trade close → record_trade(pnl)
    daily rotation → get_sharpe() / get_stats()

Subclasses must:
    1. Set WARMUP_BARS class-level constant.
    2. Implement on_bar(bar), calling self._push(bar) first.
    3. Return a Signal when a setup fires, None otherwise.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np

from core.risk.kelly_sizer import StrategyStats
from core.types import Regime


# ── Signal domain types ────────────────────────────────────────────────────────

class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"   # close existing position regardless of direction


@dataclass
class Bar:
    """One closed OHLCV candle."""
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def hl2(self) -> float:
        return (self.high + self.low) / 2

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3

    def true_range(self, prev_close: Optional[float] = None) -> float:
        if prev_close is None:
            return self.high - self.low
        return max(
            self.high - self.low,
            abs(self.high - prev_close),
            abs(self.low - prev_close),
        )


@dataclass
class Signal:
    """Trading signal emitted by a strategy."""
    strategy_id: str
    symbol: str
    side: SignalSide
    confidence: float       # 0.0 – 1.0; used to weight Kelly capital
    entry_price: float      # expected fill (current close / LTP)
    stop_loss: float        # hard stop-loss price
    take_profit: float      # primary target
    timeframe: str
    reason: str             # human-readable; recorded by intent_logger
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk if risk > 0 else 0.0


# ── Base class ─────────────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    WARMUP_BARS: int = 20
    MAX_HISTORY: int = 500
    TRADE_HISTORY_LEN: int = 100

    def __init__(self, strategy_id: str, supported_regimes: list[Regime]) -> None:
        self.strategy_id = strategy_id
        self.supported_regimes = supported_regimes
        self._bars: deque[Bar] = deque(maxlen=self.MAX_HISTORY)
        self._trade_pnls: deque[float] = deque(maxlen=self.TRADE_HISTORY_LEN)
        self._bar_count: int = 0

    # ── Plugin interface ───────────────────────────────────────────────────────

    @abstractmethod
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Process one closed candle. Call self._push(bar) at the top."""

    # ── Regime filter ──────────────────────────────────────────────────────────

    def is_active(self, regime: Regime) -> bool:
        return regime in self.supported_regimes

    # ── Trade accounting ───────────────────────────────────────────────────────

    def record_trade(self, pnl: float) -> None:
        self._trade_pnls.append(pnl)

    def get_stats(self) -> StrategyStats:
        trades = list(self._trade_pnls)
        n = len(trades)
        if n == 0:
            return StrategyStats(win_rate=0.5, avg_win=100.0, avg_loss=80.0, sample_size=0)
        wins = [t for t in trades if t > 0]
        losses = [abs(t) for t in trades if t < 0]
        win_rate = max(0.01, min(0.99, len(wins) / n))
        avg_win = float(np.mean(wins)) if wins else 100.0
        avg_loss = float(np.mean(losses)) if losses else 80.0
        return StrategyStats(
            win_rate=win_rate,
            avg_win=max(1.0, avg_win),
            avg_loss=max(1.0, avg_loss),
            sample_size=n,
        )

    def get_sharpe(self, risk_free_daily: float = 0.0) -> float:
        trades = list(self._trade_pnls)
        if len(trades) < 5:
            return 0.0
        arr = np.array(trades, dtype=float)
        std = arr.std()
        if std < 1e-9:
            return 0.0
        return float((arr.mean() - risk_free_daily) / std * math.sqrt(252))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _push(self, bar: Bar) -> None:
        self._bars.append(bar)
        self._bar_count += 1

    def _ready(self) -> bool:
        return self._bar_count >= self.WARMUP_BARS

    def _closes(self) -> np.ndarray:
        return np.array([b.close for b in self._bars], dtype=float)

    def _highs(self) -> np.ndarray:
        return np.array([b.high for b in self._bars], dtype=float)

    def _lows(self) -> np.ndarray:
        return np.array([b.low for b in self._bars], dtype=float)

    def _volumes(self) -> np.ndarray:
        return np.array([b.volume for b in self._bars], dtype=float)

    @staticmethod
    def _ema(series: np.ndarray, period: int) -> np.ndarray:
        """EMA computed from scratch on full series (initial SMA seed)."""
        alpha = 2.0 / (period + 1)
        out = np.empty_like(series)
        out[0] = series[0]
        for i in range(1, len(series)):
            out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
        return out

    @staticmethod
    def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
        n = len(highs)
        tr = np.empty(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        return BaseStrategy._ema(tr, period)

    @staticmethod
    def _rsi(closes: np.ndarray, period: int) -> float:
        """Wilder RSI — returns the latest value."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0.0).mean()
        losses = np.where(deltas < 0, -deltas, 0.0).mean()
        if losses < 1e-9:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gains / losses)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.strategy_id} bars={self._bar_count}>"
