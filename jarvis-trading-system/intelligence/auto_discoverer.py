"""
AutoDiscoverer — scans the full Indian market universe every 5 minutes
via NSE India's live API, scores each instrument, and returns the top
trading opportunities for the AI Brain to analyse and trade.

Equity scoring weights (each sub-score normalised 0–1, total → 0–100):
  momentum   35%  |daily % change| — today's price action
  day_range  25%  intraday high–low as % of LTP — ATR proxy
  range_pos  20%  proximity to 52-week high or low — breakout zone
  trend_30d  20%  |30-day % return| — sustained directional trend

Options scoring weights:
  atm_prox   30%  proximity to ATM — most sensitive to underlying move
  oi_score   30%  open interest rank — liquidity proxy
  vol_score  25%  traded volume rank — activity proxy
  iv_score   15%  moderate IV sweet-spot (not too cheap, not too expensive)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_W_MOM    = 0.35
_W_RANGE  = 0.25
_W_RPOS   = 0.20
_W_TREND  = 0.20

_OW_ATM   = 0.30
_OW_OI    = 0.30
_OW_VOL   = 0.25
_OW_IV    = 0.15

TOP_EQUITY     = 7    # max equity picks per scan
TOP_OPTIONS    = 5    # max options picks per scan
SCAN_INTERVAL  = 300  # seconds between scans

# NSE index groups to scan for equity opportunities
_INDEX_KEYS = ("NIFTY50", "BANKNIFTY", "MIDCAP50")

# Index options to scan — maps underlying symbol → NSE option-chain key
_INDEX_OPTIONS = {
    "NIFTY":     "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY":  "FINNIFTY",
}

# Strikes ATM ± this many to include per expiry per index
_ATM_WING = 3


@dataclass
class DiscoveredInstrument:
    symbol:           str
    ltp:              float
    segment:          str            # "NSE_EQ" | "NSE_FNO" | "MCX_COMM" | "NSE_CURR"
    asset_class:      str            # "Equity" | "Index Option" | "Stock Option" | "ETF" | "MCX" | "Currency"
    score:            float          # 0–100
    rank:             int
    direction:        str            # "BUY" | "SELL" | "FLAT"
    change_pct:       float          # today's % change of underlying
    day_range_pct:    float          # intraday range as % of LTP
    week52_high:      Optional[float] = None
    week52_low:       Optional[float] = None
    trend_30d:        float = 0.0
    reasoning:        str   = ""
    # Options-specific fields (None for equities)
    underlying:       Optional[str]   = None   # "NIFTY", "BANKNIFTY"
    underlying_price: Optional[float] = None
    strike:           Optional[float] = None
    option_type:      Optional[str]   = None   # "CE" | "PE"
    expiry:           Optional[str]   = None   # ISO "2026-04-30"
    open_interest:    Optional[float] = None
    volume:           Optional[float] = None
    iv:               Optional[float] = None
    security_id:      Optional[str]   = None   # pre-resolved from scrip master


class AutoDiscoverer:
    """
    Periodically fetches NSE live data for equities AND index options,
    scores every instrument, and returns the top-ranked opportunities.
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
        """Run a full universe scan (non-blocking via thread pool)."""
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
        self._market_open = self._nse.is_market_open()

        # ── Equity scan ───────────────────────────────────────────────────────
        eq_candidates: list[DiscoveredInstrument] = []
        seen: set[str] = set()
        for idx_key in _INDEX_KEYS:
            rows = self._nse.get_index_stocks(idx_key)
            for row in rows:
                sym = row.get("symbol", "").strip()
                if not sym or sym in seen or " " in sym:
                    continue
                seen.add(sym)
                inst = self._score_equity_row(row)
                if inst:
                    eq_candidates.append(inst)

        eq_candidates.sort(key=lambda x: x.score, reverse=True)
        for i, c in enumerate(eq_candidates):
            c.rank = i + 1
        top_equity = eq_candidates[:TOP_EQUITY]

        # ── Index options scan ────────────────────────────────────────────────
        opt_candidates: list[DiscoveredInstrument] = []
        for underlying, oc_key in _INDEX_OPTIONS.items():
            try:
                opts = self._scan_options(underlying, oc_key)
                opt_candidates.extend(opts)
            except Exception as exc:
                logger.warning("[AutoDisc] options scan failed for %s: %s", underlying, exc)

        opt_candidates.sort(key=lambda x: x.score, reverse=True)
        top_options = opt_candidates[:TOP_OPTIONS]
        for i, o in enumerate(top_options):
            o.rank = len(top_equity) + i + 1

        combined = top_equity + top_options
        logger.info(
            "[AutoDisc] equity=%d  options=%d  market_open=%s  top: %s",
            len(top_equity), len(top_options), self._market_open,
            "  ".join(f"{c.symbol[:18]}({c.score:.0f}/{c.direction})" for c in combined[:5]),
        )
        return combined

    def _scan_options(self, underlying: str, oc_key: str) -> list[DiscoveredInstrument]:
        """Score ATM ± _ATM_WING strikes of the nearest expiry for an index."""
        chain = self._nse.get_option_chain_index(oc_key)
        uv    = float(chain.get("underlying_value") or 0)
        data  = chain.get("data", [])
        expiry_dates = chain.get("expiry_dates", [])
        if not data or uv <= 0 or not expiry_dates:
            return []

        nearest_expiry = expiry_dates[0]
        near_data = [r for r in data if r.get("expiryDate") == nearest_expiry]

        strikes_available = sorted({float(r["strikePrice"]) for r in near_data})
        if not strikes_available:
            return []
        atm_strike = min(strikes_available, key=lambda s: abs(s - uv))
        atm_idx    = strikes_available.index(atm_strike)
        wing_strikes = set(
            strikes_available[max(0, atm_idx - _ATM_WING): atm_idx + _ATM_WING + 1]
        )

        # Determine index trend
        index_pchange = self._get_index_pchange(underlying)

        # Build normalisation baselines from the full near-expiry data
        all_oi, all_vol = [], []
        for row in near_data:
            for side in ("CE", "PE"):
                s = row.get(side) or {}
                if float(s.get("openInterest") or 0) > 0:
                    all_oi.append(float(s["openInterest"]))
                if float(s.get("totalTradedVolume") or 0) > 0:
                    all_vol.append(float(s["totalTradedVolume"]))
        max_oi  = max(all_oi,  default=1)
        max_vol = max(all_vol, default=1)

        results: list[DiscoveredInstrument] = []
        for row in near_data:
            strike = float(row.get("strikePrice") or 0)
            if strike not in wing_strikes:
                continue
            for side in ("CE", "PE"):
                opt = row.get(side) or {}
                ltp = float(opt.get("lastPrice") or 0)
                if ltp <= 0:
                    continue
                oi   = float(opt.get("openInterest")       or 0)
                vol  = float(opt.get("totalTradedVolume")   or 0)
                iv   = float(opt.get("impliedVolatility")   or 0)
                pchg = float(opt.get("pChange")             or 0)

                atm_score = max(0.0, 1.0 - abs(strike - uv) / max(uv * 0.015, 1))
                oi_score  = oi  / max_oi  if max_oi  > 0 else 0.0
                vol_score = vol / max_vol if max_vol > 0 else 0.0
                iv_score  = max(0.0, 1.0 - abs(iv - 17.5) / 17.5) if iv > 0 else 0.0

                score = (
                    _OW_ATM * atm_score +
                    _OW_OI  * oi_score  +
                    _OW_VOL * vol_score +
                    _OW_IV  * iv_score
                ) * 100

                # Penalise wrong-direction option in a strong trend
                if index_pchange > 0.5 and side == "PE":
                    score *= 0.70
                elif index_pchange < -0.5 and side == "CE":
                    score *= 0.70

                direction = ("BUY" if side == "CE" else "SELL") if abs(index_pchange) > 0.3 else "FLAT"

                # Build display symbol and ISO expiry
                try:
                    from datetime import datetime as _dt
                    # NSE expiry format is "30-Apr-2026"
                    exp_dt    = _dt.strptime(nearest_expiry, "%d-%b-%Y")
                    exp_str   = exp_dt.strftime("%d %b %Y")
                    expiry_iso = exp_dt.strftime("%Y-%m-%d")
                except Exception:
                    exp_str    = nearest_expiry
                    expiry_iso = nearest_expiry

                display_sym = f"{underlying} {int(strike)} {side} {exp_str}"

                results.append(DiscoveredInstrument(
                    symbol=display_sym,
                    ltp=round(ltp, 2),
                    segment="NSE_FNO",
                    asset_class="Index Option",
                    score=round(score, 1),
                    rank=0,
                    direction=direction,
                    change_pct=round(pchg, 2),
                    day_range_pct=0.0,
                    reasoning=(
                        f"OI={int(oi/1000)}K  vol={int(vol/1000)}K  "
                        f"IV={iv:.1f}%  Δ{index_pchange:+.2f}%  spot={int(uv)}"
                    ),
                    underlying=underlying,
                    underlying_price=round(uv, 2),
                    strike=strike,
                    option_type=side,
                    expiry=expiry_iso,
                    open_interest=oi,
                    volume=vol,
                    iv=round(iv, 2),
                ))
        return results

    def _get_index_pchange(self, underlying: str) -> float:
        """Fetch today's % change for an index to determine direction bias."""
        try:
            idx_key = "NIFTY50" if underlying == "NIFTY" else (
                "BANKNIFTY" if underlying == "BANKNIFTY" else "FINNIFTY"
            )
            rows = self._nse.get_index_stocks(idx_key)
            for r in rows:
                sym = r.get("symbol", "").upper()
                if sym in (underlying, "NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE"):
                    return float(r.get("pChange") or 0)
            # Fallback: first row is usually the index summary
            if rows:
                return float(rows[0].get("pChange") or 0)
        except Exception:
            pass
        return 0.0

    def _score_equity_row(self, row: dict) -> Optional[DiscoveredInstrument]:
        try:
            sym   = row.get("symbol", "").strip()
            ltp   = float(row.get("lastPrice")  or 0)
            if ltp <= 0:
                return None

            pchange = float(row.get("pChange")      or 0)
            day_hi  = float(row.get("dayHigh")       or ltp)
            day_lo  = float(row.get("dayLow")        or ltp)
            wk52_hi = float(row.get("nearWKH")       or ltp * 1.3)
            wk52_lo = float(row.get("nearWKL")       or ltp * 0.7)
            ch30d   = float(row.get("perChange30d")  or 0)

            mom_s  = min(abs(pchange) / 5.0, 1.0)
            rng_s  = min((day_hi - day_lo) / ltp / 0.03, 1.0)
            span   = wk52_hi - wk52_lo
            pos52  = (ltp - wk52_lo) / span if span > 0 else 0.5
            rpos_s = max(1.0 - abs(pos52 - 0.5) * 2, 0.0)
            t30_s  = min(abs(ch30d) / 15.0, 1.0)
            score  = (_W_MOM * mom_s + _W_RANGE * rng_s + _W_RPOS * rpos_s + _W_TREND * t30_s) * 100

            if pchange > 0.5 and pos52 > 0.60:
                direction = "BUY"
            elif pchange < -0.5 and pos52 < 0.40:
                direction = "SELL"
            else:
                direction = "FLAT"

            from core.market.universe import ASSET_CLASS
            asset_class = ASSET_CLASS.get(sym, "Equity")

            return DiscoveredInstrument(
                symbol=sym, ltp=ltp, segment="NSE_EQ",
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
