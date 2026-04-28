"""
Analyst — Layer 5 context payload builder.

Transforms a ShortlistEntry + live system state into a rich JSON context dict
and a focused LLM prompt that the DecisionEngine sends through AIRouter.

AnalystPayload
──────────────
  symbol          : str
  direction_hint  : "long" | "short" | "flat"
  context         : dict   — full structured JSON for the LLM
  prompt          : str    — the task instruction appended after context

Context schema
──────────────
  instrument      — symbol, type, exchange, ltp, ticks, last_tick_ago_s
  signals         — composite summary + per-signal breakdown
  shortlist       — final_score, base_confidence, soft_penalties
  market_context  — regime, session_pnl, available_capital, open_count,
                    kill_switch, portfolio_value
  position        — existing_qty, direction, avg_price, unrealized_pnl
  price_stats     — last_close, high_20, low_20, volatility_pct,
                    trend_direction, atr_pct
  recent_signals  — last 5 strategy signals for this symbol (side, conf, ts)

Decision prompt rules (embedded in every call)
────────────────────────────────────────────────
  - Only trade if conviction ≥ 72
  - size_pct in [0.05, 0.25] of available_capital
  - stop_loss_pct in [0.003, 0.020]
  - take_profit_pct ≥ 2 × stop_loss_pct  (minimum R:R = 2)
  - Respond ONLY with valid JSON (no markdown, no prose)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from core.types import Regime
from ai_brain.shortlister import ShortlistEntry

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_RECENT_SIGNALS_LIMIT = 5
_PRICE_STAT_WINDOW    = 20

_INSTRUMENT_TYPE: dict[tuple[bool, bool], str] = {
    (True,  False): "currency_future",
    (False, True):  "commodity_future",
    (False, False): "equity",
}

_EXCHANGE_MAP: dict[tuple[bool, bool], str] = {
    (True,  False): "NSE_CURR",
    (False, True):  "MCX_COMM",
    (False, False): "NSE",
}


# ── Payload container ─────────────────────────────────────────────────────────

@dataclass
class AnalystPayload:
    symbol: str
    direction_hint: str
    context: dict[str, Any]
    prompt: str
    entry_ref: ShortlistEntry = field(repr=False)   # keep reference for executor

    def full_message(self) -> str:
        """Return the single string sent to the LLM as the user turn."""
        ctx_json = json.dumps(self.context, indent=2, default=str)
        return f"MARKET CONTEXT:\n{ctx_json}\n\nDECISION TASK:\n{self.prompt}"

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "direction_hint": self.direction_hint,
            "context":        self.context,
            "prompt":         self.prompt,
        }


# ── Analyst ───────────────────────────────────────────────────────────────────

class Analyst:
    """
    Stateless context builder.  Call build() once per shortlisted instrument.
    """

    def build(
        self,
        entry: ShortlistEntry,
        broker_state: dict,
        regime: Regime,
        recent_signals: list[dict],
        close_history: Optional[list[float]] = None,
    ) -> AnalystPayload:
        """
        Parameters
        ----------
        entry          : ShortlistEntry from Shortlister.run()
        broker_state   : dict from broker.snapshot()  (capital, daily_pnl, …)
        regime         : current Regime enum
        recent_signals : list of signal dicts from the strategy engine
        close_history  : recent closing prices for price_stats (optional)
        """
        context = {
            "instrument":     self._instrument_block(entry),
            "signals":        self._signals_block(entry),
            "shortlist":      self._shortlist_block(entry),
            "market_context": self._market_block(broker_state, regime, entry),
            "position":       self._position_block(entry, broker_state),
            "price_stats":    self._price_stats(entry, close_history),
            "recent_signals": self._recent_signals(entry.symbol, recent_signals),
        }

        prompt = self._build_prompt(entry, broker_state)

        logger.debug(
            "Analyst payload built  sym=%s  dir_hint=%s  final_score=%.2f",
            entry.symbol, entry.direction, entry.final_score,
        )

        return AnalystPayload(
            symbol=entry.symbol,
            direction_hint=entry.direction,
            context=context,
            prompt=prompt,
            entry_ref=entry,
        )

    # ── Context blocks ────────────────────────────────────────────────────────

    @staticmethod
    def _instrument_block(entry: ShortlistEntry) -> dict:
        key = (entry.is_currency, entry.is_commodity)
        return {
            "symbol":          entry.symbol,
            "type":            _INSTRUMENT_TYPE.get(key, "equity"),
            "exchange":        _EXCHANGE_MAP.get(key, "NSE"),
            "ltp":             entry.ltp,
            "ticks":           entry.ticks,
            "last_tick_ago_s": round(entry.last_tick_ago, 1) if entry.last_tick_ago is not None else None,
        }

    @staticmethod
    def _signals_block(entry: ShortlistEntry) -> dict:
        scan = entry.scan
        return {
            "composite": {
                "direction":        scan.composite_direction,
                "confidence":       round(scan.composite_confidence, 4),
                "signal_count":     scan.signal_count,
                "agreeing_count":   scan.agreeing_count,
                "reasoning":        scan.reasoning,
            },
            "breakdown": {
                name: {
                    "direction":  sig.direction,
                    "confidence": round(sig.confidence, 4),
                    "value":      round(sig.value, 6),
                    "fired":      sig.fired,
                }
                for name, sig in scan.signals.items()
            },
        }

    @staticmethod
    def _shortlist_block(entry: ShortlistEntry) -> dict:
        return {
            "final_score":      round(entry.final_score, 4),
            "base_confidence":  round(entry.base_confidence, 4),
            "rank":             entry.rank,
            "soft_penalties":   entry.penalty_summary(),
            "total_penalty":    round(entry.total_penalty(), 4),
        }

    @staticmethod
    def _market_block(
        broker_state: dict,
        regime: Regime,
        entry: ShortlistEntry,
    ) -> dict:
        open_pos = broker_state.get("open_positions", {})
        return {
            "regime":            str(regime).replace("Regime.", ""),
            "session_pnl":       broker_state.get("daily_pnl", 0.0),
            "available_capital": broker_state.get("capital", 0.0),
            "portfolio_value":   broker_state.get("portfolio_value", 0.0),
            "open_positions":    len(open_pos),
            "kill_switch_active": broker_state.get("kill_switch_active", False),
        }

    @staticmethod
    def _position_block(entry: ShortlistEntry, broker_state: dict) -> dict:
        open_pos = broker_state.get("open_positions", {})
        pos_data = open_pos.get(entry.symbol, {})
        return {
            "existing_qty":       entry.existing_qty,
            "existing_direction": entry.existing_direction,
            "avg_price":          pos_data.get("avg_price") if pos_data else None,
            "unrealized_pnl":     pos_data.get("unrealized_pnl", 0.0) if pos_data else 0.0,
        }

    @staticmethod
    def _price_stats(
        entry: ShortlistEntry,
        close_history: Optional[list[float]],
    ) -> dict:
        ltp = entry.ltp or 0.0
        if not close_history or len(close_history) < 3:
            return {
                "last_close":      ltp,
                "high_20":         None,
                "low_20":          None,
                "volatility_pct":  None,
                "trend_direction": "unknown",
                "atr_pct":         None,
            }

        arr = np.array(close_history[-_PRICE_STAT_WINDOW:], dtype=float)
        high_20 = float(arr.max())
        low_20  = float(arr.min())
        rets    = np.diff(arr) / arr[:-1]
        vol_pct = float(rets.std()) * 100 if len(rets) > 1 else 0.0
        atr_pct = float(np.abs(rets).mean()) * 100

        # Trend: fraction of bars that moved up
        up_frac = float((rets > 0).mean())
        if up_frac > 0.60:
            trend = "up"
        elif up_frac < 0.40:
            trend = "down"
        else:
            trend = "sideways"

        return {
            "last_close":      round(ltp, 4),
            "high_20":         round(high_20, 4),
            "low_20":          round(low_20, 4),
            "volatility_pct":  round(vol_pct, 4),
            "trend_direction": trend,
            "atr_pct":         round(atr_pct, 4),
        }

    @staticmethod
    def _recent_signals(symbol: str, all_signals: list[dict]) -> list[dict]:
        sym_sigs = [
            s for s in all_signals
            if s.get("symbol") == symbol
        ][-_RECENT_SIGNALS_LIMIT:]
        return [
            {
                "side":       s.get("side"),
                "confidence": s.get("confidence"),
                "strategy":   s.get("strategy_id"),
                "ts":         str(s.get("ts", "")),
            }
            for s in sym_sigs
        ]

    # ── Prompt ────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(entry: ShortlistEntry, broker_state: dict) -> str:
        capital   = broker_state.get("capital", 0.0)
        max_size  = round(capital * 0.25, 2)
        min_size  = round(capital * 0.05, 2)
        daily_pnl = broker_state.get("daily_pnl", 0.0)
        pnl_sign  = "+" if daily_pnl >= 0 else ""

        return (
            f"You are JARVIS, an autonomous quantitative trading AI operating on Indian "
            f"financial markets (NSE / MCX).\n\n"
            f"Instrument : {entry.symbol}  ({_INSTRUMENT_TYPE.get((entry.is_currency, entry.is_commodity), 'equity')})\n"
            f"Signal hint: {entry.direction.upper()}  (final_score={entry.final_score:.0%})\n"
            f"Capital    : ₹{capital:,.0f}  |  session P&L: {pnl_sign}₹{daily_pnl:,.0f}\n\n"
            f"RULES — you MUST follow these exactly:\n"
            f"  1. Only trade (non-flat) if conviction ≥ 72.\n"
            f"  2. size_pct must be between 0.05 and 0.25 of available_capital "
            f"(₹{min_size:,.0f} – ₹{max_size:,.0f}).\n"
            f"  3. stop_loss_pct must be in [0.003, 0.020].\n"
            f"  4. take_profit_pct must be ≥ 2 × stop_loss_pct  (R:R ≥ 2:1).\n"
            f"  5. If uncertain → direction=flat, conviction=0, size_pct=0.\n"
            f"  6. Respond ONLY with valid JSON — no markdown, no prose.\n\n"
            f"Required JSON schema:\n"
            f"{{\n"
            f'  "direction":       "long" | "short" | "flat",\n'
            f'  "conviction":      0-100,\n'
            f'  "size_pct":        0.05-0.25  (fraction of available capital),\n'
            f'  "stop_loss_pct":   0.003-0.020,\n'
            f'  "take_profit_pct": 0.006-0.040  (must be ≥ 2 × stop_loss_pct),\n'
            f'  "reasoning":       "concise explanation ≤ 40 words",\n'
            f'  "risk_notes":      "any concerns or N/A"\n'
            f"}}"
        )
