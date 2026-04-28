"""
Dynamic security ID lookup for Dhan instruments.

Downloads Dhan's scrip master CSV at startup to find near-month
NSE currency futures security IDs (which change monthly with expiry).
Equity IDs are static and hardcoded here.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# ── Static equity instruments ─────────────────────────────────────────────────
# (exchange_segment, security_id)
EQUITY_INSTRUMENTS: dict[str, tuple[str, str]] = {
    "RELIANCE": ("NSE_EQ",  "2885"),
    "TCS":      ("NSE_EQ",  "11536"),
    "INFY":     ("NSE_EQ",  "1594"),
    "HDFCBANK": ("NSE_EQ",  "1333"),
    "SBIN":     ("NSE_EQ",  "3045"),
}

# ── Currency pair base names ───────────────────────────────────────────────────
CURRENCY_PAIRS = ["USDINR", "EURINR", "GBPINR", "JPYINR"]


def fetch_near_month_currency_ids() -> dict[str, tuple[str, str]]:
    """
    Download scrip master and return near-month NSE currency futures.
    Returns: {"USDINR": ("NSE_CURR", "13149"), ...}
    Falls back gracefully if the download fails.
    """
    import requests
    try:
        logger.info("[Instruments] downloading Dhan scrip master…")
        resp = requests.get(_SCRIP_MASTER_URL, timeout=20)
        resp.raise_for_status()
        logger.info("[Instruments] scrip master downloaded  size=%dKB", len(resp.content) // 1024)
        return _parse_currency_ids(resp.text)
    except Exception as exc:
        logger.error("[Instruments] scrip master download failed: %s", exc)
        return {}


def _parse_currency_ids(csv_text: str) -> dict[str, tuple[str, str]]:
    today = datetime.today().date()
    reader = csv.DictReader(io.StringIO(csv_text))

    # Normalise header names — Dhan has used different column names across versions
    headers = reader.fieldnames or []
    _col = _make_col_resolver(headers)

    best: dict[str, tuple] = {}  # pair → (expiry_date, security_id)

    for row in reader:
        seg   = row.get(_col("SEM_SEGMENT",    "Sgmt"),         "")
        instr = row.get(_col("SEM_INSTRUMENT_NAME", "FinInstrmTp"), "")
        sym   = row.get(_col("SEM_TRADING_SYMBOL",  "TckrSymb"),    "")
        sid   = row.get(_col("SEM_SMST_SECURITY_ID", "ScrpCd"),     "").strip()
        exp_s = row.get(_col("SEM_EXPIRY_DATE", "XpryDt"),          "")

        if "CURR" not in seg.upper() or "FUTCUR" not in instr.upper():
            continue

        pair = next((p for p in CURRENCY_PAIRS if sym.startswith(p)), None)
        if not pair or not sid:
            continue

        try:
            expiry = datetime.strptime(exp_s[:10], "%Y-%m-%d").date()
        except Exception:
            try:
                expiry = datetime.strptime(exp_s[:10], "%d-%m-%Y").date()
            except Exception:
                continue

        if expiry < today:
            continue

        if pair not in best or expiry < best[pair][0]:
            best[pair] = (expiry, sid)

    result: dict[str, tuple[str, str]] = {}
    for pair, (expiry, sid) in best.items():
        result[pair] = ("NSE_CURR", sid)
        logger.info("[Instruments] %s → security_id=%s  expiry=%s (near-month)", pair, sid, expiry)

    missing = [p for p in CURRENCY_PAIRS if p not in result]
    if missing:
        logger.warning("[Instruments] could not find near-month IDs for: %s", missing)

    return result


def _make_col_resolver(headers: list[str]):
    """Return a function that picks the first header variant that exists."""
    header_set = set(headers)

    def resolve(*candidates: str) -> str:
        for c in candidates:
            if c in header_set:
                return c
        return candidates[0]  # fall back to first (will return "" from row.get)

    return resolve


def build_instrument_map(
    equity_symbols: list[str],
    currency_symbols: list[str],
) -> dict[str, tuple[str, str]]:
    """
    Build a complete symbol → (exchange_segment, security_id) map.
    Downloads currency IDs dynamically; uses static equity IDs.
    """
    result: dict[str, tuple[str, str]] = {}

    for sym in equity_symbols:
        if sym in EQUITY_INSTRUMENTS:
            result[sym] = EQUITY_INSTRUMENTS[sym]
        else:
            logger.warning("[Instruments] unknown equity symbol: %s (skipped)", sym)

    if currency_symbols:
        currency_ids = fetch_near_month_currency_ids()
        for sym in currency_symbols:
            if sym in currency_ids:
                result[sym] = currency_ids[sym]
            else:
                logger.warning("[Instruments] no near-month contract found for: %s (skipped)", sym)

    return result
