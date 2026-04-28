"""
Dhan live market feed — real NSE prices, paper orders.

Connects to Dhan's WebSocket market feed using dhanhq.
The trading engine still uses PaperBroker so no real orders are placed.
Falls back to SimulatedFeed automatically if credentials are missing or
the connection fails.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# NSE Equity security IDs (exchange segment = NSE_EQ)
_NSE_IDS: dict[str, str] = {
    "RELIANCE": "2885",
    "TCS":      "11536",
    "INFY":     "1594",
    "HDFCBANK": "1333",
    "SBIN":     "3045",
}
_ID_TO_SYM: dict[str, str] = {v: k for k, v in _NSE_IDS.items()}


class DhanFeed:
    """
    Live tick feed from Dhan's market WebSocket.
    Same start(tick_callback) interface as SimulatedFeed — drop-in swap.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        symbols: list[str],
    ) -> None:
        self._client_id    = client_id
        self._access_token = access_token
        self._symbols      = symbols
        self._ltp: dict[str, float] = {}
        self._running      = False

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        try:
            from dhanhq import marketfeed as mf
        except ImportError:
            logger.error("dhanhq not installed — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        instruments = [
            (mf.NSE, _NSE_IDS[sym], mf.Ticker)
            for sym in self._symbols
            if sym in _NSE_IDS
        ]
        if not instruments:
            logger.error("No Dhan security IDs matched — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        def _on_ticks(ticks: list[dict]) -> None:
            for tick in ticks:
                sid = str(tick.get("security_id", ""))
                sym = _ID_TO_SYM.get(sid)
                if not sym or not self._running:
                    continue
                ltp = float(tick.get("LTP") or tick.get("last_price") or 0)
                vol = int(tick.get("volume") or 800)
                if ltp > 0:
                    self._ltp[sym] = ltp
                    asyncio.run_coroutine_threadsafe(
                        tick_callback(sym, ltp, vol), loop
                    )

        def _run() -> None:
            try:
                feed = mf.DhanFeed(
                    self._client_id,
                    self._access_token,
                    instruments,
                    subscription_type=mf.Ticker,
                    on_ticks=_on_ticks,
                )
                logger.info("Dhan feed connected for %s", self._symbols)
                feed.run_forever()
            except Exception as exc:
                logger.error("Dhan feed error: %s — falling back to SimulatedFeed", exc)
                # Signal the async side to switch to sim
                asyncio.run_coroutine_threadsafe(
                    self._fallback(tick_callback), loop
                )

        loop.run_in_executor(None, _run)

        # Keep coroutine alive (same pattern as SimulatedFeed)
        while self._running:
            await asyncio.sleep(1.0)

    async def _fallback(self, tick_callback: Callable) -> None:
        from core.feeds.simulated_feed import SimulatedFeed
        sim = SimulatedFeed(self._symbols)
        await sim.start(tick_callback)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._ltp.get(symbol)

    def set_regime(self, regime) -> None:
        pass  # Dhan feed doesn't adjust volatility by regime
