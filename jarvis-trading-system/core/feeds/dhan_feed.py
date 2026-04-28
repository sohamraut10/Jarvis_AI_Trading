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
import time
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

        # Connection state (read by heartbeat logger in engine)
        self.connected:       bool  = False
        self.ticks_received:  int   = 0
        self.connect_time:    Optional[float] = None
        self.last_tick_time:  Optional[float] = None
        self._reconnects:     int   = 0

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        try:
            from dhanhq import marketfeed as mf
        except ImportError:
            logger.error("[Dhan] dhanhq package not installed — pip install dhanhq")
            logger.warning("[Dhan] falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        instruments = [
            (mf.NSE, _NSE_IDS[sym], mf.Ticker)
            for sym in self._symbols
            if sym in _NSE_IDS
        ]
        skipped = [s for s in self._symbols if s not in _NSE_IDS]
        if skipped:
            logger.warning("[Dhan] no security ID for: %s (skipped)", skipped)

        if not instruments:
            logger.error("[Dhan] no valid instruments — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        logger.info("[Dhan] connecting  client=%s...  symbols=%s",
                    self._client_id[:6] + "***", self._symbols)

        def _on_open() -> None:
            self.connected    = True
            self.connect_time = time.time()
            self._reconnects += 1
            attempt = "connected" if self._reconnects == 1 else f"reconnected (attempt #{self._reconnects})"
            logger.info("[Dhan] %s ✓  subscribing to %d instruments", attempt, len(instruments))

        def _on_close() -> None:
            was_connected = self.connected
            self.connected = False
            if was_connected:
                uptime = time.time() - (self.connect_time or time.time())
                logger.warning("[Dhan] disconnected  uptime=%.0fs  ticks_received=%d",
                               uptime, self.ticks_received)
            else:
                logger.warning("[Dhan] connection closed before establishing")

        def _on_error(err) -> None:
            logger.error("[Dhan] feed error: %s", err)

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
                    self.ticks_received += 1
                    self.last_tick_time = time.time()

                    # Log first tick per symbol to confirm data is flowing
                    if self.ticks_received <= len(self._symbols):
                        logger.info("[Dhan] first tick  %s=₹%.2f  vol=%d", sym, ltp, vol)

                    asyncio.run_coroutine_threadsafe(
                        tick_callback(sym, ltp, vol), loop
                    )

        def _run() -> None:
            # dhanhq calls asyncio.get_event_loop() in __init__ and run_forever;
            # thread-pool threads have no event loop, so create one explicitly.
            import asyncio as _asyncio
            thread_loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(thread_loop)

            attempt = 0
            while self._running:
                attempt += 1
                try:
                    logger.info("[Dhan] WebSocket connect attempt #%d", attempt)
                    feed = mf.DhanFeed(
                        self._client_id,
                        self._access_token,
                        instruments,       # each tuple already contains subscription type
                    )
                    # Callbacks are set as attributes, not constructor kwargs
                    feed.on_ticks = _on_ticks
                    _on_open()
                    feed.run_forever()
                    # run_forever() returned — means disconnected
                    _on_close()
                except Exception as exc:
                    self.connected = False
                    logger.error("[Dhan] feed error: %s", exc)

                if not self._running:
                    break

                logger.warning("[Dhan] disconnected — retrying in 10s  (attempt #%d done)", attempt)
                time.sleep(10)

            logger.info("[Dhan] feed stopped")

        loop.run_in_executor(None, _run)

        # Status logger: print connection state every 60s
        while self._running:
            await asyncio.sleep(60)
            if self.connected:
                idle = time.time() - (self.last_tick_time or time.time())
                logger.info("[Dhan] status=CONNECTED  ticks=%d  last_tick=%.0fs ago  prices=%s",
                            self.ticks_received, idle,
                            "  ".join(f"{s}=₹{p:.2f}" for s, p in self._ltp.items()))
                if idle > 30:
                    logger.warning("[Dhan] no ticks for %.0fs — feed may be stale", idle)
            else:
                logger.warning("[Dhan] status=DISCONNECTED  reconnects=%d", self._reconnects)

    async def _fallback(self, tick_callback: Callable) -> None:
        logger.info("[Dhan] switching to SimulatedFeed")
        from core.feeds.simulated_feed import SimulatedFeed
        sim = SimulatedFeed(self._symbols)
        await sim.start(tick_callback)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._ltp.get(symbol)

    def set_regime(self, regime) -> None:
        pass
