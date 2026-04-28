"""
Shortlister — Layer 5 hard + soft filter gate.

Takes the full dict[str, ScanResult] from SignalScanner and returns an ordered
shortlist of instruments that are worth sending to the Analyst and Decision Engine.

Hard filters  (fail → rejected immediately, no LLM call)
──────────────────────────────────────────────────────────
  H1  scan_result.scannable == True
  H2  composite_confidence  >= MIN_CONFIDENCE   (default 0.35)
  H3  signal_count          >= MIN_SIGNALS      (default 2)
  H4  symbol status == "live" in scanner snapshot
  H5  kill switch not active  (system-wide gate)
  H6  open-position count has not hit MAX_CONCURRENT_POSITIONS  (default 3)
  H7  same-symbol same-direction block — already holding this exact position

Soft penalties  (reduce score; instrument still passes to LLM if hard filters pass)
───────────────────────────────────────────────────────────────────────────────────
  S1  low signal agreement   (agree / fired < 0.60)      → −0.10
  S2  regime_align disagrees with composite direction      → −0.15
  S3  low tick count         (< 20 ticks)                 → −0.05
  S4  stale data             (last_tick_ago > 30 s)       → −0.15
  S5  opposing open position (already long, signal short)  → −0.10
  S6  high-vol regime + confidence < 0.50                 → −0.05

Final score = composite_confidence × (1 − Σ penalties), clamped [0, 1].
Output list sorted descending by final_score; only hard-pass entries included.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.types import Regime
from ai_brain.signal_scanner import ScanResult

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

MIN_CONFIDENCE            = 0.35
MIN_SIGNALS               = 2
MAX_CONCURRENT_POSITIONS  = 3
STALE_TICK_SECONDS        = 30.0

# Soft penalty magnitudes
_P_LOW_AGREE   = 0.10
_P_REGIME_DIS  = 0.15
_P_LOW_TICKS   = 0.05
_P_STALE       = 0.15
_P_OPP_POS     = 0.10
_P_HIGHVOL_LOW = 0.05


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class SoftPenalty:
    code: str
    reason: str
    penalty: float


@dataclass
class ShortlistEntry:
    symbol: str
    scan: ScanResult
    direction: str                  # composite_direction from scan
    base_confidence: float          # composite_confidence before soft penalties
    soft_penalties: list[SoftPenalty] = field(default_factory=list)
    final_score: float = 0.0
    rank: int = 0

    # Scanner meta snapshot (populated by Shortlister)
    ltp: Optional[float] = None
    ticks: int = 0
    last_tick_ago: Optional[float] = None
    is_currency: bool = False
    is_commodity: bool = False

    # Position context
    existing_qty: int = 0           # current open qty (+ long, − short, 0 flat)
    existing_direction: Optional[str] = None   # "long" | "short" | None

    def total_penalty(self) -> float:
        return sum(p.penalty for p in self.soft_penalties)

    def penalty_summary(self) -> list[dict]:
        return [{"code": p.code, "reason": p.reason, "penalty": p.penalty}
                for p in self.soft_penalties]

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "direction":        self.direction,
            "base_confidence":  round(self.base_confidence, 4),
            "final_score":      round(self.final_score, 4),
            "rank":             self.rank,
            "ltp":              self.ltp,
            "ticks":            self.ticks,
            "last_tick_ago":    round(self.last_tick_ago, 1) if self.last_tick_ago is not None else None,
            "is_currency":      self.is_currency,
            "is_commodity":     self.is_commodity,
            "existing_qty":     self.existing_qty,
            "soft_penalties":   self.penalty_summary(),
            "scan_summary":     {
                "signal_count":    self.scan.signal_count,
                "agreeing_count":  self.scan.agreeing_count,
                "reasoning":       self.scan.reasoning,
                "signals": {
                    k: {"direction": v.direction, "confidence": round(v.confidence, 4)}
                    for k, v in self.scan.signals.items()
                },
            },
        }


@dataclass
class ShortlistReport:
    passed: list[ShortlistEntry]
    rejected: list[dict]            # {"symbol": .., "reason": ..}
    kill_switch_active: bool
    open_position_count: int
    regime: str

    def to_dict(self) -> dict:
        return {
            "passed":              [e.to_dict() for e in self.passed],
            "rejected":            self.rejected,
            "kill_switch_active":  self.kill_switch_active,
            "open_position_count": self.open_position_count,
            "regime":              self.regime,
            "pass_count":          len(self.passed),
            "reject_count":        len(self.rejected),
        }


# ── Shortlister ───────────────────────────────────────────────────────────────

class Shortlister:
    """
    Stateless filter gate.  All state comes from arguments so it is safe to
    call from multiple async contexts.
    """

    def __init__(
        self,
        min_confidence: float = MIN_CONFIDENCE,
        min_signals: int      = MIN_SIGNALS,
        max_positions: int    = MAX_CONCURRENT_POSITIONS,
        stale_seconds: float  = STALE_TICK_SECONDS,
    ) -> None:
        self._min_conf    = min_confidence
        self._min_signals = min_signals
        self._max_pos     = max_positions
        self._stale_s     = stale_seconds

    # ── Public ────────────────────────────────────────────────────────────────

    def run(
        self,
        scan_results: dict[str, ScanResult],
        scanner_meta: dict,
        open_positions: dict,       # symbol → Position (or dict with qty key)
        regime: Regime,
        kill_switch_active: bool = False,
    ) -> ShortlistReport:
        rejected: list[dict] = []
        passed:   list[ShortlistEntry] = []

        open_count = len(open_positions)

        for sym, scan in scan_results.items():
            meta  = scanner_meta.get(sym, {})
            pos   = open_positions.get(sym)

            # ── Hard filters ──────────────────────────────────────────────────
            fail = self._hard_check(
                sym, scan, meta, pos,
                kill_switch_active, open_count,
            )
            if fail:
                rejected.append({"symbol": sym, "reason": fail})
                logger.debug("shortlist REJECT %s — %s", sym, fail)
                continue

            # ── Build entry ───────────────────────────────────────────────────
            existing_qty = self._pos_qty(pos)
            existing_dir = ("long" if existing_qty > 0 else "short") if existing_qty else None
            ltp          = meta.get("ltp")
            ticks        = int(meta.get("ticks", 0))
            last_tick_ago = meta.get("last_tick_ago")

            entry = ShortlistEntry(
                symbol=sym,
                scan=scan,
                direction=scan.composite_direction,
                base_confidence=scan.composite_confidence,
                ltp=ltp,
                ticks=ticks,
                last_tick_ago=last_tick_ago,
                is_currency=bool(meta.get("is_currency", False)),
                is_commodity=bool(meta.get("is_commodity", False)),
                existing_qty=existing_qty,
                existing_direction=existing_dir,
            )

            # ── Soft penalties ────────────────────────────────────────────────
            self._apply_soft(entry, regime)

            # ── Final score ───────────────────────────────────────────────────
            raw = entry.base_confidence * (1.0 - entry.total_penalty())
            entry.final_score = max(0.0, min(1.0, raw))

            passed.append(entry)
            logger.debug(
                "shortlist PASS  %s  dir=%s  base=%.2f  penalty=%.2f  final=%.2f",
                sym, entry.direction,
                entry.base_confidence, entry.total_penalty(), entry.final_score,
            )

        # Sort by final score
        passed.sort(key=lambda e: e.final_score, reverse=True)
        for i, e in enumerate(passed):
            e.rank = i + 1

        report = ShortlistReport(
            passed=passed,
            rejected=rejected,
            kill_switch_active=kill_switch_active,
            open_position_count=open_count,
            regime=str(regime).replace("Regime.", ""),
        )
        logger.info(
            "Shortlist complete — %d passed / %d rejected  regime=%s  kill_sw=%s",
            len(passed), len(rejected), regime, kill_switch_active,
        )
        return report

    # ── Hard filter logic ─────────────────────────────────────────────────────

    def _hard_check(
        self,
        sym: str,
        scan: ScanResult,
        meta: dict,
        pos,
        kill_switch_active: bool,
        open_count: int,
    ) -> Optional[str]:
        """Return a rejection reason string, or None if the symbol passes."""

        # H5 — system kill switch
        if kill_switch_active:
            return "kill switch active"

        # H1 — not enough bars
        if not scan.scannable:
            return scan.reasoning   # e.g. "insufficient bars (12/30)"

        # H2 — confidence too low
        if scan.composite_confidence < self._min_conf:
            return (
                f"confidence {scan.composite_confidence:.2%} < "
                f"threshold {self._min_conf:.0%}"
            )

        # H3 — too few signals fired
        if scan.signal_count < self._min_signals:
            return (
                f"only {scan.signal_count} signal(s) fired "
                f"(min {self._min_signals})"
            )

        # H4 — not live
        if meta.get("status") != "live":
            return f"status={meta.get('status', 'unknown')} (not live)"

        # H6 — too many open positions
        if open_count >= self._max_pos and pos is None:
            return (
                f"max concurrent positions reached "
                f"({open_count}/{self._max_pos})"
            )

        # H7 — same symbol, same direction already open
        if pos is not None:
            qty = self._pos_qty(pos)
            if qty > 0 and scan.composite_direction == "long":
                return "already long — no add"
            if qty < 0 and scan.composite_direction == "short":
                return "already short — no add"

        return None

    # ── Soft penalty logic ────────────────────────────────────────────────────

    def _apply_soft(self, entry: ShortlistEntry, regime: Regime) -> None:
        scan = entry.scan

        # S1 — low signal agreement
        if scan.signal_count > 0:
            agree_ratio = scan.agreeing_count / scan.signal_count
            if agree_ratio < 0.60:
                entry.soft_penalties.append(SoftPenalty(
                    "S1",
                    f"low agreement {agree_ratio:.0%} ({scan.agreeing_count}/{scan.signal_count})",
                    _P_LOW_AGREE,
                ))

        # S2 — regime_align fires in opposite direction
        ra = scan.signals.get("regime_align")
        if ra and ra.fired and ra.direction != entry.direction:
            entry.soft_penalties.append(SoftPenalty(
                "S2",
                f"regime_align={ra.direction} disagrees with composite={entry.direction}",
                _P_REGIME_DIS,
            ))

        # S3 — low tick count
        if entry.ticks < 20:
            entry.soft_penalties.append(SoftPenalty(
                "S3",
                f"low liquidity ({entry.ticks} ticks)",
                _P_LOW_TICKS,
            ))

        # S4 — stale tick data
        if entry.last_tick_ago is not None and entry.last_tick_ago > self._stale_s:
            entry.soft_penalties.append(SoftPenalty(
                "S4",
                f"stale data ({entry.last_tick_ago:.0f}s ago > {self._stale_s:.0f}s)",
                _P_STALE,
            ))

        # S5 — opposing open position (reversal context)
        if entry.existing_qty:
            if entry.existing_qty > 0 and entry.direction == "short":
                entry.soft_penalties.append(SoftPenalty(
                    "S5",
                    "opposing position (long open, short signal)",
                    _P_OPP_POS,
                ))
            elif entry.existing_qty < 0 and entry.direction == "long":
                entry.soft_penalties.append(SoftPenalty(
                    "S5",
                    "opposing position (short open, long signal)",
                    _P_OPP_POS,
                ))

        # S6 — high-vol regime + low confidence
        if regime == Regime.HIGH_VOL and entry.base_confidence < 0.50:
            entry.soft_penalties.append(SoftPenalty(
                "S6",
                f"high-vol regime + low confidence ({entry.base_confidence:.0%})",
                _P_HIGHVOL_LOW,
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _pos_qty(pos) -> int:
        if pos is None:
            return 0
        if hasattr(pos, "qty"):
            return int(pos.qty)
        if isinstance(pos, dict):
            return int(pos.get("qty", 0))
        return 0
