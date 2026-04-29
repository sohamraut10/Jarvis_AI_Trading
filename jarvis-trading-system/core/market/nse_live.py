"""
NSE India live market snapshot client.

Uses the unofficial but widely-used NSE web API.  Requires a valid session
cookie (obtained by hitting the main page first) and standard browser headers.
Falls back gracefully to empty results on any network or parse failure.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": _NSE_BASE + "/",
    "Connection": "keep-alive",
}

# index_key → NSE API parameter
NSE_INDICES: dict[str, str] = {
    "NIFTY50":    "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "MIDCAP50":   "NIFTY MIDCAP 50",
    "FINNIFTY":   "NIFTY FIN SERVICE",
    "IT":         "NIFTY IT",
    "AUTO":       "NIFTY AUTO",
    "PHARMA":     "NIFTY PHARMA",
    "METAL":      "NIFTY METAL",
    "FMCG":       "NIFTY FMCG",
    "REALTY":     "NIFTY REALTY",
}


class NSEClient:
    """Sync HTTP client for NSE India's public JSON API."""

    _COOKIE_TTL = 600   # refresh session cookies every 10 min

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )
        self._cookie_ts: float = 0.0

    def close(self) -> None:
        self._client.close()

    # ── Session management ────────────────────────────────────────────────────

    def _refresh_cookies(self) -> None:
        now = time.time()
        if now - self._cookie_ts < self._COOKIE_TTL:
            return
        try:
            self._client.get(_NSE_BASE)
            self._cookie_ts = now
            logger.debug("[NSE] session cookies refreshed")
        except Exception as exc:
            logger.warning("[NSE] cookie refresh failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_index_stocks(self, index_key: str = "NIFTY50") -> list[dict]:
        """
        Return live snapshot rows for every stock in the given index.

        Each row contains: symbol, lastPrice, pChange, dayHigh, dayLow,
        nearWKH (52w high), nearWKL (52w low), totalTradedVolume,
        perChange30d, perChange365d, open, previousClose.
        """
        self._refresh_cookies()
        index_name = NSE_INDICES.get(index_key, index_key)
        try:
            r = self._client.get(
                f"{_NSE_BASE}/api/equity-stockIndices",
                params={"index": index_name},
            )
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as exc:
            logger.error("[NSE] get_index_stocks(%s) failed: %s", index_key, exc)
            return []

    def get_market_status(self) -> dict:
        """Raw market-status response from NSE."""
        self._refresh_cookies()
        try:
            r = self._client.get(f"{_NSE_BASE}/api/market-status")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("[NSE] market-status failed: %s", exc)
            return {}

    def is_market_open(self) -> bool:
        """True if NSE Capital Market / Equity segment is currently open."""
        status = self.get_market_status()
        for mkt in status.get("marketState", []):
            if (mkt.get("market") in ("Capital Market", "Equity")
                    and mkt.get("marketStatus") == "Open"):
                return True
        return False
