"""
Versioned brain snapshots.

Directory layout:
    brain_versions/
        v20240101_160000/
            weights.npy
            params.json
            metrics.json
        v20240102_160000/
            ...
        current -> v20240102_160000   (symlink, updated on every save)

Auto-rollback: if live Sharpe drops >5% relative to the immediately prior
version's stored Sharpe, `rollback()` points `current` back to that version.

The kill-switch in core/broker/paper_broker.py is hardcoded and unaffected by
brain version changes.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_ROLLBACK_THRESHOLD = 0.05  # 5% relative Sharpe drop


class BrainVersionManager:
    def __init__(self, base_dir: str = "brain_versions") -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    # ── version list ──────────────────────────────────────────────────────────

    def list_versions(self) -> list[str]:
        return [p.name for p in self._sorted_versions()]

    def _sorted_versions(self) -> list[Path]:
        """Real directories only (not the `current` symlink)."""
        return sorted(
            [p for p in self._base.iterdir() if not p.is_symlink() and p.is_dir()],
            key=lambda p: p.name,
        )

    # ── save ──────────────────────────────────────────────────────────────────

    def save_version(
        self,
        weights: np.ndarray,
        params: dict,
        metrics: dict,
    ) -> Path:
        ts = datetime.utcnow().strftime("v%Y%m%d_%H%M%S_%f")
        vdir = self._base / ts
        vdir.mkdir(parents=True)

        np.save(str(vdir / "weights.npy"), weights)
        with open(vdir / "params.json", "w") as f:
            json.dump(params, f, indent=2)
        with open(vdir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)

        self._point_current(vdir)
        logger.info(
            "BrainVersionManager: saved %s  sharpe=%.3f",
            ts, float(metrics.get("sharpe", 0)),
        )
        return vdir

    # ── load ──────────────────────────────────────────────────────────────────

    def load_current(self) -> tuple[np.ndarray | None, dict, dict]:
        path = self.current_path()
        if path is None:
            return None, {}, {}
        return self._load(path)

    def current_path(self) -> Path | None:
        link = self._base / "current"
        if link.exists() or link.is_symlink():
            resolved = link.resolve()
            if resolved.is_dir():
                return resolved
        return None

    # ── rollback ──────────────────────────────────────────────────────────────

    def should_rollback(self, live_sharpe: float) -> bool:
        """True when live Sharpe dropped >5% relative to the prior version."""
        versions = self._sorted_versions()
        if len(versions) < 2:
            return False
        _, _, prev_metrics = self._load(versions[-2])
        prev_sharpe = float(prev_metrics.get("sharpe", 0.0))
        if prev_sharpe <= 0:
            return False
        drop = (prev_sharpe - live_sharpe) / prev_sharpe
        return drop > _ROLLBACK_THRESHOLD

    def rollback(self) -> tuple[np.ndarray | None, dict, dict] | None:
        """Restore the previous version and return its (weights, params, metrics)."""
        versions = self._sorted_versions()
        if len(versions) < 2:
            logger.warning("BrainVersionManager: no prior version to roll back to")
            return None
        prev = versions[-2]
        self._point_current(prev)
        logger.warning("BrainVersionManager: rolled back to %s", prev.name)
        return self._load(prev)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _point_current(self, target: Path) -> None:
        link = self._base / "current"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target.resolve())

    @staticmethod
    def _load(path: Path) -> tuple[np.ndarray | None, dict, dict]:
        weights_file = path / "weights.npy"
        weights = np.load(str(weights_file)) if weights_file.exists() else None
        try:
            with open(path / "params.json") as f:
                params = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            params = {}
        try:
            with open(path / "metrics.json") as f:
                metrics = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            metrics = {}
        return weights, params, metrics
