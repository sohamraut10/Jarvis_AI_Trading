"""
Indian market trading universe for auto-discovery scanning.

Covers: NSE equities (Nifty 50, Bank Nifty, Midcap, sectoral),
        F&O high-volume extras, ETFs, MCX commodities, currency futures.
"""
from __future__ import annotations

# ── NSE Equities ──────────────────��──────────────────────────────────────────

NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "KOTAKBANK", "LT", "AXISBANK",
    "SBIN", "BAJFINANCE", "BHARTIARTL", "ASIANPAINT", "MARUTI",
    "TITAN", "SUNPHARMA", "ULTRACEMCO", "WIPRO", "NESTLEIND",
    "TECHM", "HCLTECH", "POWERGRID", "NTPC", "ONGC",
    "TATAMOTORS", "JSWSTEEL", "TATASTEEL", "HINDALCO", "COALINDIA",
    "ADANIPORTS", "BAJAJFINSV", "HDFCLIFE", "SBILIFE",
    "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "BRITANNIA",
    "EICHERMOT", "HEROMOTOCO", "BPCL", "GRASIM", "INDUSINDBK",
    "M&M", "TATACONSUM", "VEDL", "SHRIRAMFIN", "UPL", "ADANIENT",
]

NIFTY_BANK = [
    "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
    "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK",
    "BANKBARODA", "PNB",
]

NIFTY_MIDCAP_EXTRAS = [
    "ZOMATO", "PAYTM", "NYKAA", "POLICYBZR", "DELHIVERY",
    "IRFC", "RECLTD", "PFC", "IRCTC", "CONCOR",
    "TATAPOWER", "NHPC", "SAIL", "NMDC", "NATIONALUM",
]

FNO_EXTRAS = [
    "IDEA", "YESBANK", "MOTHERSON", "MFSL", "CHOLAFIN",
    "ABCAPITAL", "PIIND", "DEEPAKNTR", "AARTIIND",
]

ETF_UNIVERSE = [
    "NIFTYBEES", "BANKBEES", "GOLDBEES", "ITBEES", "JUNIORBEES",
    "LICMFGOLD", "SETFNIF50", "CPSEETF", "INFRABEES", "PSUBNKBEES",
]

# ── MCX Commodities ───────────────────────────────────────────────────────────

MCX_UNIVERSE = [
    "GOLD", "GOLDM", "SILVER", "SILVERM",
    "CRUDEOIL", "CRUDEOILM", "NATURALGAS",
    "COPPER", "ZINC", "ALUMINIUM", "NICKEL", "LEAD",
]

# ── Currency Futures (NSE) ────────────────────────────────────────────────���───

CURRENCY_FUTURES = ["USDINR", "EURINR", "GBPINR", "JPYINR"]

# ── Derived sets ─────────────────────────────────────────────────────────────

# Full equity scan universe (deduped, preserving order)
SCAN_EQUITY: list[str] = list(dict.fromkeys(
    NIFTY50 + NIFTY_BANK + NIFTY_MIDCAP_EXTRAS + FNO_EXTRAS + ETF_UNIVERSE
))

SCAN_ALL: list[str] = SCAN_EQUITY + MCX_UNIVERSE + CURRENCY_FUTURES

# Default Dhan exchange segment per symbol (used for WebSocket subscription)
SEGMENT_MAP: dict[str, str] = {
    **{s: "NSE_EQ"   for s in SCAN_EQUITY},
    **{s: "MCX_COMM" for s in MCX_UNIVERSE},
    **{s: "NSE_CURR" for s in CURRENCY_FUTURES},
}

# Human-readable asset class labels
ASSET_CLASS: dict[str, str] = {
    **{s: "Equity"    for s in NIFTY50 + NIFTY_BANK + NIFTY_MIDCAP_EXTRAS + FNO_EXTRAS},
    **{s: "ETF"       for s in ETF_UNIVERSE},
    **{s: "MCX"       for s in MCX_UNIVERSE},
    **{s: "Currency"  for s in CURRENCY_FUTURES},
}
