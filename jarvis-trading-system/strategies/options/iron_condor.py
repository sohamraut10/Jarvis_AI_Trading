"""
IronCondor — NSE F&O neutral options strategy.

Regime: SIDEWAYS
Setup: sell OTM CE + sell OTM PE + buy further OTM CE + buy further OTM PE.
Max profit: net premium received (when spot stays between short strikes).
Max loss: width of spread minus net premium (capped, known upfront).

Parameters
──────────
  lots              : lots per leg (default 1)
  short_otm_pct     : distance of short strikes from spot (default 1.5%)
  long_otm_pct      : distance of long strikes from spot (default 3%)
  min_net_premium   : minimum net credit to enter in ₹ (default 30)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from strategies.options.option_types import (
    OptionLeg, OptionSide, OptionType, OptionsSignal,
    atm_strike, otm_strike, get_lot_size,
)

logger = logging.getLogger(__name__)

_STRATEGY_ID = "iron_condor"


def _nearest(price: float, step: int) -> int:
    return round(price / step) * step


def _strike_step(underlying: str) -> int:
    return 100 if "BANK" in underlying.upper() else 50


@dataclass
class IronCondor:
    """
    Iron condor signal generator.

    Usage:
        signal = condor.generate(
            underlying="NIFTY", spot=24000, expiry="25MAY",
            short_ce_premium=80, short_pe_premium=75,
            long_ce_premium=30, long_pe_premium=28,
        )
    """
    lots: int = 1
    short_otm_pct: float = 0.015    # 1.5%
    long_otm_pct:  float = 0.030    # 3.0%
    min_net_premium: float = 30.0

    def generate(
        self,
        underlying: str,
        spot: float,
        expiry: str,
        short_ce_premium: float,
        short_pe_premium: float,
        long_ce_premium: float,
        long_pe_premium: float,
    ) -> Optional[OptionsSignal]:
        """Generate an iron condor signal."""
        net_premium = (short_ce_premium + short_pe_premium
                       - long_ce_premium - long_pe_premium)

        if net_premium < self.min_net_premium:
            logger.debug(
                "IronCondor: net premium %.2f < min %.2f — skip",
                net_premium, self.min_net_premium,
            )
            return None

        step      = _strike_step(underlying)
        lot_sz    = get_lot_size(underlying)
        total_qty = self.lots * lot_sz

        short_ce_strike = _nearest(spot * (1 + self.short_otm_pct), step)
        short_pe_strike = _nearest(spot * (1 - self.short_otm_pct), step)
        long_ce_strike  = _nearest(spot * (1 + self.long_otm_pct),  step)
        long_pe_strike  = _nearest(spot * (1 - self.long_otm_pct),  step)

        spread_width    = (long_ce_strike - short_ce_strike) * lot_sz * self.lots
        max_loss        = spread_width - net_premium * total_qty
        max_profit      = net_premium * total_qty

        be_upper = short_ce_strike + net_premium
        be_lower = short_pe_strike - net_premium

        legs = [
            # Sell OTM CE
            OptionLeg(
                underlying=underlying, strike=short_ce_strike,
                option_type=OptionType.CE, side=OptionSide.SELL,
                lots=self.lots, expiry=expiry, premium=short_ce_premium,
            ),
            # Buy further OTM CE (cap upside loss)
            OptionLeg(
                underlying=underlying, strike=long_ce_strike,
                option_type=OptionType.CE, side=OptionSide.BUY,
                lots=self.lots, expiry=expiry, premium=long_ce_premium,
            ),
            # Sell OTM PE
            OptionLeg(
                underlying=underlying, strike=short_pe_strike,
                option_type=OptionType.PE, side=OptionSide.SELL,
                lots=self.lots, expiry=expiry, premium=short_pe_premium,
            ),
            # Buy further OTM PE (cap downside loss)
            OptionLeg(
                underlying=underlying, strike=long_pe_strike,
                option_type=OptionType.PE, side=OptionSide.BUY,
                lots=self.lots, expiry=expiry, premium=long_pe_premium,
            ),
        ]

        reasoning = (
            f"IRON CONDOR {underlying} {expiry}: "
            f"short {short_pe_strike}P/{short_ce_strike}C  "
            f"long {long_pe_strike}P/{long_ce_strike}C  "
            f"net=₹{net_premium:.0f}  "
            f"BE={be_lower:.0f}–{be_upper:.0f}"
        )

        logger.info(reasoning)
        return OptionsSignal(
            strategy_id=_STRATEGY_ID,
            underlying=underlying,
            legs=legs,
            max_loss=max_loss,
            max_profit=max_profit,
            breakeven_upper=be_upper,
            breakeven_lower=be_lower,
            reasoning=reasoning,
        )

    def is_profitable_zone(self, spot: float, signal: OptionsSignal) -> bool:
        """True if spot is inside the breakeven range (profitable zone)."""
        lo = signal.breakeven_lower
        hi = signal.breakeven_upper
        if lo is None or hi is None:
            return True
        return lo <= spot <= hi
