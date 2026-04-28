"""
Dhan live market feed — real NSE prices, paper orders.

Connects to Dhan's WebSocket market feed directly (bypassing dhanhq's
run_forever which has no receive loop) to get full control and visibility.
Falls back to SimulatedFeed automatically if credentials are missing or
the connection fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# NSE Equity security IDs
_NSE_IDS: dict[str, str] = {
    "RELIANCE": "2885",
    "TCS":      "11536",
    "INFY":     "1594",
    "HDFCBANK": "1333",
    "SBIN":     "3045",
}
_ID_TO_SYM: dict[str, str] = {v: k for k, v in _NSE_IDS.items()}

_DHAN_WSS = "wss://api-feed.dhan.co"

_DISCONNECT_CODES = {
    805: "max WebSocket connections exceeded — close other sessions",
    806: "not subscribed to Dhan Data APIs — enable market data plan",
    807: "access token expired — generate a new token from Dhan",
    808: "invalid client ID",
    809: "authentication failed — check client_id / access_token",
}


def _parse_tick(data: bytes) -> Optional[dict]:
    """Parse a single binary message from Dhan's feed. Returns dict or None."""
    if not data or len(data) < 1:
        return None
    first_byte = data[0]
    try:
        if first_byte == 2:          # Ticker
            _, _, exch, sec_id, ltp, ltt = struct.unpack('<BHBIfI', data[:16])
            return {"type": "Ticker", "security_id": sec_id, "LTP": f"{ltp:.2f}"}
        elif first_byte == 4:        # Quote
            fields = struct.unpack('<BHBIfHIfIIIffff', data[:50])
            return {"type": "Quote", "security_id": fields[3],
                    "LTP": f"{fields[4]:.2f}", "volume": fields[8]}
        elif first_byte == 8:        # Full
            fields = struct.unpack('<BHBIfHIfIIIIIIffff100s', data[:162])
            return {"type": "Full", "security_id": fields[3],
                    "LTP": f"{fields[4]:.2f}", "volume": fields[8]}
        elif first_byte == 50:       # Server disconnect
            code = struct.unpack('<BHBIH', data[:10])[4]
            reason = _DISCONNECT_CODES.get(code, f"unknown code {code}")
            logger.error("[Dhan] SERVER DISCONNECT  code=%d  reason: %s", code, reason)
            return None
        else:
            return None
    except Exception as exc:
        logger.debug("[Dhan] parse error  byte=%d  err=%s", first_byte, exc)
        return None


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

        self.connected:       bool  = False
        self.ticks_received:  int   = 0
        self.connect_time:    Optional[float] = None
        self.last_tick_time:  Optional[float] = None
        self._reconnects:     int   = 0

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        sec_ids = [_NSE_IDS[s] for s in self._symbols if s in _NSE_IDS]
        skipped  = [s for s in self._symbols if s not in _NSE_IDS]
        if skipped:
            logger.warning("[Dhan] no security ID for: %s (skipped)", skipped)
        if not sec_ids:
            logger.error("[Dhan] no valid instruments — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        def _on_tick(sym: str, ltp: float, vol: int) -> None:
            self._ltp[sym] = ltp
            self.ticks_received += 1
            self.last_tick_time = time.time()
            if self.ticks_received <= len(self._symbols):
                logger.info("[Dhan] first tick  %s=₹%.2f  vol=%d", sym, ltp, vol)
            asyncio.run_coroutine_threadsafe(tick_callback(sym, ltp, vol), loop)

        def _run() -> None:
            import asyncio as _asyncio
            thread_loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(thread_loop)
            thread_loop.run_until_complete(self._connect_loop(sec_ids, _on_tick))
            logger.info("[Dhan] feed thread exiting")

        loop.run_in_executor(None, _run)

        # Status logger every 60 s
        while self._running:
            await asyncio.sleep(60)
            if self.connected:
                idle = time.time() - (self.last_tick_time or time.time())
                logger.info("[Dhan] CONNECTED  ticks=%d  last_tick=%.0fs ago  %s",
                            self.ticks_received, idle,
                            "  ".join(f"{s}=₹{p:.2f}" for s, p in self._ltp.items()))
                if idle > 30:
                    logger.warning("[Dhan] no ticks for %.0fs — feed may be stale", idle)
            else:
                logger.warning("[Dhan] DISCONNECTED  reconnects=%d", self._reconnects)

    async def _connect_loop(self, sec_ids: list[str], on_tick: Callable) -> None:
        import websockets

        url = (
            f"{_DHAN_WSS}"
            f"?version=2"
            f"&token={self._access_token}"
            f"&clientId={self._client_id}"
            f"&authType=2"
        )

        sub_msg = json.dumps({
            "RequestCode": 15,   # Ticker subscription
            "InstrumentCount": len(sec_ids),
            "InstrumentList": [
                {"ExchangeSegment": "NSE_EQ", "SecurityId": sid}
                for sid in sec_ids
            ],
        })

        attempt = 0
        while self._running:
            attempt += 1
            logger.info("[Dhan] connect attempt #%d", attempt)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.connected    = True
                    self.connect_time = time.time()
                    self._reconnects += 1
                    label = "connected" if self._reconnects == 1 else f"reconnected #{self._reconnects}"
                    logger.info("[Dhan] %s — sending subscription for %d instruments", label, len(sec_ids))

                    await ws.send(sub_msg)
                    logger.info("[Dhan] subscription sent — waiting for ticks…")

                    while self._running:
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Dhan] no data for 30 s — connection may be idle")
                            continue

                        if isinstance(data, str):
                            logger.info("[Dhan] text from server: %s", data[:300])
                            continue

                        tick = _parse_tick(data)
                        if tick is None:
                            continue

                        sid  = str(tick.get("security_id", ""))
                        sym  = _ID_TO_SYM.get(sid)
                        if not sym:
                            continue
                        ltp = float(tick.get("LTP") or 0)
                        vol = int(tick.get("volume") or 800)
                        if ltp > 0:
                            on_tick(sym, ltp, vol)

            except Exception as exc:
                was = self.connected
                self.connected = False
                # Try to surface the WebSocket close reason
                close_info = ""
                if hasattr(exc, 'rcvd') and exc.rcvd:
                    close_info = f"  ws_code={exc.rcvd.code}  reason={exc.rcvd.reason!r}"
                elif hasattr(exc, 'code'):
                    close_info = f"  code={exc.code}"
                uptime = ""
                if was and self.connect_time:
                    uptime = f"  uptime={time.time()-self.connect_time:.0f}s"
                logger.error("[Dhan] disconnected: %s%s%s", exc, close_info, uptime)

            if not self._running:
                break

            wait = min(10 * attempt, 60)
            logger.warning("[Dhan] retrying in %ds (attempt #%d done)", wait, attempt)
            await asyncio.sleep(wait)

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
