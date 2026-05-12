"""
DhanScanner — dynamic instrument discovery using Dhan's own market data.

At startup (and on refresh), it:
  1. Loads the scrip master to get the F&O-eligible equity universe
     (stocks that have FUTSTK/OPTSTK derivatives are confirmed liquid)
  2. Queries Dhan's REST Quote API for live prices + volume
  3. Ranks by volume × |% change| (momentum-weighted liquidity)
  4. Returns the top N instruments ready for WebSocket subscription
  5. Also picks near-month NSE currency futures from scrip master
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DHAN_API  = "https://api.dhan.co"
_BATCH_SZ  = 500   # Dhan allows up to 1000 per quote request
_TIMEOUT   = 15    # seconds per API call

# Ranking weight: volume gets 60%, % change momentum gets 40%
_W_VOL = 0.60
_W_MOM = 0.40

# How many equity + currency instruments to pick
DEFAULT_TOP_EQUITY   = 25
DEFAULT_TOP_CURRENCY = 4   # USDINR, EURINR, GBPINR, JPYINR near-month


class DhanScanner:
    """
    Uses Dhan's Quote REST API to discover the most active NSE instruments.
    All stock selection is purely data-driven — zero hardcoded symbols.
    """

    def __init__(self, client_id: str, access_token: str) -> None:
        self._cid = client_id
        self._tok = access_token
        self._session = None   # httpx.Client, lazy-init

    def _client(self):
        if self._session is None:
            import httpx
            self._session = httpx.Client(
                headers={
                    "client-id":    self._cid,
                    "access-token": self._tok,
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
                timeout=_TIMEOUT,
            )
        return self._session

    # ── Public ────────────────────────────────────────────────────────────────

    def get_top_instruments(
        self,
        top_equity: int = DEFAULT_TOP_EQUITY,
        top_currency: int = DEFAULT_TOP_CURRENCY,
    ) -> list[dict]:
        """
        Return a list of the most active instruments ready for subscription.

        Each entry:
          symbol, security_id, segment, ltp, volume, lot_size, score
        """
        from core.feeds.dhan_instruments import get_scrip_master
        sm = get_scrip_master()
        if not sm.is_loaded():
            logger.warning("[DhanScanner] scrip master not loaded — returning empty list")
            return []

        results: list[dict] = []
        results.extend(self._top_equity(sm, top_equity))
        results.extend(self._top_currency(sm, top_currency))
        logger.info(
            "[DhanScanner] selected %d instruments: %s",
            len(results),
            ", ".join(r["symbol"] for r in results),
        )
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _top_equity(self, sm, limit: int) -> list[dict]:
        """Pick top `limit` NSE equity stocks ranked by Dhan live volume × momentum."""
        # Step 1: collect F&O-eligible equities from scrip master
        # (any stock with a futures/options contract is confirmed liquid)
        fno_underlying: dict[str, str] = {}   # underlying_sym → eq_security_id
        for inst in sm._instruments:
            if inst.get("instrument_type") not in ("FUTSTK", "OPTSTK"):
                continue
            undl = (inst.get("underlying") or inst.get("symbol") or "").upper()
            if not undl or undl in fno_underlying:
                continue
            fno_underlying[undl] = ""

        # Step 2: find the EQ security ID for each F&O underlying
        for inst in sm._instruments:
            if inst.get("instrument_type") != "EQ":
                continue
            seg = inst.get("segment", "")
            if seg not in ("NSE_EQ", "E"):
                continue
            sym = (inst.get("symbol") or "").upper()
            if sym in fno_underlying and not fno_underlying[sym]:
                fno_underlying[sym] = str(inst.get("security_id", ""))

        # Keep only those with a resolved EQ security ID
        candidates = {sym: sid for sym, sid in fno_underlying.items() if sid}
        if not candidates:
            logger.warning("[DhanScanner] no F&O-eligible equities found in scrip master")
            return []

        logger.info("[DhanScanner] %d F&O-eligible equities found, querying Dhan quotes…",
                    len(candidates))

        # Step 3: batch-query Dhan Quote API
        syms   = list(candidates.keys())
        sids   = [candidates[s] for s in syms]
        quotes = self._fetch_quotes("NSE_EQ", sids)

        # Step 4: rank by volume × |% change|
        ranked: list[dict] = []
        for sym, sid in zip(syms, sids):
            q = quotes.get(sid) or quotes.get(int(sid) if sid.isdigit() else sid)
            if not q:
                continue
            vol  = float(q.get("volume") or q.get("totalTradedQuantity") or 0)
            pct  = abs(float(q.get("percentageChange") or q.get("pChange") or
                              q.get("percentage_change") or 0))
            ltp  = float(q.get("lastTradedPrice") or q.get("last_price") or
                         q.get("lastPrice") or 0)
            if ltp <= 0:
                continue
            score = _W_VOL * self._norm(vol) + _W_MOM * min(pct / 5.0, 1.0)
            ranked.append({
                "symbol":      sym,
                "security_id": sid,
                "segment":     "NSE_EQ",
                "ltp":         round(ltp, 2),
                "volume":      int(vol),
                "lot_size":    1,
                "score":       round(score, 4),
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        top = ranked[:limit]
        logger.info("[DhanScanner] top equity picks: %s",
                    ", ".join(f"{r['symbol']}(vol={r['volume']//1000}K)" for r in top))
        return top

    def _top_currency(self, sm, limit: int) -> list[dict]:
        """Pick near-month NSE currency futures from scrip master."""
        futures = sm.near_month_futures(segments=["NSE_CURR", "C"])
        results = []
        seen: set[str] = set()
        for f in futures:
            sym = (f.get("underlying") or f.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            results.append({
                "symbol":      sym,
                "security_id": str(f.get("security_id", "")),
                "segment":     "NSE_CURR",
                "ltp":         float(f.get("last_price") or 84.0),
                "volume":      0,
                "lot_size":    int(f.get("lot_size") or 1000),
                "score":       0.0,
            })
            if len(results) >= limit:
                break
        return results

    def _fetch_quotes(self, segment: str, security_ids: list[str]) -> dict:
        """
        Query POST /v2/marketfeed/quote for up to _BATCH_SZ ids at a time.
        Returns {security_id_str: quote_dict}.
        """
        merged: dict = {}
        for i in range(0, len(security_ids), _BATCH_SZ):
            batch = security_ids[i: i + _BATCH_SZ]
            # Dhan expects integer security IDs in the list
            int_batch = []
            for sid in batch:
                try:
                    int_batch.append(int(sid))
                except (ValueError, TypeError):
                    pass
            if not int_batch:
                continue
            try:
                import json as _json
                resp = self._client().post(
                    f"{_DHAN_API}/v2/marketfeed/quote",
                    content=_json.dumps({segment: int_batch}),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Response: {"data": {"NSE_EQ": {sid: {...}, ...}}}
                    seg_data = (data.get("data") or data).get(segment, {})
                    for sid_key, q in seg_data.items():
                        merged[str(sid_key)] = q
                else:
                    logger.warning("[DhanScanner] quote API %d: %s",
                                   resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.warning("[DhanScanner] quote fetch failed (batch %d): %s", i, exc)
            time.sleep(0.3)   # gentle rate-limit
        return merged

    @staticmethod
    def _norm(value: float) -> float:
        """Soft normalise a volume value to 0–1 (log scale, 1M vol → ~1.0)."""
        import math
        if value <= 0:
            return 0.0
        return min(math.log10(max(value, 1)) / 6.0, 1.0)   # log10(1M) = 6
