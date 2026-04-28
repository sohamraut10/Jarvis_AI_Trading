"""
JARVIS FastAPI + WebSocket Server — the central nervous system.

Architecture
------------
  SimulatedFeed / DhanFeed   — price source (selected at startup)
  BarAggregator              — tick → 1 / 5 / 15-min OHLCV bars
  JarvisEngine               — orchestrates broker, risk, strategies, intelligence
  ConnectionManager          — tracks WS clients; broadcasts snapshots
  FastAPI app                — REST + WebSocket endpoints

WebSocket protocol (server → client every BROADCAST_INTERVAL_MS)
-----------------------------------------------------------------
  {"type": "snapshot", "ts": "...", "regime": "...", "broker": {...},
   "allocations": {...}, "signals": [...], "ltp": {...}, "regime_features": {...}}

Client → server commands
------------------------
  {"type": "kill_switch"}                  — manual hard stop
  {"type": "ping"}                         — keep-alive

REST endpoints
--------------
  GET  /api/status    — health check
  GET  /api/snapshot  — current state (one-shot)
  GET  /api/pnl       — session P&L summary
  GET  /api/equity    — 30-day equity curve
  GET  /api/intent    — last 50 intent-log entries
  GET  /api/strategies— strategy rankings + allocations
  POST /api/kill      — manual kill-switch trigger
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from core.broker.base_broker import (
    Exchange, Order, OrderSide, OrderType, ProductType,
)
from core.broker.paper_broker import PaperBroker
from core.risk.kelly_sizer import KellySizer
from core.risk.risk_manager import RiskManager
from core.types import Regime
from intelligence.alpha_decay_monitor import AlphaDecayMonitor
from intelligence.intent_logger import IntentLogger
from intelligence.pnl_tracker import PnLTracker
from intelligence.regime_classifier import RegimeClassifier
from intelligence.strategy_shift_engine import StrategyShiftEngine
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide
from strategies.momentum.orb_breakout import ORBBreakout
from strategies.momentum.rsi_momentum import RSIMomentum
from strategies.momentum.vwap_breakout import VWAPBreakout
from strategies.trend.ema_crossover import EMACrossover
from strategies.trend.supertrend import SuperTrend

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

BROADCAST_INTERVAL_MS = 500     # snapshot push cadence
REGIME_RECLASSIFY_BARS = 5      # re-run HMM every N 5-min bars
SIGNAL_DEDUP_SECONDS = 60       # ignore repeat signal from same strategy+symbol

WATCH_SYMBOLS: list[str] = ["RELIANCE", "TCS", "INFY", "HDFC", "SBIN"]

BASE_PRICES: dict[str, float] = {
    "RELIANCE": 2500.0,
    "TCS": 3800.0,
    "INFY": 1500.0,
    "HDFC": 1700.0,
    "SBIN": 800.0,
}

# Timeframe → strategies that consume it
_TF_MAP: dict[str, list[str]] = {
    "1min": ["vwap_breakout"],
    "5min": ["ema_crossover", "orb_breakout", "rsi_momentum"],
    "15min": ["supertrend"],
}


# ── Bar Aggregator ─────────────────────────────────────────────────────────────

class BarAggregator:
    """
    Accumulates price ticks into OHLCV bars for each (symbol, timeframe).
    Emits a closed Bar whenever the interval boundary is crossed.
    """

    _INTERVALS: dict[str, int] = {"1min": 60, "5min": 300, "15min": 900}

    def __init__(self) -> None:
        # (symbol, tf) → {"open", "high", "low", "close", "volume", "bucket"}
        self._state: dict[tuple[str, str], dict] = {}

    def update(
        self, symbol: str, price: float, volume: float, ts: datetime
    ) -> list[Bar]:
        """Feed a tick. Returns list of newly closed bars (may be empty)."""
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
                # Close the current bar
                closed.append(Bar(
                    symbol=symbol,
                    timeframe=tf,
                    open=st["open"],
                    high=st["high"],
                    low=st["low"],
                    close=st["close"],
                    volume=st["volume"],
                    timestamp=st["bucket"],
                ))
                # Open a fresh bar
                self._state[key] = {
                    "open": price, "high": price, "low": price,
                    "close": price, "volume": volume, "bucket": bucket,
                }
            else:
                st["high"] = max(st["high"], price)
                st["low"] = min(st["low"], price)
                st["close"] = price
                st["volume"] += volume

        return closed

    @staticmethod
    def _align(ts: datetime, seconds: int) -> datetime:
        mins = seconds // 60
        aligned = (ts.minute // mins) * mins
        return ts.replace(minute=aligned, second=0, microsecond=0)

    def get_current_bar(self, symbol: str, tf: str) -> Optional[dict]:
        return self._state.get((symbol, tf))


# ── Simulated Price Feed ───────────────────────────────────────────────────────

class SimulatedFeed:
    """
    Geometric Brownian Motion price feed.
    Used when Dhan credentials are absent or PAPER_MODE is forced.
    Generates ~10 ticks per second per symbol.
    """

    TICK_INTERVAL = 0.1     # seconds between tick batches
    SIGMA = 0.0004          # per-tick volatility (≈0.04% std)
    VOL_REGIME_MULT = 3.0   # multiplier during HIGH_VOL regime

    def __init__(
        self,
        symbols: list[str],
        base_prices: Optional[dict[str, float]] = None,
    ) -> None:
        self._symbols = symbols
        self._prices = dict(base_prices or {s: 100.0 for s in symbols})
        self._running = False
        self._regime = Regime.SIDEWAYS

    def set_regime(self, regime: Regime) -> None:
        self._regime = regime

    async def start(self, tick_callback) -> None:
        self._running = True
        rng = np.random.default_rng()
        sigma = self.SIGMA
        while self._running:
            if self._regime == Regime.HIGH_VOL:
                sigma = self.SIGMA * self.VOL_REGIME_MULT
            else:
                sigma = self.SIGMA
            for symbol in self._symbols:
                noise = float(rng.normal(0.0, sigma))
                self._prices[symbol] = max(
                    self._prices[symbol] * (1.0 + noise), 0.01
                )
                volume = int(rng.exponential(800))
                await tick_callback(symbol, self._prices[symbol], volume)
            await asyncio.sleep(self.TICK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def current_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)


# ── WebSocket Connection Manager ───────────────────────────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("WS client connected (total=%d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("WS client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, payload: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ── JARVIS Engine ──────────────────────────────────────────────────────────────

class JarvisEngine:
    """
    Orchestrates: feed → bar aggregation → regime classification →
    strategy signals → Kelly sizing → risk gate → paper broker →
    intent logging → P&L tracking → WS broadcast.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        kill_switch_amount: float = 300.0,
        kelly_fraction: float = 0.5,
        intent_log_path: str = "logs/intent.jsonl",
        pnl_db_path: str = "data/pnl.db",
        paper_mode: bool = True,
    ) -> None:
        # ── Core components ────────────────────────────────────────────────────
        self._broker = PaperBroker(
            initial_capital=initial_capital,
            kill_switch_amount=kill_switch_amount,
        )
        self._risk_manager = RiskManager(
            self._broker, kill_switch_amount=kill_switch_amount
        )
        self._kelly_sizer = KellySizer(kelly_fraction=kelly_fraction)
        self._aggregator = BarAggregator()

        # ── Intelligence ───────────────────────────────────────────────────────
        self._regime_clf = RegimeClassifier()
        self._alpha_monitor = AlphaDecayMonitor()
        self._shift_engine = StrategyShiftEngine()
        self._intent_logger = IntentLogger(intent_log_path)
        self._pnl_tracker = PnLTracker(pnl_db_path)

        # ── Strategies ─────────────────────────────────────────────────────────
        self._strategies: dict[str, BaseStrategy] = {
            "ema_crossover": EMACrossover(),
            "supertrend": SuperTrend(),
            "orb_breakout": ORBBreakout(),
            "rsi_momentum": RSIMomentum(),
            "vwap_breakout": VWAPBreakout(),
        }

        # ── Feed ───────────────────────────────────────────────────────────────
        self._feed = SimulatedFeed(WATCH_SYMBOLS, BASE_PRICES)

        # ── State ──────────────────────────────────────────────────────────────
        self._regime: Regime = Regime.UNKNOWN
        self._regime_features: dict = {}
        self._allocations: dict[str, float] = {}
        self._recent_signals: deque[dict] = deque(maxlen=50)
        self._close_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=250)
        )
        self._bars_since_reclassify: int = 0
        self._signal_dedup: dict[str, datetime] = {}   # "strat:sym:side" → last ts

        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._pnl_tracker.init()
        self._running = True
        self._tasks.append(asyncio.create_task(self._feed.start(self._on_tick)))
        logger.info("JarvisEngine started (paper_mode=True)")

    async def stop(self) -> None:
        self._running = False
        self._feed.stop()
        for t in self._tasks:
            t.cancel()
        logger.info("JarvisEngine stopped")

    # ── Tick processing ────────────────────────────────────────────────────────

    async def _on_tick(self, symbol: str, ltp: float, volume: float) -> None:
        if not self._running:
            return
        try:
            await self._broker.update_ltp(symbol, ltp)
            self._close_history[symbol].append(ltp)

            closed_bars = self._aggregator.update(symbol, ltp, volume, datetime.utcnow())
            for bar in closed_bars:
                await self._on_bar(bar)
        except Exception as exc:
            logger.error("tick error symbol=%s: %s", symbol, exc)

    async def _on_bar(self, bar: Bar) -> None:
        self._bars_since_reclassify += 1

        # Periodic regime reclassification (every N 5-min bars)
        if (
            bar.timeframe == "5min"
            and self._bars_since_reclassify >= REGIME_RECLASSIFY_BARS
        ):
            self._bars_since_reclassify = 0
            old_regime = self._regime
            closes = np.array(list(self._close_history[bar.symbol]))
            self._regime = self._regime_clf.predict_from_closes(closes)
            self._regime_features = self._regime_clf.feature_dict()
            self._feed.set_regime(self._regime)

            if old_regime != self._regime:
                await self._intent_logger.log_regime_change(
                    str(old_regime), str(self._regime), self._regime_features
                )
                await self._recompute_allocations()

        # Dispatch bar to matching strategies
        for sid in _TF_MAP.get(bar.timeframe, []):
            strategy = self._strategies.get(sid)
            if strategy is None or not strategy.is_active(self._regime):
                continue
            try:
                signal = strategy.on_bar(bar)
                if signal:
                    await self._on_signal(signal, strategy)
            except Exception as exc:
                logger.error("strategy %s error: %s", sid, exc)

    async def _on_signal(self, signal: Signal, strategy: BaseStrategy) -> None:
        # Deduplication: ignore repeat signals within SIGNAL_DEDUP_SECONDS
        dedup_key = f"{signal.strategy_id}:{signal.symbol}:{signal.side}"
        last = self._signal_dedup.get(dedup_key)
        now = datetime.utcnow()
        if last and (now - last).total_seconds() < SIGNAL_DEDUP_SECONDS:
            return
        self._signal_dedup[dedup_key] = now

        ltp = self._broker.get_ltp(signal.symbol)
        if ltp is None:
            return

        # Kelly sizing
        available = await self._broker.get_available_capital()
        stats = strategy.get_stats()
        qty = self._kelly_sizer.size(stats, ltp, available)
        if qty == 0:
            return

        kelly_explain = self._kelly_sizer.explain(stats, ltp, available)

        # Build order
        side = OrderSide.BUY if signal.side == SignalSide.BUY else OrderSide.SELL
        order = Order(
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            qty=qty,
            product=ProductType.INTRADAY,
            strategy_id=signal.strategy_id,
        )

        # Risk gate
        decision = await self._risk_manager.check(order, ltp, signal.strategy_id)

        # Log intent (signal + order decision)
        await self._intent_logger.log_signal(signal, str(self._regime), kelly_explain)
        await self._intent_logger.log_order(order, decision, str(self._regime))

        # Place if approved
        if decision.approved and not self._broker.is_killed():
            order.qty = decision.adjusted_qty
            await self._broker.place_order(order)

        # Track in recent signals for broadcast
        self._recent_signals.append({
            "ts": signal.timestamp.isoformat(),
            "strategy": signal.strategy_id,
            "symbol": signal.symbol,
            "side": str(signal.side),
            "confidence": signal.confidence,
            "entry": signal.entry_price,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
            "rr": round(signal.risk_reward, 2),
            "reason": signal.reason,
            "approved": decision.approved,
        })

    async def _recompute_allocations(self) -> None:
        available = await self._broker.get_available_capital()
        result = self._shift_engine.compute_allocations(
            list(self._strategies.values()), self._regime, available
        )
        self._allocations = result.allocations
        await self._intent_logger.log_allocation(result)

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Build the broadcast payload."""
        return {
            "type": "snapshot",
            "ts": datetime.utcnow().isoformat(),
            "regime": str(self._regime),
            "regime_features": self._regime_features,
            "broker": self._broker.snapshot(),
            "allocations": self._allocations,
            "signals": list(self._recent_signals)[-10:],
            "ltp": {s: round(v, 2) for s, v in self._broker._ltp.items()},
        }

    # ── Kill-switch ────────────────────────────────────────────────────────────

    async def manual_kill(self) -> None:
        daily_pnl = await self._broker.get_daily_pnl()
        await self._broker.square_off_all()
        await self._intent_logger.log_kill_switch(daily_pnl, 0.0)
        logger.critical("MANUAL KILL-SWITCH triggered via API")


# ── Module-level engine instance ───────────────────────────────────────────────

_engine: Optional[JarvisEngine] = None
_manager = ConnectionManager()


def _get_engine() -> Optional[JarvisEngine]:
    return _engine


# ── FastAPI lifecycle ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine
    _engine = JarvisEngine(
        initial_capital=float(os.getenv("INITIAL_CAPITAL", "10000")),
        kill_switch_amount=float(os.getenv("INITIAL_CAPITAL", "10000"))
        * float(os.getenv("KILL_SWITCH_PCT", "0.03")),
        kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.5")),
        intent_log_path=os.getenv("INTENT_LOG_PATH", "logs/intent.jsonl"),
        pnl_db_path=os.getenv("PNL_DB_PATH", "data/pnl.db"),
    )
    await _engine.start()
    # Broadcast loop
    broadcast_task = asyncio.create_task(_broadcast_loop())
    yield
    broadcast_task.cancel()
    await _engine.stop()


async def _broadcast_loop() -> None:
    interval = BROADCAST_INTERVAL_MS / 1000.0
    while True:
        await asyncio.sleep(interval)
        if _engine and _manager.client_count > 0:
            await _manager.broadcast(_engine.snapshot())


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS Trading Command Center", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await _manager.connect(ws)
    # Send first snapshot immediately on connect
    if _engine:
        await ws.send_json(_engine.snapshot())
    try:
        while True:
            data = await ws.receive_json()
            cmd = data.get("type")
            if cmd == "kill_switch" and _engine:
                await _engine.manual_kill()
                await ws.send_json({"type": "ack", "cmd": "kill_switch"})
            elif cmd == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        _manager.disconnect(ws)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    if _engine is None:
        return {"status": "initializing"}
    return {
        "status": "running",
        "regime": str(_engine._regime),
        "kill_switch": _engine._broker.is_killed(),
        "ws_clients": _manager.client_count,
        "strategies": list(_engine._strategies.keys()),
    }


@app.get("/api/snapshot")
async def get_snapshot():
    if _engine is None:
        return {"error": "engine not ready"}
    return _engine.snapshot()


@app.get("/api/pnl")
async def get_pnl():
    if _engine is None:
        return {"error": "engine not ready"}
    return await _engine._pnl_tracker.get_session_summary()


@app.get("/api/equity")
async def get_equity():
    if _engine is None:
        return {"error": "engine not ready"}
    return await _engine._pnl_tracker.get_equity_curve(days=30)


@app.get("/api/intent")
async def get_intent():
    if _engine is None:
        return {"error": "engine not ready"}
    return await _engine._intent_logger.tail(50)


@app.get("/api/strategies")
async def get_strategies():
    if _engine is None:
        return {"error": "engine not ready"}
    strategies_info = []
    for sid, strat in _engine._strategies.items():
        stats = strat.get_stats()
        strategies_info.append({
            "id": sid,
            "sharpe": round(strat.get_sharpe(), 3),
            "win_rate": round(stats.win_rate, 3),
            "sample_size": stats.sample_size,
            "active_in_regime": strat.is_active(_engine._regime),
            "allocation": _engine._allocations.get(sid, 0.0),
        })
    strategies_info.sort(key=lambda x: x["sharpe"], reverse=True)
    return {"regime": str(_engine._regime), "strategies": strategies_info}


@app.post("/api/kill")
async def trigger_kill():
    if _engine is None:
        return {"error": "engine not ready"}
    await _engine.manual_kill()
    return {"status": "kill_switch_activated"}
