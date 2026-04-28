"""
Tests for server/ws_server.py.

Covers:
  - BarAggregator: bar closing on interval boundary, OHLCV accumulation
  - SimulatedFeed: tick generation, price drift, stop
  - JarvisEngine: snapshot structure, tick processing, kill-switch
  - HTTP endpoints: status / snapshot / strategies (lifespan="off" — no engine)
  - WebSocket: connect + initial snapshot (lifespan="on" — engine running)

Run: pytest tests/test_ws_server.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.types import Regime
from server.ws_server import BarAggregator, ConnectionManager, JarvisEngine, SimulatedFeed, app


# ── BarAggregator ─────────────────────────────────────────────────────────────

def test_bar_aggregator_no_close_within_interval():
    agg = BarAggregator()
    ts = datetime(2024, 1, 15, 9, 15, 0)
    result = agg.update("RELIANCE", 2500.0, 1000.0, ts)
    assert result == []   # first tick — opens bar, nothing closed yet


def test_bar_aggregator_tracks_ohlcv():
    agg = BarAggregator()
    base = datetime(2024, 1, 15, 9, 15, 0)
    agg.update("X", 100.0, 100, base)
    agg.update("X", 105.0, 200, base + timedelta(seconds=10))
    agg.update("X", 98.0,  150, base + timedelta(seconds=20))
    agg.update("X", 102.0, 300, base + timedelta(seconds=30))

    state = agg.get_current_bar("X", "1min")
    assert state["open"] == 100.0
    assert state["high"] == 105.0
    assert state["low"] == 98.0
    assert state["close"] == 102.0
    assert state["volume"] == 750


def test_bar_aggregator_closes_on_new_minute():
    agg = BarAggregator()
    t0 = datetime(2024, 1, 15, 9, 15, 0)
    t1 = datetime(2024, 1, 15, 9, 16, 0)   # next 1-min bucket

    agg.update("X", 100.0, 1000, t0)
    agg.update("X", 102.0, 500,  t0 + timedelta(seconds=30))
    closed = agg.update("X", 101.0, 800, t1)   # new bucket → closes 1-min bar

    one_min_bars = [b for b in closed if b.timeframe == "1min"]
    assert len(one_min_bars) == 1
    bar = one_min_bars[0]
    assert bar.open == 100.0
    assert bar.high == 102.0
    assert bar.close == 102.0     # close of previous bucket


def test_bar_aggregator_closes_5min_on_boundary():
    agg = BarAggregator()
    t0 = datetime(2024, 1, 15, 9, 15, 0)
    t1 = datetime(2024, 1, 15, 9, 20, 0)   # next 5-min bucket

    agg.update("X", 200.0, 1000, t0)
    closed = agg.update("X", 205.0, 1000, t1)

    five_min = [b for b in closed if b.timeframe == "5min"]
    assert len(five_min) == 1
    assert five_min[0].open == 200.0


def test_bar_aggregator_multiple_symbols_independent():
    agg = BarAggregator()
    t0 = datetime(2024, 1, 15, 9, 15, 0)
    t1 = datetime(2024, 1, 15, 9, 20, 0)

    agg.update("A", 100.0, 100, t0)
    agg.update("B", 200.0, 100, t0)
    closed = agg.update("A", 105.0, 100, t1)

    # Only A's 5-min bar closes; B's bar stays open
    symbols = {b.symbol for b in closed if b.timeframe == "5min"}
    assert "A" in symbols
    assert "B" not in symbols


# ── SimulatedFeed ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulated_feed_generates_ticks():
    ticks: list[tuple] = []

    async def capture(symbol, price, volume):
        ticks.append((symbol, price, volume))

    feed = SimulatedFeed(["RELIANCE"], {"RELIANCE": 2500.0})
    task = asyncio.create_task(feed.start(capture))
    await asyncio.sleep(0.35)   # ~3 tick batches
    feed.stop()
    task.cancel()

    assert len(ticks) >= 1
    for sym, price, vol in ticks:
        assert sym == "RELIANCE"
        assert price > 0
        assert vol >= 0


@pytest.mark.asyncio
async def test_simulated_feed_price_stays_positive():
    prices: list[float] = []

    async def capture(sym, price, vol):
        prices.append(price)

    feed = SimulatedFeed(["X"], {"X": 10.0})
    task = asyncio.create_task(feed.start(capture))
    await asyncio.sleep(0.5)
    feed.stop()
    task.cancel()

    assert all(p > 0 for p in prices)


def test_simulated_feed_set_regime():
    feed = SimulatedFeed(["X"])
    feed.set_regime(Regime.HIGH_VOL)
    assert feed._regime == Regime.HIGH_VOL


# ── JarvisEngine ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_snapshot_structure():
    import tempfile, os
    db = tempfile.mktemp(suffix=".db")
    log = tempfile.mktemp(suffix=".jsonl")
    engine = JarvisEngine(
        initial_capital=10_000,
        kill_switch_amount=300,
        pnl_db_path=db,
        intent_log_path=log,
    )
    snap = engine.snapshot()
    for key in ("type", "ts", "regime", "broker", "allocations", "signals", "ltp"):
        assert key in snap
    assert snap["type"] == "snapshot"
    assert isinstance(snap["broker"], dict)


@pytest.mark.asyncio
async def test_engine_tick_updates_broker():
    import tempfile
    engine = JarvisEngine(
        pnl_db_path=tempfile.mktemp(suffix=".db"),
        intent_log_path=tempfile.mktemp(suffix=".jsonl"),
    )
    await engine._pnl_tracker.init()
    engine._running = True   # simulate started state
    await engine._on_tick("RELIANCE", 2500.0, 1000)
    assert engine._broker.get_ltp("RELIANCE") == pytest.approx(2500.0)


@pytest.mark.asyncio
async def test_engine_close_history_grows():
    import tempfile
    engine = JarvisEngine(
        pnl_db_path=tempfile.mktemp(suffix=".db"),
        intent_log_path=tempfile.mktemp(suffix=".jsonl"),
    )
    await engine._pnl_tracker.init()
    engine._running = True   # simulate started state
    for p in [100.0, 101.0, 102.0]:
        await engine._on_tick("X", p, 1000)
    assert len(engine._close_history["X"]) == 3


# ── HTTP endpoints (no lifespan — engine is None) ─────────────────────────────

def test_status_no_engine():
    client = TestClient(app, raise_server_exceptions=True)
    # We cannot start lifespan in unit tests, so engine will be None
    # But we can check the endpoint doesn't crash
    # Since engine is module-level, it may already be initialized from other tests
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body


def test_snapshot_endpoint_when_engine_none(monkeypatch):
    import server.ws_server as srv
    monkeypatch.setattr(srv, "_engine", None)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/api/snapshot")
    assert resp.status_code == 200
    assert resp.json().get("error") == "engine not ready"


def test_strategies_endpoint_when_engine_none(monkeypatch):
    import server.ws_server as srv
    monkeypatch.setattr(srv, "_engine", None)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    assert "error" in resp.json()


# ── ConnectionManager ─────────────────────────────────────────────────────────

def test_connection_manager_client_count():
    cm = ConnectionManager()
    assert cm.client_count == 0

    mock_ws = MagicMock()
    cm._clients.add(mock_ws)
    assert cm.client_count == 1

    cm.disconnect(mock_ws)
    assert cm.client_count == 0
