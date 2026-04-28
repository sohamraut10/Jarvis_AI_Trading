"""
Dhan live market feed — real NSE prices, paper orders.

Supports NSE equities (NSE_EQ) and NSE currency futures (NSE_CURR).
Connects directly via WebSocket v2 (URL-param auth).
Auto-discovers which subscribed symbols are actually live by tracking
tick activity. Falls back to SimulatedFeed on repeated failures.
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

# How long without a tick before a symbol is considered stale / offline
_LIVE_WINDOW   = 60    # seconds — "LIVE"
_STALE_WINDOW  = 300   # seconds — "STALE" (was live, now quiet)

_DISCONNECT_CODES = {
    805: "max WebSocket connections exceeded — close other sessions",
    806: "not subscribed to Dhan Data APIs — enable market data plan",
    807: "access token expired — generate a new token from Dhan",
    808: "invalid client ID",
    809: "authentication failed — check client_id / access_token",
}


def _parse_tick(data: bytes) -> Optional[dict]:
    if not data or len(data) < 1:
        return None
    first_byte = data[0]
    try:
        if first_byte == 2:
            _, _, exch, sec_id, ltp, _ = struct.unpack('<BHBIfI', data[:16])
            return {"security_id": sec_id, "LTP": f"{ltp:.4f}"}
        elif first_byte == 4:
            fields = struct.unpack('<BHBIfHIfIIIffff', data[:50])
            return {"security_id": fields[3], "LTP": f"{fields[4]:.4f}", "volume": fields[8]}
        elif first_byte == 8:
            fields = struct.unpack('<BHBIfHIfIIIIIIffff100s', data[:162])
            return {"security_id": fields[3], "LTP": f"{fields[4]:.4f}", "volume": fields[8]}
        elif first_byte == 50:
            code = struct.unpack('<BHBIH', data[:10])[4]
            reason = _DISCONNECT_CODES.get(code, f"unknown code {code}")
            logger.error("[Dhan] SERVER DISCONNECT  code=%d  %s", code, reason)
            return None
    except Exception as exc:
        logger.debug("[Dhan] parse error  byte=%d  err=%s", first_byte, exc)
    return None


class DhanFeed:
    """
    Live tick feed from Dhan's market WebSocket.
    Tracks per-symbol activity for auto-discovery of live pairs.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        symbols: list[str],
        currency_symbols: Optional[list[str]] = None,
    ) -> None:
        self._client_id    = client_id
        self._access_token = access_token
        self._eq_symbols   = symbols
        self._curr_symbols = currency_symbols or []
        self._all_symbols  = symbols + (currency_symbols or [])
        self._ltp: dict[str, float] = {}
        self._running      = False

        # Per-symbol discovery stats
        self._sym_ticks:     dict[str, int]   = {s: 0   for s in self._all_symbols}
        self._sym_last_tick: dict[str, float] = {}   # sym → epoch time of last tick
        self._sym_ltp:       dict[str, float] = {}

        self.connected:      bool           = False
        self.ticks_received: int            = 0
        self.connect_time:   Optional[float] = None
        self.last_tick_time: Optional[float] = None
        self._reconnects:    int            = 0

    # ── Public scanner interface ───────────────────────────────────────────────

    def scanner_data(self) -> dict:
        """
        Return per-symbol discovery state for the frontend scanner panel.
        Status: "searching" | "live" | "stale" | "offline"
        """
        now = time.time()
        result = {}
        for sym in self._all_symbols:
            ticks     = self._sym_ticks.get(sym, 0)
            last_time = self._sym_last_tick.get(sym)
            ltp       = self._sym_ltp.get(sym)
            idle      = (now - last_time) if last_time else None

            if last_time is None:
                status = "searching"
            elif idle <= _LIVE_WINDOW:
                status = "live"
            elif idle <= _STALE_WINDOW:
                status = "stale"
            else:
                status = "offline"

            result[sym] = {
                "status":       status,
                "ticks":        ticks,
                "ltp":          round(ltp, 4) if ltp else None,
                "last_tick_ago": round(idle, 1) if idle is not None else None,
                "is_currency":  sym.endswith("INR"),
            }
        return result

    def active_symbols(self) -> list[str]:
        """Symbols with ticks in the last _LIVE_WINDOW seconds."""
        now = time.time()
        return [
            s for s in self._all_symbols
            if s in self._sym_last_tick
            and (now - self._sym_last_tick[s]) <= _LIVE_WINDOW
        ]

    # ── Feed lifecycle ─────────────────────────────────────────────────────────

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        from core.feeds.dhan_instruments import build_instrument_map
        instrument_map = build_instrument_map(self._eq_symbols, self._curr_symbols)

        if not instrument_map:
            logger.error("[Dhan] no instruments resolved — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        id_to_sym: dict[str, str] = {sid: sym for sym, (_, sid) in instrument_map.items()}
        sub_list = [
            {"ExchangeSegment": seg, "SecurityId": sid}
            for _, (seg, sid) in instrument_map.items()
        ]

        logger.info("[Dhan] auto-discovery: subscribing to %d instruments — %s",
                    len(sub_list),
                    ", ".join(f"{s}({seg})" for s, (seg, _) in instrument_map.items()))

        sub_msg = json.dumps({
            "RequestCode": 15,
            "InstrumentCount": len(sub_list),
            "InstrumentList": sub_list,
        })

        await self._preflight_check()

        def _on_tick(sym: str, ltp: float, vol: int) -> None:
            now = time.time()
            self._ltp[sym]          = ltp
            self._sym_ltp[sym]      = ltp
            self._sym_ticks[sym]    = self._sym_ticks.get(sym, 0) + 1
            self._sym_last_tick[sym] = now
            self.ticks_received    += 1
            self.last_tick_time     = now

            n = self._sym_ticks[sym]
            if n == 1:
                logger.info("[Dhan] DISCOVERED  %s=%.4f  (first tick)", sym, ltp)
            elif n % 500 == 0:
                logger.info("[Dhan] alive  %s=%.4f  ticks=%d", sym, ltp, n)

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

        # Status log every 60 s
        while self._running:
            await asyncio.sleep(60)
            if self.connected:
                active = self.active_symbols()
                all_data = self.scanner_data()
                summary = "  ".join(
                    f"{s}={'%.4f' % d['ltp'] if d['ltp'] else '—'} ({d['status']})"
                    for s, d in all_data.items()
                )
                logger.info("[Dhan] CONNECTED  active=%s  scanning: %s",
                            len(active), summary)
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
                    logger.info("[Dhan] %s — discovering live pairs…", label)

                    try:
                        greeting = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        logger.info("[Dhan] server greeting: %r",
                                    greeting[:80] if isinstance(greeting, bytes) else greeting[:200])
                    except asyncio.TimeoutError:
                        pass

                    await ws.send(sub_msg)
                    logger.info("[Dhan] subscription sent — waiting for ticks…")

                    while self._running:
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Dhan] no data for 30s")
                            continue

                        if isinstance(data, str):
                            logger.info("[Dhan] server: %s", data[:300])
                            continue

                        connect_ok = True
                        fast_fails = 0
                        tick = _parse_tick(data)
                        if not tick:
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
                    "[Dhan] giving up after %d instant disconnects — "
                    "enable 'Live Market Feed' in Dhan app → API Access. "
                    "Falling back to SimulatedFeed.", fast_fails)
                return

            wait = min(10 * attempt, 60)
            logger.warning("[Dhan] retrying in %ds", wait)
            await asyncio.sleep(wait)

    async def _preflight_check(self) -> None:
        import requests
        try:
            resp = requests.get(
                "https://api.dhan.co/v2/fundlimit",
                headers={"access-token": self._access_token,
                         "client-id": self._client_id,
                         "Content-Type": "application/json"},
                timeout=8,
            )
            if resp.status_code == 200:
                logger.info("[Dhan] REST pre-check OK — token valid")
            elif resp.status_code == 401:
                logger.error("[Dhan] REST pre-check 401 — token EXPIRED → regenerate in Dhan app")
            else:
                logger.warning("[Dhan] REST pre-check HTTP %d", resp.status_code)
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
