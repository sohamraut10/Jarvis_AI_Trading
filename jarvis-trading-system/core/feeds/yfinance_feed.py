"""
Yahoo Finance forex feed — no account, no token, works globally.

Polls yfinance every 10 seconds for current prices on major forex pairs.
Between polls, injects tiny random-walk ticks so strategies receive a
continuous bar stream. Price delay is ~15 seconds (Yahoo Finance free tier).

Supported pairs (internal name → Yahoo ticker):
  EURUSD → EURUSD=X    GBPUSD → GBPUSD=X    USDJPY → USDJPY=X
  AUDUSD → AUDUSD=X    USDCHF → USDCHF=X    USDCAD → USDCAD=X
  NZDUSD → NZDUSD=X    EURJPY → EURJPY=X    GBPJPY → GBPJPY=X
  EURGBP → EURGBP=X    XAUUSD → GC=F (gold)
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Internal symbol → Yahoo Finance ticker
_YF_TICKER: dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "USDCHF=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "XAUUSD": "GC=F",
}

# Micro-lot sizes for paper trading (1000 units per trade)
FOREX_LOT_SIZES: dict[str, int] = {s: 1000 for s in _YF_TICKER}

# Fallback prices when Yahoo Finance is unreachable at startup
_FALLBACK_PRICES: dict[str, float] = {
    "EURUSD": 1.0820, "GBPUSD": 1.2720, "USDJPY": 149.50,
    "AUDUSD": 0.6480, "USDCHF": 0.9010, "USDCAD": 1.3640,
    "NZDUSD": 0.5980, "EURJPY": 161.80, "GBPJPY": 190.20,
    "EURGBP": 0.8510, "XAUUSD": 2320.0,
}

# Realistic pip sizes per pair (for the random-walk simulation)
_PIP: dict[str, float] = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01,
    "AUDUSD": 0.0001, "USDCHF": 0.0001, "USDCAD": 0.0001,
    "NZDUSD": 0.0001, "EURJPY": 0.01,  "GBPJPY": 0.01,
    "EURGBP": 0.0001, "XAUUSD": 0.10,
}

_POLL_INTERVAL  = 10    # seconds between Yahoo Finance fetches
_TICK_INTERVAL  = 0.5   # seconds between simulated ticks (between polls)
_LIVE_WINDOW    = 30    # seconds — symbol is "live"
_STALE_WINDOW   = 120   # seconds — symbol is "stale"


class YFinanceFeed:
    """
    Continuous tick feed backed by Yahoo Finance polling.
    Implements the same interface as DhanFeed / SimulatedFeed so the
    rest of the system (strategies, AI brain, dashboard) needs no changes.
    """

    def __init__(self, symbols: list[str]) -> None:
        self._symbols   = list(symbols)
        self._tickers   = {s: _YF_TICKER.get(s, s + "=X") for s in symbols}
        self._prices:   dict[str, float] = {}
        self._volumes:  dict[str, int]   = {s: 500_000 for s in symbols}
        self._running   = False

        # Scanner state
        self._sym_ticks:     dict[str, int]   = {s: 0 for s in symbols}
        self._sym_last_tick: dict[str, float] = {}
        self._sym_ltp:       dict[str, float] = {}
        self._last_poll_ok:  dict[str, bool]  = {}

        self.ticks_received = 0

    # ── Public feed interface ─────────────────────────────────────────────────

    async def start(self, tick_callback: Callable) -> None:
        self._running = True
        loop = asyncio.get_event_loop()

        logger.info("[YFinance] starting forex feed — %d pairs: %s",
                    len(self._symbols), ", ".join(self._symbols))

        # First fetch (blocking, in thread pool)
        await loop.run_in_executor(None, self._fetch_all)

        # Seed fallback prices for any pair that failed initial fetch
        for sym in self._symbols:
            if sym not in self._prices and sym in _FALLBACK_PRICES:
                self._prices[sym] = _FALLBACK_PRICES[sym]
                logger.warning("[YFinance] %s using fallback price %.5f", sym, _FALLBACK_PRICES[sym])

        logger.info("[YFinance] initial prices: %s",
                    "  ".join(f"{s}={p:.5f}" for s, p in self._prices.items()))

        last_poll  = time.time()
        tick_count = 0

        while self._running:
            now = time.time()

            # Periodic real fetch
            if now - last_poll >= _POLL_INTERVAL:
                await loop.run_in_executor(None, self._fetch_all)
                last_poll = now

            # Emit one tick per symbol per interval (random-walk around real price)
            for sym in self._symbols:
                price = self._prices.get(sym)
                if not price:
                    continue

                # Random walk: ±1–3 pips per tick
                pip   = _PIP.get(sym, 0.0001)
                move  = pip * random.uniform(-3, 3)
                price = max(pip, price + move)
                self._prices[sym] = price

                t = time.time()
                self._sym_ticks[sym]     = self._sym_ticks.get(sym, 0) + 1
                self._sym_last_tick[sym] = t
                self._sym_ltp[sym]       = price
                self.ticks_received     += 1
                tick_count              += 1

                vol = self._volumes.get(sym, 500_000)
                await tick_callback(sym, price, vol)

                if tick_count % 500 == 0:
                    logger.info("[YFinance] alive  ticks=%d  %s=%.5f", tick_count, sym, price)

            await asyncio.sleep(_TICK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def lot_size(self, symbol: str) -> int:
        return FOREX_LOT_SIZES.get(symbol, 1000)

    def set_regime(self, regime) -> None:
        pass

    # ── Scanner interface (same shape as DhanFeed) ────────────────────────────

    def scanner_data(self) -> dict:
        now = time.time()
        result = {}
        for sym in self._symbols:
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
                "status":        status,
                "ticks":         ticks,
                "ltp":           round(ltp, 5) if ltp else None,
                "last_tick_ago": round(idle, 1) if idle is not None else None,
                "is_currency":   True,
                "is_commodity":  False,
                "exchange":      "FOREX",
                "data_source":   "Yahoo Finance (~15s delay)",
            }
        return result

    def active_symbols(self) -> list[str]:
        now = time.time()
        return [
            s for s in self._symbols
            if s in self._sym_last_tick
            and (now - self._sym_last_tick[s]) <= _LIVE_WINDOW
        ]

    def add_instrument(self, *args, **kwargs):
        return False

    # ── Internal fetch ────────────────────────────────────────────────────────

    def _fetch_all(self) -> None:
        """Download latest 1-minute bars from Yahoo Finance; extract last close."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("[YFinance] yfinance not installed — run: pip install yfinance")
            return

        tickers = list(self._tickers.values())
        try:
            raw = yf.download(
                tickers,
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=True,
                threads=True,
            )
            if raw.empty:
                logger.warning("[YFinance] download returned empty DataFrame")
                return

            for sym, yf_ticker in self._tickers.items():
                try:
                    if len(tickers) == 1:
                        # Single ticker: columns are flat (Open, High, Low, Close, Volume)
                        col = raw["Close"]
                    else:
                        # Multi-ticker: MultiIndex columns (metric, ticker)
                        col = raw["Close"][yf_ticker]

                    price = float(col.dropna().iloc[-1])
                    if price > 0:
                        self._prices[sym] = price
                        self._last_poll_ok[sym] = True
                        logger.debug("[YFinance] polled %s = %.5f", sym, price)
                except Exception as e:
                    logger.debug("[YFinance] extract failed for %s: %s", sym, e)
                    self._last_poll_ok[sym] = False

        except Exception as exc:
            logger.warning("[YFinance] fetch error: %s", exc)
