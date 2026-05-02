"""
ATMStraddle — short straddle on NSE F&O.

Regime: HIGH_VOL
Setup: sell ATM CE + sell ATM PE simultaneously.
Max profit: combined premium received (if spot expires at ATM).
Max loss: unlimited theoretically; managed via stop on underlying.

Parameters
──────────
  lots          : number of lots per leg (default 1)
  iv_threshold  : minimum implied volatility % to enter (default 20)
  premium_target: minimum combined premium in ₹ to enter (default 100)
  stop_loss_pct : underlying move % that triggers exit (default 2%)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

from strategies.options.option_types import (
    OptionLeg, OptionSide, OptionType, OptionsSignal,
    atm_strike, get_lot_size,
)

logger = logging.getLogger(__name__)

_STRATEGY_ID  = "atm_straddle"
_NIFTY_STEP   = 50     # NIFTY strikes move in 50-point steps
_BANKNIFTY_STEP = 100


def _strike_step(underlying: str) -> int:
    return _BANKNIFTY_STEP if "BANK" in underlying.upper() else _NIFTY_STEP


@dataclass
class ATMStraddle:
    """
    ATM short straddle signal generator.

    Usage:
        signal = straddle.generate(underlying="NIFTY", spot=24150, expiry="25MAY",
                                   ce_premium=180, pe_premium=175)
    """
    lots: int = 1
    iv_threshold: float = 20.0
    premium_target: float = 100.0
    stop_loss_pct: float = 0.02

    def generate(
        self,
        underlying: str,
        spot: float,
        expiry: str,
        ce_premium: float,
        pe_premium: float,
        iv_pct: float = 0.0,
    ) -> Optional[OptionsSignal]:
        """
        Generate a short straddle signal.

        Returns None if setup conditions are not met.
        """
        combined_premium = ce_premium + pe_premium

        if combined_premium < self.premium_target:
            logger.debug(
                "ATMStraddle: combined premium %.2f < target %.2f — skip",
                combined_premium, self.premium_target,
            )
            return None

        if iv_pct > 0 and iv_pct < self.iv_threshold:
            logger.debug(
                "ATMStraddle: IV %.1f%% < threshold %.1f%% — skip",
                iv_pct, self.iv_threshold,
            )
            return None

        step = _strike_step(underlying)
        strike = atm_strike(spot, step)
        lot_sz = get_lot_size(underlying)
        total_qty = self.lots * lot_sz

        # Max loss per lot = spot × stop_loss_pct × qty (one side unhedged)
        max_loss_per_lot = spot * self.stop_loss_pct * lot_sz
        max_loss = max_loss_per_lot * self.lots

        # Max profit = combined premium received × qty
        max_profit = combined_premium * total_qty

        # Breakevens
        be_upper = strike + combined_premium
        be_lower = strike - combined_premium

        legs = [
            OptionLeg(
                underlying=underlying,
                strike=strike,
                option_type=OptionType.CE,
                side=OptionSide.SELL,
                lots=self.lots,
                expiry=expiry,
                premium=ce_premium,
            ),
            OptionLeg(
                underlying=underlying,
                strike=strike,
                option_type=OptionType.PE,
                side=OptionSide.SELL,
                lots=self.lots,
                expiry=expiry,
                premium=pe_premium,
            ),
        ]

        reasoning = (
            f"SHORT STRADDLE {underlying} {expiry} {strike}: "
            f"premium=₹{combined_premium:.0f}  "
            f"BE={be_lower:.0f}–{be_upper:.0f}  "
            f"stop={self.stop_loss_pct*100:.1f}%"
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

    def should_exit(self, spot: float, entry_spot: float) -> bool:
        """True if underlying has moved beyond stop_loss_pct from entry."""
        move_pct = abs(spot - entry_spot) / entry_spot
        return move_pct >= self.stop_loss_pct
