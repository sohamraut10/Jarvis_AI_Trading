"""
Unit tests for RiskManager.
Run: pytest tests/test_risk_manager.py -v
"""

import pytest
from core.broker.base_broker import Exchange, Order, OrderSide, OrderType, ProductType
from core.broker.paper_broker import PaperBroker
from core.risk.risk_manager import RiskManager


def _buy(symbol: str, qty: int, strategy_id: str = "strat_a") -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=qty,
        product=ProductType.INTRADAY,
        exchange=Exchange.NSE,
        strategy_id=strategy_id,
    )


def _sell(symbol: str, qty: int, strategy_id: str = "strat_a") -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=qty,
        product=ProductType.INTRADAY,
        exchange=Exchange.NSE,
        strategy_id=strategy_id,
    )


def make_broker_and_rm(capital=10_000.0, kill_amount=300.0):
    broker = PaperBroker(initial_capital=capital, kill_switch_amount=kill_amount, slippage_pct=0.0)
    rm = RiskManager(broker, kill_switch_amount=kill_amount, max_open_positions=5)
    return broker, rm


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_order_approved():
    broker, rm = make_broker_and_rm()
    await broker.update_ltp("RELIANCE", 2500.0)
    decision = await rm.check(_buy("RELIANCE", 1), ltp=2500.0)
    assert decision.approved


# ── Kill-switch gate ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejected_when_kill_switch_active():
    broker, rm = make_broker_and_rm(capital=10_000, kill_amount=100.0)
    await broker.update_ltp("SBIN", 500.0)
    await broker.place_order(_buy("SBIN", 10, strategy_id="s"))    # cost=5000
    await broker.update_ltp("SBIN", 480.0)                          # loss=-200 < -100
    assert broker.is_killed()
    decision = await rm.check(_buy("SBIN", 1, "s"), ltp=480.0)
    assert not decision.approved
    assert "kill_switch" in decision.reason


# ── Daily P&L gate ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejected_when_daily_pnl_at_threshold():
    broker, rm = make_broker_and_rm(capital=10_000, kill_amount=50.0)
    await broker.update_ltp("TCS", 1000.0)
    await broker.place_order(_buy("TCS", 1, "s"))   # buy at 1000
    await broker.update_ltp("TCS", 940.0)            # unrealized = -60 < -50
    decision = await rm.check(_buy("TCS", 1, "s"), ltp=940.0)
    assert not decision.approved


# ── Capital sufficiency ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejected_when_no_capital():
    broker, rm = make_broker_and_rm(capital=100.0)
    decision = await rm.check(_buy("INFY", 1), ltp=500.0)
    assert not decision.approved
    assert "insufficient" in decision.reason


# ── Single-trade cap — qty trimmed ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_trade_cap_trims_qty():
    # max trade = 20% of 10 000 = ₹2 000; at ltp=500, max qty = 4
    broker, rm = make_broker_and_rm()
    order = _buy("HDFC", 100)   # would be ₹50 000 at ltp=500 — way over cap
    decision = await rm.check(order, ltp=500.0)
    assert decision.approved
    assert decision.adjusted_qty == 4


# ── Symbol concentration ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_symbol_concentration_blocks_excess():
    # trade cap (20% of 10 000 = ₹2 000 → 4 shares) fires before
    # symbol concentration (30% = ₹3 000 → 6 shares); binding = 4
    broker, rm = make_broker_and_rm()
    order = _buy("WIPRO", 7)
    decision = await rm.check(order, ltp=500.0)
    assert decision.approved
    assert decision.adjusted_qty == 4   # single-trade cap is binding


# ── Open-position count ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_open_positions_blocks_new_symbol():
    broker, rm = make_broker_and_rm()
    syms = ["A", "B", "C", "D", "E"]
    for s in syms:
        await broker.update_ltp(s, 100.0)
        await broker.place_order(_buy(s, 1))

    # 6th distinct symbol should be rejected
    await broker.update_ltp("F", 100.0)
    decision = await rm.check(_buy("F", 1), ltp=100.0)
    assert not decision.approved
    assert "max open positions" in decision.reason


# ── Sell orders skip concentration check ─────────────────────────────────────

@pytest.mark.asyncio
async def test_sell_always_passes_concentration():
    broker, rm = make_broker_and_rm()
    await broker.update_ltp("SBIN", 500.0)
    await broker.place_order(_buy("SBIN", 2))
    decision = await rm.check(_sell("SBIN", 2), ltp=500.0)
    assert decision.approved
