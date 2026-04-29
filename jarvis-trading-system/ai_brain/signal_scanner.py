"""
SignalScanner — Layer 5 six-signal composite scanner.

Signals (each fires long / short / flat with a 0–1 confidence):
  1. ema_cross    — 9-EMA / 21-EMA crossover (trend direction)
  2. rsi          — RSI-14 extremes: <30 oversold (long), >70 overbought (short)
  3. volume_surge — current bar volume > 2× 20-bar average (confirms moves)
  4. atr_breakout — close breaks above/below N×ATR channel (volatility entry)
  5. momentum     — 10-bar rate-of-change; strong positive or negative move
  6. regime_align — how well the instrument class fits the current macro regime

Composite rule
--------------
Weighted vote across fired signals.  Direction wins if it holds a majority.
Composite confidence = weighted average of agreeing signal confidences.
A symbol is "scannable" once it has ≥ WARMUP_BARS bars available.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from core.types import Regime
from strategies.base_strategy import Bar

logger = logging.getLogger(__name__)

Direction = Literal["long", "short", "flat"]

WARMUP_BARS = 5    # minimum bars before any signal is computed

# Signal weights in the composite vote
_WEIGHTS: dict[str, float] = {
    "ema_cross":    0.25,
    "rsi":          0.20,
    "volume_surge": 0.15,
    "atr_breakout": 0.20,
    "momentum":     0.10,
    "regime_align": 0.10,
}


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    name: str
    direction: Direction
    confidence: float       # 0–1
    value: float            # raw indicator value (for transparency)
    fired: bool             # True when direction != "flat"


@dataclass
class ScanResult:
    symbol: str
    scannable: bool
    composite_direction: Direction
    composite_confidence: float     # 0–1
    signal_count: int               # number of non-flat signals
    agreeing_count: int             # signals that agree with composite direction
    signals: dict[str, SignalResult] = field(default_factory=dict)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol":                self.symbol,
            "scannable":             self.scannable,
            "composite_direction":   self.composite_direction,
            "composite_confidence":  round(self.composite_confidence, 4),
            "signal_count":          self.signal_count,
            "agreeing_count":        self.agreeing_count,
            "reasoning":             self.reasoning,
            "signals": {
                k: {
                    "direction":  v.direction,
                    "confidence": round(v.confidence, 4),
                    "value":      round(v.value, 6),
                    "fired":      v.fired,
                }
                for k, v in self.signals.items()
            },
        }


# ── Scanner ───────────────────────────────────────────────────────────────────

class SignalScanner:
    """
    Stateless per-call scanner.  All state lives in the Bar deques passed by
    the caller (typically JarvisEngine._bar_history).
    """

    def scan(
        self,
        symbol: str,
        bars: deque[Bar] | list[Bar],
        regime: Regime,
        is_currency: bool = False,
        is_commodity: bool = False,
    ) -> ScanResult:
        bar_list = list(bars)
        if len(bar_list) < WARMUP_BARS:
            return ScanResult(
                symbol=symbol,
                scannable=False,
                composite_direction="flat",
                composite_confidence=0.0,
                signal_count=0,
                agreeing_count=0,
                reasoning=f"insufficient bars ({len(bar_list)}/{WARMUP_BARS})",
            )

        closes  = np.array([b.close  for b in bar_list], dtype=float)
        highs   = np.array([b.high   for b in bar_list], dtype=float)
        lows    = np.array([b.low    for b in bar_list], dtype=float)
        volumes = np.array([b.volume for b in bar_list], dtype=float)

        results: dict[str, SignalResult] = {
            "ema_cross":    self._ema_cross(closes),
            "rsi":          self._rsi(closes),
            "volume_surge": self._volume_surge(closes, volumes),
            "atr_breakout": self._atr_breakout(closes, highs, lows),
            "momentum":     self._momentum(closes),
            "regime_align": self._regime_align(regime, is_currency, is_commodity),
        }

        composite_dir, composite_conf, n_fired, n_agree = self._vote(results)

        reasoning = self._make_reasoning(
            composite_dir, composite_conf, n_fired, n_agree, results, regime
        )

        return ScanResult(
            symbol=symbol,
            scannable=True,
            composite_direction=composite_dir,
            composite_confidence=composite_conf,
            signal_count=n_fired,
            agreeing_count=n_agree,
            signals=results,
            reasoning=reasoning,
        )

    def scan_all(
        self,
        bars_by_symbol: dict[str, deque[Bar] | list[Bar]],
        regime: Regime,
        scanner_meta: Optional[dict] = None,
    ) -> dict[str, ScanResult]:
        """
        Scan every symbol.  scanner_meta is the snapshot["scanner"] dict
        used to pick up is_currency / is_commodity flags.
        """
        meta = scanner_meta or {}
        out: dict[str, ScanResult] = {}
        for sym, bars in bars_by_symbol.items():
            m            = meta.get(sym, {})
            is_curr      = bool(m.get("is_currency", False))
            is_comm      = bool(m.get("is_commodity", False))
            out[sym]     = self.scan(sym, bars, regime, is_curr, is_comm)
        return out

    # ── Signal implementations ────────────────────────────────────────────────

    @staticmethod
    def _ema(arr: np.ndarray, period: int) -> np.ndarray:
        k   = 2.0 / (period + 1)
        ema = np.empty_like(arr)
        ema[0] = arr[0]
        for i in range(1, len(arr)):
            ema[i] = arr[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _ema_cross(self, closes: np.ndarray) -> SignalResult:
        fast = self._ema(closes, 9)
        slow = self._ema(closes, 21)
        diff     = fast[-1] - slow[-1]
        prev_diff = fast[-2] - slow[-2]

        # Magnitude of separation relative to price
        sep = abs(diff) / closes[-1] if closes[-1] else 0.0
        conf = min(sep / 0.003, 1.0)   # 0.3% gap → full confidence

        if diff > 0 and prev_diff <= 0:          # fresh bullish cross
            return SignalResult("ema_cross", "long",  min(conf * 1.2, 1.0), diff, True)
        if diff < 0 and prev_diff >= 0:          # fresh bearish cross
            return SignalResult("ema_cross", "short", min(conf * 1.2, 1.0), diff, True)
        if diff > 0:                             # above slow, no fresh cross
            return SignalResult("ema_cross", "long",  conf * 0.6, diff, conf > 0.3)
        if diff < 0:
            return SignalResult("ema_cross", "short", conf * 0.6, diff, conf > 0.3)
        return SignalResult("ema_cross", "flat", 0.0, diff, False)

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> SignalResult:
        if len(closes) < period + 2:
            return SignalResult("rsi", "flat", 0.0, 50.0, False)

        deltas = np.diff(closes[-(period + 2):])
        gains  = np.where(deltas > 0, deltas,  0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g  = gains.mean()
        avg_l  = losses.mean()
        if avg_l == 0:
            rsi_val = 100.0
        else:
            rs      = avg_g / avg_l
            rsi_val = 100.0 - (100.0 / (1.0 + rs))

        if rsi_val < 30:
            conf = (30 - rsi_val) / 30       # 0 = RSI at 30, 1 = RSI at 0
            return SignalResult("rsi", "long",  conf, rsi_val, True)
        if rsi_val > 70:
            conf = (rsi_val - 70) / 30
            return SignalResult("rsi", "short", conf, rsi_val, True)
        return SignalResult("rsi", "flat", 0.0, rsi_val, False)

    @staticmethod
    def _volume_surge(closes: np.ndarray, volumes: np.ndarray) -> SignalResult:
        if len(volumes) < 21:
            return SignalResult("volume_surge", "flat", 0.0, 1.0, False)

        avg_vol  = volumes[-21:-1].mean()
        cur_vol  = volumes[-1]
        ratio    = cur_vol / avg_vol if avg_vol else 1.0
        if ratio < 1.5:
            return SignalResult("volume_surge", "flat", 0.0, ratio, False)

        conf = min((ratio - 1.5) / 2.0, 1.0)
        price_move = closes[-1] - closes[-2]
        direction: Direction = "long" if price_move >= 0 else "short"
        return SignalResult("volume_surge", direction, conf, ratio, True)

    @staticmethod
    def _atr_breakout(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        period: int = 14,
        multiplier: float = 1.5,
    ) -> SignalResult:
        if len(closes) < period + 2:
            return SignalResult("atr_breakout", "flat", 0.0, 0.0, False)

        trs = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1]),
            ),
        )
        atr      = trs[-period:].mean()
        mid      = closes[-period - 1:-1].mean()
        upper    = mid + multiplier * atr
        lower    = mid - multiplier * atr
        current  = closes[-1]

        if current > upper:
            overshoot = (current - upper) / atr
            conf = min(overshoot / 0.5, 1.0)
            return SignalResult("atr_breakout", "long",  conf, current - upper, True)
        if current < lower:
            overshoot = (lower - current) / atr
            conf = min(overshoot / 0.5, 1.0)
            return SignalResult("atr_breakout", "short", conf, current - lower, True)
        return SignalResult("atr_breakout", "flat", 0.0, 0.0, False)

    @staticmethod
    def _momentum(closes: np.ndarray, period: int = 10) -> SignalResult:
        if len(closes) < period + 1:
            return SignalResult("momentum", "flat", 0.0, 0.0, False)

        roc = (closes[-1] - closes[-period - 1]) / closes[-period - 1] if closes[-period - 1] else 0.0
        abs_roc = abs(roc)
        if abs_roc < 0.003:           # < 0.3% — not meaningful
            return SignalResult("momentum", "flat", 0.0, roc, False)

        conf = min(abs_roc / 0.015, 1.0)   # 1.5% move → full confidence
        direction: Direction = "long" if roc > 0 else "short"
        return SignalResult("momentum", direction, conf, roc, True)

    @staticmethod
    def _regime_align(
        regime: Regime,
        is_currency: bool,
        is_commodity: bool,
    ) -> SignalResult:
        """
        Regime alignment isn't directional on its own — it amplifies or
        dampens confidence.  We map each regime to a 'long' bias if the
        macro environment favours the instrument class.
        """
        if regime == Regime.TRENDING_UP:
            conf = 0.8 if (is_currency or is_commodity) else 0.75
            return SignalResult("regime_align", "long",  conf, 1.0, True)
        if regime == Regime.TRENDING_DOWN:
            conf = 0.8 if (is_currency or is_commodity) else 0.75
            return SignalResult("regime_align", "short", conf, -1.0, True)
        if regime == Regime.HIGH_VOL:
            # high-vol = opportunity but no clear bias; slight short bias (risk-off)
            conf = 0.55 if is_commodity else 0.45
            return SignalResult("regime_align", "short", conf, -0.5, True)
        if regime == Regime.SIDEWAYS:
            return SignalResult("regime_align", "flat", 0.3, 0.0, False)
        return SignalResult("regime_align", "flat", 0.0, 0.0, False)

    # ── Composite vote ────────────────────────────────────────────────────────

    @staticmethod
    def _vote(
        results: dict[str, SignalResult],
    ) -> tuple[Direction, float, int, int]:
        long_w  = 0.0
        short_w = 0.0
        fired   = 0

        for name, sig in results.items():
            w = _WEIGHTS.get(name, 0.1)
            if sig.direction == "long":
                long_w  += w * sig.confidence
                if sig.fired:
                    fired += 1
            elif sig.direction == "short":
                short_w += w * sig.confidence
                if sig.fired:
                    fired += 1

        total = long_w + short_w
        if total < 0.05:
            return "flat", 0.0, fired, 0

        if long_w >= short_w:
            raw_conf = long_w / total
            conf     = min(raw_conf, 1.0)
            direction: Direction = "long"
            agree = sum(
                1 for s in results.values()
                if s.direction == "long" and s.fired
            )
        else:
            raw_conf = short_w / total
            conf     = min(raw_conf, 1.0)
            direction = "short"
            agree = sum(
                1 for s in results.values()
                if s.direction == "short" and s.fired
            )

        return direction, round(conf, 4), fired, agree

    # ── Reasoning ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_reasoning(
        direction: Direction,
        confidence: float,
        n_fired: int,
        n_agree: int,
        results: dict[str, SignalResult],
        regime: Regime,
    ) -> str:
        fired_names = [
            f"{n}({r.direction[:1].upper()})"
            for n, r in results.items()
            if r.fired
        ]
        regime_str = str(regime).replace("Regime.", "").replace("_", " ").lower()
        return (
            f"{direction.upper()} conf={confidence:.0%} "
            f"[{n_agree}/{n_fired} agree] "
            f"signals={', '.join(fired_names) or 'none'} "
            f"regime={regime_str}"
        )
