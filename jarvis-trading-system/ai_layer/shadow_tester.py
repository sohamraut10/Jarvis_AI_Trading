"""
Shadow testing gate for new brain versions.

Paper mode : new version deploys immediately.
Live mode  : new version must shadow the live system for 3 full days.
             Shadow Sharpe must reach ≥ 95 % of the current live Sharpe.
             If it doesn't qualify after 3 days the version is blocked.

State is persisted to a JSON file so it survives process restarts.

The kill-switch (core/broker/paper_broker.py) is hardcoded and cannot be
disabled or bypassed by any shadow-tester decision.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SHADOW_DAYS = 3
_MIN_SHARPE_RATIO = 0.95  # shadow Sharpe ÷ live Sharpe must reach this floor


class ShadowTester:
    def __init__(self, state_file: str = "shadow_state.json", paper_mode: bool = True) -> None:
        self._state_file = Path(state_file)
        self._paper_mode = paper_mode
        self._state: dict[str, Any] = {}
        self._load()

    # ── deployment gate ───────────────────────────────────────────────────────

    def should_deploy(self, version: str) -> bool:
        """
        Return True if `version` is cleared for deployment.
        Paper mode always returns True.
        """
        if self._paper_mode:
            logger.info("ShadowTester[paper]: deploying %s immediately", version)
            return True

        shadow = self._state.get("shadow", {})
        if shadow.get("version") != version:
            logger.info("ShadowTester: shadow not started for %s — blocking", version)
            return False

        started_str = shadow.get("started_at")
        if not started_str:
            return False
        started = datetime.fromisoformat(started_str)
        elapsed = datetime.utcnow() - started

        if elapsed < timedelta(days=_SHADOW_DAYS):
            days_left = _SHADOW_DAYS - elapsed.days
            logger.info("ShadowTester: %s — %d day(s) remaining in shadow", version, days_left)
            return False

        shadow_sharpe = float(shadow.get("sharpe", 0.0))
        live_sharpe = float(self._state.get("live_sharpe", 0.0))

        if live_sharpe <= 0:
            logger.info("ShadowTester: no live benchmark — promoting %s", version)
            return True

        ratio = shadow_sharpe / live_sharpe
        if ratio >= _MIN_SHARPE_RATIO:
            logger.info("ShadowTester: %s promoted (ratio=%.2f)", version, ratio)
            return True

        logger.warning(
            "ShadowTester: %s blocked — ratio=%.2f < %.2f",
            version, ratio, _MIN_SHARPE_RATIO,
        )
        return False

    # ── shadow lifecycle ──────────────────────────────────────────────────────

    def start_shadow(self, version: str, initial_sharpe: float = 0.0) -> None:
        """Begin a 3-day shadow window for `version` (live mode only)."""
        if self._paper_mode:
            return
        self._state["shadow"] = {
            "version": version,
            "started_at": datetime.utcnow().isoformat(),
            "sharpe": initial_sharpe,
        }
        self._save()
        logger.info("ShadowTester: started 3-day shadow for %s", version)

    def update_shadow_sharpe(self, version: str, sharpe: float) -> None:
        shadow = self._state.get("shadow", {})
        if shadow.get("version") == version:
            shadow["sharpe"] = sharpe
            self._state["shadow"] = shadow
            self._save()

    def update_live_sharpe(self, sharpe: float) -> None:
        self._state["live_sharpe"] = sharpe
        self._save()

    # ── status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        shadow = self._state.get("shadow", {})
        if not shadow:
            return {"active": False, "paper_mode": self._paper_mode}
        started_str = shadow.get("started_at")
        elapsed_days = 0
        if started_str:
            elapsed_days = (datetime.utcnow() - datetime.fromisoformat(started_str)).days
        return {
            "active": True,
            "paper_mode": self._paper_mode,
            "version": shadow.get("version"),
            "elapsed_days": elapsed_days,
            "remaining_days": max(0, _SHADOW_DAYS - elapsed_days),
            "shadow_sharpe": float(shadow.get("sharpe", 0.0)),
            "live_sharpe": float(self._state.get("live_sharpe", 0.0)),
        }

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self._state_file) as f:
                self._state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._state = {}

    def _save(self) -> None:
        with open(self._state_file, "w") as f:
            json.dump(self._state, f, indent=2, default=str)
