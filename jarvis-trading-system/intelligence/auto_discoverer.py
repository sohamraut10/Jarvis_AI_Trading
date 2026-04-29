"""
AutoDiscoverer — scans the full Indian market universe every 5 minutes
via NSE India's live API, scores each instrument, and returns the top
trading opportunities for the AI Brain to analyse and trade.

Scoring weights (each sub-score normalised 0–1, total → 0–100):
  momentum   35%  |daily % change| — today's price action
  day_range  25%  intraday high–low as % of LTP — ATR proxy
  range_pos  20%  proximity to 52-week high or low — breakout zone
  trend_30d  20%  |30-day % return| — sustained directional trend

Direction assignment:
  BUY  — positive momentum AND price in upper 40% of 52-week range
  SELL — negative momentum AND price in lower 40% of 52-week range
  FLAT — everything else
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_W_MOM    = 0.35
_W_RANGE  = 0.25
_W_RPOS   = 0.20
_W_TREND  = 0.20

TOP_N          = 10
SCAN_INTERVAL  = 300   # seconds between scans

# Which NSE index groups to include in the scan
_INDEX_KEYS = ("NIFTY50", "BANKNIFTY", "MIDCAP50")


@dataclass
class DiscoveredInstrument:
    symbol:        str
    ltp:           float
    segment:       str          # "NSE_EQ" | "MCX_COMM" | "NSE_CURR"
    asset_class:   str          # "Equity" | "ETF" | "MCX" | "Currency"
    score:         float        # 0–100
    rank:          int
    direction:     str          # "BUY" | "SELL" | "FLAT"
    change_pct:    float        # today's % change
    day_range_pct: float        # intraday range as % of LTP
    week52_high:   Optional[float] = None
    week52_low:    Optional[float] = None
    trend_30d:     float = 0.0
    reasoning:     str   = ""


class AutoDiscoverer:
    """
    Periodically fetches NSE live data for the equity universe,
    scores every instrument, and returns the top-ranked opportunities.

    Usage (from JarvisEngine):
        self._discoverer = AutoDiscoverer()
        # in async loop:
        top = await self._discoverer.scan()
    """

    def __init__(self) -> None:
        from core.market.nse_live import NSEClient
        self._nse          = NSEClient()
        self._last_scan:   float = 0.0
        self._results:     list[DiscoveredInstrument] = []
        self._market_open: Optional[bool] = None
        self._error:       Optional[str]  = None

    # ── Public ────────────────────────────────────────────────────────────────

    def last_results(self) -> list[DiscoveredInstrument]:
        return list(self._results)

    def market_open(self) -> Optional[bool]:
        return self._market_open

    def stale(self) -> bool:
        return (time.time() - self._last_scan) > SCAN_INTERVAL * 1.5

    def last_error(self) -> Optional[str]:
        return self._error

    def seconds_since_scan(self) -> float:
        return time.time() - self._last_scan if self._last_scan else float("inf")

    async def scan(self) -> list[DiscoveredInstrument]:
        """Run a full universe scan (runs in thread pool, non-blocking)."""
        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(None, self._scan_sync)
            self._results = results
            self._error   = None
        except Exception as exc:
            logger.error("[AutoDisc] scan failed: %s", exc)
            self._error = str(exc)
        self._last_scan = time.time()
        return self._results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _scan_sync(self) -> list[DiscoveredInstrument]:
        """Blocking: fetch NSE data for all index groups, score, and rank."""
        self._market_open = self._nse.is_market_open()

        candidates: list[DiscoveredInstrument] = []
        seen: set[str] = set()

        for idx_key in _INDEX_KEYS:
            rows = self._nse.get_index_stocks(idx_key)
            for row in rows:
                sym = row.get("symbol", "").strip()
                # Skip blank, already-seen, and the index-summary row itself
                if not sym or sym in seen or " " in sym:
                    continue
                seen.add(sym)
                inst = self._score_row(row, segment="NSE_EQ")
                if inst:
                    candidates.append(inst)

        candidates.sort(key=lambda x: x.score, reverse=True)
        for i, c in enumerate(candidates):
            c.rank = i + 1

        top = candidates[:TOP_N]
        logger.info(
            "[AutoDisc] scanned=%d  market_open=%s  top10: %s",
            len(candidates),
            self._market_open,
            "  ".join(f"{c.symbol}({c.score:.0f}/{c.direction})" for c in top),
        )
        return top

    def _score_row(self, row: dict, segment: str) -> Optional[DiscoveredInstrument]:
        try:
            sym   = row.get("symbol", "").strip()
            ltp   = float(row.get("lastPrice")  or 0)
            if ltp <= 0:
                return None

            pchange = float(row.get("pChange")       or 0)
            day_hi  = float(row.get("dayHigh")        or ltp)
            day_lo  = float(row.get("dayLow")         or ltp)
            wk52_hi = float(row.get("nearWKH")        or ltp * 1.3)
            wk52_lo = float(row.get("nearWKL")        or ltp * 0.7)
            ch30d   = float(row.get("perChange30d")   or 0)

            # Sub-scores (each clamped to 0–1)
            mom_s   = min(abs(pchange) / 5.0,  1.0)         # 5% change → max
            rng_s   = min((day_hi - day_lo) / ltp / 0.03, 1.0)  # 3% range → max
            span    = wk52_hi - wk52_lo
            pos52   = (ltp - wk52_lo) / span if span > 0 else 0.5
            rpos_s  = max(1.0 - abs(pos52 - 0.5) * 2, 0.0)  # peaks at extremes
            t30_s   = min(abs(ch30d) / 15.0, 1.0)           # 15% 30d trend → max

            score = (_W_MOM * mom_s + _W_RANGE * rng_s + _W_RPOS * rpos_s + _W_TREND * t30_s) * 100

            if pchange > 0.5 and pos52 > 0.60:
                direction = "BUY"
            elif pchange < -0.5 and pos52 < 0.40:
                direction = "SELL"
            else:
                direction = "FLAT"

            from core.market.universe import ASSET_CLASS
            asset_class = ASSET_CLASS.get(sym, "Equity")

            return DiscoveredInstrument(
                symbol=sym, ltp=ltp, segment=segment,
                asset_class=asset_class,
                score=round(score, 1), rank=0,
                direction=direction,
                change_pct=round(pchange, 2),
                day_range_pct=round((day_hi - day_lo) / ltp * 100, 2),
                week52_high=round(wk52_hi, 2),
                week52_low=round(wk52_lo, 2),
                trend_30d=round(ch30d, 2),
                reasoning=(
                    f"Δ{pchange:+.2f}%  range={((day_hi-day_lo)/ltp*100):.2f}%  "
                    f"52w_pos={pos52*100:.0f}%  trend30={ch30d:+.1f}%"
                ),
            )
        except Exception as exc:
            logger.debug("[AutoDisc] score_row failed for %s: %s", row.get("symbol"), exc)
            return None
