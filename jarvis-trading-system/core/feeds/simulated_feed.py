"""Geometric Brownian Motion price feed — used when Dhan credentials absent."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import numpy as np

from core.types import Regime

logger = logging.getLogger(__name__)


class SimulatedFeed:
    TICK_INTERVAL   = 0.1
    SIGMA           = 0.0004
    VOL_REGIME_MULT = 3.0

    _BASE_PRICES: dict[str, float] = {
        "RELIANCE": 2500.0, "TCS": 3800.0, "INFY": 1500.0,
        "HDFCBANK": 1700.0, "SBIN": 800.0,
    }

    def __init__(
        self,
        symbols: list[str],
        base_prices: Optional[dict[str, float]] = None,
    ) -> None:
        self._symbols = symbols
        self._prices  = dict(base_prices or {s: self._BASE_PRICES.get(s, 100.0) for s in symbols})
        self._running = False
        self._regime  = Regime.SIDEWAYS

    def set_regime(self, regime: Regime) -> None:
        self._regime = regime

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        rng = np.random.default_rng()
        logger.info("SimulatedFeed started for %s", self._symbols)
        while self._running:
            sigma = self.SIGMA * (self.VOL_REGIME_MULT if self._regime == Regime.HIGH_VOL else 1.0)
            for sym in self._symbols:
                noise = float(rng.normal(0.0, sigma))
                self._prices[sym] = max(self._prices[sym] * (1.0 + noise), 0.01)
                await tick_callback(sym, self._prices[sym], int(rng.exponential(800)))
            await asyncio.sleep(self.TICK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)
