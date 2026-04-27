"""
Unit tests for KellySizer.
Run: pytest tests/test_kelly_sizer.py -v
"""

import pytest
from core.risk.kelly_sizer import KellySizer, StrategyStats


def make_stats(win_rate=0.55, avg_win=120.0, avg_loss=80.0, sample_size=30):
    return StrategyStats(win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
                         sample_size=sample_size)


# ── raw_kelly ──────────────────────────────────────────────────────────────────

def test_positive_edge():
    s = KellySizer()
    # b=1.5, f*=0.55-0.45/1.5 = 0.55-0.3=0.25
    assert s.raw_kelly(0.55, 120, 80) == pytest.approx(0.25, rel=1e-4)


def test_zero_edge_returns_zero():
    s = KellySizer()
    # win_rate=0.4, avg_win=120, avg_loss=80 → b=1.5, f*=0.4-0.6/1.5=0.4-0.4=0
    assert s.raw_kelly(0.4, 120, 80) == pytest.approx(0.0, abs=1e-9)


def test_negative_edge():
    s = KellySizer()
    assert s.raw_kelly(0.3, 80, 120) < 0


def test_invalid_avg_loss_raises():
    s = KellySizer()
    with pytest.raises(ValueError):
        s.raw_kelly(0.5, 100, 0)


# ── scaled_fraction ────────────────────────────────────────────────────────────

def test_half_kelly_halves_raw():
    s = KellySizer(kelly_fraction=0.5)
    stats = make_stats()
    raw = s.raw_kelly(stats.win_rate, stats.avg_win, stats.avg_loss)
    assert s.scaled_fraction(stats) == pytest.approx(raw * 0.5, rel=1e-4)


def test_capped_at_max_trade_fraction():
    s = KellySizer(kelly_fraction=1.0)
    # Extreme edge → raw Kelly could exceed MAX_TRADE_FRACTION
    extreme = StrategyStats(win_rate=0.9, avg_win=500, avg_loss=10, sample_size=100)
    assert s.scaled_fraction(extreme) <= KellySizer.MAX_TRADE_FRACTION


def test_negative_edge_returns_zero():
    s = KellySizer()
    bad = StrategyStats(win_rate=0.3, avg_win=50, avg_loss=200, sample_size=50)
    assert s.scaled_fraction(bad) == 0.0


def test_small_sample_returns_min_bet():
    s = KellySizer()
    stats = StrategyStats(win_rate=0.6, avg_win=100, avg_loss=80, sample_size=5)
    assert s.scaled_fraction(stats) == KellySizer.MIN_BET_FRACTION


# ── size ──────────────────────────────────────────────────────────────────────

def test_size_positive_result():
    s = KellySizer(kelly_fraction=0.5)
    stats = make_stats()
    qty = s.size(stats, ltp=500.0, available_capital=10_000.0)
    assert qty > 0


def test_size_zero_for_negative_edge():
    s = KellySizer()
    bad = StrategyStats(win_rate=0.3, avg_win=50, avg_loss=200, sample_size=50)
    assert s.size(bad, ltp=500, available_capital=10_000) == 0


def test_size_zero_when_no_capital():
    s = KellySizer()
    assert s.size(make_stats(), ltp=500, available_capital=0) == 0


def test_size_respects_lot_size():
    s = KellySizer(kelly_fraction=0.5)
    stats = make_stats()
    qty = s.size(stats, ltp=100.0, available_capital=50_000.0, lot_size=50)
    assert qty % 50 == 0


def test_explain_keys():
    s = KellySizer()
    result = s.explain(make_stats(), ltp=500.0, available_capital=10_000.0)
    for key in ("win_rate", "avg_win", "avg_loss", "reward_to_risk",
                "raw_kelly_f", "half_kelly_f", "capital_at_risk", "ltp", "qty"):
        assert key in result
