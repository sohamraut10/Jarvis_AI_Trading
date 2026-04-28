"""
P&L Tracker — aiosqlite-backed session + historical accounting.

Tables
------
trades          — one row per closed trade (entry → exit pair)
daily_summary   — one row per calendar day, upserted on every trade

Usage
-----
    tracker = PnLTracker("data/pnl.db")
    await tracker.init()
    trade_id = await tracker.record_trade(...)
    summary = await tracker.get_session_summary()
    curve   = await tracker.get_equity_curve(days=30)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import aiosqlite


_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id  TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    qty          INTEGER NOT NULL,
    entry_price  REAL    NOT NULL,
    exit_price   REAL    NOT NULL,
    pnl          REAL    NOT NULL,
    trade_date   TEXT    NOT NULL,
    entry_time   TEXT    NOT NULL,
    exit_time    TEXT    NOT NULL,
    regime       TEXT,
    created_at   TEXT    DEFAULT (datetime('now'))
)
"""

_CREATE_DAILY = """
CREATE TABLE IF NOT EXISTS daily_summary (
    date          TEXT    PRIMARY KEY,
    realized_pnl  REAL    DEFAULT 0.0,
    num_trades    INTEGER DEFAULT 0,
    num_wins      INTEGER DEFAULT 0,
    updated_at    TEXT    DEFAULT (datetime('now'))
)
"""

_UPSERT_DAILY = """
INSERT INTO daily_summary (date, realized_pnl, num_trades, num_wins)
VALUES (?, ?, 1, ?)
ON CONFLICT(date) DO UPDATE SET
    realized_pnl = realized_pnl + excluded.realized_pnl,
    num_trades   = num_trades + 1,
    num_wins     = num_wins + excluded.num_wins,
    updated_at   = datetime('now')
"""


class PnLTracker:
    def __init__(self, db_path: str = "data/pnl.db") -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Create tables if they don't exist. Call once on startup."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TRADES)
            await db.execute(_CREATE_DAILY)
            await db.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    async def record_trade(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        exit_price: float,
        pnl: float,
        entry_time: Optional[datetime] = None,
        exit_time: Optional[datetime] = None,
        regime: Optional[str] = None,
    ) -> int:
        """Insert a closed trade and update the daily summary. Returns row id."""
        now = datetime.utcnow()
        entry_time = entry_time or now
        exit_time = exit_time or now
        trade_date = exit_time.date().isoformat()

        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO trades
                        (strategy_id, symbol, side, qty,
                         entry_price, exit_price, pnl,
                         trade_date, entry_time, exit_time, regime)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id, symbol, side, qty,
                        entry_price, exit_price, pnl,
                        trade_date,
                        entry_time.isoformat(), exit_time.isoformat(),
                        regime,
                    ),
                )
                trade_id = cursor.lastrowid
                await db.execute(_UPSERT_DAILY, (trade_date, pnl, 1 if pnl > 0 else 0))
                await db.commit()
        return trade_id

    # ── Read ───────────────────────────────────────────────────────────────────

    async def get_daily_pnl(self, for_date: Optional[date] = None) -> float:
        d = (for_date or date.today()).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE trade_date = ?", (d,)
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def get_session_summary(self, for_date: Optional[date] = None) -> dict:
        d = (for_date or date.today()).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT
                    COALESCE(SUM(pnl), 0.0)                           AS total_pnl,
                    COUNT(*)                                           AS num_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)          AS num_wins,
                    COALESCE(MAX(pnl), 0.0)                           AS best_trade,
                    COALESCE(MIN(pnl), 0.0)                           AS worst_trade
                FROM trades WHERE trade_date = ?
                """,
                (d,),
            ) as cur:
                row = await cur.fetchone()

        if not row or row[1] == 0:
            return {"date": d, "total_pnl": 0.0, "num_trades": 0,
                    "num_wins": 0, "win_rate": 0.0, "best_trade": 0.0, "worst_trade": 0.0}

        total_pnl, num_trades, num_wins, best, worst = row
        return {
            "date": d,
            "total_pnl": round(float(total_pnl), 2),
            "num_trades": int(num_trades),
            "num_wins": int(num_wins or 0),
            "win_rate": round(int(num_wins or 0) / int(num_trades), 3),
            "best_trade": round(float(best), 2),
            "worst_trade": round(float(worst), 2),
        }

    async def get_strategy_pnl(self, strategy_id: str, days: int = 20) -> list[dict]:
        """Rolling per-day breakdown for one strategy."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT trade_date, SUM(pnl) AS daily_pnl, COUNT(*) AS trades
                FROM trades
                WHERE strategy_id = ?
                  AND trade_date >= date('now', ?)
                GROUP BY trade_date
                ORDER BY trade_date ASC
                """,
                (strategy_id, f"-{days} days"),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {"date": r[0], "pnl": round(float(r[1]), 2), "trades": int(r[2])}
            for r in rows
        ]

    async def get_equity_curve(self, days: int = 30) -> list[dict]:
        """Daily P&L + running cumulative for the dashboard PnL chart."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT date, realized_pnl
                FROM daily_summary
                WHERE date >= date('now', ?)
                ORDER BY date ASC
                """,
                (f"-{days} days",),
            ) as cur:
                rows = await cur.fetchall()

        cumulative = 0.0
        curve = []
        for r in rows:
            daily = float(r[1])
            cumulative += daily
            curve.append({
                "date": r[0],
                "pnl": round(daily, 2),
                "cumulative": round(cumulative, 2),
            })
        return curve

    async def get_all_strategies_today(self) -> list[dict]:
        """Per-strategy P&L breakdown for today (WebSocket snapshot)."""
        d = date.today().isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT strategy_id,
                       SUM(pnl)      AS pnl,
                       COUNT(*)      AS trades,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins
                FROM trades WHERE trade_date = ?
                GROUP BY strategy_id
                ORDER BY pnl DESC
                """,
                (d,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "strategy_id": r[0],
                "pnl": round(float(r[1]), 2),
                "trades": int(r[2]),
                "win_rate": round(int(r[3]) / int(r[2]), 3) if r[2] > 0 else 0.0,
            }
            for r in rows
        ]
