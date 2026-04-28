"""
Dhan live market feed — real NSE prices, paper orders.

Supports both NSE equities (NSE_EQ) and NSE currency futures (NSE_CURR).
Connects directly via WebSocket v2 (URL-param auth).
Falls back to SimulatedFeed automatically on repeated failures.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

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
        if first_byte == 2:      # Ticker
            _, _, exch, sec_id, ltp, _ = struct.unpack('<BHBIfI', data[:16])
            return {"security_id": sec_id, "LTP": f"{ltp:.2f}"}
        elif first_byte == 4:    # Quote
            fields = struct.unpack('<BHBIfHIfIIIffff', data[:50])
            return {"security_id": fields[3], "LTP": f"{fields[4]:.2f}", "volume": fields[8]}
        elif first_byte == 8:    # Full
            fields = struct.unpack('<BHBIfHIfIIIIIIffff100s', data[:162])
            return {"security_id": fields[3], "LTP": f"{fields[4]:.2f}", "volume": fields[8]}
        elif first_byte == 50:   # Server disconnect
            code = struct.unpack('<BHBIH', data[:10])[4]
            reason = _DISCONNECT_CODES.get(code, f"unknown code {code}")
            logger.error("[Dhan] SERVER DISCONNECT  code=%d  %s", code, reason)
            return None
        else:
            return None
    except Exception as exc:
        logger.debug("[Dhan] parse error  byte=%d  err=%s", first_byte, exc)
        return None


class DhanFeed:
    """
    Live tick feed from Dhan's market WebSocket.
    Supports NSE equities and NSE currency futures.
    Same start(tick_callback) interface as SimulatedFeed — drop-in swap.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        symbols: list[str],
        currency_symbols: Optional[list[str]] = None,
    ) -> None:
        self._client_id     = client_id
        self._access_token  = access_token
        self._eq_symbols    = symbols           # equity symbols
        self._curr_symbols  = currency_symbols or []  # currency pair symbols
        self._all_symbols   = symbols + (currency_symbols or [])
        self._ltp: dict[str, float] = {}
        self._running       = False

        self.connected:      bool          = False
        self.ticks_received: int           = 0
        self.connect_time:   Optional[float] = None
        self.last_tick_time: Optional[float] = None
        self._reconnects:    int           = 0

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        # Build instrument map (equity static + currency dynamic from scrip master)
        from core.feeds.dhan_instruments import build_instrument_map
        instrument_map = build_instrument_map(self._eq_symbols, self._curr_symbols)

        if not instrument_map:
            logger.error("[Dhan] no valid instruments resolved — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        # Reverse map: security_id (str) → symbol
        id_to_sym: dict[str, str] = {sid: sym for sym, (_, sid) in instrument_map.items()}

        # Build subscription list: [{ExchangeSegment, SecurityId}]
        sub_list = [
            {"ExchangeSegment": seg, "SecurityId": sid}
            for _, (seg, sid) in instrument_map.items()
        ]
        logger.info("[Dhan] instruments resolved: %s",
                    ", ".join(f"{s}({seg}:{sid})" for s, (seg, sid) in instrument_map.items()))

        sub_msg = json.dumps({
            "RequestCode": 15,   # Ticker
            "InstrumentCount": len(sub_list),
            "InstrumentList": sub_list,
        })

        await self._preflight_check()

        def _on_tick(sym: str, ltp: float, vol: int) -> None:
            self._ltp[sym] = ltp
            self.ticks_received += 1
            self.last_tick_time = time.time()
            if self.ticks_received <= len(self._all_symbols):
                logger.info("[Dhan] first tick  %s=%.4f  vol=%d", sym, ltp, vol)
            asyncio.run_coroutine_threadsafe(tick_callback(sym, ltp, vol), loop)

        def _run() -> None:
            import asyncio as _asyncio
            thread_loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(thread_loop)
            thread_loop.run_until_complete(
                self._connect_loop(sub_msg, id_to_sym, _on_tick)
            )
            logger.info("[Dhan] feed thread exiting")
            if self.ticks_received == 0 and self._running:
                asyncio.run_coroutine_threadsafe(self._fallback(tick_callback), loop)

        loop.run_in_executor(None, _run)

        while self._running:
            await asyncio.sleep(60)
            if self.connected:
                idle = time.time() - (self.last_tick_time or time.time())
                prices = "  ".join(
                    f"{s}={p:.4f}" if s.endswith("INR") else f"{s}=₹{p:.2f}"
                    for s, p in self._ltp.items()
                )
                logger.info("[Dhan] CONNECTED  ticks=%d  last_tick=%.0fs ago  %s",
                            self.ticks_received, idle, prices)
                if idle > 30:
                    logger.warning("[Dhan] no ticks for %.0fs — feed may be stale", idle)
            else:
                logger.warning("[Dhan] DISCONNECTED  reconnects=%d", self._reconnects)

    async def _connect_loop(
        self,
        sub_msg: str,
        id_to_sym: dict[str, str],
        on_tick: Callable,
    ) -> None:
        import websockets

        url = (
            f"{_DHAN_WSS}"
            f"?version=2"
            f"&token={self._access_token}"
            f"&clientId={self._client_id}"
            f"&authType=2"
        )

        _MAX_FAST_FAILS = 3
        attempt    = 0
        fast_fails = 0

        while self._running:
            attempt += 1
            logger.info("[Dhan] connect attempt #%d", attempt)
            connect_ok = False
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.connected    = True
                    self.connect_time = time.time()
                    self._reconnects += 1
                    label = "connected" if self._reconnects == 1 else f"reconnected #{self._reconnects}"
                    logger.info("[Dhan] %s", label)

                    # Wait for any server greeting
                    try:
                        greeting = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        logger.info("[Dhan] server greeting: %r",
                                    greeting[:80] if isinstance(greeting, bytes) else greeting[:200])
                    except asyncio.TimeoutError:
                        pass

                    await ws.send(sub_msg)
                    logger.info("[Dhan] subscription sent — waiting for ticks…")
                    sub_time = time.time()

                    while self._running:
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Dhan] no data for 30 s — connection may be idle")
                            continue

                        if isinstance(data, str):
                            logger.info("[Dhan] text from server: %s", data[:300])
                            continue

                        connect_ok = True
                        fast_fails = 0
                        tick = _parse_tick(data)
                        if tick is None:
                            continue

                        sid = str(tick.get("security_id", ""))
                        sym = id_to_sym.get(sid)
                        if not sym:
                            continue
                        ltp = float(tick.get("LTP") or 0)
                        vol = int(tick.get("volume") or 800)
                        if ltp > 0:
                            on_tick(sym, ltp, vol)

            except Exception as exc:
                was = self.connected
                self.connected = False
                close_info = ""
                if hasattr(exc, 'rcvd') and exc.rcvd:
                    close_info = f"  ws_code={exc.rcvd.code}  reason={exc.rcvd.reason!r}"
                elif hasattr(exc, 'code'):
                    close_info = f"  code={exc.code}"
                uptime = ""
                if was and self.connect_time:
                    elapsed = time.time() - self.connect_time
                    uptime  = f"  uptime={elapsed:.0f}s"
                    if not connect_ok and elapsed < 2:
                        fast_fails += 1
                logger.error("[Dhan] disconnected: %s%s%s", exc, close_info, uptime)

            if not self._running:
                break

            if fast_fails >= _MAX_FAST_FAILS:
                logger.error(
                    "[Dhan] *** giving up after %d instant disconnects ***\n"
                    "  Most likely: Live Market Feed API not enabled on your account.\n"
                    "  Fix: Dhan app → My Profile → API Access → enable 'Live Market Feed'\n"
                    "  Falling back to SimulatedFeed.",
                    fast_fails,
                )
                return

            wait = min(10 * attempt, 60)
            logger.warning("[Dhan] retrying in %ds (attempt #%d done)", wait, attempt)
            await asyncio.sleep(wait)

    async def _preflight_check(self) -> None:
        import requests
        try:
            resp = requests.get(
                "https://api.dhan.co/v2/fundlimit",
                headers={
                    "access-token": self._access_token,
                    "client-id":    self._client_id,
                    "Content-Type": "application/json",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                logger.info("[Dhan] REST pre-check OK — token valid, account active")
            elif resp.status_code == 401:
                logger.error(
                    "[Dhan] REST pre-check FAILED 401 — access token EXPIRED\n"
                    "  → Dhan app → My Profile → Access Token → generate new\n"
                    "  → Update ACCESS_TOKEN in .env"
                )
            elif resp.status_code == 403:
                logger.error("[Dhan] REST pre-check FAILED 403 — API access not enabled")
            else:
                logger.warning("[Dhan] REST pre-check: HTTP %d", resp.status_code)
        except Exception as exc:
            logger.warning("[Dhan] REST pre-check skipped: %s", exc)

    async def _fallback(self, tick_callback: Callable) -> None:
        logger.info("[Dhan] switching to SimulatedFeed")
        from core.feeds.simulated_feed import SimulatedFeed
        sim = SimulatedFeed(self._all_symbols)
        await sim.start(tick_callback)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._ltp.get(symbol)

    def set_regime(self, regime) -> None:
        pass
