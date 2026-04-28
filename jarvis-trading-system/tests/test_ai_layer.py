"""
Tests for the ai_layer modules.

experience_buffer  — 4 tests
rl_agent           — 5 tests  (env + reward + agent stubs)
bayesian_optimizer — 4 tests
regime_relabeler   — 5 tests
brain_versioning   — 5 tests
shadow_tester      — 6 tests
"""
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _make_exp(regime="TRENDING_UP", pnl=100.0, outcome="closed", strategy_id=0):
    return {
        "strategy": "EMACrossover",
        "symbol": "RELIANCE",
        "regime": regime,
        "side": "BUY",
        "entry_price": 1000.0,
        "exit_price": 1010.0,
        "qty": 1,
        "pnl": pnl,
        "drawdown": 20.0,
        "duration_secs": 300.0,
        "feature_vector": json.dumps({
            "regime_id": 0,
            "strategy_id": strategy_id,
            "capital_ratio": 0.5,
            "time_fraction": 0.4,
            "volatility": 0.01,
            "recent_sharpe": 1.2,
            "win_rate": 0.6,
            "drawdown_ratio": 0.02,
        }),
        "outcome": outcome,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ExperienceBuffer
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_experience_buffer_log_and_count():
    from ai_layer.experience_buffer import ExperienceBuffer
    buf = ExperienceBuffer(_tmp_db())
    await buf.initialise()

    assert await buf.count() == 0
    row_id = await buf.log_trade(
        strategy="EMACrossover", symbol="RELIANCE", regime="TRENDING_UP",
        side="BUY", entry_price=1000.0, qty=2,
        feature_vector={"regime_id": 0},
    )
    assert row_id >= 1
    assert await buf.count() == 1


@pytest.mark.asyncio
async def test_experience_buffer_update_outcome():
    from ai_layer.experience_buffer import ExperienceBuffer
    buf = ExperienceBuffer(_tmp_db())
    await buf.initialise()

    row_id = await buf.log_trade(
        strategy="SuperTrend", symbol="TCS", regime="SIDEWAYS",
        side="SELL", entry_price=3500.0, qty=1,
    )
    await buf.update_outcome(row_id, exit_price=3450.0, pnl=50.0, drawdown=10.0, duration_secs=120.0)
    records = await buf.get_recent(10)
    assert len(records) == 1
    assert records[0]["outcome"] == "closed"
    assert records[0]["exit_price"] == 3450.0
    assert records[0]["pnl"] == 50.0


@pytest.mark.asyncio
async def test_experience_buffer_feature_matrix_empty():
    from ai_layer.experience_buffer import ExperienceBuffer
    buf = ExperienceBuffer(_tmp_db())
    await buf.initialise()

    X, y = await buf.get_feature_matrix()
    assert X.shape == (0, 8)
    assert y.shape == (0,)


@pytest.mark.asyncio
async def test_experience_buffer_feature_matrix_returns_arrays():
    from ai_layer.experience_buffer import ExperienceBuffer
    buf = ExperienceBuffer(_tmp_db())
    await buf.initialise()

    fv = {"regime_id": 1, "strategy_id": 0, "capital_ratio": 0.3, "time_fraction": 0.5,
          "volatility": 0.02, "recent_sharpe": 0.8, "win_rate": 0.55, "drawdown_ratio": 0.01}
    row_id = await buf.log_trade(
        strategy="RSIMomentum", symbol="INFY", regime="TRENDING_UP",
        side="BUY", entry_price=1500.0, qty=3, feature_vector=fv,
    )
    await buf.update_outcome(row_id, exit_price=1530.0, pnl=90.0, drawdown=5.0, duration_secs=60.0)

    X, y = await buf.get_feature_matrix()
    assert X.shape == (1, 8)
    assert y[0] == pytest.approx(90.0)
    assert X.dtype == np.float32


# ═══════════════════════════════════════════════════════════════════════════════
# RL Agent
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_reward_positive_pnl():
    from ai_layer.rl_agent import build_reward
    r = build_reward(pnl=100.0, capital=10000.0, drawdown=50.0, n_switches=2, sharpe_delta=0.5)
    # pnl_ratio=0.01, drawdown_ratio=0.005, switches_penalty=0.2, sharpe_delta=0.5
    expected = 0.01 - 2*0.005 - 0.5*0.2 + 0.3*0.5
    assert r == pytest.approx(expected, abs=1e-6)


def test_build_reward_loss_penalised():
    from ai_layer.rl_agent import build_reward
    r_loss = build_reward(pnl=-200.0, capital=10000.0, drawdown=200.0, n_switches=0, sharpe_delta=0.0)
    r_win  = build_reward(pnl=200.0,  capital=10000.0, drawdown=0.0,   n_switches=0, sharpe_delta=0.0)
    assert r_loss < r_win


def test_trading_env_reset_empty():
    from ai_layer.rl_agent import TradingEnv
    env = TradingEnv([], n_strategies=3)
    obs, info = env.reset()
    assert obs.shape == (10,)
    assert np.all(obs == 0)


def test_trading_env_step_terminates():
    from ai_layer.rl_agent import TradingEnv
    exps = [_make_exp() for _ in range(5)]
    env = TradingEnv(exps, n_strategies=3)
    obs, _ = env.reset()
    done = False
    steps = 0
    while not done:
        action = np.ones(3, dtype=np.float32) / 3
        obs, reward, done, truncated, _ = env.step(action)
        steps += 1
        assert steps <= 10, "env failed to terminate"
    assert steps == 5


def test_rl_agent_insufficient_data_skips():
    from ai_layer.rl_agent import RLAgent
    with tempfile.TemporaryDirectory() as d:
        agent = RLAgent(model_dir=d, n_strategies=3)
        # Only 3 experiences — below the 10-record threshold
        agent.train([_make_exp() for _ in range(3)])
        # Weights remain uniform (unchanged)
        w = agent.get_weight_array()
        np.testing.assert_allclose(w, np.ones(3) / 3, atol=1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# Bayesian Optimizer
# ═══════════════════════════════════════════════════════════════════════════════

def test_bayesian_optimizer_n_trials_paper():
    from ai_layer.bayesian_optimizer import BayesianOptimizer
    opt = BayesianOptimizer(paper_mode=True)
    assert opt.n_trials == 50


def test_bayesian_optimizer_n_trials_live():
    from ai_layer.bayesian_optimizer import BayesianOptimizer
    opt = BayesianOptimizer(paper_mode=False)
    assert opt.n_trials == 20


def test_bayesian_optimizer_optimizes_quadratic():
    pytest.importorskip("optuna")
    from ai_layer.bayesian_optimizer import BayesianOptimizer
    opt = BayesianOptimizer(paper_mode=True)

    def objective(params):
        return -(params["x"] - 4.0) ** 2  # maximum at x=4

    best = opt.optimize(
        "test_strategy",
        {"x": ("float", 0.0, 8.0)},
        objective,
        n_trials=20,
    )
    assert abs(best["x"] - 4.0) < 1.5
    assert opt.get_best_params("test_strategy") == best


def test_bayesian_optimizer_save_load():
    from ai_layer.bayesian_optimizer import BayesianOptimizer
    opt = BayesianOptimizer()
    opt._results = {"EMACrossover": {"fast_period": 9, "slow_period": 21}}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    opt.save(path)

    opt2 = BayesianOptimizer()
    ok = opt2.load(path)
    assert ok
    assert opt2.get_best_params("EMACrossover") == {"fast_period": 9, "slow_period": 21}


# ═══════════════════════════════════════════════════════════════════════════════
# Regime Relabeler
# ═══════════════════════════════════════════════════════════════════════════════

def test_relabeler_empty_input():
    from ai_layer.regime_relabeler import RegimeRelabeler
    rl = RegimeRelabeler()
    assert rl.analyse([]) == {}


def test_relabeler_insufficient_sample():
    from ai_layer.regime_relabeler import RegimeRelabeler
    rl = RegimeRelabeler()
    exps = [_make_exp(regime="SIDEWAYS", pnl=100.0) for _ in range(3)]
    result = rl.analyse(exps)
    assert result == {}  # < 5 samples — no suggestion


def test_relabeler_suggests_trending_up():
    from ai_layer.regime_relabeler import RegimeRelabeler
    rl = RegimeRelabeler()
    # All trades profit, high win-rate — looks like TRENDING_UP
    exps = [_make_exp(regime="SIDEWAYS", pnl=200.0) for _ in range(10)]
    result = rl.analyse(exps)
    assert result.get("SIDEWAYS") == "TRENDING_UP"


def test_relabeler_suggests_high_vol():
    from ai_layer.regime_relabeler import RegimeRelabeler
    rl = RegimeRelabeler()
    # Wild P&L swings → HIGH_VOL
    pnls = [1000.0, -800.0, 900.0, -750.0, 1100.0, -900.0, 800.0, -600.0]
    exps = [_make_exp(regime="SIDEWAYS", pnl=p) for p in pnls]
    result = rl.analyse(exps)
    assert result.get("SIDEWAYS") == "HIGH_VOL"


def test_relabeler_relabel_applies_corrections():
    from ai_layer.regime_relabeler import RegimeRelabeler
    rl = RegimeRelabeler()
    rl._corrections = {"SIDEWAYS": "TRENDING_UP"}
    exps = [_make_exp(regime="SIDEWAYS"), _make_exp(regime="TRENDING_DOWN")]
    relabelled = rl.relabel(exps)
    assert relabelled[0]["regime"] == "TRENDING_UP"
    assert relabelled[0]["regime_corrected"] is True
    assert relabelled[1]["regime"] == "TRENDING_DOWN"  # unchanged


# ═══════════════════════════════════════════════════════════════════════════════
# Brain Versioning
# ═══════════════════════════════════════════════════════════════════════════════

def test_brain_versioning_save_and_load():
    from ai_layer.brain_versioning import BrainVersionManager
    with tempfile.TemporaryDirectory() as d:
        bvm = BrainVersionManager(base_dir=d)
        weights = np.array([0.2, 0.3, 0.5], dtype=np.float32)
        params = {"ema_fast": 9}
        metrics = {"sharpe": 1.4}

        vdir = bvm.save_version(weights, params, metrics)
        assert vdir.exists()

        loaded_w, loaded_p, loaded_m = bvm.load_current()
        np.testing.assert_allclose(loaded_w, weights)
        assert loaded_p == params
        assert loaded_m == metrics


def test_brain_versioning_list_versions():
    from ai_layer.brain_versioning import BrainVersionManager
    with tempfile.TemporaryDirectory() as d:
        bvm = BrainVersionManager(base_dir=d)
        bvm.save_version(np.ones(3), {}, {"sharpe": 1.0})
        time.sleep(0.01)  # ensure distinct timestamps
        bvm.save_version(np.ones(3) * 2, {}, {"sharpe": 1.1})
        versions = bvm.list_versions()
        assert len(versions) == 2
        assert versions[0] < versions[1]  # chronological order


def test_brain_versioning_no_prior_should_not_rollback():
    from ai_layer.brain_versioning import BrainVersionManager
    with tempfile.TemporaryDirectory() as d:
        bvm = BrainVersionManager(base_dir=d)
        bvm.save_version(np.ones(3), {}, {"sharpe": 1.5})
        assert bvm.should_rollback(0.5) is False  # only 1 version


def test_brain_versioning_should_rollback_on_drop():
    from ai_layer.brain_versioning import BrainVersionManager
    with tempfile.TemporaryDirectory() as d:
        bvm = BrainVersionManager(base_dir=d)
        bvm.save_version(np.ones(3), {}, {"sharpe": 1.0})   # prior
        time.sleep(0.01)
        bvm.save_version(np.ones(3), {}, {"sharpe": 1.2})   # current
        # Live Sharpe dropped 10% vs prior (1.0 → 0.9)
        assert bvm.should_rollback(0.9) is True
        # Live Sharpe dropped only 3% vs prior → below threshold
        assert bvm.should_rollback(0.97) is False


def test_brain_versioning_rollback_restores_previous():
    from ai_layer.brain_versioning import BrainVersionManager
    with tempfile.TemporaryDirectory() as d:
        bvm = BrainVersionManager(base_dir=d)
        w1 = np.array([0.1, 0.2, 0.7], dtype=np.float32)
        bvm.save_version(w1, {"v": 1}, {"sharpe": 1.0})
        time.sleep(0.01)
        bvm.save_version(np.array([0.4, 0.4, 0.2], dtype=np.float32), {"v": 2}, {"sharpe": 1.2})

        result = bvm.rollback()
        assert result is not None
        loaded_w, loaded_p, _ = result
        np.testing.assert_allclose(loaded_w, w1, atol=1e-6)
        assert loaded_p == {"v": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# Shadow Tester
# ═══════════════════════════════════════════════════════════════════════════════

def _st(paper=True):
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    f.close()
    return f.name, paper


def test_shadow_paper_mode_always_deploys():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=True)
    st = ShadowTester(state_file=fname, paper_mode=True)
    assert st.should_deploy("v001") is True
    assert st.should_deploy("v002") is True


def test_shadow_live_blocks_before_shadow_started():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=False)
    st = ShadowTester(state_file=fname, paper_mode=False)
    assert st.should_deploy("v001") is False


def test_shadow_live_blocks_within_3_days():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=False)
    st = ShadowTester(state_file=fname, paper_mode=False)
    st.start_shadow("v001", initial_sharpe=1.0)
    assert st.should_deploy("v001") is False   # just started


def test_shadow_live_deploys_after_3_days_good_sharpe():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=False)
    st = ShadowTester(state_file=fname, paper_mode=False)
    # Manually craft state: 4 days old, shadow Sharpe ≥ 95% of live
    st._state = {
        "shadow": {
            "version": "v001",
            "started_at": (datetime.utcnow() - timedelta(days=4)).isoformat(),
            "sharpe": 1.5,
        },
        "live_sharpe": 1.4,
    }
    assert st.should_deploy("v001") is True   # 1.5/1.4 ≈ 1.07 > 0.95


def test_shadow_live_blocks_after_3_days_low_sharpe():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=False)
    st = ShadowTester(state_file=fname, paper_mode=False)
    st._state = {
        "shadow": {
            "version": "v001",
            "started_at": (datetime.utcnow() - timedelta(days=4)).isoformat(),
            "sharpe": 0.5,
        },
        "live_sharpe": 2.0,
    }
    # 0.5/2.0 = 0.25 < 0.95 → blocked
    assert st.should_deploy("v001") is False


def test_shadow_get_status_reflects_state():
    from ai_layer.shadow_tester import ShadowTester
    fname, _ = _st(paper=False)
    st = ShadowTester(state_file=fname, paper_mode=False)
    st.start_shadow("v001", initial_sharpe=0.9)
    st.update_live_sharpe(1.0)
    status = st.get_status()
    assert status["active"] is True
    assert status["version"] == "v001"
    assert status["live_sharpe"] == pytest.approx(1.0)
    assert status["remaining_days"] == 3
