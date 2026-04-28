"""
Unit tests for intelligence/ layer.
Run: pytest tests/test_intelligence.py -v
"""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pytest

from core.types import Regime
from intelligence.alpha_decay_monitor import AlphaDecayMonitor, DecaySeverity
from intelligence.intent_logger import EventType, IntentLogger
from intelligence.pnl_tracker import PnLTracker
from intelligence.regime_classifier import RegimeClassifier
from intelligence.strategy_shift_engine import AllocationResult, StrategyShiftEngine


# ─── RegimeClassifier ─────────────────────────────────────────────────────────

def test_feature_extraction_shape():
    closes = np.linspace(100, 110, 30)
    features = RegimeClassifier.extract_features(closes)
    assert features is not None
    assert features.shape == (7,)


def test_feature_extraction_insufficient_data():
    closes = np.array([100.0, 101.0])   # only 2 bars, need 21
    assert RegimeClassifier.extract_features(closes) is None


def test_rule_based_high_vol():
    """High realized vol → HIGH_VOL."""
    clf = RegimeClassifier()
    closes = np.array([100.0] * 5 + [100.0 * (1 + 0.05 * (i % 2 == 0) - 0.05 * (i % 2))
                                     for i in range(30)])
    features = np.array([0.001, 0.0, 0.001, 0.025, 1.0, 1.0, 0.0])  # vol=0.025 > 0.015
    regime = clf.predict(features)
    assert regime == Regime.HIGH_VOL


def test_rule_based_trending_up():
    clf = RegimeClassifier()
    features = np.array([0.005, 0.02, 0.02, 0.008, 1.0, 0.9, 0.0])  # positive return, low vol
    assert clf.predict(features) == Regime.TRENDING_UP


def test_rule_based_trending_down():
    clf = RegimeClassifier()
    features = np.array([-0.005, -0.02, 0.02, 0.008, 1.0, 1.2, 0.0])  # negative return, low vol
    assert clf.predict(features) == Regime.TRENDING_DOWN


def test_rule_based_sideways():
    clf = RegimeClassifier()
    features = np.array([0.0001, 0.001, 0.001, 0.005, 0.9, 1.0, 0.0])  # flat, low vol
    assert clf.predict(features) == Regime.SIDEWAYS


def test_feature_dict_populated_after_predict():
    clf = RegimeClassifier()
    features = np.array([0.001, 0.01, 0.01, 0.008, 1.0, 1.0, 0.0])
    clf.predict(features)
    d = clf.feature_dict()
    assert "realized_vol" in d
    assert "vix_normalized" in d


def test_state_labeling_assigns_all_regimes():
    """_label_states must assign exactly one state per regime."""
    # 4 × 7 fake means
    means = np.array([
        [0.004,  0.02,  0.02,  0.005, 1.0, 1.0, 0.0],   # trending up
        [-0.004, -0.02, 0.02,  0.005, 1.0, 1.2, 0.0],   # trending down
        [0.0001, 0.001, 0.001, 0.004, 0.9, 1.0, 0.0],   # sideways
        [0.001,  0.005, 0.01,  0.025, 2.0, 1.0, 0.0],   # high vol (high vol+vix)
    ])
    mapping = RegimeClassifier._label_states(means)
    assert set(mapping.values()) == {
        Regime.TRENDING_UP, Regime.TRENDING_DOWN, Regime.SIDEWAYS, Regime.HIGH_VOL
    }
    assert len(mapping) == 4


# ─── AlphaDecayMonitor ────────────────────────────────────────────────────────

def test_no_data_before_short_window():
    m = AlphaDecayMonitor()
    status = m.check_decay("strat_x")
    assert status.severity == DecaySeverity.NO_DATA


def test_healthy_stable_profits():
    m = AlphaDecayMonitor()
    for _ in range(50):
        m.update("s", 100.0)
    status = m.check_decay("s")
    assert status.severity == DecaySeverity.HEALTHY


def test_degraded_consecutive_losses():
    m = AlphaDecayMonitor()
    # Build up history first
    for _ in range(20):
        m.update("s", 50.0)
    # Then 5 consecutive losses
    for _ in range(AlphaDecayMonitor.MAX_CONSEC_LOSSES):
        m.update("s", -100.0)
    status = m.check_decay("s")
    assert status.severity == DecaySeverity.DEGRADED
    assert status.consecutive_losses >= AlphaDecayMonitor.MAX_CONSEC_LOSSES


def test_warning_on_declining_sharpe():
    m = AlphaDecayMonitor()
    # Long window: consistent profit → positive long Sharpe
    for _ in range(40):
        m.update("s", 50.0)
    # Short window: near-zero, much lower Sharpe
    for _ in range(AlphaDecayMonitor.SHORT_WINDOW):
        m.update("s", 1.0)
    status = m.check_decay("s")
    # Short Sharpe should be much lower than long → WARNING or DEGRADED
    assert status.severity in (DecaySeverity.WARNING, DecaySeverity.DEGRADED)


def test_reset_clears_history():
    m = AlphaDecayMonitor()
    for _ in range(20):
        m.update("s", -50.0)
    m.reset("s")
    status = m.check_decay("s")
    assert status.severity == DecaySeverity.NO_DATA


def test_is_decaying_property():
    m = AlphaDecayMonitor()
    for _ in range(AlphaDecayMonitor.MAX_CONSEC_LOSSES + 5):
        m.update("s", -1.0)
    assert m.check_decay("s").is_decaying


# ─── StrategyShiftEngine ──────────────────────────────────────────────────────

class _MockStrategy:
    """Minimal stub for engine tests."""
    def __init__(self, sid, regimes, sharpe=0.0):
        self.strategy_id = sid
        self._regimes = regimes
        self._sharpe = sharpe

    def is_active(self, regime):
        return regime in self._regimes

    def get_sharpe(self):
        return self._sharpe


def test_engine_filters_by_regime():
    engine = StrategyShiftEngine()
    strategies = [
        _MockStrategy("trend", [Regime.TRENDING_UP], sharpe=1.0),
        _MockStrategy("mean_rev", [Regime.SIDEWAYS], sharpe=2.0),
    ]
    result = engine.compute_allocations(strategies, Regime.TRENDING_UP, 10_000)
    assert "trend" in result.allocations
    assert "mean_rev" not in result.allocations


def test_engine_returns_empty_when_no_match():
    engine = StrategyShiftEngine()
    strategies = [_MockStrategy("s", [Regime.SIDEWAYS], sharpe=1.0)]
    result = engine.compute_allocations(strategies, Regime.HIGH_VOL, 10_000)
    assert result.allocations == {}
    assert result.active_count == 0


def test_engine_higher_sharpe_gets_more_capital():
    engine = StrategyShiftEngine()
    strategies = [
        _MockStrategy("low_sharpe", [Regime.TRENDING_UP], sharpe=0.5),
        _MockStrategy("high_sharpe", [Regime.TRENDING_UP], sharpe=2.0),
    ]
    result = engine.compute_allocations(strategies, Regime.TRENDING_UP, 10_000)
    assert result.allocations["high_sharpe"] > result.allocations["low_sharpe"]


def test_engine_allocations_cap_respected():
    engine = StrategyShiftEngine(max_strategy_pct=0.25)
    strategies = [_MockStrategy(f"s{i}", [Regime.TRENDING_UP], sharpe=float(i + 1))
                  for i in range(5)]
    result = engine.compute_allocations(strategies, Regime.TRENDING_UP, 10_000)
    cap = 10_000 * 0.25
    for alloc in result.allocations.values():
        assert alloc <= cap + 0.01   # tiny float tolerance


def test_engine_bootstrap_allocation_when_zero_sharpe():
    engine = StrategyShiftEngine(bootstrap_pct=0.02)
    strategies = [_MockStrategy("s1", [Regime.TRENDING_UP], sharpe=0.0),
                  _MockStrategy("s2", [Regime.TRENDING_UP], sharpe=0.0)]
    result = engine.compute_allocations(strategies, Regime.TRENDING_UP, 10_000)
    # Bootstrap: 2% of 10 000 = 200 per strategy
    for alloc in result.allocations.values():
        assert alloc == pytest.approx(200.0)


def test_engine_should_rotate_on_major_regime_change():
    engine = StrategyShiftEngine()
    strategies = [
        _MockStrategy("trend", [Regime.TRENDING_UP], sharpe=1.0),
        _MockStrategy("mean_rev", [Regime.SIDEWAYS], sharpe=1.0),
    ]
    assert engine.should_rotate(
        Regime.TRENDING_UP, Regime.SIDEWAYS, {"trend": 2000}, strategies
    )


def test_engine_no_rotate_on_same_regime():
    engine = StrategyShiftEngine()
    assert not engine.should_rotate(
        Regime.TRENDING_UP, Regime.TRENDING_UP, {}, []
    )


# ─── IntentLogger ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logger_writes_valid_jsonl():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    logger = IntentLogger(path)
    await logger.log_session_start(capital=10_000.0, regime="TRENDING_UP")
    await logger.log_session_end({"total_pnl": 150.0, "num_trades": 5})

    lines = Path(path).read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)   # must be valid JSON
        assert "timestamp" in obj
        assert "event_type" in obj


@pytest.mark.asyncio
async def test_logger_regime_change_entry():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    logger = IntentLogger(path)
    await logger.log_regime_change("SIDEWAYS", "TRENDING_UP", {"realized_vol": 0.008})
    entries = await logger.tail(5)
    assert entries[0]["event_type"] == EventType.REGIME_CHANGE
    assert entries[0]["new_regime"] == "TRENDING_UP"


@pytest.mark.asyncio
async def test_logger_kill_switch_entry():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    logger = IntentLogger(path)
    await logger.log_kill_switch(daily_pnl=-350.0, threshold=-300.0)
    entries = await logger.tail(1)
    assert entries[0]["event_type"] == EventType.KILL_SWITCH
    assert entries[0]["severity"] == "HARD_STOP"


@pytest.mark.asyncio
async def test_logger_tail_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    logger = IntentLogger(path + "_nonexistent")
    entries = await logger.tail()
    assert entries == []


# ─── PnLTracker ───────────────────────────────────────────────────────────────

async def _make_tracker() -> tuple[PnLTracker, str]:
    """Create a PnLTracker backed by a temp file (avoids :memory: per-connection issue)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    t = PnLTracker(db_path)
    await t.init()
    return t, db_path


@pytest.mark.asyncio
async def test_pnl_tracker_record_and_query():
    t, _ = await _make_tracker()
    await t.record_trade("ema", "RELIANCE", "BUY", 5, 2500.0, 2600.0, 500.0)
    await t.record_trade("ema", "RELIANCE", "BUY", 5, 2600.0, 2550.0, -250.0)
    summary = await t.get_session_summary()
    assert summary["num_trades"] == 2
    assert summary["total_pnl"] == pytest.approx(250.0)
    assert summary["num_wins"] == 1
    assert summary["win_rate"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_pnl_tracker_strategy_breakdown():
    t, _ = await _make_tracker()
    today = datetime.utcnow()
    await t.record_trade("orb", "TCS", "BUY", 2, 3000.0, 3100.0, 200.0,
                         entry_time=today, exit_time=today)
    await t.record_trade("ema", "INFY", "BUY", 3, 1500.0, 1450.0, -150.0,
                         entry_time=today, exit_time=today)
    breakdown = await t.get_all_strategies_today()
    ids = {r["strategy_id"] for r in breakdown}
    assert "orb" in ids
    assert "ema" in ids


@pytest.mark.asyncio
async def test_pnl_tracker_equity_curve_empty():
    t, _ = await _make_tracker()
    curve = await t.get_equity_curve()
    assert curve == []


@pytest.mark.asyncio
async def test_pnl_tracker_daily_pnl():
    t, _ = await _make_tracker()
    today = datetime.utcnow()
    await t.record_trade("s", "X", "BUY", 1, 100.0, 110.0, 10.0,
                         entry_time=today, exit_time=today)
    await t.record_trade("s", "X", "BUY", 1, 100.0, 108.0, 8.0,
                         entry_time=today, exit_time=today)
    pnl = await t.get_daily_pnl()
    assert pnl == pytest.approx(18.0)
