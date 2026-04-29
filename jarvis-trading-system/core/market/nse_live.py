"""
NSE India live market snapshot client.

Uses the unofficial but widely-used NSE web API.
Key fixes vs v1:
  - Accept-Encoding omitted from custom headers so httpx only negotiates
    encodings it can decompress (gzip/deflate); avoids brotli decode errors.
  - Cookie pre-fetch is best-effort (403 is ignored); most API endpoints
    work without a live session on the main page.
  - market-status uses the correct camelCase endpoint /api/marketStatus.
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"

# Omit Accept-Encoding — let httpx negotiate only what it can decode natively.
# Including "br" without the brotli package installed causes decode failures.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _NSE_BASE + "/",
}

# index_key → NSE API parameter value
NSE_INDICES: dict[str, str] = {
    "NIFTY50":   "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "MIDCAP50":  "NIFTY MIDCAP 50",
    "FINNIFTY":  "NIFTY FIN SERVICE",
    "IT":        "NIFTY IT",
    "AUTO":      "NIFTY AUTO",
    "PHARMA":    "NIFTY PHARMA",
    "METAL":     "NIFTY METAL",
    "FMCG":      "NIFTY FMCG",
    "REALTY":    "NIFTY REALTY",
}

_COOKIE_TTL = 600   # seconds between cookie refresh attempts


class NSEClient:
    """Sync HTTP client for NSE India's public JSON API."""

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
        """
        Best-effort session cookie refresh.  NSE may return 403 (Cloudflare);
        we log at DEBUG and continue — the data endpoints often work regardless.
        """
        now = time.time()
        if now - self._cookie_ts < _COOKIE_TTL:
            return
        try:
            r = self._client.get(_NSE_BASE, timeout=10.0)
            if r.status_code < 400:
                self._cookie_ts = now
                logger.debug("[NSE] session cookies refreshed  status=%d", r.status_code)
            else:
                # 403 from Cloudflare — mark as refreshed to avoid hammering
                self._cookie_ts = now
                logger.debug("[NSE] cookie refresh returned %d — continuing anyway", r.status_code)
        except Exception as exc:
            self._cookie_ts = time.time()   # back-off
            logger.debug("[NSE] cookie refresh skipped: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_index_stocks(self, index_key: str = "NIFTY50") -> list[dict]:
        """
        Return live snapshot rows for all stocks in the given index.

        Each row includes: symbol, lastPrice, pChange, dayHigh, dayLow,
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
        """Raw marketStatus response from NSE (camelCase endpoint)."""
        self._refresh_cookies()
        try:
            r = self._client.get(f"{_NSE_BASE}/api/marketStatus", timeout=10.0)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.debug("[NSE] marketStatus failed: %s", exc)
            return {}

    def is_market_open(self) -> bool:
        """True if NSE Capital Market / Equity segment is currently open."""
        status = self.get_market_status()
        for mkt in status.get("marketState", []):
            if (mkt.get("market") in ("Capital Market", "Equity")
                    and mkt.get("marketStatus") == "Open"):
                return True
        return False
