"""
Append-only experience buffer backed by SQLite.

Every closed trade is stored with its full feature vector so the RL agent
and Bayesian optimiser can train on real outcomes rather than simulated ones.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import aiosqlite
import numpy as np

logger = logging.getLogger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS experiences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT    NOT NULL,
    strategy       TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    regime         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    entry_price    REAL    NOT NULL,
    exit_price     REAL,
    qty            INTEGER NOT NULL,
    pnl            REAL    DEFAULT 0.0,
    drawdown       REAL    DEFAULT 0.0,
    duration_secs  REAL    DEFAULT 0.0,
    feature_vector TEXT    NOT NULL DEFAULT '{}',
    outcome        TEXT    DEFAULT 'open'
)
"""


class ExperienceBuffer:
    """Thread-safe async experience store. One instance per process."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()

    async def initialise(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE)
            await db.commit()

    async def log_trade(
        self,
        *,
        strategy: str,
        symbol: str,
        regime: str,
        side: str,
        entry_price: float,
        qty: int,
        feature_vector: dict[str, Any] | None = None,
        exit_price: float | None = None,
        pnl: float = 0.0,
        drawdown: float = 0.0,
        duration_secs: float = 0.0,
        outcome: str = "open",
    ) -> int:
        fv = json.dumps(feature_vector or {})
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                cur = await db.execute(
                    """INSERT INTO experiences
                       (timestamp, strategy, symbol, regime, side,
                        entry_price, exit_price, qty, pnl, drawdown,
                        duration_secs, feature_vector, outcome)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.utcnow().isoformat(),
                        strategy, symbol, regime, side,
                        entry_price, exit_price, qty,
                        pnl, drawdown, duration_secs, fv, outcome,
                    ),
                )
                await db.commit()
                return cur.lastrowid  # type: ignore[return-value]

    async def update_outcome(
        self,
        row_id: int,
        *,
        exit_price: float,
        pnl: float,
        drawdown: float,
        duration_secs: float,
        outcome: str = "closed",
    ) -> None:
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """UPDATE experiences
                       SET exit_price=?, pnl=?, drawdown=?, duration_secs=?, outcome=?
                       WHERE id=?""",
                    (exit_price, pnl, drawdown, duration_secs, outcome, row_id),
                )
                await db.commit()

    async def get_recent(self, n: int = 1000) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM experiences ORDER BY id DESC LIMIT ?", (n,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def get_feature_matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (X, y) float32 arrays from closed trades for RL training."""
        records = await self.get_recent(5000)
        closed = [r for r in records if r["outcome"] == "closed"]
        if not closed:
            return np.empty((0, 8), dtype=np.float32), np.empty((0,), dtype=np.float32)

        X_rows, y_rows = [], []
        for r in closed:
            fv = json.loads(r["feature_vector"]) if isinstance(r["feature_vector"], str) else r["feature_vector"]
            X_rows.append([
                fv.get("regime_id", 0),
                fv.get("strategy_id", 0),
                fv.get("capital_ratio", 0.5),
                fv.get("time_fraction", 0.5),
                fv.get("volatility", 0.01),
                fv.get("recent_sharpe", 0.0),
                fv.get("win_rate", 0.5),
                fv.get("drawdown_ratio", 0.0),
            ])
            y_rows.append(float(r["pnl"]))

        return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.float32)

    async def count(self) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM experiences") as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0
