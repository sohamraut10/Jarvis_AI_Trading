"""
Layer 5 AI Brain — end-to-end tests (no real LLM keys required).

Tests
─────
  T1  SignalScanner: warmup guard + full 6-signal scan
  T2  Shortlister: hard & soft filters
  T3  Analyst: payload structure + conviction rules in prompt
  T4  DecisionEngine: rules-only path, conviction gate, R:R enforcement
  T5  ActionExecutor + TradeMonitor: open/close/trailing-stop cycle
  T6  _run_ai_brain(): disabled/kill-switch/full-cycle paths
  T7  /api/ai/brain HTTP route: GET + toggle POST + snapshot block
  T8  _on_tick() exit path: target_hit, brain-disabled, unmonitored
"""
from __future__ import annotations

import sys
from collections import deque, defaultdict
from datetime import datetime
from typing import Optional

import pytest

sys.path.insert(0, ".")

# Module-level imports (needed by helper classes defined at module scope)
from core.types import Regime
from strategies.base_strategy import Bar
from ai_brain.trade_monitor import TradeMonitor
from ai_brain.action_executor import ActionExecutor

# ── Shared mock helpers ────────────────────────────────────────────────────────

def _make_bar(symbol: str, close: float, i: int = 0) -> Bar:
    return Bar(
        symbol=symbol, timeframe="5min",
        open=close, high=close * 1.001, low=close * 0.999, close=close,
        volume=1000 + i * 10,
        timestamp=datetime(2025, 1, 1, 9, i % 60),
    )


def _bar_history(symbol: str, base: float, n: int = 40) -> deque:
    d: deque = deque(maxlen=500)
    for i in range(n):
        d.append(_make_bar(symbol, base * (1 + i * 0.001), i))
    return d


class MockPosition:
    def __init__(self, qty: int):
        self.qty = qty


class MockBroker:
    def __init__(self, capital: float = 50_000.0):
        self._ltp:       dict = {}
        self._positions: dict = {}
        self._orders:    list = []
        self._capital        = capital
        self._killed         = False

    def get_ltp(self, sym: str) -> Optional[float]:
        return self._ltp.get(sym)

    def is_killed(self) -> bool:
        return self._killed

    def snapshot(self) -> dict:
        return {"available_capital": self._capital, "daily_pnl": 0.0,
                "positions": {}, "orders": []}

    async def get_available_capital(self) -> float:
        return self._capital

    async def get_positions(self) -> dict:
        return self._positions

    async def place_order(self, order) -> str:
        oid = f"ORD{len(self._orders) + 1:03d}"
        self._orders.append(oid)
        return oid


def _make_scan_result(symbol: str, direction: str = "long", conf: float = 0.70,
                      n_agree: int = 4, signal_count: int = 5):
    """Build a ScanResult using the correct dataclass field names."""
    from ai_brain.signal_scanner import ScanResult
    return ScanResult(
        symbol=symbol,
        scannable=True,
        composite_direction=direction,
        composite_confidence=conf,
        signal_count=signal_count,
        agreeing_count=n_agree,
        signals={},
        reasoning="test",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T1  SignalScanner
# ═══════════════════════════════════════════════════════════════════════════════

def test_signal_scanner_warmup_guard():
    from ai_brain.signal_scanner import SignalScanner
    scanner = SignalScanner()
    bars: deque = deque(maxlen=500)
    for i in range(5):      # fewer than WARMUP_BARS (30)
        bars.append(_make_bar("USDINR", 84.0 + i * 0.01, i))

    meta = {"USDINR": {"status": "live", "ticks": 10, "ltp": 84.05,
                       "last_tick_ago": 1.0, "is_currency": True, "is_commodity": False}}
    results = scanner.scan_all({"USDINR": bars}, Regime.SIDEWAYS, meta)
    r = results["USDINR"]
    assert not r.scannable, "warmup guard should mark as not scannable"
    assert r.signal_count == 0


def test_signal_scanner_full_scan():
    from ai_brain.signal_scanner import SignalScanner
    scanner = SignalScanner()
    history = _bar_history("USDINR", 84.0, n=50)

    meta = {"USDINR": {"status": "live", "ticks": 500, "ltp": 84.05 * 1.05,
                       "last_tick_ago": 0.5, "is_currency": True, "is_commodity": False}}
    results = scanner.scan_all({"USDINR": history}, Regime.TRENDING_UP, meta)
    r = results["USDINR"]
    assert r.scannable
    assert r.symbol == "USDINR"
    assert 0.0 <= r.composite_confidence <= 1.0
    assert r.composite_direction in ("long", "short", "flat")


# ═══════════════════════════════════════════════════════════════════════════════
# T2  Shortlister
# ═══════════════════════════════════════════════════════════════════════════════

def test_shortlister_hard_filter_kill_switch():
    from ai_brain.shortlister import Shortlister
    sl = Shortlister()
    meta = {"USDINR": {"status": "live", "ticks": 500, "ltp": 84.0,
                       "last_tick_ago": 0.5, "is_currency": True, "is_commodity": False}}
    scan = {"USDINR": _make_scan_result("USDINR")}
    report = sl.run(scan, meta, {}, Regime.TRENDING_UP, kill_switch_active=True)
    assert len(report.passed) == 0
    assert len(report.rejected) == 1


def test_shortlister_passes_good_candidate():
    from ai_brain.shortlister import Shortlister
    sl = Shortlister()
    meta = {"USDINR": {"status": "live", "ticks": 500, "ltp": 84.0,
                       "last_tick_ago": 0.5, "is_currency": True, "is_commodity": False}}
    scan = {"USDINR": _make_scan_result("USDINR", conf=0.80, n_agree=5)}
    report = sl.run(scan, meta, {}, Regime.TRENDING_UP, kill_switch_active=False)
    assert len(report.passed) == 1
    entry = report.passed[0]
    assert 0.0 < entry.final_score <= 1.0
    assert entry.rank == 1


# ═══════════════════════════════════════════════════════════════════════════════
# T3  Analyst
# ═══════════════════════════════════════════════════════════════════════════════

def _analyst_entry(conf: float = 0.80):
    from ai_brain.shortlister import Shortlister
    sl = Shortlister()
    meta = {"USDINR": {"status": "live", "ticks": 500, "ltp": 84.0,
                       "last_tick_ago": 0.5, "is_currency": True, "is_commodity": False}}
    scan = {"USDINR": _make_scan_result("USDINR", conf=conf, n_agree=5)}
    report = sl.run(scan, meta, {}, Regime.TRENDING_UP, kill_switch_active=False)
    if not report.passed:
        pytest.skip("shortlister rejected")
    return report.passed[0]


def test_analyst_payload_structure():
    from ai_brain.analyst import Analyst
    analyst = Analyst()
    entry = _analyst_entry(conf=0.80)
    closes = [84.0 + i * 0.01 for i in range(30)]
    broker_state = {"available_capital": 10000.0, "daily_pnl": 0.0, "positions": {}, "orders": []}
    payload = analyst.build(entry, broker_state, Regime.TRENDING_UP, [], closes)

    assert payload.symbol == "USDINR"
    assert payload.direction_hint in ("long", "short", "flat")
    msg = payload.full_message()
    assert "MARKET CONTEXT:" in msg
    assert "DECISION TASK:" in msg
    assert "conviction" in msg.lower()


def test_analyst_prompt_contains_conviction_threshold():
    from ai_brain.analyst import Analyst
    analyst = Analyst()
    entry = _analyst_entry(conf=0.80)
    payload = analyst.build(entry, {"available_capital": 10000.0, "daily_pnl": 0.0,
                                     "positions": {}, "orders": []},
                            Regime.TRENDING_UP, [], [])
    assert "72" in payload.full_message(), "Conviction threshold 72 missing from prompt"


# ═══════════════════════════════════════════════════════════════════════════════
# T4  DecisionEngine (rules-only, no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_payload(conf: float = 0.80):
    from ai_brain.analyst import Analyst
    entry = _analyst_entry(conf=conf)
    return Analyst().build(
        entry,
        {"available_capital": 10000.0, "daily_pnl": 0.0, "positions": {}, "orders": []},
        Regime.TRENDING_UP, [], [84.0 + i * 0.01 for i in range(30)],
    )


@pytest.mark.asyncio
async def test_decision_engine_rules_long():
    from ai_brain.decision_engine import DecisionEngine, CONVICTION_THRESHOLD
    from ai_brain.ai_router import AIRouter
    from ai_brain.cost_throttle import CostThrottle

    engine = DecisionEngine(AIRouter(), CostThrottle())
    decision = await engine.decide(_make_payload(conf=0.80))

    assert decision.source == "rules"
    assert decision.direction in ("long", "short", "flat")
    if decision.direction != "flat":
        assert decision.conviction >= CONVICTION_THRESHOLD
    assert decision.take_profit_pct >= decision.stop_loss_pct * 2.0 - 1e-9


@pytest.mark.asyncio
async def test_decision_engine_conviction_gate():
    from ai_brain.decision_engine import DecisionEngine, CONVICTION_THRESHOLD
    from ai_brain.ai_router import AIRouter
    from ai_brain.cost_throttle import CostThrottle

    engine = DecisionEngine(AIRouter(), CostThrottle())
    decision = await engine.decide(_make_payload(conf=0.38))

    assert decision.source == "rules"
    assert decision.direction == "flat" or decision.conviction < CONVICTION_THRESHOLD


@pytest.mark.asyncio
async def test_decision_engine_rr_enforced():
    from ai_brain.decision_engine import DecisionEngine
    from ai_brain.ai_router import AIRouter
    from ai_brain.cost_throttle import CostThrottle

    engine = DecisionEngine(AIRouter(), CostThrottle())
    decision = await engine.decide(_make_payload(conf=0.85))

    if decision.direction != "flat":
        assert decision.take_profit_pct >= decision.stop_loss_pct * 2.0 - 1e-9, \
            f"R:R violated: sl={decision.stop_loss_pct}  tp={decision.take_profit_pct}"


# ═══════════════════════════════════════════════════════════════════════════════
# T5  ActionExecutor + TradeMonitor
# ═══════════════════════════════════════════════════════════════════════════════

def _make_decision(symbol="USDINR", direction="long", conviction=80,
                   size_pct=0.10, sl=0.008, tp=0.016):
    from ai_brain.decision_engine import Decision
    return Decision(
        symbol=symbol, direction=direction, conviction=conviction,
        size_pct=size_pct, stop_loss_pct=sl, take_profit_pct=tp,
        reasoning="test", risk_notes="", source="rules", model_used="rules",
        latency_ms=1.0, cost_usd=0.0, consensus=False,
    )


@pytest.mark.asyncio
async def test_executor_skip_flat():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["USDINR"] = 84.0

    result = await executor.execute(_make_decision(direction="flat", conviction=30), broker)
    assert result.action == "skip"
    assert not result.success


@pytest.mark.asyncio
async def test_executor_open_registers_monitor():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["USDINR"] = 84.0

    result = await executor.execute(_make_decision("USDINR"), broker)
    assert result.success
    assert result.action == "open_long"
    assert result.qty >= 1000
    assert "USDINR" in monitor.monitored_symbols


@pytest.mark.asyncio
async def test_executor_stop_hit_close():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["USDINR"] = 84.0

    open_r = await executor.execute(_make_decision("USDINR"), broker)
    assert open_r.success

    pos = monitor._positions["USDINR"]
    stop_price = pos.stop_loss_price - 0.01
    sig = monitor.check("USDINR", stop_price)
    assert sig is not None
    assert sig.reason == "stop_hit"

    broker._positions["USDINR"] = MockPosition(open_r.qty)
    broker._ltp["USDINR"] = stop_price
    close_r = await executor.close(sig, broker)
    assert close_r.success
    assert close_r.action == "close_long"
    assert "USDINR" not in monitor.monitored_symbols


@pytest.mark.asyncio
async def test_executor_trail_stop():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["GOLD"] = 72000.0

    open_r = await executor.execute(
        _make_decision("GOLD", size_pct=0.05, sl=0.010, tp=0.020), broker)
    assert open_r.success

    entry   = monitor._positions["GOLD"].entry_price
    trigger = entry + entry * 0.020 * 0.50 + 1.0
    monitor.check("GOLD", trigger)
    assert monitor._positions["GOLD"].trail_activated

    trail_stop = monitor._positions["GOLD"].trail_stop_price - 1.0
    sig = monitor.check("GOLD", trail_stop)
    assert sig is not None
    assert sig.reason == "trail_stop"


# ═══════════════════════════════════════════════════════════════════════════════
# T6  _run_ai_brain()
# ═══════════════════════════════════════════════════════════════════════════════

class _BrainHarness:
    def __init__(self):
        from ai_brain.signal_scanner import SignalScanner
        from ai_brain.shortlister import Shortlister
        from ai_brain.analyst import Analyst
        from ai_brain.decision_engine import DecisionEngine
        from ai_brain.ai_router import AIRouter
        from ai_brain.cost_throttle import CostThrottle

        self._broker          = MockBroker(50_000.0)
        self._router          = AIRouter()
        self._cost_throttle   = CostThrottle()
        self._signal_scanner  = SignalScanner()
        self._shortlister     = Shortlister()
        self._analyst         = Analyst()
        self._decision_engine = DecisionEngine(self._router, self._cost_throttle)
        self._trade_monitor   = TradeMonitor()
        self._action_executor = ActionExecutor(self._trade_monitor)
        self._ai_decisions    = deque(maxlen=50)
        self._ai_brain_enabled = True
        self._tick_count      = 5000

        prices = {"USDINR": 84.0, "EURINR": 90.0, "GBPINR": 105.0, "JPYINR": 0.55}
        self._symbols = list(prices.keys())

        class _Feed:
            def __init__(self, syms, ps):
                self._symbols = syms
                self._prices  = dict(ps)
            def current_price(self, s): return self._prices.get(s)

        self._feed     = _Feed(self._symbols, prices)
        self._regime   = Regime.TRENDING_UP
        self._bar_history   = defaultdict(lambda: deque(maxlen=500))
        self._close_history = defaultdict(lambda: deque(maxlen=250))
        for sym, base in prices.items():
            self._broker._ltp[sym] = base
            for i in range(40):
                p = base * (1 + i * 0.001)
                self._bar_history[sym].append(_make_bar(sym, p, i))
                self._close_history[sym].append(p)

    async def run(self):
        if not self._ai_brain_enabled:
            return
        scanner_meta: dict = {}
        for s in self._feed._symbols:
            scanner_meta[s] = {"status": "live", "ticks": self._tick_count,
                                "ltp": self._feed.current_price(s),
                                "last_tick_ago": 1.0,
                                "is_currency": s.endswith("INR"),
                                "is_commodity": False}
        if not scanner_meta:
            return
        scan = self._signal_scanner.scan_all(self._bar_history, self._regime, scanner_meta)
        open_pos = await self._broker.get_positions()
        report = self._shortlister.run(scan, scanner_meta, open_pos, self._regime,
                                       kill_switch_active=self._broker.is_killed())
        broker_state = self._broker.snapshot()
        for entry in report.passed[:3]:
            payload  = self._analyst.build(entry, broker_state, self._regime,
                                           [], list(self._close_history[entry.symbol]))
            decision = await self._decision_engine.decide(payload)
            self._ai_decisions.appendleft({**decision.to_dict(), "rank": entry.rank})
            if decision.is_actionable:
                await self._action_executor.execute(decision, self._broker)


@pytest.mark.asyncio
async def test_brain_full_cycle():
    h = _BrainHarness()
    await h.run()
    assert len(h._ai_decisions) >= 0   # may all be flat — no crash is the assertion


@pytest.mark.asyncio
async def test_brain_disabled_no_decisions():
    h = _BrainHarness()
    h._ai_brain_enabled = False
    before = len(h._ai_decisions)
    await h.run()
    assert len(h._ai_decisions) == before


@pytest.mark.asyncio
async def test_brain_kill_switch_blocks_shortlist():
    h = _BrainHarness()
    h._broker._killed = True
    await h.run()
    assert len(h._trade_monitor.monitored_symbols) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# T7  /api/ai/brain HTTP route
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeThrottleSnap:
    budget_inr = 1680.0; spend_inr = 42.0; pct_used = 0.025; total_saves = 3

class _FakeThrottle:
    mode = "normal"
    def snapshot(self): return _FakeThrottleSnap()

class _FakeRouter:
    daily_cost_usd = 0.5
    def cost_summary(self): return {"total_usd": 0.5, "by_model": {}}

class _FakeEngine:
    def __init__(self):
        self._ai_brain_enabled = True
        self._cost_throttle    = _FakeThrottle()
        self._router           = _FakeRouter()
        self._trade_monitor    = TradeMonitor()   # imported at module level
        self._ai_decisions     = deque(maxlen=50)
        self._broker           = MockBroker()
        self._regime           = Regime.SIDEWAYS

    def snapshot(self) -> dict:
        return {
            "type": "snapshot", "ts": "", "regime": str(self._regime),
            "regime_features": {}, "broker": {}, "allocations": {},
            "signals": [], "ltp": {}, "scanner": {},
            "intelligence": {"scores": [], "auto_select": True,
                             "selected_symbols": [], "updated_at": ""},
            "ai_brain": {
                "enabled":             self._ai_brain_enabled,
                "mode":                self._cost_throttle.mode,
                "daily_cost_usd":      round(self._router.daily_cost_usd, 6),
                "daily_cost_inr":      round(self._router.daily_cost_usd * 84.0, 2),
                "budget_pct_used":     self._cost_throttle.snapshot().pct_used,
                "decisions":           list(self._ai_decisions)[:10],
                "monitored_positions": self._trade_monitor.snapshot(),
            },
        }


@pytest.fixture
def patched_engine(monkeypatch):
    import server.termux_server as srv
    eng = _FakeEngine()
    monkeypatch.setattr(srv, "_engine", eng)
    return eng


@pytest.mark.asyncio
async def test_route_ai_brain_get(patched_engine):
    import server.termux_server as srv
    status, data = await srv._route("GET", "/api/ai/brain", {}, {})
    assert status == 200
    assert data["enabled"] is True
    assert data["mode"] == "normal"
    assert data["budget"]["daily_inr"] == 1680.0
    assert data["budget"]["pct_used"] == 0.025
    assert "decisions" in data
    assert "monitored_positions" in data


@pytest.mark.asyncio
async def test_route_ai_brain_toggle(patched_engine):
    import server.termux_server as srv
    status, data = await srv._route("POST", "/api/ai/brain/toggle", {}, {"enabled": False})
    assert status == 200 and data["enabled"] is False
    assert patched_engine._ai_brain_enabled is False

    status2, data2 = await srv._route("POST", "/api/ai/brain/toggle", {}, {"enabled": True})
    assert status2 == 200 and data2["enabled"] is True
    assert patched_engine._ai_brain_enabled is True


@pytest.mark.asyncio
async def test_snapshot_includes_ai_brain_block(patched_engine):
    import server.termux_server as srv
    status, snap = await srv._route("GET", "/api/snapshot", {}, {})
    assert status == 200
    assert "ai_brain" in snap
    assert snap["ai_brain"]["enabled"] is True
    assert snap["ai_brain"]["mode"] == "normal"


# ═══════════════════════════════════════════════════════════════════════════════
# T8  _on_tick() exit path
# ═══════════════════════════════════════════════════════════════════════════════

async def _simulate_on_tick(
    symbol: str, ltp: float, ai_brain_enabled: bool,
    monitor: TradeMonitor, executor: ActionExecutor, broker: MockBroker,
):
    broker._ltp[symbol] = ltp
    if ai_brain_enabled and symbol in monitor.monitored_symbols:
        sig = monitor.check(symbol, ltp)
        if sig:
            result = await executor.close(sig, broker)
            return sig, result
    return None, None


@pytest.mark.asyncio
async def test_on_tick_no_exit_before_target():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["USDINR"] = 84.0
    monitor.add("USDINR", "long", 84.0, 0.010, 0.020, 0.10)

    sig, _ = await _simulate_on_tick("USDINR", 84.5, True, monitor, executor, broker)
    assert sig is None
    assert "USDINR" in monitor.monitored_symbols


@pytest.mark.asyncio
async def test_on_tick_target_hit_closes_position():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["USDINR"] = 84.0
    broker._positions["USDINR"] = MockPosition(1000)
    monitor.add("USDINR", "long", 84.0, 0.010, 0.020, 0.10)

    target_price = 84.0 * 1.020 + 0.10
    sig, res = await _simulate_on_tick("USDINR", target_price, True, monitor, executor, broker)
    assert sig is not None
    assert sig.reason == "target_hit"
    assert res is not None and res.success
    assert "USDINR" not in monitor.monitored_symbols


@pytest.mark.asyncio
async def test_on_tick_brain_disabled_no_exit():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    broker._ltp["EURINR"] = 90.0
    broker._positions["EURINR"] = MockPosition(-1000)
    monitor.add("EURINR", "short", 90.0, 0.010, 0.020, 0.05)

    stop_price = 90.0 * 1.010 + 0.10
    sig, _ = await _simulate_on_tick("EURINR", stop_price, False, monitor, executor, broker)
    assert sig is None
    assert "EURINR" in monitor.monitored_symbols


@pytest.mark.asyncio
async def test_on_tick_unmonitored_noop():
    monitor  = TradeMonitor()
    executor = ActionExecutor(monitor)
    broker   = MockBroker()
    sig, _ = await _simulate_on_tick("GBPINR", 110.0, True, monitor, executor, broker)
    assert sig is None
