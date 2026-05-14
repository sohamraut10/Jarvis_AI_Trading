"""
DhanScanner — dynamic instrument discovery using Dhan's own market data.

Priority order (F&O first):
  1. Near-month NSE index futures — NIFTY, BANKNIFTY, FINNIFTY (always)
  2. Top stock futures (FUTSTK) ranked by Dhan live volume × momentum
  3. Top F&O-eligible equity stocks ranked by Dhan live volume × momentum
  4. Near-month NSE currency futures (USDINR, EURINR)
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

# F&O-first slot allocation
DEFAULT_TOP_INDEX_FUT  = 3    # NIFTY, BANKNIFTY, FINNIFTY near-month futures
DEFAULT_TOP_STOCK_FUT  = 12   # top stock futures by live Dhan volume
DEFAULT_TOP_EQUITY     = 10   # F&O-eligible equity stocks (cash)
DEFAULT_TOP_CURRENCY   = 2    # USDINR, EURINR near-month

# Index underlyings to always include as futures
_INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY"}


class DhanScanner:
    """
    Uses Dhan's Quote REST API to discover the most active NSE instruments.
    F&O instruments are selected first; equity fills remaining slots.
    All selection is purely data-driven — zero hardcoded symbols.
    """

    def __init__(self, client_id: str, access_token: str) -> None:
        self._cid = client_id
        self._tok = access_token
        self._session = None   # requests.Session, lazy-init

    def _client(self):
        if self._session is None:
            import requests as _requests
            s = _requests.Session()
            s.headers.update({
                "client-id":    self._cid,
                "access-token": self._tok,
                "Content-Type": "application/json",
                "Accept":       "application/json",
            })
            s.timeout = _TIMEOUT
            self._session = s
        return self._session

    # ── Public ────────────────────────────────────────────────────────────────

    def get_top_instruments(
        self,
        top_index_fut:  int = DEFAULT_TOP_INDEX_FUT,
        top_stock_fut:  int = DEFAULT_TOP_STOCK_FUT,
        top_equity:     int = DEFAULT_TOP_EQUITY,
        top_currency:   int = DEFAULT_TOP_CURRENCY,
    ) -> list[dict]:
        """
        Return the most active instruments ready for WebSocket subscription.
        Priority: index futures → stock futures → equity → currency.

        Each entry: symbol, security_id, segment, ltp, volume, lot_size, score
        """
        from core.feeds.dhan_instruments import get_scrip_master
        sm = get_scrip_master()
        if not sm.is_loaded():
            logger.warning("[DhanScanner] scrip master not loaded — returning empty list")
            return []

        results: list[dict] = []
        results.extend(self._top_index_futures(sm, top_index_fut))
        results.extend(self._top_stock_futures(sm, top_stock_fut))
        results.extend(self._top_equity(sm, top_equity))
        results.extend(self._top_currency(sm, top_currency))
        logger.info(
            "[DhanScanner] selected %d instruments: %s",
            len(results),
            ", ".join(r["symbol"] for r in results),
        )
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _top_index_futures(self, sm, limit: int) -> list[dict]:
        """Always include NIFTY/BANKNIFTY/FINNIFTY near-month index futures."""
        futures = sm.near_month_futures(segments=["NSE_FNO", "D"])
        results: list[dict] = []
        seen: set[str] = set()
        for f in futures:
            if f.get("instrument_type") != "FUTIDX":
                continue
            undl = (f.get("underlying") or f.get("symbol") or "").upper()
            # Normalize common variants
            for idx in _INDEX_UNDERLYINGS:
                if undl.startswith(idx):
                    undl = idx
                    break
            if undl not in _INDEX_UNDERLYINGS or undl in seen:
                continue
            seen.add(undl)
            sid = str(f.get("security_id", ""))
            if not sid:
                continue
            display = f.get("display_name") or f"{undl} FUT"
            results.append({
                "symbol":          display,
                "security_id":     sid,
                "segment":         "NSE_FNO",
                "ltp":             float(f.get("last_price") or 0),
                "volume":          0,
                "lot_size":        int(f.get("lot_size") or 50),
                "score":           100.0,   # highest priority — always include
                "instrument_type": "FUTIDX",
            })
            if len(results) >= limit:
                break
        logger.info("[DhanScanner] index futures: %s",
                    ", ".join(r["symbol"] for r in results) or "none found")
        return results

    def _top_stock_futures(self, sm, limit: int) -> list[dict]:
        """Top stock futures (FUTSTK) ranked by Dhan live volume × momentum."""
        # Collect near-month FUTSTK — one per underlying (nearest expiry)
        futures = sm.near_month_futures(segments=["NSE_FNO", "D"])
        candidates: dict[str, dict] = {}   # security_id → instrument
        for f in futures:
            if f.get("instrument_type") != "FUTSTK":
                continue
            sid = str(f.get("security_id", ""))
            if sid:
                candidates[sid] = f

        if not candidates:
            logger.info("[DhanScanner] no FUTSTK found in scrip master")
            return []

        logger.info("[DhanScanner] %d stock futures found, querying Dhan quotes…",
                    len(candidates))

        sids   = list(candidates.keys())
        quotes = self._fetch_quotes("NSE_FNO", sids)

        ranked: list[dict] = []
        for sid, inst in candidates.items():
            q = quotes.get(sid) or quotes.get(int(sid) if sid.isdigit() else sid)
            if not q:
                continue
            vol = float(q.get("volume") or q.get("totalTradedQuantity") or 0)
            pct = abs(float(q.get("percentageChange") or q.get("pChange") or
                            q.get("percentage_change") or 0))
            ltp = float(q.get("lastTradedPrice") or q.get("last_price") or
                        q.get("lastPrice") or 0)
            if ltp <= 0:
                continue
            score = _W_VOL * self._norm(vol) + _W_MOM * min(pct / 5.0, 1.0)
            display = inst.get("display_name") or inst.get("symbol") or sid
            ranked.append({
                "symbol":          display,
                "security_id":     sid,
                "segment":         "NSE_FNO",
                "ltp":             round(ltp, 2),
                "volume":          int(vol),
                "lot_size":        int(inst.get("lot_size") or 1),
                "score":           round(score, 4),
                "instrument_type": "FUTSTK",
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        top = ranked[:limit]
        logger.info("[DhanScanner] top stock futures: %s",
                    ", ".join(f"{r['symbol']}(vol={r['volume']//1000}K)" for r in top))
        return top

    def _top_equity(self, sm, limit: int) -> list[dict]:
        """F&O-eligible equity stocks ranked by Dhan live volume × momentum."""
        fno_underlying: dict[str, str] = {}   # underlying_sym → eq_security_id
        for inst in sm._instruments:
            if inst.get("instrument_type") not in ("FUTSTK", "OPTSTK"):
                continue
            undl = (inst.get("underlying") or inst.get("symbol") or "").upper()
            if not undl or undl in fno_underlying:
                continue
            fno_underlying[undl] = ""

        for inst in sm._instruments:
            if inst.get("instrument_type") != "EQ":
                continue
            seg = inst.get("segment", "")
            if seg not in ("NSE_EQ", "E"):
                continue
            sym = (inst.get("symbol") or "").upper()
            if sym in fno_underlying and not fno_underlying[sym]:
                fno_underlying[sym] = str(inst.get("security_id", ""))

        candidates = {sym: sid for sym, sid in fno_underlying.items() if sid}
        if not candidates:
            logger.warning("[DhanScanner] no F&O-eligible equities found in scrip master")
            return []

        logger.info("[DhanScanner] %d F&O-eligible equities found, querying Dhan quotes…",
                    len(candidates))

        syms   = list(candidates.keys())
        sids   = [candidates[s] for s in syms]
        quotes = self._fetch_quotes("NSE_EQ", sids)

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
        """Near-month NSE currency futures from scrip master."""
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
            int_batch = []
            for sid in batch:
                try:
                    int_batch.append(int(sid))
                except (ValueError, TypeError):
                    pass
            if not int_batch:
                continue
            try:
                resp = self._client().post(
                    f"{_DHAN_API}/v2/marketfeed/quote",
                    json={segment: int_batch},
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
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
