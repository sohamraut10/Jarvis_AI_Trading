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
        # Equities
        "RELIANCE": 2500.0, "TCS": 3800.0, "INFY": 1500.0,
        "HDFCBANK": 1700.0, "SBIN": 800.0,
        # Currency futures (NSE)
        "USDINR": 84.0, "EURINR": 90.0, "GBPINR": 105.0, "JPYINR": 0.55,
        # MCX commodities
        "CRUDEOIL": 6500.0, "GOLD": 72000.0, "SILVER": 88000.0,
        "NATURALGAS": 230.0, "COPPER": 780.0,
    }

    def __init__(
        self,
        symbols: list[str],
        base_prices: Optional[dict[str, float]] = None,
    ) -> None:
        self._symbols = symbols
        # Use provided prices if given, otherwise look up _BASE_PRICES, fallback to 100
        provided = base_prices if base_prices is not None else {}
        self._prices = {s: provided.get(s, self._BASE_PRICES.get(s, 100.0)) for s in symbols}
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

    def add_instrument(
        self,
        exchange_segment: str = "",
        security_id: str = "",
        symbol: str = "",
        lot_size: int = 1,
        initial_price: Optional[float] = None,
    ) -> bool:
        """Dynamically add a symbol; it will be included in the next tick loop iteration."""
        if symbol and symbol not in self._symbols:
            price = initial_price or self._BASE_PRICES.get(symbol, 100.0)
            self._prices[symbol] = price
            self._symbols.append(symbol)   # list is iterated live — takes effect next loop
            logger.info("SimulatedFeed added %-12s  start_price=%.4f", symbol, price)
        return True

    def remove_symbol(self, symbol: str) -> None:
        if symbol in self._symbols:
            self._symbols.remove(symbol)
            self._prices.pop(symbol, None)
