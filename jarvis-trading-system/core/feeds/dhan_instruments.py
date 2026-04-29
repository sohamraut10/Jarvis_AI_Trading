"""
Dhan scrip master — full market instrument discovery and search.

Downloads the complete Dhan instrument list (~100K rows) at startup,
parses it into a searchable in-memory index, and provides:
  - search(query) — find any stock, future, option, or currency contract
  - near_month(segment) — get near-month futures for a segment
  - build_instrument_map() — build the feed subscription map
"""
from __future__ import annotations

import csv
import io
import logging
import os
import pathlib
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SCRIP_MASTER_URL   = "https://images.dhan.co/api-data/api-scrip-master.csv"
_SCRIP_CACHE_PATH   = pathlib.Path("data/scrip_master.csv")
_CACHE_MAX_AGE_SECS = 8 * 3600   # re-download after 8 hours

# Segment → display label
SEGMENT_LABEL = {
    "NSE_EQ":   "NSE EQ",
    "NSE_FNO":  "NSE F&O",
    "NSE_CURR": "NSE CURR",
    "BSE_EQ":   "BSE EQ",
    "BSE_FNO":  "BSE F&O",
    "MCX_COMM": "MCX",
    "BSE_CURR": "BSE CURR",
}

# instrument_type → short badge
INSTR_BADGE = {
    "EQ":     "EQ",
    "FUTSTK": "FUT",
    "FUTIDX": "FUT",
    "FUTCUR": "FUT",
    "FUTCOM": "FUT",
    "OPTSTK": "OPT",
    "OPTIDX": "OPT",
    "OPTCUR": "OPT",
    "INDEX":  "IDX",
}

# Default lot sizes per segment (fallback when CSV field is missing)
DEFAULT_LOT = {
    "NSE_EQ":   1,
    "NSE_FNO":  50,
    "NSE_CURR": 1000,
    "MCX_COMM": 1,
}


def _resolve_cols(headers: list[str]):
    """Return a function that picks the first header variant that exists."""
    hset = set(headers)
    def pick(*candidates: str) -> str:
        for c in candidates:
            if c in hset:
                return c
        return candidates[0]
    return pick


class ScripMaster:
    """In-memory Dhan scrip master with fast prefix search."""

    def __init__(self) -> None:
        self._instruments: list[dict] = []
        self._by_sid: dict[str, dict] = {}
        self._loaded = False
        self._load_time = 0.0

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        Download (or use cached) scrip master CSV and parse it.
        Returns True on success.
        """
        csv_text = self._read_or_download()
        if not csv_text:
            return False
        try:
            self._parse(csv_text)
            self._loaded = True
            self._load_time = time.time()
            logger.info("[ScripMaster] loaded %d instruments", len(self._instruments))
            return True
        except Exception as exc:
            logger.error("[ScripMaster] parse error: %s", exc)
            return False

    def _read_or_download(self) -> Optional[str]:
        import requests
        # Use cache if fresh
        if _SCRIP_CACHE_PATH.exists():
            age = time.time() - _SCRIP_CACHE_PATH.stat().st_mtime
            if age < _CACHE_MAX_AGE_SECS:
                logger.info("[ScripMaster] using cached scrip master (age=%.0fh)", age / 3600)
                return _SCRIP_CACHE_PATH.read_text(encoding="utf-8", errors="replace")

        logger.info("[ScripMaster] downloading full market instrument list…")
        try:
            resp = requests.get(_SCRIP_MASTER_URL, timeout=30)
            resp.raise_for_status()
            _SCRIP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SCRIP_CACHE_PATH.write_bytes(resp.content)
            size_kb = len(resp.content) // 1024
            logger.info("[ScripMaster] downloaded %dKB  (%d chars)", size_kb, len(resp.text))
            return resp.text
        except Exception as exc:
            logger.error("[ScripMaster] download failed: %s", exc)
            if _SCRIP_CACHE_PATH.exists():
                logger.warning("[ScripMaster] using stale cache as fallback")
                return _SCRIP_CACHE_PATH.read_text(encoding="utf-8", errors="replace")
            return None

    def _parse(self, csv_text: str) -> None:
        today = datetime.today().date()
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = list(reader.fieldnames or [])
        col = _resolve_cols(headers)

        c_sid   = col("SEM_SMST_SECURITY_ID", "ScrpCd",   "SECURITY_ID")
        c_sym   = col("SEM_TRADING_SYMBOL",   "TckrSymb",  "TRADING_SYMBOL")
        c_seg   = col("SEM_SEGMENT",          "Sgmt",      "SEGMENT")
        c_instr = col("SEM_INSTRUMENT_NAME",  "FinInstrmTp","INSTRUMENT_NAME")
        c_exp   = col("SEM_EXPIRY_DATE",      "XpryDt",    "EXPIRY_DATE")
        c_strk  = col("SEM_STRIKE_PRICE",     "StrkPric",  "STRIKE_PRICE")
        c_opt   = col("SEM_OPTION_TYPE",      "OptnTp",    "OPTION_TYPE")
        c_lot   = col("SEM_LOT_UNITS",        "LotSz",     "LOT_UNITS")
        c_undl  = col("SEM_UNDERLYING_SYMBOL","UndrlygScty","UNDERLYING_SYMBOL")
        c_name  = col("SEM_CUSTOM_SYMBOL",    "SEM_TRADING_SYMBOL", "CUSTOM_SYMBOL")

        instruments = []
        by_sid: dict[str, dict] = {}

        for row in reader:
            sid   = (row.get(c_sid)   or "").strip()
            sym   = (row.get(c_sym)   or "").strip()
            seg   = (row.get(c_seg)   or "").strip()
            instr = (row.get(c_instr) or "").strip().upper()
            exp_s = (row.get(c_exp)   or "").strip()
            strk  = (row.get(c_strk)  or "").strip()
            opt   = (row.get(c_opt)   or "").strip().upper()
            lot_s = (row.get(c_lot)   or "").strip()
            undl  = (row.get(c_undl)  or sym).strip()

            if not sid or not sym or not seg:
                continue

            # Skip expired contracts
            expiry_date = None
            if exp_s:
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
                    try:
                        expiry_date = datetime.strptime(exp_s[:10], fmt).date()
                        break
                    except ValueError:
                        continue
                if expiry_date and expiry_date < today:
                    continue

            # Lot size
            try:
                lot = int(float(lot_s)) if lot_s else DEFAULT_LOT.get(seg, 1)
            except ValueError:
                lot = DEFAULT_LOT.get(seg, 1)

            # Strike
            strike = None
            try:
                strike = float(strk) if strk and strk not in ("0", "0.0") else None
            except ValueError:
                pass

            # Build a human-readable display name
            display = _make_display(sym, instr, expiry_date, strike, opt)

            entry = {
                "security_id":    sid,
                "symbol":         sym,
                "display":        display,
                "underlying":     undl,
                "segment":        seg,
                "instrument_type": instr,
                "expiry":         expiry_date.isoformat() if expiry_date else None,
                "strike":         strike,
                "option_type":    opt if opt in ("CE", "PE") else None,
                "lot_size":       lot,
                "badge":          INSTR_BADGE.get(instr, instr[:3]),
                "seg_label":      SEGMENT_LABEL.get(seg, seg),
            }
            instruments.append(entry)
            by_sid[sid] = entry

        # Sort: EQ first, then futures (near-month first), then options
        def _sort_key(e):
            t = e["instrument_type"]
            order = {"EQ": 0, "FUTSTK": 1, "FUTIDX": 1, "FUTCUR": 1,
                     "FUTCOM": 1, "OPTSTK": 2, "OPTIDX": 2, "OPTCUR": 2}
            return (order.get(t, 9), e["expiry"] or "", e["symbol"])

        instruments.sort(key=_sort_key)
        self._instruments = instruments
        self._by_sid = by_sid

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        segments: Optional[list[str]] = None,
        limit: int = 30,
    ) -> list[dict]:
        """
        Search instruments by symbol prefix or underlying name.
        Returns at most `limit` results, prioritising exact prefix matches.
        """
        if not self._loaded:
            return []

        q = query.strip().upper()
        if not q:
            return []

        exact: list[dict] = []
        starts: list[dict] = []
        contains: list[dict] = []

        for inst in self._instruments:
            if segments and inst["segment"] not in segments:
                continue
            sym  = inst["symbol"].upper()
            undl = (inst["underlying"] or "").upper()

            if sym == q or undl == q:
                exact.append(inst)
            elif sym.startswith(q) or undl.startswith(q):
                starts.append(inst)
            elif q in sym or q in undl:
                contains.append(inst)

            if len(exact) + len(starts) + len(contains) > limit * 3:
                break

        combined = (exact + starts + contains)[:limit]
        return combined

    def get_by_sid(self, security_id: str) -> Optional[dict]:
        return self._by_sid.get(str(security_id))

    def near_month_futures(self, segments: Optional[list[str]] = None) -> list[dict]:
        """Return one near-month futures contract per underlying symbol."""
        today = datetime.today().date()
        best: dict[str, dict] = {}
        FUT_TYPES = {"FUTSTK", "FUTIDX", "FUTCUR", "FUTCOM"}

        for inst in self._instruments:
            if inst["instrument_type"] not in FUT_TYPES:
                continue
            if segments and inst["segment"] not in segments:
                continue
            if not inst["expiry"]:
                continue
            exp = datetime.fromisoformat(inst["expiry"]).date()
            if exp < today:
                continue
            key = inst["underlying"] or inst["symbol"]
            if key not in best or exp < datetime.fromisoformat(best[key]["expiry"]).date():
                best[key] = inst

        return list(best.values())

    def is_loaded(self) -> bool:
        return self._loaded

    def stats(self) -> dict:
        from collections import Counter
        counts = Counter(i["segment"] for i in self._instruments)
        return {"total": len(self._instruments), "by_segment": dict(counts)}


def _make_display(sym: str, instr: str, expiry, strike, opt: str) -> str:
    """Build a human-friendly display name."""
    if instr == "EQ":
        return sym
    exp_str = expiry.strftime("%d %b").upper() if expiry else ""
    if instr in ("OPTSTK", "OPTIDX", "OPTCUR") and strike:
        return f"{sym} {int(strike) if strike == int(strike) else strike} {opt} {exp_str}"
    if instr in ("FUTSTK", "FUTIDX", "FUTCUR", "FUTCOM"):
        return f"{sym} {exp_str} FUT"
    return sym


# ── Module-level singleton ────────────────────────────────────────────────────

_scrip_master = ScripMaster()


def get_scrip_master() -> ScripMaster:
    return _scrip_master


def load_scrip_master() -> bool:
    return _scrip_master.load()


def search_instruments(query: str, segments: Optional[list[str]] = None, limit: int = 30) -> list[dict]:
    return _scrip_master.search(query, segments=segments, limit=limit)


# ── Legacy helpers (used by build_instrument_map) ────────────────────────────

EQUITY_INSTRUMENTS: dict[str, tuple[str, str]] = {
    "RELIANCE": ("NSE_EQ", "2885"),
    "TCS":      ("NSE_EQ", "11536"),
    "INFY":     ("NSE_EQ", "1594"),
    "HDFCBANK": ("NSE_EQ", "1333"),
    "SBIN":     ("NSE_EQ", "3045"),
}

CURRENCY_PAIRS = ["USDINR", "EURINR", "GBPINR", "JPYINR"]


def build_instrument_map(
    equity_symbols: list[str],
    currency_symbols: list[str],
    commodity_symbols: Optional[list[str]] = None,
) -> dict[str, tuple[str, str, int]]:
    """
    Build symbol → (exchange_segment, security_id, lot_size) map.
    Uses scrip master if loaded, falls back to static equity IDs + currency search.
    """
    result: dict[str, tuple[str, str, int]] = {}
    commodity_symbols = commodity_symbols or []

    if _scrip_master.is_loaded():
        for sym in equity_symbols:
            hits = _scrip_master.search(sym, segments=["NSE_EQ"], limit=5)
            exact = next((h for h in hits if h["symbol"] == sym and h["instrument_type"] == "EQ"), None)
            if exact:
                result[sym] = (exact["segment"], exact["security_id"], exact["lot_size"])
            elif sym in EQUITY_INSTRUMENTS:
                seg, sid = EQUITY_INSTRUMENTS[sym]
                result[sym] = (seg, sid, 1)
            else:
                logger.warning("[Instruments] %s not found in scrip master", sym)

        for pair in currency_symbols:
            # Try NSE_CURR segment first, then broaden search if nothing found
            hits = _scrip_master.search(pair, segments=["NSE_CURR"], limit=20)
            futures = [h for h in hits if h["instrument_type"] == "FUTCUR" and h["expiry"]]

            if not futures:
                # Broader fallback: search all segments for anything currency-like
                all_hits = _scrip_master.search(pair, limit=30)
                logger.info("[Instruments] %s — NSE_CURR gave 0, broad search found %d: %s",
                            pair, len(all_hits),
                            [(h["segment"], h["instrument_type"], h["symbol"]) for h in all_hits[:5]])
                # Accept any segment that looks like currency futures
                futures = [
                    h for h in all_hits
                    if h["expiry"] and (
                        h["instrument_type"] in ("FUTCUR", "FUTIDX", "FUTSTK") or
                        "CUR" in h["segment"].upper()
                    )
                ]

            futures.sort(key=lambda h: h["expiry"])
            if futures:
                f = futures[0]
                result[pair] = (f["segment"], f["security_id"], f["lot_size"])
                logger.info("[Instruments] %s → seg=%s  sid=%s  expiry=%s  lot=%d",
                            pair, f["segment"], f["security_id"], f["expiry"], f["lot_size"])
            else:
                logger.warning("[Instruments] no contract found for %s — skipping", pair)

        for sym in commodity_symbols:
            hits = _scrip_master.search(sym, segments=["MCX_COMM"], limit=20)
            futures = [h for h in hits if h["instrument_type"] == "FUTCOM" and h["expiry"]]
            futures.sort(key=lambda h: h["expiry"])
            if futures:
                f = futures[0]
                result[sym] = (f["segment"], f["security_id"], f["lot_size"])
                logger.info("[Instruments] %s (MCX) → sid=%s  expiry=%s  lot=%d",
                            sym, f["security_id"], f["expiry"], f["lot_size"])
            else:
                logger.warning("[Instruments] no near-month MCX contract for %s", sym)

    else:
        # Fallback: static equity IDs + raw currency CSV fetch
        for sym in equity_symbols:
            if sym in EQUITY_INSTRUMENTS:
                seg, sid = EQUITY_INSTRUMENTS[sym]
                result[sym] = (seg, sid, 1)

        if currency_symbols:
            for pair in currency_symbols:
                hits = _raw_currency_search(pair)
                if hits:
                    result[pair] = hits[0]

        if commodity_symbols:
            for sym in commodity_symbols:
                hits = _raw_commodity_search(sym)
                if hits:
                    result[sym] = hits[0]

    return result


def _raw_commodity_search(sym: str) -> list[tuple[str, str, int]]:
    """Minimal fallback: find near-month MCX FUTCOM contract from scrip master CSV."""
    import requests
    try:
        resp = requests.get(_SCRIP_MASTER_URL, timeout=20)
        resp.raise_for_status()
        today = datetime.today().date()
        reader = csv.DictReader(io.StringIO(resp.text))
        headers = list(reader.fieldnames or [])
        col = _resolve_cols(headers)
        c_sid   = col("SEM_SMST_SECURITY_ID", "ScrpCd")
        c_sym   = col("SEM_TRADING_SYMBOL",   "TckrSymb")
        c_seg   = col("SEM_SEGMENT",          "Sgmt")
        c_instr = col("SEM_INSTRUMENT_NAME",  "FinInstrmTp")
        c_exp   = col("SEM_EXPIRY_DATE",      "XpryDt")
        c_lot   = col("SEM_LOT_UNITS",        "LotSz")
        best = None
        best_exp = None
        for row in reader:
            seg   = (row.get(c_seg)   or "").strip().upper()
            instr = (row.get(c_instr) or "").strip().upper()
            tsym  = (row.get(c_sym)   or "").strip()
            sid   = (row.get(c_sid)   or "").strip()
            exp_s = (row.get(c_exp)   or "").strip()
            lot_s = (row.get(c_lot)   or "1").strip()
            if "MCX" not in seg or instr != "FUTCOM":
                continue
            if not tsym.startswith(sym):
                continue
            if not sid:
                continue
            try:
                exp = datetime.strptime(exp_s[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if exp < today:
                continue
            if best_exp is None or exp < best_exp:
                best_exp = exp
                try:
                    lot = int(float(lot_s))
                except Exception:
                    lot = 1
                best = ("MCX_COMM", sid, lot)
        return [best] if best else []
    except Exception as exc:
        logger.error("[Instruments] fallback commodity search failed: %s", exc)
        return []


def _raw_currency_search(pair: str) -> list[tuple[str, str, int]]:
    """Minimal fallback: fetch scrip master and find near-month currency future."""
    import requests
    try:
        resp = requests.get(_SCRIP_MASTER_URL, timeout=20)
        resp.raise_for_status()
        today = datetime.today().date()
        reader = csv.DictReader(io.StringIO(resp.text))
        headers = list(reader.fieldnames or [])
        col = _resolve_cols(headers)
        c_sid = col("SEM_SMST_SECURITY_ID", "ScrpCd")
        c_sym = col("SEM_TRADING_SYMBOL", "TckrSymb")
        c_seg = col("SEM_SEGMENT", "Sgmt")
        c_instr = col("SEM_INSTRUMENT_NAME", "FinInstrmTp")
        c_exp = col("SEM_EXPIRY_DATE", "XpryDt")
        c_lot = col("SEM_LOT_UNITS", "LotSz")
        best = None
        best_exp = None
        for row in reader:
            seg = (row.get(c_seg) or "").strip()
            instr = (row.get(c_instr) or "").strip().upper()
            sym = (row.get(c_sym) or "").strip()
            sid = (row.get(c_sid) or "").strip()
            exp_s = (row.get(c_exp) or "").strip()
            lot_s = (row.get(c_lot) or "1000").strip()
            if "CURR" not in seg.upper() or instr != "FUTCUR":
                continue
            if not sym.startswith(pair):
                continue
            if not sid:
                continue
            try:
                exp = datetime.strptime(exp_s[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if exp < today:
                continue
            if best_exp is None or exp < best_exp:
                best_exp = exp
                try:
                    lot = int(float(lot_s))
                except Exception:
                    lot = 1000
                best = (seg, sid, lot)
        return [best] if best else []
    except Exception as exc:
        logger.error("[Instruments] fallback currency search failed: %s", exc)
        return []
