"""Shared types for NSE F&O options strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import time


class OptionType(str, Enum):
    CE = "CE"   # Call
    PE = "PE"   # Put


class OptionSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


# NSE F&O lot sizes (units per lot)
NSE_LOT_SIZES: dict[str, int] = {
    "NIFTY":       50,
    "BANKNIFTY":   15,
    "FINNIFTY":    40,
    "MIDCPNIFTY":  75,
    "SENSEX":      10,
}


def get_lot_size(underlying: str) -> int:
    return NSE_LOT_SIZES.get(underlying.upper(), 1)


def atm_strike(spot: float, step: int = 50) -> int:
    """Round spot to nearest step to get ATM strike."""
    return round(spot / step) * step


def otm_strike(spot: float, option_type: OptionType, distance_pct: float = 0.02, step: int = 50) -> int:
    """Calculate OTM strike at distance_pct away from spot."""
    if option_type == OptionType.CE:
        raw = spot * (1 + distance_pct)
    else:
        raw = spot * (1 - distance_pct)
    return round(raw / step) * step


@dataclass
class OptionLeg:
    """One leg of an options strategy."""
    underlying: str              # e.g. "NIFTY"
    strike: int
    option_type: OptionType      # CE | PE
    side: OptionSide             # BUY | SELL
    lots: int                    # number of lots
    expiry: str                  # "25MAY" or full date string
    premium: float = 0.0         # filled at execution
    order_id: Optional[str] = None

    @property
    def symbol(self) -> str:
        """Construct Dhan symbol string, e.g. NIFTY25MAY24000CE."""
        return f"{self.underlying}{self.expiry}{self.strike}{self.option_type.value}"

    @property
    def qty(self) -> int:
        return self.lots * get_lot_size(self.underlying)

    def to_dict(self) -> dict:
        return {
            "underlying":  self.underlying,
            "strike":      self.strike,
            "option_type": self.option_type.value,
            "side":        self.side.value,
            "lots":        self.lots,
            "expiry":      self.expiry,
            "qty":         self.qty,
            "symbol":      self.symbol,
            "premium":     round(self.premium, 2),
        }


@dataclass
class OptionsSignal:
    """Multi-leg options signal produced by an options strategy."""
    strategy_id: str
    underlying: str
    legs: List[OptionLeg]
    max_loss: float              # maximum possible loss in ₹
    max_profit: float            # maximum possible profit in ₹ (inf for unlimited)
    breakeven_upper: Optional[float] = None
    breakeven_lower: Optional[float] = None
    reasoning: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "strategy_id":      self.strategy_id,
            "underlying":       self.underlying,
            "legs":             [l.to_dict() for l in self.legs],
            "max_loss":         round(self.max_loss, 2),
            "max_profit":       round(self.max_profit, 2) if self.max_profit != float("inf") else None,
            "breakeven_upper":  round(self.breakeven_upper, 2) if self.breakeven_upper else None,
            "breakeven_lower":  round(self.breakeven_lower, 2) if self.breakeven_lower else None,
            "reasoning":        self.reasoning,
            "ts":               self.ts,
        }
