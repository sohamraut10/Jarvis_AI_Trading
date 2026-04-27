"""
Unit tests for PaperBroker.
Run: pytest tests/test_paper_broker.py -v
"""

import asyncio
import pytest
from core.broker.base_broker import Order, OrderSide, OrderType, ProductType, Exchange
from core.broker.paper_broker import KillSwitchError, PaperBroker


def _market_buy(symbol: str, qty: int, strategy_id: str = "test") -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=qty,
        product=ProductType.INTRADAY,
        exchange=Exchange.NSE,
        strategy_id=strategy_id,
    )


def _market_sell(symbol: str, qty: int) -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=qty,
        product=ProductType.INTRADAY,
        exchange=Exchange.NSE,
    )


def _limit_buy(symbol: str, qty: int, price: float) -> Order:
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=qty,
        price=price,
        product=ProductType.INTRADAY,
        exchange=Exchange.NSE,
    )


# ── Market order fill ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_market_buy_fills_immediately():
    broker = PaperBroker(initial_capital=10_000)
    await broker.update_ltp("RELIANCE", 2500.0)
    order = _market_buy("RELIANCE", 2)
    await broker.place_order(order)

    positions = await broker.get_positions()
    assert "RELIANCE" in positions
    assert positions["RELIANCE"].qty == 2


@pytest.mark.asyncio
async def test_capital_reduces_after_buy():
    broker = PaperBroker(initial_capital=10_000, slippage_pct=0.0)
    await broker.update_ltp("TCS", 3000.0)
    await broker.place_order(_market_buy("TCS", 1))

    capital = await broker.get_available_capital()
    assert capital == pytest.approx(7000.0)


# ── Realized P&L ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_round_trip_realized_pnl():
    broker = PaperBroker(initial_capital=10_000, slippage_pct=0.0)
    await broker.update_ltp("INFY", 1500.0)
    await broker.place_order(_market_buy("INFY", 4))
    await broker.update_ltp("INFY", 1600.0)
    await broker.place_order(_market_sell("INFY", 4))

    positions = await broker.get_positions()
    # Position closed
    assert positions.get("INFY") is None or positions["INFY"].qty == 0

    daily_pnl = await broker.get_daily_pnl()
    assert daily_pnl == pytest.approx(400.0)   # (1600-1500) * 4


# ── Limit order ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_limit_buy_does_not_fill_above_price():
    broker = PaperBroker(initial_capital=10_000)
    await broker.update_ltp("HDFC", 1800.0)
    await broker.place_order(_limit_buy("HDFC", 1, price=1750.0))

    positions = await broker.get_positions()
    assert not positions   # LTP > limit price → should not fill yet


@pytest.mark.asyncio
async def test_limit_buy_fills_when_ltp_drops():
    broker = PaperBroker(initial_capital=10_000)
    await broker.update_ltp("HDFC", 1800.0)
    await broker.place_order(_limit_buy("HDFC", 1, price=1750.0))
    await broker.update_ltp("HDFC", 1740.0)   # crosses below limit

    positions = await broker.get_positions()
    assert "HDFC" in positions
    assert positions["HDFC"].qty == 1


# ── Kill-switch ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kill_switch_blocks_new_orders():
    broker = PaperBroker(initial_capital=10_000, kill_switch_amount=100.0, slippage_pct=0.0)
    await broker.update_ltp("SBIN", 500.0)
    await broker.place_order(_market_buy("SBIN", 10))   # cost=5000, capital=5000
    # Simulate price crash past kill-switch threshold
    await broker.update_ltp("SBIN", 480.0)              # unrealized = -200 < -100

    assert broker.is_killed()
    with pytest.raises(KillSwitchError):
        await broker.place_order(_market_buy("SBIN", 1))


# ── Cancel order ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_open_order():
    broker = PaperBroker(initial_capital=10_000)
    order = _limit_buy("WIPRO", 5, price=400.0)
    await broker.update_ltp("WIPRO", 450.0)
    await broker.place_order(order)
    cancelled = await broker.cancel_order(order.order_id)
    assert cancelled is True


# ── Snapshot ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_keys():
    broker = PaperBroker()
    snap = broker.snapshot()
    for key in ("capital", "portfolio_value", "daily_pnl", "kill_switch_active", "open_positions"):
        assert key in snap
