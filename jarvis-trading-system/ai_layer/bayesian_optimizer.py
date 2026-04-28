"""
Optuna TPE-based per-strategy parameter optimiser.

Paper mode : 50 trials per strategy.
Live mode  : 20 trials per strategy.

Each strategy defines its own param space as a dict:
    {"param_name": ("float", lo, hi) | ("int", lo, hi) | ("categorical", [opts])}

The objective callable receives a params dict and must return a float
(higher = better; internally negated for Optuna minimisation).
"""
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default search spaces for the 5 built-in strategy plugins
DEFAULT_PARAM_SPACES: dict[str, dict[str, tuple]] = {
    "EMACrossover": {
        "fast_period": ("int", 5, 20),
        "slow_period": ("int", 15, 50),
    },
    "SuperTrend": {
        "atr_period": ("int", 7, 21),
        "multiplier": ("float", 1.5, 5.0),
    },
    "ORBBreakout": {
        "orb_minutes": ("int", 15, 60),
        "atr_multiplier": ("float", 0.5, 2.0),
    },
    "RSIMomentum": {
        "rsi_period": ("int", 7, 21),
        "overbought": ("int", 55, 75),
        "oversold": ("int", 25, 45),
    },
    "VWAPBreakout": {
        "volume_multiplier": ("float", 1.2, 3.0),
        "atr_buffer": ("float", 0.3, 1.5),
    },
}


class BayesianOptimizer:
    def __init__(self, paper_mode: bool = True) -> None:
        self._paper_mode = paper_mode
        self._n_trials = 50 if paper_mode else 20
        self._results: dict[str, dict[str, Any]] = {}

    @property
    def n_trials(self) -> int:
        return self._n_trials

    def optimize(
        self,
        strategy_name: str,
        param_space: dict[str, tuple],
        objective: Callable[[dict[str, Any]], float],
        n_trials: int | None = None,
    ) -> dict[str, Any]:
        """
        Run TPE optimisation and return the best params dict.
        Falls back to the first value in each spec if Optuna is unavailable.
        """
        trials = n_trials if n_trials is not None else self._n_trials

        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.warning("optuna not installed; returning defaults for %s", strategy_name)
            return self._defaults(param_space)

        def _objective(trial: "optuna.Trial") -> float:
            params: dict[str, Any] = {}
            for name, spec in param_space.items():
                kind = spec[0]
                if kind == "float":
                    params[name] = trial.suggest_float(name, spec[1], spec[2])
                elif kind == "int":
                    params[name] = trial.suggest_int(name, spec[1], spec[2])
                elif kind == "categorical":
                    params[name] = trial.suggest_categorical(name, spec[1])
                else:
                    params[name] = spec[1]
            return -objective(params)  # Optuna minimises; we maximise

        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(_objective, n_trials=trials, show_progress_bar=False)

        best = dict(study.best_params)
        self._results[strategy_name] = best
        logger.info(
            "BayesianOptimizer[%s]: best=%s (value=%.4f)",
            strategy_name, best, -study.best_value,
        )
        return best

    def get_best_params(self, strategy_name: str) -> dict[str, Any] | None:
        return self._results.get(strategy_name)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self._results, f, indent=2)

    def load(self, path: str) -> bool:
        try:
            with open(path) as f:
                self._results = json.load(f)
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    @staticmethod
    def _defaults(param_space: dict[str, tuple]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, spec in param_space.items():
            kind = spec[0]
            if kind == "categorical":
                out[name] = spec[1][0]
            else:
                out[name] = spec[1]  # lower bound as default
        return out
