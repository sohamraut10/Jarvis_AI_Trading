"""
PPO-based strategy weight agent (stable-baselines3).

Observation (10-dim): regime_id, strategy_id, capital_ratio, time_fraction,
                       volatility, recent_sharpe, win_rate, drawdown_ratio,
                       normalised_pnl, position_in_episode

Action (N-dim): raw strategy allocations → softmax → weights

Reward: pnl_ratio - 2×drawdown_ratio - 0.5×switches_penalty + 0.3×sharpe_delta

Retrain window: called once daily at 16:00 IST by the server scheduler.
Graceful no-op when stable-baselines3 or gymnasium are not installed.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_N_STRATEGIES = 5
_OBS_DIM = 10
_IST = timezone(timedelta(hours=5, minutes=30))


# ── Reward ────────────────────────────────────────────────────────────────────

def build_reward(
    pnl: float,
    capital: float,
    drawdown: float,
    n_switches: int,
    sharpe_delta: float,
) -> float:
    pnl_ratio = pnl / max(capital, 1.0)
    drawdown_ratio = drawdown / max(capital, 1.0)
    switches_penalty = min(n_switches / 10.0, 1.0)
    return pnl_ratio - 2.0 * drawdown_ratio - 0.5 * switches_penalty + 0.3 * sharpe_delta


def is_retrain_time() -> bool:
    """True during the 16:00–16:05 IST window (daily retrain slot)."""
    now = datetime.now(_IST)
    return now.hour == 16 and now.minute < 5


# ── Gymnasium environment ─────────────────────────────────────────────────────

try:
    import gymnasium as gym
    _GymBase = gym.Env
except ImportError:
    _GymBase = object  # type: ignore[assignment,misc]


class TradingEnv(_GymBase):
    """
    Offline gymnasium env: replays experience buffer records as episodes.
    Each step = one historical trade; action = proposed strategy weights.
    """

    metadata: dict[str, Any] = {}

    def __init__(self, experiences: list[dict[str, Any]], n_strategies: int = _N_STRATEGIES) -> None:
        super().__init__()
        self._experiences = experiences
        self._n = n_strategies
        self._idx = 0
        self._prev_sharpe = 0.0
        self._switches = 0
        self._prev_weights = np.ones(n_strategies, dtype=np.float32) / n_strategies

        try:
            import gymnasium as gym
            self.observation_space = gym.spaces.Box(
                low=-np.inf, high=np.inf, shape=(_OBS_DIM,), dtype=np.float32
            )
            self.action_space = gym.spaces.Box(
                low=0.0, high=1.0, shape=(n_strategies,), dtype=np.float32
            )
        except ImportError:
            pass

    def _parse_fv(self, exp: dict[str, Any]) -> dict[str, Any]:
        fv = exp.get("feature_vector", "{}")
        return json.loads(fv) if isinstance(fv, str) else (fv or {})

    def _obs(self, exp: dict[str, Any]) -> np.ndarray:
        fv = self._parse_fv(exp)
        return np.array([
            fv.get("regime_id", 0),
            fv.get("strategy_id", 0),
            fv.get("capital_ratio", 0.5),
            fv.get("time_fraction", 0.5),
            fv.get("volatility", 0.01),
            fv.get("recent_sharpe", 0.0),
            fv.get("win_rate", 0.5),
            fv.get("drawdown_ratio", 0.0),
            float(exp.get("pnl", 0.0)) / 10_000.0,
            float(self._idx) / max(len(self._experiences), 1),
        ], dtype=np.float32)

    def reset(self, *, seed: int | None = None, options: dict | None = None):  # type: ignore[override]
        super_reset = getattr(super(), "reset", None)
        if super_reset:
            super_reset(seed=seed)
        self._idx = 0
        self._prev_sharpe = 0.0
        self._switches = 0
        self._prev_weights = np.ones(self._n, dtype=np.float32) / self._n
        if not self._experiences:
            return np.zeros(_OBS_DIM, dtype=np.float32), {}
        return self._obs(self._experiences[0]), {}

    def step(self, action: np.ndarray):  # type: ignore[override]
        exp = self._experiences[self._idx]
        pnl = float(exp.get("pnl", 0.0))
        drawdown = float(exp.get("drawdown", 0.0))

        weights = np.asarray(action, dtype=np.float32)
        weights = np.clip(weights, 0.0, 1.0)
        weights /= weights.sum() + 1e-8

        if int(np.argmax(weights)) != int(np.argmax(self._prev_weights)):
            self._switches += 1

        fv = self._parse_fv(exp)
        curr_sharpe = float(fv.get("recent_sharpe", 0.0))
        reward = build_reward(pnl, 10_000.0, drawdown, self._switches, curr_sharpe - self._prev_sharpe)
        self._prev_sharpe = curr_sharpe
        self._prev_weights = weights

        self._idx += 1
        terminated = self._idx >= len(self._experiences)
        next_obs = np.zeros(_OBS_DIM, dtype=np.float32) if terminated else self._obs(self._experiences[self._idx])
        return next_obs, reward, terminated, False, {}


# ── RL Agent ──────────────────────────────────────────────────────────────────

class RLAgent:
    def __init__(
        self,
        model_dir: str,
        n_strategies: int = _N_STRATEGIES,
        paper_mode: bool = True,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._n = n_strategies
        self._paper_mode = paper_mode
        self._model: Any = None
        self._weights = np.ones(n_strategies, dtype=np.float32) / n_strategies

    # ── training ──────────────────────────────────────────────────────────────

    def train(self, experiences: list[dict[str, Any]], timesteps: int | None = None) -> None:
        if len(experiences) < 10:
            logger.info("RLAgent: only %d records — skipping retrain", len(experiences))
            return
        try:
            from stable_baselines3 import PPO
        except ImportError:
            logger.warning("stable-baselines3 not installed; skipping RL retrain")
            return

        env = TradingEnv(experiences, self._n)
        n_steps = min(64, len(experiences))
        total = timesteps or max(len(experiences) * 2, n_steps * 2)

        model_path = self._model_dir / "ppo_jarvis"
        if self._model is None and (model_path.with_suffix(".zip")).exists():
            self._model = PPO.load(str(model_path), env=env)
            self._model.set_env(env)
        elif self._model is None:
            self._model = PPO(
                "MlpPolicy", env, verbose=0,
                n_steps=n_steps, batch_size=min(n_steps, 32),
            )
        else:
            self._model.set_env(env)

        self._model.learn(total_timesteps=total, reset_num_timesteps=False)
        self._model.save(str(model_path))

        # Extract implied weights from a single prediction pass
        obs, _ = env.reset()
        raw, _ = self._model.predict(obs, deterministic=True)
        w = np.clip(np.asarray(raw, dtype=np.float32), 0.0, 1.0)
        self._weights = w / (w.sum() + 1e-8)
        logger.info("RLAgent: retrained — weights=%s", self._weights.round(3).tolist())

    # ── access ────────────────────────────────────────────────────────────────

    def get_weight_array(self) -> np.ndarray:
        return self._weights.copy()

    def get_named_weights(self, strategy_names: list[str]) -> dict[str, float]:
        """Map weight array onto strategy names; uniform fallback on mismatch."""
        if len(self._weights) != len(strategy_names):
            n = len(strategy_names)
            return {name: 1.0 / n for name in strategy_names}
        return {name: float(w) for name, w in zip(strategy_names, self._weights)}

    # ── persistence ───────────────────────────────────────────────────────────

    def save_weights(self, path: Path) -> None:
        np.save(str(path), self._weights)

    def load_weights(self, path: Path) -> bool:
        try:
            self._weights = np.load(str(path))
            return True
        except Exception:
            return False

    def load_model(self) -> bool:
        try:
            from stable_baselines3 import PPO
            p = self._model_dir / "ppo_jarvis.zip"
            if p.exists():
                self._model = PPO.load(str(p.with_suffix("")))
                return True
        except Exception as exc:
            logger.warning("RLAgent.load_model failed: %s", exc)
        return False
