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
            logger.error("[Dhan] ✗ SERVER DISCONNECT  code=%d  %s", code, reason)
            if code in (807, 809):
                logger.error("[Dhan]   → regenerate token: Dhan app → My Profile → Dhan API → Generate Token")
            elif code == 806:
                logger.error("[Dhan]   → enable 'Live Market Feed' plan in Dhan app → API Access")
            elif code == 805:
                logger.error("[Dhan]   → close other Dhan API sessions (max connections exceeded)")
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
        commodity_symbols: Optional[list[str]] = None,
    ) -> None:
        self._client_id    = client_id
        self._access_token = access_token
        self._eq_symbols   = symbols
        self._curr_symbols = currency_symbols or []
        self._comm_symbols = commodity_symbols or []
        self._all_symbols  = symbols + (currency_symbols or []) + (commodity_symbols or [])
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

        # Dynamic subscription support (populated in start())
        self._id_to_sym:       dict[str, str]   = {}   # security_id → symbol
        self._sub_list:        list[dict]        = []   # [{ExchangeSegment, SecurityId}, ...]
        self._instrument_lot:  dict[str, int]    = {}   # symbol → lot_size
        self._instrument_info: dict[str, dict]   = {}   # symbol → {segment, security_id, lot_size}
        self._thread_loop: Optional[asyncio.AbstractEventLoop] = None
        self._current_ws  = None

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

            info     = self._instrument_info.get(sym, {})
            seg      = info.get("segment", "")
            is_comm  = seg == "MCX_COMM"
            is_curr  = sym.endswith("INR") and not is_comm
            exchange = "MCX" if is_comm else ("NSE_CURR" if is_curr else "NSE")
            result[sym] = {
                "status":        status,
                "ticks":         ticks,
                "ltp":           round(ltp, 4) if ltp else None,
                "last_tick_ago": round(idle, 1) if idle is not None else None,
                "is_currency":   is_curr,
                "is_commodity":  is_comm,
                "exchange":      exchange,
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

        # Wait up to 45 s for the scrip master (downloading in background).
        # This avoids _raw_currency_search downloading the full 8 MB CSV four
        # times in the event-loop thread, which would freeze the server.
        from core.feeds.dhan_instruments import get_scrip_master, build_instrument_map
        sm = get_scrip_master()
        if not sm.is_loaded():
            logger.info("[Dhan] waiting for scrip master to finish loading…")
            deadline = time.time() + 45
            while not sm.is_loaded() and time.time() < deadline:
                await asyncio.sleep(2)
            if sm.is_loaded():
                logger.info("[Dhan] scrip master ready — resolving instruments")
            else:
                logger.warning("[Dhan] scrip master still loading after 45s — using fallback lookup")

        # Run synchronously in thread pool to avoid blocking the event loop
        instrument_map = await loop.run_in_executor(
            None, build_instrument_map, self._eq_symbols, self._curr_symbols, self._comm_symbols
        )

        if not instrument_map:
            logger.error("[Dhan] no instruments resolved — falling back to SimulatedFeed")
            await self._fallback(tick_callback)
            return

        # Populate instance vars so dynamic add_instrument() works after startup
        self._id_to_sym = {sid: sym for sym, (_, sid, _) in instrument_map.items()}
        self._sub_list  = [
            {"ExchangeSegment": seg, "SecurityId": sid}
            for _, (seg, sid, _) in instrument_map.items()
        ]
        self._instrument_lot  = {sym: lot for sym, (_, _, lot) in instrument_map.items()}
        self._instrument_info = {
            sym: {"segment": seg, "security_id": sid, "lot_size": lot}
            for sym, (seg, sid, lot) in instrument_map.items()
        }

        logger.info("[Dhan] auto-discovery: subscribing to %d instruments — %s",
                    len(self._sub_list),
                    ", ".join(f"{s}({seg})" for s, (seg, _, _) in instrument_map.items()))

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
            self._thread_loop = thread_loop   # saved for dynamic subscriptions
            thread_loop.run_until_complete(self._connect_loop(_on_tick))
            self._thread_loop = None
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

    async def _connect_loop(self, on_tick: Callable) -> None:
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
                    self._current_ws  = ws
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

                    # Build sub msg from current instance vars (includes dynamically added)
                    sub_msg = json.dumps({
                        "RequestCode": 15,
                        "InstrumentCount": len(self._sub_list),
                        "InstrumentList": self._sub_list,
                    })
                    await ws.send(sub_msg)
                    logger.info("[Dhan] subscription sent (%d instruments) — waiting for ticks…",
                                len(self._sub_list))

                    while self._running:
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            logger.warning("[Dhan] no data for 30s")
                            continue

                        if isinstance(data, str):
                            try:
                                msg = json.loads(data)
                                logger.info("[Dhan] server message: %s", msg)
                            except Exception:
                                logger.info("[Dhan] server: %s", data[:300])
                            continue

                        connect_ok = True
                        fast_fails = 0
                        tick = _parse_tick(data)
                        if not tick:
                            continue

                        sid = str(tick.get("security_id", ""))
                        sym = self._id_to_sym.get(sid)
                        if not sym:
                            continue
                        ltp = float(tick.get("LTP") or 0)
                        vol = int(tick.get("volume") or 800)
                        if ltp > 0:
                            on_tick(sym, ltp, vol)

            except Exception as exc:
                was = self.connected
                self.connected   = False
                self._current_ws = None
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
            else:
                self._current_ws = None
                self.connected   = False

            if not self._running:
                break

            if fast_fails >= _MAX_FAST_FAILS:
                logger.error("[Dhan] ✗ giving up after %d instant disconnects", fast_fails)
                logger.error("[Dhan]   possible causes:")
                logger.error("[Dhan]   1. Token expired   → Dhan app → My Profile → Dhan API → Generate Token")
                logger.error("[Dhan]   2. No market feed  → Dhan app → API Access → enable Live Market Feed")
                logger.error("[Dhan]   3. Wrong client_id → check data/settings.json dhan_client_id")
                logger.error("[Dhan]   falling back to SimulatedFeed (paper trading continues)")
                return

            wait = min(10 * attempt, 60)
            logger.warning("[Dhan] retrying in %ds", wait)
            await asyncio.sleep(wait)

    # ── Dynamic subscription ───────────────────────────────────────────────────

    async def add_instrument(
        self,
        exchange_segment: str,
        security_id: str,
        symbol: str,
        lot_size: int = 1,
    ) -> bool:
        """Subscribe to a new instrument on the live WS connection (or queue for next connect)."""
        sid = str(security_id)
        if sid in self._id_to_sym:
            logger.info("[Dhan] %s already subscribed", symbol)
            return True

        entry = {"ExchangeSegment": exchange_segment, "SecurityId": sid}
        self._id_to_sym[sid] = symbol
        self._instrument_lot[symbol] = lot_size
        self._instrument_info[symbol] = {
            "segment": exchange_segment, "security_id": sid, "lot_size": lot_size,
        }
        if entry not in self._sub_list:
            self._sub_list.append(entry)
        if symbol not in self._all_symbols:
            self._all_symbols.append(symbol)
            self._sym_ticks[symbol] = 0

        if self._current_ws is not None and self._thread_loop is not None:
            sub_msg = json.dumps({
                "RequestCode": 15,
                "InstrumentCount": 1,
                "InstrumentList": [entry],
            })
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._current_ws.send(sub_msg), self._thread_loop
                )
                future.result(timeout=5)
                logger.info("[Dhan] dynamically subscribed: %s (%s/%s)", symbol, exchange_segment, sid)
                return True
            except Exception as exc:
                logger.error("[Dhan] dynamic subscribe error: %s", exc)
                return False

        logger.info("[Dhan] queued for next connection: %s (%s/%s)", symbol, exchange_segment, sid)
        return True

    def remove_symbol(self, sym: str) -> None:
        """Stop processing ticks for a symbol (WS unsub not supported by Dhan — we just ignore ticks)."""
        sids = {k for k, v in self._id_to_sym.items() if v == sym}
        for sid in sids:
            del self._id_to_sym[sid]
        self._sub_list = [e for e in self._sub_list if e["SecurityId"] not in sids]
        self._instrument_lot.pop(sym, None)
        self._instrument_info.pop(sym, None)
        if sym in self._all_symbols:
            self._all_symbols.remove(sym)
        self._sym_ticks.pop(sym, None)
        self._sym_last_tick.pop(sym, None)
        self._sym_ltp.pop(sym, None)
        self._ltp.pop(sym, None)
        logger.info("[Dhan] removed symbol: %s", sym)

    def lot_size(self, sym: str) -> int:
        return self._instrument_lot.get(sym, 1)

    async def _preflight_check(self) -> None:
        import requests

        cid_masked   = self._client_id[:4] + "•" * max(0, len(self._client_id) - 4)
        tok_masked   = self._access_token[:6] + "•" * 10 + self._access_token[-4:]
        logger.info("[Dhan] ┌─ credential check ──────────────────────────────")
        logger.info("[Dhan] │  client_id    : %s", cid_masked)
        logger.info("[Dhan] │  access_token : %s  (len=%d)", tok_masked, len(self._access_token))

        def _check() -> tuple[int, dict]:
            resp = requests.get(
                "https://api.dhan.co/v2/fundlimit",
                headers={
                    "access-token":   self._access_token,
                    "client-id":      self._client_id,
                    "Content-Type":   "application/json",
                },
                timeout=10,
            )
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body

        try:
            loop = asyncio.get_event_loop()
            status, body = await loop.run_in_executor(None, _check)

            if status == 200:
                avail   = body.get("availabelBalance", body.get("availableBalance", "?"))
                used    = body.get("utilizedAmount", "?")
                logger.info("[Dhan] │  REST check     : ✓ OK  (HTTP 200)")
                logger.info("[Dhan] │  available bal  : ₹%s   utilised: ₹%s", avail, used)
                logger.info("[Dhan] └─ token VALID — connecting to live feed")
            elif status == 401:
                logger.error("[Dhan] │  REST check     : ✗ UNAUTHORIZED (HTTP 401)")
                logger.error("[Dhan] │  → access_token is EXPIRED or INVALID")
                logger.error("[Dhan] │  → open Dhan app  →  My Profile  →  Dhan API  →  Generate Token")
                logger.error("[Dhan] └─ will fall back to SimulatedFeed after WS fails")
            elif status == 429:
                logger.warning("[Dhan] │  REST check     : rate-limited (HTTP 429) — skipping balance check")
                logger.info("[Dhan] └─ token may still be valid; proceeding")
            else:
                logger.warning("[Dhan] │  REST check     : HTTP %d — %s", status, str(body)[:120])
                logger.info("[Dhan] └─ proceeding (non-fatal)")
        except Exception as exc:
            logger.warning("[Dhan] │  REST check     : skipped (%s)", exc)
            logger.info("[Dhan] └─ network unavailable — proceeding anyway")

    async def _fallback(self, tick_callback: Callable) -> None:
        logger.info("[Dhan] switching to SimulatedFeed")
        from core.feeds.simulated_feed import SimulatedFeed

        # Wrap tick callback so DhanFeed scanner state updates → pairs show as "live"
        async def _tick_and_track(sym: str, ltp: float, vol: float) -> None:
            now = time.time()
            self._ltp[sym]           = ltp
            self._sym_ltp[sym]       = ltp
            self._sym_ticks[sym]     = self._sym_ticks.get(sym, 0) + 1
            self._sym_last_tick[sym] = now
            await tick_callback(sym, ltp, vol)

        sim = SimulatedFeed(self._all_symbols)
        await sim.start(_tick_and_track)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._ltp.get(symbol)

    def set_regime(self, regime) -> None:
        pass
