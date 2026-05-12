"""
NSE India live market snapshot client.

Uses the unofficial but widely-used NSE web API.
Uses `requests` (pure Python, works on Termux/Android) instead of httpx.
Cookie pre-fetch is best-effort (403 from Cloudflare is ignored).
"""
from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # no brotli — not available everywhere
    "Referer": _NSE_BASE + "/",
}

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

_COOKIE_TTL = 600


class NSEClient:
    """Sync HTTP client for NSE India's public JSON API (requests-based)."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        # Retry on transient errors
        retry = Retry(total=3, backoff_factor=0.5,
                      status_forcelist=[500, 502, 503, 504])
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._cookie_ts: float = 0.0

    def close(self) -> None:
        self._session.close()

    def _refresh_cookies(self) -> None:
        now = time.time()
        if now - self._cookie_ts < _COOKIE_TTL:
            return
        try:
            r = self._session.get(_NSE_BASE, timeout=10)
            self._cookie_ts = now
            logger.debug("[NSE] cookies refreshed  status=%d", r.status_code)
        except Exception as exc:
            self._cookie_ts = time.time()
            logger.debug("[NSE] cookie refresh skipped: %s", exc)

    def get_index_stocks(self, index_key: str = "NIFTY50") -> list[dict]:
        self._refresh_cookies()
        index_name = NSE_INDICES.get(index_key, index_key)
        try:
            r = self._session.get(
                f"{_NSE_BASE}/api/equity-stockIndices",
                params={"index": index_name},
                timeout=20,
            )
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as exc:
            logger.error("[NSE] get_index_stocks(%s) failed: %s", index_key, exc)
            return []

    def get_market_status(self) -> dict:
        self._refresh_cookies()
        try:
            r = self._session.get(f"{_NSE_BASE}/api/marketStatus", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.debug("[NSE] marketStatus failed: %s", exc)
            return {}

    def is_market_open(self) -> bool:
        status = self.get_market_status()
        for mkt in status.get("marketState", []):
            if (mkt.get("market") in ("Capital Market", "Equity")
                    and mkt.get("marketStatus") == "Open"):
                return True
        return False

    def get_option_chain_index(self, symbol: str = "NIFTY") -> dict:
        self._refresh_cookies()
        try:
            r = self._session.get(
                f"{_NSE_BASE}/api/option-chain-indices",
                params={"symbol": symbol.upper()},
                timeout=20,
            )
            r.raise_for_status()
            records = r.json().get("records", {})
            return {
                "underlying_value": records.get("underlyingValue", 0.0),
                "expiry_dates":     records.get("expiryDates", []),
                "data":             records.get("data", []),
            }
        except Exception as exc:
            logger.error("[NSE] get_option_chain_index(%s) failed: %s", symbol, exc)
            return {"underlying_value": 0.0, "expiry_dates": [], "data": []}

    def get_index_quote(self, symbol: str) -> dict:
        self._refresh_cookies()
        try:
            r = self._session.get(
                f"{_NSE_BASE}/api/equity-stockIndices",
                params={"index": symbol.upper()},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            return data[0] if data else {}
        except Exception as exc:
            logger.debug("[NSE] get_index_quote(%s) failed: %s", symbol, exc)
            return {}
