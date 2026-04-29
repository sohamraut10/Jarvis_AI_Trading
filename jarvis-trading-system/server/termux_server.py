"""
JARVIS Termux Server — no FastAPI, no pydantic, no Rust dependencies.

Ports
-----
  8765  WebSocket   (websockets library — pure Python)
  8766  HTTP API    (asyncio TCP — pure stdlib)

Start: python -m server.termux_server
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, parse_qs

import numpy as np

from core.broker.base_broker import Order, OrderSide, OrderType, ProductType
from core.broker.paper_broker import PaperBroker
from core.risk.kelly_sizer import KellySizer
from core.risk.risk_manager import RiskManager
from core.types import Regime
from intelligence.alpha_decay_monitor import AlphaDecayMonitor
from intelligence.intent_logger import IntentLogger
from intelligence.pair_selector import PairSelector
from intelligence.pnl_tracker import PnLTracker
from intelligence.regime_classifier import RegimeClassifier
from intelligence.strategy_shift_engine import StrategyShiftEngine
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide
# ── Layer 5 AI Brain ───────────────────────────────────────────────────────────
from ai_brain.ai_router import AIRouter
from ai_brain.cost_throttle import CostThrottle
from ai_brain.signal_scanner import SignalScanner
from ai_brain.shortlister import Shortlister
from ai_brain.analyst import Analyst
from ai_brain.decision_engine import DecisionEngine
from ai_brain.trade_monitor import TradeMonitor
from ai_brain.action_executor import ActionExecutor
from strategies.momentum.orb_breakout import ORBBreakout
from strategies.momentum.rsi_momentum import RSIMomentum
from strategies.momentum.vwap_breakout import VWAPBreakout
from strategies.trend.ema_crossover import EMACrossover
from strategies.trend.supertrend import SuperTrend

import os

import websockets

logger = logging.getLogger(__name__)

# ── Termux / Android resource profile ─────────────────────────────────────────
_TERMUX = os.environ.get("TERMUX_MODE", "").lower() in ("1", "true", "yes")

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ── Settings (pure JSON, no pydantic) ─────────────────────────────────────────

SETTINGS_FILE = pathlib.Path("data/settings.json")

_DEFAULTS: dict = {
    "paper_mode": True, "initial_capital": 10_000,
    "kill_switch_pct": 0.03, "kelly_fraction": 0.5,
    "hmm_states": 4, "regime_lookback_bars": 200,
    "sharpe_rank_window_days": 20, "log_level": "INFO",
    "intent_log_path": "logs/intent.jsonl", "pnl_db_path": "data/pnl.db",
}

def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return {**_DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULTS)

# ── Constants ─────────────────────────────────────────────────────────────────

BROADCAST_INTERVAL_MS  = 1000 if _TERMUX else 500   # halve WS broadcast rate on Android
REGIME_RECLASSIFY_BARS = 5
SIGNAL_DEDUP_SECONDS   = 60
WS_PORT   = 8765
HTTP_PORT = 8766
AI_BRAIN_WARMUP_S      = 90     # wait before first brain cycle (bars need to accumulate)
AI_BRAIN_INTERVAL_S    = 300    # re-run full pipeline every 5 min
AI_BRAIN_MAX_PER_CYCLE = 2 if _TERMUX else 3        # fewer LLM calls on Android (cost + latency)

WATCH_SYMBOLS: list[str] = []   # populated by auto-discovery (NSE equities)
CURRENCY_SYMBOLS: list[str] = ["USDINR", "EURINR", "GBPINR", "JPYINR"]  # NSE currency futures
MCX_SYMBOLS: list[str] = []        # disabled — focus on NSE equities + currency
BASE_PRICES: dict[str, float] = {
    "RELIANCE": 2500.0, "TCS": 3800.0, "INFY": 1500.0,
    "HDFCBANK": 1700.0, "SBIN": 800.0,
    "USDINR": 84.0, "EURINR": 90.0, "GBPINR": 105.0, "JPYINR": 0.55,
    "CRUDEOIL": 6500.0, "GOLD": 72000.0, "SILVER": 88000.0,
    "NATURALGAS": 230.0, "COPPER": 780.0,
}
# Lot sizes for currency futures (NSE standard)
CURRENCY_LOT_SIZES: dict[str, int] = {
    "USDINR": 1000, "EURINR": 1000, "GBPINR": 1000, "JPYINR": 100_000,
}
_TF_MAP: dict[str, list[str]] = {
    "1min": ["vwap_breakout"],
    "5min": ["ema_crossover", "orb_breakout", "rsi_momentum"],
    "15min": ["supertrend"],
}

# ── Bar Aggregator ─────────────────────────────────────────────────────────────

class BarAggregator:
    _INTERVALS: dict[str, int] = {"1min": 60, "5min": 300, "15min": 900}

    def __init__(self) -> None:
        self._state: dict[tuple, dict] = {}

    def update(self, symbol: str, price: float, volume: float, ts: datetime) -> list[Bar]:
        closed: list[Bar] = []
        for tf, seconds in self._INTERVALS.items():
            key = (symbol, tf)
            bucket = self._align(ts, seconds)
            if key not in self._state:
                self._state[key] = {
                    "open": price, "high": price, "low": price,
                    "close": price, "volume": volume, "bucket": bucket,
                }
                continue
            st = self._state[key]
            if bucket > st["bucket"]:
                closed.append(Bar(
                    symbol=symbol, timeframe=tf,
                    open=st["open"], high=st["high"], low=st["low"],
                    close=st["close"], volume=st["volume"], timestamp=st["bucket"],
                ))
                self._state[key] = {
                    "open": price, "high": price, "low": price,
                    "close": price, "volume": volume, "bucket": bucket,
                }
            else:
                st["high"] = max(st["high"], price)
                st["low"]  = min(st["low"],  price)
                st["close"] = price
                st["volume"] += volume
        return closed

    @staticmethod
    def _align(ts: datetime, seconds: int) -> datetime:
        mins = seconds // 60
        return ts.replace(minute=(ts.minute // mins) * mins, second=0, microsecond=0)

# ── Simulated Price Feed ───────────────────────────────────────────────────────

class SimulatedFeed:
    TICK_INTERVAL  = 0.5 if _TERMUX else 0.1   # 2 ticks/s on Android vs 10/s on desktop
    SIGMA          = 0.0004
    VOL_REGIME_MULT = 3.0

    def __init__(self, symbols: list[str], base_prices: Optional[dict] = None) -> None:
        self._symbols = symbols
        self._prices  = dict(base_prices or {s: 100.0 for s in symbols})
        self._running = False
        self._regime  = Regime.SIDEWAYS

    def set_regime(self, regime: Regime) -> None:
        self._regime = regime

    async def start(self, tick_cb) -> None:
        self._running = True
        rng = np.random.default_rng()
        while self._running:
            sigma = self.SIGMA * (self.VOL_REGIME_MULT if self._regime == Regime.HIGH_VOL else 1.0)
            for sym in self._symbols:
                noise = float(rng.normal(0.0, sigma))
                self._prices[sym] = max(self._prices[sym] * (1.0 + noise), 0.01)
                await tick_cb(sym, self._prices[sym], int(rng.exponential(800)))
            await asyncio.sleep(self.TICK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def add_instrument(
        self,
        exchange_segment: str = "",
        security_id: str = "",
        symbol: str = "",
        lot_size: int = 1,
        initial_price: Optional[float] = None,
    ) -> bool:
        if symbol and symbol not in self._symbols:
            price = initial_price or BASE_PRICES.get(symbol, 100.0)
            self._prices[symbol] = price
            self._symbols.append(symbol)
        return True

    def remove_symbol(self, symbol: str) -> None:
        if symbol in self._symbols:
            self._symbols.remove(symbol)
            self._prices.pop(symbol, None)

# ── JARVIS Engine ──────────────────────────────────────────────────────────────

class JarvisEngine:
    def __init__(
        self,
        initial_capital: float = 10_000.0,
        kill_switch_amount: float = 300.0,
        kelly_fraction: float = 0.5,
        intent_log_path: str = "logs/intent.jsonl",
        pnl_db_path: str = "data/pnl.db",
        feed=None,
    ) -> None:
        self._broker       = PaperBroker(initial_capital=initial_capital, kill_switch_amount=kill_switch_amount)
        self._risk_manager = RiskManager(self._broker, kill_switch_amount=kill_switch_amount)
        self._kelly_sizer  = KellySizer(kelly_fraction=kelly_fraction)
        self._aggregator   = BarAggregator()
        self._regime_clf   = RegimeClassifier()
        self._alpha_monitor = AlphaDecayMonitor()
        self._shift_engine = StrategyShiftEngine()
        self._intent_logger = IntentLogger(intent_log_path)
        self._pnl_tracker  = PnLTracker(pnl_db_path)
        self._strategies: dict[str, BaseStrategy] = {
            "ema_crossover": EMACrossover(), "supertrend": SuperTrend(),
            "orb_breakout": ORBBreakout(), "rsi_momentum": RSIMomentum(),
            "vwap_breakout": VWAPBreakout(),
        }
        self._feed            = feed or SimulatedFeed(WATCH_SYMBOLS, BASE_PRICES)
        self._regime          = Regime.UNKNOWN
        self._regime_features: dict = {}
        self._allocations: dict[str, float] = {}
        _sig_q   = 20  if _TERMUX else 50
        _close_q = 120 if _TERMUX else 250
        _bar_q   = 200 if _TERMUX else 500
        self._recent_signals: deque[dict] = deque(maxlen=_sig_q)
        self._close_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=_close_q))
        self._bar_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=_bar_q))
        self._bars_since_reclassify = 0
        self._signal_dedup: dict[str, datetime] = {}
        self._disabled_strategies: set[str] = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._tick_count = 0
        # Intelligence / pair-selector
        self._pair_selector         = PairSelector()
        self._intelligence_scores: list[dict] = []
        self._auto_select_enabled:  bool      = True
        self._selected_symbols:     set[str]  = set()
        self._intelligence_updated: datetime  = _utcnow()
        # ── Layer 5 AI Brain ──────────────────────────────────────────────────
        self._router           = AIRouter()
        self._cost_throttle    = CostThrottle()
        self._signal_scanner   = SignalScanner()
        self._shortlister      = Shortlister()
        self._analyst          = Analyst()
        self._decision_engine  = DecisionEngine(self._router, self._cost_throttle)
        self._trade_monitor    = TradeMonitor()
        self._action_executor  = ActionExecutor(self._trade_monitor)
        self._ai_decisions: deque[dict] = deque(maxlen=20 if _TERMUX else 50)
        self._ai_brain_enabled = True
        # ── Auto-discovery (market-wide NSE scanner) ──────────────────────────
        from intelligence.auto_discoverer import AutoDiscoverer
        self._discoverer              = AutoDiscoverer()
        self._discovery_results: list = []
        self._discovery_market_open: Optional[bool] = None

    async def start(self) -> None:
        await self._pnl_tracker.init()
        self._running = True
        feed_type = type(self._feed).__name__
        logger.info("=" * 55)
        logger.info("  JARVIS ENGINE STARTED  [feed=%s]", feed_type)
        logger.info("  Strategies : %s", ", ".join(self._strategies.keys()))
        all_syms = WATCH_SYMBOLS + CURRENCY_SYMBOLS
        logger.info("  Equities   : %s", ", ".join(WATCH_SYMBOLS) or "none")
        if CURRENCY_SYMBOLS:
            logger.info("  Currency   : %s", ", ".join(CURRENCY_SYMBOLS))
        if MCX_SYMBOLS:
            logger.info("  MCX        : %s", ", ".join(MCX_SYMBOLS))
        logger.info("  Capital    : ₹%.0f", self._broker._initial_capital if hasattr(self._broker, '_initial_capital') else 0)
        logger.info("=" * 55)
        self._tasks.append(asyncio.create_task(self._feed.start(self._on_tick)))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._intelligence_loop()))
        self._tasks.append(asyncio.create_task(self._ai_brain_loop()))
        self._tasks.append(asyncio.create_task(self._auto_discovery_loop()))

    async def stop(self) -> None:
        self._running = False
        self._feed.stop()
        for t in self._tasks:
            t.cancel()

    async def _heartbeat_loop(self) -> None:
        """Log a status summary every 5 minutes."""
        await asyncio.sleep(30)  # first beat after 30s
        while self._running:
            try:
                pnl   = await self._broker.get_daily_pnl()
                cap   = await self._broker.get_available_capital()
                pos   = getattr(self._broker, '_positions', {})
                ticks = self._tick_count
                ltp   = {s: round(v, 2) for s, v in self._broker._ltp.items()}
                logger.info("─" * 55)
                logger.info("  HEARTBEAT  ticks=%d  regime=%s", ticks, str(self._regime).replace("Regime.", ""))
                logger.info("  Daily P&L  : ₹%.2f   available: ₹%.0f", pnl, cap)
                logger.info("  Prices     : %s", "  ".join(f"{s}={p}" for s, p in ltp.items()))
                if pos:
                    for sym, p in pos.items():
                        logger.info("  Position   : %s  qty=%s  entry=₹%s",
                                    sym,
                                    getattr(p, 'qty', '?'),
                                    getattr(p, 'avg_price', '?'))
                else:
                    logger.info("  Positions  : none open")
                logger.info("─" * 55)
            except Exception as exc:
                logger.warning("heartbeat error: %s", exc)
            await asyncio.sleep(300)  # every 5 min

    async def _intelligence_loop(self) -> None:
        await asyncio.sleep(60)   # wait for prices to accumulate
        while self._running:
            try:
                self._run_pair_selection()
            except Exception as exc:
                logger.warning("intelligence loop error: %s", exc)
            await asyncio.sleep(300)  # re-score every 5 min

    # ── Auto-discovery (market-wide NSE scan) ─────────────────────────────────

    @staticmethod
    def _equity_market_open_now() -> bool:
        """Fast time-based check: NSE equity hours are Mon–Fri 09:15–15:30 IST."""
        from datetime import timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        if now.weekday() >= 5:          # Saturday=5, Sunday=6
            return False
        t = now.hour * 60 + now.minute
        return 555 <= t <= 930          # 09:15 → 15:30 in minutes

    async def _auto_discovery_loop(self) -> None:
        """Scan NSE universe every 5 min during market hours; idle when closed."""
        await asyncio.sleep(30)   # brief startup grace period
        while self._running:
            try:
                if not self._equity_market_open_now():
                    # Market closed — update status flag but don't subscribe equities
                    self._discovery_market_open = False
                    logger.debug("[AutoDisc] equity market closed — skipping scan")
                    await asyncio.sleep(1800)   # check again in 30 min
                    continue

                results = await self._discoverer.scan()
                self._discovery_results      = [self._disc_to_dict(r) for r in results]
                self._discovery_market_open  = self._discoverer.market_open()
                if results and self._discovery_market_open:
                    await self._subscribe_discoveries(results)
            except Exception as exc:
                logger.warning("auto-discovery loop error: %s", exc)
            await asyncio.sleep(300)

    async def _subscribe_discoveries(self, discoveries) -> None:
        """Subscribe top discovered instruments to the live feed if not already tracked."""
        from core.feeds.dhan_instruments import get_scrip_master
        sm = get_scrip_master()

        for disc in discoveries:
            sym = disc.symbol
            # Skip if already receiving ticks
            if hasattr(self._feed, "scanner_data"):
                sd = self._feed.scanner_data()
                if sym in sd and sd[sym].get("status") in ("live", "stale", "searching"):
                    continue
            elif sym in getattr(self._feed, "_symbols", []):
                continue

            # Look up Dhan security ID via scrip master
            seg = disc.segment
            sid = ""
            lot = 1
            if sm.is_loaded():
                hits = sm.search(sym, limit=5)
                for h in hits:
                    if h.get("symbol") == sym and h.get("segment") == seg:
                        sid = str(h.get("security_id", ""))
                        lot = int(h.get("lot_size") or 1)
                        break
                if not sid and hits:
                    # Accept first NSE_EQ match by symbol prefix
                    for h in hits:
                        if h.get("symbol", "").startswith(sym) and "EQ" in h.get("segment", ""):
                            sid = str(h.get("security_id", ""))
                            lot = int(h.get("lot_size") or 1)
                            break

            if not sid:
                logger.debug("[AutoDisc] no security_id for %s — skipping subscription", sym)
                continue

            if hasattr(self._feed, "add_instrument"):
                add_fn = self._feed.add_instrument
                if asyncio.iscoroutinefunction(add_fn):
                    ok = await add_fn(seg, sid, sym, lot, initial_price=disc.ltp)
                else:
                    ok = add_fn(seg, sid, sym, lot, initial_price=disc.ltp)
                if ok:
                    logger.info("[AutoDisc] subscribed %s (%s/%s) ltp=%.2f", sym, seg, sid, disc.ltp)

    @staticmethod
    def _disc_to_dict(d) -> dict:
        return {
            "symbol":        d.symbol,
            "ltp":           d.ltp,
            "segment":       d.segment,
            "asset_class":   d.asset_class,
            "score":         d.score,
            "rank":          d.rank,
            "direction":     d.direction,
            "change_pct":    d.change_pct,
            "day_range_pct": d.day_range_pct,
            "week52_high":   d.week52_high,
            "week52_low":    d.week52_low,
            "trend_30d":     d.trend_30d,
            "reasoning":     d.reasoning,
        }

    def _run_pair_selection(self) -> None:
        scanner = {}
        if hasattr(self._feed, "scanner_data"):
            scanner = self._feed.scanner_data()
        else:
            syms = getattr(self._feed, "_symbols", None) or []
            for s in syms:
                scanner[s] = {"status": "live", "ticks": self._tick_count,
                               "ltp": self._feed.current_price(s),
                               "last_tick_ago": 1.0, "is_currency": s.endswith("INR")}

        scores = self._pair_selector.score_all(
            scanner, self._close_history, list(self._recent_signals), self._regime
        )
        self._intelligence_scores = [
            {
                "symbol":      s.symbol,
                "score":       s.score,
                "rank":        s.rank,
                "recommended": s.recommended,
                "components":  s.components,
                "reasoning":   s.reasoning,
            }
            for s in scores
        ]
        if self._auto_select_enabled:
            self._selected_symbols = {s.symbol for s in scores if s.recommended}
        self._intelligence_updated = _utcnow()
        if scores:
            top = [f"{s.symbol}({s.score:.0f})" for s in scores[:3]]
            logger.info("intelligence  top-3: %s  auto_select=%s", ", ".join(top), self._auto_select_enabled)

    async def _ai_brain_loop(self) -> None:
        await asyncio.sleep(AI_BRAIN_WARMUP_S)
        while self._running:
            try:
                await self._run_ai_brain()
            except Exception as exc:
                logger.warning("AI brain loop error: %s", exc)
            await asyncio.sleep(AI_BRAIN_INTERVAL_S)

    async def _run_ai_brain(self) -> None:
        if not self._ai_brain_enabled:
            return

        # Build scanner meta (same shape used by Shortlister / Analyst)
        scanner_meta: dict = {}
        if hasattr(self._feed, "scanner_data"):
            scanner_meta = self._feed.scanner_data()
        else:
            for s in getattr(self._feed, "_symbols", []) + getattr(self._feed, "_all_symbols", []):
                scanner_meta[s] = {
                    "status": "live", "ticks": self._tick_count,
                    "ltp": self._feed.current_price(s),
                    "last_tick_ago": 1.0,
                    "is_currency":  s.endswith("INR"),
                    "is_commodity": s in MCX_SYMBOLS,
                }

        if not scanner_meta:
            return

        # 1. Signal scan (rules-based, zero cost)
        scan_results = self._signal_scanner.scan_all(
            self._bar_history, self._regime, scanner_meta
        )

        # 2. Shortlist (rules-based, zero cost)
        open_positions = await self._broker.get_positions()
        broker_state   = self._broker.snapshot()
        report = self._shortlister.run(
            scan_results, scanner_meta, open_positions, self._regime,
            kill_switch_active=self._broker.is_killed(),
        )

        scannable = sum(1 for r in scan_results.values() if r.scannable)
        logger.info(
            "AI brain  scannable=%d  shortlisted=%d  rejected=%d  mode=%s",
            scannable, len(report.passed), len(report.rejected),
            self._cost_throttle.mode,
        )

        if not report.passed:
            return

        # 3. Analyse + decide (LLM calls — may degrade to rules-only)
        for entry in report.passed[:AI_BRAIN_MAX_PER_CYCLE]:
            try:
                payload  = self._analyst.build(
                    entry, broker_state, self._regime,
                    list(self._recent_signals),
                    list(self._close_history.get(entry.symbol, [])),
                )
                decision = await self._decision_engine.decide(payload)

                self._ai_decisions.appendleft({
                    **decision.to_dict(),
                    "shortlist_rank": entry.rank,
                    "final_score":    round(entry.final_score, 4),
                })

                if decision.is_actionable:
                    result = await self._action_executor.execute(decision, self._broker)
                    logger.info(
                        "AI BRAIN OPEN  %-8s  dir=%-5s  conv=%3d  qty=%d"
                        "  sl=%.2f%%  tp=%.2f%%  src=%s  ok=%s",
                        decision.symbol, decision.direction, decision.conviction,
                        result.qty,
                        decision.stop_loss_pct * 100, decision.take_profit_pct * 100,
                        decision.source, result.success,
                    )
                else:
                    logger.debug(
                        "AI brain FLAT  %s  conv=%d  src=%s",
                        decision.symbol, decision.conviction, decision.source,
                    )
            except Exception as exc:
                logger.warning("AI brain decision error (%s): %s", entry.symbol, exc)

    async def _on_tick(self, symbol: str, ltp: float, volume: float) -> None:
        if not self._running:
            return
        try:
            self._tick_count += 1
            await self._broker.update_ltp(symbol, ltp)
            self._close_history[symbol].append(ltp)
            # Log feed alive every 500 ticks (~50s with SimulatedFeed)
            if self._tick_count % 500 == 0:
                logger.info("feed alive  ticks=%d  %s=₹%.2f", self._tick_count, symbol, ltp)
            for bar in self._aggregator.update(symbol, ltp, volume, _utcnow()):
                await self._on_bar(bar)
            # AI brain: check exit conditions for this symbol on every tick
            if self._ai_brain_enabled and symbol in self._trade_monitor.monitored_symbols:
                exit_sig = self._trade_monitor.check(symbol, ltp)
                if exit_sig:
                    result = await self._action_executor.close(exit_sig, self._broker)
                    logger.info(
                        "AI BRAIN EXIT  %-8s  reason=%-12s  pnl=%.2f%%  qty=%d  ok=%s",
                        symbol, exit_sig.reason,
                        exit_sig.unrealised_pnl_pct * 100,
                        result.qty, result.success,
                    )
        except Exception as exc:
            logger.error("tick error %s: %s", symbol, exc)

    async def _on_bar(self, bar: Bar) -> None:
        logger.debug("bar  %s %s  O=%.2f H=%.2f L=%.2f C=%.2f  vol=%d",
                     bar.timeframe, bar.symbol,
                     bar.open, bar.high, bar.low, bar.close, bar.volume)
        # Accumulate bar history for AI signal scanner (OHLCV needed)
        self._bar_history[bar.symbol].append(bar)

        self._bars_since_reclassify += 1
        if bar.timeframe == "5min" and self._bars_since_reclassify >= REGIME_RECLASSIFY_BARS:
            self._bars_since_reclassify = 0
            old = self._regime
            closes = np.array(list(self._close_history[bar.symbol]))
            self._regime = self._regime_clf.predict_from_closes(closes)
            self._regime_features = self._regime_clf.feature_dict()
            self._feed.set_regime(self._regime)
            regime_name = str(self._regime).replace("Regime.", "")
            if old != self._regime:
                logger.info("REGIME CHANGE  %s → %s  (trigger: %s)",
                            str(old).replace("Regime.", ""), regime_name, bar.symbol)
                await self._intent_logger.log_regime_change(str(old), str(self._regime), self._regime_features)
                await self._recompute_allocations()
            else:
                logger.info("regime check  still %s  (%s bars until next)", regime_name, REGIME_RECLASSIFY_BARS)

        # Intelligence auto-select: skip symbols not in the recommended set
        if self._auto_select_enabled and self._selected_symbols and \
                bar.symbol not in self._selected_symbols:
            logger.debug("bar skipped — %s not in AI-selected set %s",
                         bar.symbol, self._selected_symbols)
            return

        for sid in _TF_MAP.get(bar.timeframe, []):
            strat = self._strategies.get(sid)
            if strat is None:
                continue
            if sid in self._disabled_strategies:
                logger.debug("strategy %s skipped (disabled by user)", sid)
                continue
            # Allow all strategies during UNKNOWN regime (startup phase before enough bars)
            if self._regime != Regime.UNKNOWN and not strat.is_active(self._regime):
                logger.debug("strategy %s skipped (not active in %s)", sid, self._regime)
                continue
            try:
                logger.debug("scanning  %s  on  %s %s", sid, bar.timeframe, bar.symbol)
                sig = strat.on_bar(bar)
                if sig:
                    await self._on_signal(sig, strat)
                else:
                    logger.debug("no signal  %s %s", sid, bar.symbol)
            except Exception as exc:
                logger.error("strategy %s error: %s", sid, exc)

    async def _on_signal(self, signal: Signal, strategy: BaseStrategy) -> None:
        key = f"{signal.strategy_id}:{signal.symbol}:{signal.side}"
        now = _utcnow()
        last = self._signal_dedup.get(key)
        if last and (now - last).total_seconds() < SIGNAL_DEDUP_SECONDS:
            logger.debug("signal deduped  %s (last: %ds ago)",
                         key, int((now - last).total_seconds()))
            return
        self._signal_dedup[key] = now

        logger.info(">> SIGNAL  %s  %s %s @ ₹%.2f  conf=%.0f%%  R/R=%.1f  [%s]",
                    signal.strategy_id, signal.side.value, signal.symbol,
                    signal.entry_price or 0, (signal.confidence or 0) * 100,
                    signal.risk_reward or 0, signal.reason or "")

        ltp = self._broker.get_ltp(signal.symbol)
        if ltp is None:
            logger.warning("signal dropped — no LTP for %s", signal.symbol)
            return

        available = await self._broker.get_available_capital()
        stats = strategy.get_stats()
        # Use instrument's actual lot size (scrip master > currency map > equity default)
        if hasattr(self._feed, "lot_size"):
            lot_size = self._feed.lot_size(signal.symbol)
        else:
            lot_size = 1
        if lot_size <= 1:
            lot_size = CURRENCY_LOT_SIZES.get(signal.symbol, 1)
        qty = self._kelly_sizer.size(stats, ltp, available, lot_size=lot_size)
        if qty == 0:
            logger.info("   Kelly → qty=0  (win_rate=%.0f%%  n=%d  available=₹%.0f) — no trade",
                        (stats.win_rate or 0) * 100, stats.sample_size or 0, available)
            return

        kelly_explain = self._kelly_sizer.explain(stats, ltp, available)
        logger.info("   Kelly → qty=%d  (₹%.0f exposure)", qty, qty * ltp)

        side = OrderSide.BUY if signal.side == SignalSide.BUY else OrderSide.SELL
        order = Order(symbol=signal.symbol, side=side, order_type=OrderType.MARKET,
                      qty=qty, product=ProductType.INTRADAY, strategy_id=signal.strategy_id)
        decision = await self._risk_manager.check(order, ltp, signal.strategy_id)
        await self._intent_logger.log_signal(signal, str(self._regime), kelly_explain)
        await self._intent_logger.log_order(order, decision, str(self._regime))

        if decision.approved and not self._broker.is_killed():
            order.qty = decision.adjusted_qty
            await self._broker.place_order(order)
            logger.info("   PAPER ORDER PLACED  %s %d %s @ ₹%.2f  (paper)",
                        side.value, order.qty, signal.symbol, ltp)
        else:
            reason = "kill switch active" if self._broker.is_killed() else getattr(decision, 'reason', 'risk gate')
            logger.info("   ORDER REJECTED  reason=%s", reason)

        self._recent_signals.append({
            "ts": signal.timestamp.isoformat(), "strategy": signal.strategy_id,
            "symbol": signal.symbol, "side": signal.side.value,
            "confidence": signal.confidence, "entry": signal.entry_price,
            "sl": signal.stop_loss, "tp": signal.take_profit,
            "rr": round(signal.risk_reward, 2), "reason": signal.reason,
            "approved": decision.approved,
        })

    async def _recompute_allocations(self) -> None:
        available = await self._broker.get_available_capital()
        result = self._shift_engine.compute_allocations(list(self._strategies.values()), self._regime, available)
        self._allocations = result.allocations
        logger.info("allocations recomputed  %s",
                    "  ".join(f"{k}={v:.0%}" for k, v in result.allocations.items() if v > 0))
        await self._intent_logger.log_allocation(result)
        # re-score pairs when regime changes
        self._run_pair_selection()

    def snapshot(self) -> dict:
        scanner = {}
        if hasattr(self._feed, "scanner_data"):
            raw = self._feed.scanner_data()
            # When equity market is closed, hide equity symbols that never received a tick
            # (they'd just clutter the dashboard as endless "searching" entries)
            mkt_open = self._equity_market_open_now()
            for sym, data in raw.items():
                is_currency  = sym in CURRENCY_SYMBOLS
                is_commodity = sym in MCX_SYMBOLS
                is_equity    = not is_currency and not is_commodity
                if is_equity and not mkt_open and data.get("status") == "searching":
                    continue    # don't show offline equities that never got a tick
                scanner[sym] = data
        else:
            # SimulatedFeed — _symbols attr; mark all as live
            syms = getattr(self._feed, "_symbols", None) or getattr(self._feed, "_all_symbols", [])
            for s in syms:
                scanner[s] = {"status": "live", "ticks": self._tick_count,
                               "ltp": self._feed.current_price(s),
                               "last_tick_ago": 1.0,
                               "is_currency":  s.endswith("INR"),
                               "is_commodity": s in MCX_SYMBOLS,
                               "exchange":     "MCX" if s in MCX_SYMBOLS else ("NSE_CURR" if s.endswith("INR") else "NSE")}
        return {
            "type": "snapshot", "ts": _utcnow().isoformat(),
            "regime": str(self._regime), "regime_features": self._regime_features,
            "broker": self._broker.snapshot(), "allocations": self._allocations,
            "signals": list(self._recent_signals)[-10:],
            "ltp": {s: round(v, 4) if s.endswith("INR") else round(v, 2)
                    for s, v in self._broker._ltp.items()},
            "scanner": scanner,
            "intelligence": {
                "scores":           self._intelligence_scores,
                "auto_select":      self._auto_select_enabled,
                "selected_symbols": list(self._selected_symbols),
                "updated_at":       self._intelligence_updated.isoformat(),
            },
            "ai_brain": {
                "enabled":             self._ai_brain_enabled,
                "mode":                self._cost_throttle.mode,
                "daily_cost_usd":      round(self._router.daily_cost_usd, 6),
                "daily_cost_inr":      round(self._router.daily_cost_usd * 84.0, 2),
                "budget_pct_used":     self._cost_throttle.snapshot().pct_used,
                "decisions":           list(self._ai_decisions)[:10],
                "monitored_positions": self._trade_monitor.snapshot(),
            },
            "discovery": {
                "results":      self._discovery_results,
                "market_open":  self._discovery_market_open,
                "last_scan_ago": round(self._discoverer.seconds_since_scan(), 0),
                "error":        self._discoverer.last_error(),
            },
        }

    async def manual_kill(self) -> None:
        daily_pnl = await self._broker.get_daily_pnl()
        await self._broker.square_off_all()
        await self._intent_logger.log_kill_switch(daily_pnl, 0.0)
        logger.critical("!!! MANUAL KILL-SWITCH TRIGGERED  daily_pnl=₹%.2f !!!", daily_pnl)

# ── Module-level state ────────────────────────────────────────────────────────

_engine: Optional[JarvisEngine] = None
_ws_clients: set = set()

# ── WebSocket server ──────────────────────────────────────────────────────────

async def _ws_handler(websocket) -> None:
    _ws_clients.add(websocket)
    logger.info("dashboard connected  clients=%d", len(_ws_clients))
    try:
        if _engine:
            await websocket.send(json.dumps(_engine.snapshot()))
        async for raw in websocket:
            try:
                data = json.loads(raw)
                cmd = data.get("type")
                if cmd == "kill_switch" and _engine:
                    await _engine.manual_kill()
                    await websocket.send(json.dumps({"type": "ack", "cmd": "kill_switch"}))
                elif cmd == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _ws_clients.discard(websocket)
        logger.info("WS disconnected (total=%d)", len(_ws_clients))

async def _broadcast_loop() -> None:
    interval = BROADCAST_INTERVAL_MS / 1000.0
    while True:
        await asyncio.sleep(interval)
        if not _engine or not _ws_clients:
            continue
        payload = json.dumps(_engine.snapshot())
        dead: set = set()
        for ws in list(_ws_clients):
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
            except Exception as exc:
                logger.debug("broadcast send error (removing client): %s", exc)
                dead.add(ws)
        if dead:
            _ws_clients.difference_update(dead)  # in-place, avoids UnboundLocalError
            logger.debug("removed %d stale WS client(s)", len(dead))

# ── HTTP API server ───────────────────────────────────────────────────────────

def _mask(val: str, keep: int = 4) -> str:
    if not val:
        return ""
    return val[:keep] + "•" * max(0, len(val) - keep)

async def _route(method: str, path: str, query: dict, body: dict) -> tuple[int, object]:
    if path == "/api/debug/scrip":
        from core.feeds.dhan_instruments import _scrip_master, search_instruments
        q   = query.get("q", ["USDINR"])[0]
        seg = query.get("seg", [None])[0]
        segs = [seg] if seg else None
        hits = search_instruments(q, segments=segs, limit=20)
        stats = _scrip_master.stats()
        return 200, {"query": q, "segment_filter": seg, "stats": stats, "hits": hits}

    if path == "/api/debug/feed":
        if not _engine:
            return 200, {"error": "engine not ready"}
        feed = _engine._feed
        # Extract live subscription info from DhanFeed (if it is one)
        sub_list    = getattr(feed, "_sub_list",        [])
        id_to_sym   = getattr(feed, "_id_to_sym",       {})
        inst_info   = getattr(feed, "_instrument_info", {})
        sym_ticks   = getattr(feed, "_sym_ticks",       {})
        sym_ltp     = getattr(feed, "_sym_ltp",         {})
        ticks_total = getattr(feed, "ticks_received",   0)
        connected   = getattr(feed, "connected",        None)
        sim_active  = getattr(feed, "_sim_fallback",    None) is not None
        from core.feeds.dhan_instruments import CURRENCY_LOT_SIZES
        return 200, {
            "feed_type":     type(feed).__name__,
            "connected":     connected,
            "sim_fallback":  sim_active,
            "ticks_total":   ticks_total,
            "subscription_count": len(sub_list),
            "subscriptions": sub_list,
            "security_id_map": id_to_sym,
            "instrument_info": inst_info,
            "tick_counts":   sym_ticks,
            "last_prices":   {s: round(p, 4) for s, p in sym_ltp.items()},
        }

    if path == "/api/status":
        if _engine is None:
            return 200, {"status": "initializing"}
        return 200, {
            "status": "running", "regime": str(_engine._regime),
            "kill_switch": _engine._broker.is_killed(),
            "ws_clients": len(_ws_clients),
            "strategies": list(_engine._strategies.keys()),
        }
    if path == "/api/snapshot":
        return 200, (_engine.snapshot() if _engine else {"error": "not ready"})
    if path == "/api/pnl":
        return 200, (await _engine._pnl_tracker.get_session_summary() if _engine else {"error": "not ready"})

    if path == "/api/trades":
        if not _engine:
            return 200, {"trades": [], "open_positions": []}
        # All fills from the broker (every entry and exit)
        fills = [
            {
                "timestamp":   f.filled_at.isoformat(),
                "symbol":      f.symbol,
                "side":        f.side.value,
                "qty":         f.qty,
                "entry_price": round(f.price, 4),
                "exit_price":  None,
                "pnl":         None,
                "strategy":    f.strategy_id or "",
                "regime":      "",
            }
            for f in _engine._broker._fills
        ]
        # Enrich with realized P&L from position close events
        pos_pnl: dict[str, float] = {
            sym: round(pos.realized_pnl, 2)
            for sym, pos in _engine._broker._positions.items()
            if pos.realized_pnl != 0
        }
        # Open positions
        open_pos = [
            {
                "symbol":      sym,
                "qty":         pos.qty,
                "avg_price":   round(pos.avg_price, 4),
                "ltp":         round(_engine._broker._ltp.get(sym, pos.avg_price), 4),
                "unrealized":  round(pos.unrealized_pnl, 2),
                "realized":    round(pos.realized_pnl, 2),
            }
            for sym, pos in _engine._broker._positions.items()
            if pos.qty != 0
        ]
        return 200, {"trades": fills, "open_positions": open_pos, "realized_by_symbol": pos_pnl}

    if path == "/api/equity":
        return 200, (await _engine._pnl_tracker.get_equity_curve(days=30) if _engine else {"error": "not ready"})
    if path == "/api/intent":
        n = int(query.get("n", ["50"])[0])
        return 200, (await _engine._intent_logger.tail(n) if _engine else [])
    if path == "/api/strategies":
        if _engine is None:
            return 200, {"error": "not ready"}
        rows = []
        for sid, strat in _engine._strategies.items():
            stats = strat.get_stats()
            rows.append({"id": sid, "sharpe": round(strat.get_sharpe(), 3),
                         "win_rate": round(stats.win_rate, 3), "sample_size": stats.sample_size,
                         "active_in_regime": strat.is_active(_engine._regime),
                         "allocation": _engine._allocations.get(sid, 0.0)})
        rows.sort(key=lambda x: x["sharpe"], reverse=True)
        return 200, {"regime": str(_engine._regime), "strategies": rows}
    if path == "/api/settings" and method == "GET":
        raw: dict = {}
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        _SECRET_KEYS = ("dhan_access_token", "anthropic_api_key", "openai_api_key", "google_api_key")
        return 200, {**raw,
                     "dhan_client_id":    _mask(raw.get("dhan_client_id", "")),
                     **{k: "•" * 8 if raw.get(k) else "" for k in _SECRET_KEYS}}
    if path == "/api/settings" and method == "POST":
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if SETTINGS_FILE.exists():
            try:
                existing = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        merged = {**existing, **body}
        # Don't overwrite stored secrets with masked placeholder values
        _SECRET_KEYS = ("dhan_client_id", "dhan_access_token",
                        "anthropic_api_key", "openai_api_key", "google_api_key")
        for k in _SECRET_KEYS:
            if "•" in str(merged.get(k, "")):
                merged[k] = existing.get(k, "")
        SETTINGS_FILE.write_text(json.dumps(merged, indent=2))
        _RESTART_KEYS = {"initial_capital", "paper_mode", "hmm_states", "ws_port",
                         "pnl_db_path", "intent_log_path",
                         "anthropic_api_key", "openai_api_key", "google_api_key",
                         "gemini_free_tier"}
        restart_required = bool(_RESTART_KEYS & set(body.keys()))
        if _engine:
            ic  = float(merged.get("initial_capital", 10000))
            ksp = float(merged.get("kill_switch_pct", 0.03))
            kf  = float(merged.get("kelly_fraction", 0.5))
            _engine._broker._kill_switch_amount = ic * ksp
            _engine._kelly_sizer._kelly_fraction = kf
        return 200, {"status": "saved", "restart_required": restart_required}
    if path == "/api/kill":
        if _engine:
            await _engine.manual_kill()
        return 200, {"status": "kill_switch_activated"}

    if path == "/api/kill/reset" and method == "POST":
        if _engine:
            _engine._broker._killed = False
            logger.info("Kill switch RESET by user")
        return 200, {"status": "reset"}

    if path == "/api/symbols" and method == "GET":
        from core.feeds.dhan_instruments import EQUITY_INSTRUMENTS, CURRENCY_PAIRS
        return 200, {
            "equity":             WATCH_SYMBOLS,
            "currency":           CURRENCY_SYMBOLS,
            "available_equity":   list(EQUITY_INSTRUMENTS.keys()),
            "available_currency": CURRENCY_PAIRS,
        }

    if path == "/api/symbols" and method == "POST":
        equity   = [s for s in body.get("equity",   []) if isinstance(s, str)]
        currency = [s for s in body.get("currency", []) if isinstance(s, str)]
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if SETTINGS_FILE.exists():
            try:
                existing = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        existing["watch_symbols"]    = equity
        existing["currency_symbols"] = currency
        SETTINGS_FILE.write_text(json.dumps(existing, indent=2))
        return 200, {"status": "saved", "restart_required": True,
                     "equity": equity, "currency": currency}

    if path == "/api/strategy/toggle" and method == "POST":
        sid     = body.get("id", "")
        enabled = bool(body.get("enabled", True))
        if _engine and sid in _engine._strategies:
            if enabled:
                _engine._disabled_strategies.discard(sid)
            else:
                _engine._disabled_strategies.add(sid)
            logger.info("Strategy %s %s by user", sid, "ENABLED" if enabled else "DISABLED")
        return 200, {"status": "ok", "id": sid, "enabled": enabled}

    if path == "/api/strategies/state" and method == "GET":
        if _engine is None:
            return 200, {}
        return 200, {
            sid: sid not in _engine._disabled_strategies
            for sid in _engine._strategies
        }

    if path == "/api/instruments/search":
        from core.feeds.dhan_instruments import search_instruments, get_scrip_master
        sm = get_scrip_master()
        if not sm.is_loaded():
            return 200, {"results": [], "loading": True}
        q = query.get("q", [""])[0].strip()
        segs_param = query.get("segments", [None])[0]
        segments = segs_param.split(",") if segs_param else None
        limit = int(query.get("limit", ["30"])[0])
        if not q:
            return 200, {"results": []}
        results = search_instruments(q, segments=segments, limit=limit)
        return 200, {"results": results}

    if path == "/api/instruments/subscribe" and method == "POST":
        security_id      = str(body.get("security_id", "")).strip()
        exchange_segment = str(body.get("exchange_segment", "")).strip()
        symbol           = str(body.get("symbol", "")).strip()
        lot_size         = int(body.get("lot_size", 1))
        if not all([security_id, exchange_segment, symbol]):
            return 400, {"error": "security_id, exchange_segment, symbol required"}
        if _engine and hasattr(_engine._feed, "add_instrument"):
            ok = await _engine._feed.add_instrument(exchange_segment, security_id, symbol, lot_size)
            logger.info("User subscribed: %s (%s/%s) lot=%d", symbol, exchange_segment, security_id, lot_size)
            return 200, {"status": "subscribed" if ok else "queued", "symbol": symbol}
        return 200, {"status": "no_live_feed"}

    if path == "/api/instruments/unsubscribe" and method == "POST":
        symbol = str(body.get("symbol", "")).strip()
        if not symbol:
            return 400, {"error": "symbol required"}
        if _engine and hasattr(_engine._feed, "remove_symbol"):
            _engine._feed.remove_symbol(symbol)
            logger.info("User unsubscribed: %s", symbol)
        return 200, {"status": "ok"}

    if path == "/api/instruments/watchlist":
        if not _engine:
            return 200, {"instruments": []}
        scanner = {}
        if hasattr(_engine._feed, "scanner_data"):
            scanner = _engine._feed.scanner_data()
        # Merge with instrument_info for badge / segment details
        from core.feeds.dhan_instruments import get_scrip_master
        sm = get_scrip_master()
        instruments = []
        info_map = getattr(_engine._feed, "_instrument_info", {})
        id_to_sym = getattr(_engine._feed, "_id_to_sym", {})
        for sym, scan in scanner.items():
            entry: dict = {"symbol": sym, **scan}
            info = info_map.get(sym, {})
            entry["security_id"] = info.get("security_id", "")
            entry["segment"]     = info.get("segment", "")
            entry["lot_size"]    = info.get("lot_size", 1)
            if info.get("security_id") and sm.is_loaded():
                inst = sm.get_by_sid(info["security_id"])
                if inst:
                    entry["badge"]     = inst.get("badge")
                    entry["seg_label"] = inst.get("seg_label")
                    entry["display"]   = inst.get("display")
            instruments.append(entry)
        return 200, {"instruments": instruments}

    if path == "/api/intelligence/recommendation":
        if not _engine:
            return 200, {"scores": [], "auto_select": True, "selected_symbols": []}
        _engine._run_pair_selection()   # refresh on demand
        return 200, _engine.snapshot()["intelligence"]

    if path == "/api/intelligence/toggle_auto" and method == "POST":
        if _engine:
            _engine._auto_select_enabled = bool(body.get("enabled", True))
            if not _engine._auto_select_enabled:
                _engine._selected_symbols = set()
            logger.info("Intelligence auto-select %s", "ON" if _engine._auto_select_enabled else "OFF")
        return 200, {"auto_select": _engine._auto_select_enabled if _engine else True}

    if path == "/api/intelligence/override" and method == "POST":
        symbols = [s for s in body.get("symbols", []) if isinstance(s, str)]
        if _engine:
            _engine._auto_select_enabled = False
            _engine._selected_symbols = set(symbols)
            logger.info("Intelligence manual override: %s", symbols)
        return 200, {"status": "overridden", "selected_symbols": symbols}

    if path == "/api/market/discover":
        if not _engine:
            return 200, {"results": [], "market_open": None, "error": "engine not ready"}
        if method == "POST":
            # Force an immediate scan
            import asyncio as _asyncio
            results = await _engine._discoverer.scan()
            _engine._discovery_results     = [_engine._disc_to_dict(r) for r in results]
            _engine._discovery_market_open = _engine._discoverer.market_open()
        return 200, {
            "results":      _engine._discovery_results,
            "market_open":  _engine._discovery_market_open,
            "last_scan_ago": round(_engine._discoverer.seconds_since_scan(), 0),
            "error":        _engine._discoverer.last_error(),
        }

    if path.startswith("/api/charts/"):
        symbol = path.split("/api/charts/")[-1].upper()
        tf = query.get("tf", ["5min"])[0]
        if not _engine:
            return 200, {"symbol": symbol, "timeframe": tf, "bars": [], "markers": []}

        # OHLCV bars for the requested timeframe
        raw_bars = [b for b in _engine._bar_history.get(symbol, []) if b.timeframe == tf]
        bars = [
            {
                "time":   int(b.timestamp.replace(tzinfo=timezone.utc).timestamp()),
                "open":   round(b.open,   4),
                "high":   round(b.high,   4),
                "low":    round(b.low,    4),
                "close":  round(b.close,  4),
                "volume": int(b.volume),
            }
            for b in raw_bars
        ]

        # Strategy signal markers
        markers = []
        for sig in _engine._recent_signals:
            if sig.get("symbol") != symbol:
                continue
            try:
                t = int(datetime.fromisoformat(sig["ts"]).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                continue
            markers.append({
                "time":       t,
                "type":       "signal",
                "side":       sig.get("side", ""),
                "strategy":   sig.get("strategy", ""),
                "price":      sig.get("entry"),
                "confidence": sig.get("confidence"),
                "label":      sig.get("strategy", ""),
            })

        # AI brain decision markers
        for dec in _engine._ai_decisions:
            if dec.get("symbol") != symbol:
                continue
            try:
                t = int(datetime.fromisoformat(dec["ts"]).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                continue
            markers.append({
                "time":     t,
                "type":     "decision",
                "action":   dec.get("action", ""),
                "model":    dec.get("model", ""),
                "strategy": dec.get("strategy", ""),
                "price":    dec.get("entry_price"),
                "label":    dec.get("model", "AI"),
            })

        # Trade fill markers from broker
        for fill in _engine._broker._fills:
            if fill.symbol != symbol:
                continue
            try:
                t = int(fill.timestamp.replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                continue
            markers.append({
                "time":   t,
                "type":   "trade",
                "side":   fill.side.value,
                "price":  round(fill.price, 4),
                "qty":    fill.qty,
                "label":  fill.side.value,
            })

        markers.sort(key=lambda m: m["time"])
        return 200, {"symbol": symbol, "timeframe": tf, "bars": bars, "markers": markers}

    if path == "/api/ai/brain":
        if not _engine:
            return 200, {"enabled": False, "decisions": [], "monitored_positions": {}}
        snap = _engine._cost_throttle.snapshot()
        return 200, {
            "enabled":             _engine._ai_brain_enabled,
            "mode":                _engine._cost_throttle.mode,
            "cost_summary":        _engine._router.cost_summary(),
            "budget": {
                "daily_inr":   snap.budget_inr,
                "spent_inr":   round(snap.spend_inr, 2),
                "pct_used":    snap.pct_used,
                "cache_saves": snap.total_saves,
            },
            "decisions":           list(_engine._ai_decisions)[:20],
            "monitored_positions": _engine._trade_monitor.snapshot(),
        }

    if path == "/api/ai/brain/toggle" and method == "POST":
        if _engine:
            _engine._ai_brain_enabled = bool(body.get("enabled", True))
            logger.info("AI brain %s by user", "ENABLED" if _engine._ai_brain_enabled else "DISABLED")
        return 200, {"enabled": _engine._ai_brain_enabled if _engine else False}

    if path == "/api/instruments/scrip_status":
        from core.feeds.dhan_instruments import get_scrip_master
        sm = get_scrip_master()
        return 200, (sm.stats() if sm.is_loaded() else {"total": 0, "loading": True})

    if path == "/api/position/close" and method == "POST":
        symbol  = body.get("symbol")
        all_pos = body.get("all", False)
        if _engine:
            if all_pos:
                await _engine._broker.square_off_all()
                logger.info("All positions closed by user")
            elif symbol:
                pos = (await _engine._broker.get_positions()).get(symbol)
                if pos and pos.qty != 0:
                    from core.broker.base_broker import Order, OrderSide, OrderType, ProductType
                    side  = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
                    order = Order(symbol=symbol, side=side, order_type=OrderType.MARKET,
                                  qty=abs(pos.qty), product=ProductType.INTRADAY,
                                  strategy_id="MANUAL_CLOSE")
                    await _engine._broker.place_order(order)
                    logger.info("Position %s closed by user", symbol)
        return 200, {"status": "ok"}

    return 404, {"error": f"not found: {method} {path}"}

async def _handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not line:
            return
        parts = line.decode(errors="replace").split()
        if len(parts) < 2:
            return
        method, full_path = parts[0].upper(), parts[1]
        parsed = urlparse(full_path)
        query  = parse_qs(parsed.query)

        content_length = 0
        while True:
            h = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if h in (b"\r\n", b"\n", b""):
                break
            if b"content-length" in h.lower():
                content_length = int(h.split(b":", 1)[1].strip())

        body: dict = {}
        if content_length > 0:
            try:
                body = json.loads(await asyncio.wait_for(reader.read(content_length), timeout=10.0))
            except Exception:
                pass

        if method == "OPTIONS":
            resp = (b"HTTP/1.1 204 No Content\r\n"
                    b"Access-Control-Allow-Origin: *\r\n"
                    b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                    b"Access-Control-Allow-Headers: Content-Type\r\n"
                    b"Content-Length: 0\r\n\r\n")
        else:
            status, data = await _route(method, parsed.path, query, body)
            payload = json.dumps(data).encode()
            resp = (
                f"HTTP/1.1 {status} OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + payload

        writer.write(resp)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as exc:
        logger.error("HTTP error: %s", exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

# ── Entry point ───────────────────────────────────────────────────────────────

def _build_feed(cfg: dict):
    """
    Return DhanFeed if credentials are present, else SimulatedFeed.
    Always uses PaperBroker — no real orders are placed either way.
    """
    client_id    = cfg.get("dhan_client_id", "").strip()
    access_token = cfg.get("dhan_access_token", "").strip()

    if client_id and access_token:
        try:
            from core.feeds.dhan_feed import DhanFeed
            logger.info("Dhan credentials found — using live market feed (paper orders)")
            if CURRENCY_SYMBOLS:
                logger.info("Currency pairs: %s", ", ".join(CURRENCY_SYMBOLS))
            if MCX_SYMBOLS:
                logger.info("MCX commodities: %s", ", ".join(MCX_SYMBOLS))
            return DhanFeed(client_id, access_token, WATCH_SYMBOLS,
                            currency_symbols=CURRENCY_SYMBOLS or None,
                            commodity_symbols=MCX_SYMBOLS or None)
        except Exception as exc:
            logger.warning("Could not load DhanFeed (%s) — falling back to SimulatedFeed", exc)

    all_syms = WATCH_SYMBOLS + CURRENCY_SYMBOLS + MCX_SYMBOLS
    logger.info("No Dhan credentials — using SimulatedFeed")
    return SimulatedFeed(all_syms, BASE_PRICES)


async def main() -> None:
    global _engine, WATCH_SYMBOLS, CURRENCY_SYMBOLS, MCX_SYMBOLS

    cfg = _load_settings()
    logging.basicConfig(
        level=getattr(logging, cfg.get("log_level", "INFO"), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if _TERMUX:
        logger.info(
            "TERMUX mode active — tick_interval=%.1fs broadcast=%dms max_llm_calls=%d",
            SimulatedFeed.TICK_INTERVAL, BROADCAST_INTERVAL_MS, AI_BRAIN_MAX_PER_CYCLE,
        )

    # Inject settings-stored LLM API keys into env vars (only if not already set externally)
    import os
    for env_var, cfg_key in [
        ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("OPENAI_API_KEY",    "openai_api_key"),
        ("GOOGLE_API_KEY",    "google_api_key"),
    ]:
        val = cfg.get(cfg_key, "").strip()
        if val and not os.environ.get(env_var):
            os.environ[env_var] = val
            logger.info("Loaded %s from settings.json", env_var)
    if cfg.get("gemini_free_tier"):
        os.environ.setdefault("GEMINI_FREE_TIER", "true")

    # Override symbol lists from settings if saved via frontend
    if "watch_symbols" in cfg and cfg["watch_symbols"]:
        WATCH_SYMBOLS = cfg["watch_symbols"]
    if "currency_symbols" in cfg:
        CURRENCY_SYMBOLS = cfg["currency_symbols"]
    if "mcx_symbols" in cfg:
        MCX_SYMBOLS = cfg["mcx_symbols"]

    pathlib.Path(cfg["intent_log_path"]).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(cfg["pnl_db_path"]).parent.mkdir(parents=True, exist_ok=True)

    # Load scrip master in background (non-blocking; search returns empty until done)
    async def _load_scrip_bg():
        from core.feeds.dhan_instruments import load_scrip_master
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, load_scrip_master)
        if ok:
            from core.feeds.dhan_instruments import get_scrip_master
            logger.info("[ScripMaster] ready — %d instruments indexed", get_scrip_master().stats()["total"])
        else:
            logger.warning("[ScripMaster] failed to load — instrument search unavailable")
    asyncio.create_task(_load_scrip_bg())

    feed = _build_feed(cfg)
    _engine = JarvisEngine(
        initial_capital=cfg["initial_capital"],
        kill_switch_amount=cfg["initial_capital"] * cfg["kill_switch_pct"],
        kelly_fraction=cfg["kelly_fraction"],
        intent_log_path=cfg["intent_log_path"],
        pnl_db_path=cfg["pnl_db_path"],
        feed=feed,
    )
    await _engine.start()

    ws_srv   = await websockets.serve(
        _ws_handler, "0.0.0.0", WS_PORT,
        ping_interval=20,   # send a ping every 20s to keep Vite proxy alive
        ping_timeout=10,    # close if no pong within 10s
        close_timeout=5,
    )
    http_srv = await asyncio.start_server(_handle_http, "0.0.0.0", HTTP_PORT)

    logger.info("JARVIS Termux server ready  WS=%d  HTTP=%d  feed=%s",
                WS_PORT, HTTP_PORT, type(feed).__name__)
    asyncio.create_task(_broadcast_loop())

    try:
        await asyncio.Future()
    finally:
        ws_srv.close()
        http_srv.close()
        await _engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
