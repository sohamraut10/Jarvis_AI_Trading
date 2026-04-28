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
from intelligence.pnl_tracker import PnLTracker
from intelligence.regime_classifier import RegimeClassifier
from intelligence.strategy_shift_engine import StrategyShiftEngine
from strategies.base_strategy import Bar, BaseStrategy, Signal, SignalSide
from strategies.momentum.orb_breakout import ORBBreakout
from strategies.momentum.rsi_momentum import RSIMomentum
from strategies.momentum.vwap_breakout import VWAPBreakout
from strategies.trend.ema_crossover import EMACrossover
from strategies.trend.supertrend import SuperTrend

import websockets

logger = logging.getLogger(__name__)

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

BROADCAST_INTERVAL_MS  = 500
REGIME_RECLASSIFY_BARS = 5
SIGNAL_DEDUP_SECONDS   = 60
WS_PORT   = 8765
HTTP_PORT = 8766

WATCH_SYMBOLS: list[str] = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]
# NSE currency futures — set to [] to disable, or add pairs: "EURINR", "GBPINR", "JPYINR"
CURRENCY_SYMBOLS: list[str] = ["USDINR"]
BASE_PRICES: dict[str, float] = {
    "RELIANCE": 2500.0, "TCS": 3800.0, "INFY": 1500.0,
    "HDFCBANK": 1700.0, "SBIN": 800.0,
    # Currency pair base prices (INR per unit, used only by SimulatedFeed)
    "USDINR": 84.0, "EURINR": 90.0, "GBPINR": 105.0, "JPYINR": 0.55,
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
    TICK_INTERVAL  = 0.1
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
        self._recent_signals: deque[dict] = deque(maxlen=50)
        self._close_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=250))
        self._bars_since_reclassify = 0
        self._signal_dedup: dict[str, datetime] = {}
        self._disabled_strategies: set[str] = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._tick_count = 0

    async def start(self) -> None:
        await self._pnl_tracker.init()
        self._running = True
        feed_type = type(self._feed).__name__
        logger.info("=" * 55)
        logger.info("  JARVIS ENGINE STARTED  [feed=%s]", feed_type)
        logger.info("  Strategies : %s", ", ".join(self._strategies.keys()))
        all_syms = WATCH_SYMBOLS + CURRENCY_SYMBOLS
        logger.info("  Equities   : %s", ", ".join(WATCH_SYMBOLS))
        if CURRENCY_SYMBOLS:
            logger.info("  Currency   : %s", ", ".join(CURRENCY_SYMBOLS))
        logger.info("  Capital    : ₹%.0f", self._broker._initial_capital if hasattr(self._broker, '_initial_capital') else 0)
        logger.info("=" * 55)
        self._tasks.append(asyncio.create_task(self._feed.start(self._on_tick)))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

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
        except Exception as exc:
            logger.error("tick error %s: %s", symbol, exc)

    async def _on_bar(self, bar: Bar) -> None:
        logger.debug("bar  %s %s  O=%.2f H=%.2f L=%.2f C=%.2f  vol=%d",
                     bar.timeframe, bar.symbol,
                     bar.open, bar.high, bar.low, bar.close, bar.volume)

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

        for sid in _TF_MAP.get(bar.timeframe, []):
            strat = self._strategies.get(sid)
            if strat is None:
                continue
            if sid in self._disabled_strategies:
                logger.debug("strategy %s skipped (disabled by user)", sid)
                continue
            if not strat.is_active(self._regime):
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
        qty = self._kelly_sizer.size(stats, ltp, available)
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

    def snapshot(self) -> dict:
        return {
            "type": "snapshot", "ts": _utcnow().isoformat(),
            "regime": str(self._regime), "regime_features": self._regime_features,
            "broker": self._broker.snapshot(), "allocations": self._allocations,
            "signals": list(self._recent_signals)[-10:],
            "ltp": {s: round(v, 2) for s, v in self._broker._ltp.items()},
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
        return 200, {**raw,
                     "dhan_client_id":    _mask(raw.get("dhan_client_id", "")),
                     "dhan_access_token": "•" * 8 if raw.get("dhan_access_token") else ""}
    if path == "/api/settings" and method == "POST":
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if SETTINGS_FILE.exists():
            try:
                existing = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        merged = {**existing, **body}
        for k in ("dhan_client_id", "dhan_access_token"):
            if "•" in str(merged.get(k, "")):
                merged[k] = existing.get(k, "")
        SETTINGS_FILE.write_text(json.dumps(merged, indent=2))
        restart_required = bool({"initial_capital", "paper_mode", "hmm_states",
                                  "ws_port", "pnl_db_path", "intent_log_path"} & set(body.keys()))
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
            all_syms = WATCH_SYMBOLS + CURRENCY_SYMBOLS
            logger.info("Dhan credentials found — using live market feed (paper orders)")
            if CURRENCY_SYMBOLS:
                logger.info("Currency pairs enabled: %s", ", ".join(CURRENCY_SYMBOLS))
            return DhanFeed(client_id, access_token, WATCH_SYMBOLS,
                            currency_symbols=CURRENCY_SYMBOLS or None)
        except Exception as exc:
            logger.warning("Could not load DhanFeed (%s) — falling back to SimulatedFeed", exc)

    all_syms = WATCH_SYMBOLS + CURRENCY_SYMBOLS
    logger.info("No Dhan credentials — using SimulatedFeed")
    return SimulatedFeed(all_syms, BASE_PRICES)


async def main() -> None:
    global _engine, WATCH_SYMBOLS, CURRENCY_SYMBOLS

    cfg = _load_settings()
    logging.basicConfig(
        level=getattr(logging, cfg.get("log_level", "INFO"), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Override symbol lists from settings if saved via frontend
    if "watch_symbols" in cfg and cfg["watch_symbols"]:
        WATCH_SYMBOLS = cfg["watch_symbols"]
    if "currency_symbols" in cfg:
        CURRENCY_SYMBOLS = cfg["currency_symbols"]

    pathlib.Path(cfg["intent_log_path"]).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(cfg["pnl_db_path"]).parent.mkdir(parents=True, exist_ok=True)

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
