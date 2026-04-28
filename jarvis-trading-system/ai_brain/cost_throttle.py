"""
CostThrottle — Layer 5 API budget guard and response cache.

Responsibilities:
  1. Track daily AI spend (USD + INR display) with persistence across restarts.
  2. Enforce a configurable daily budget ceiling; flip to rules-only mode when
     the limit is hit.
  3. 45-second prompt-hash cache so identical prompts within a burst never
     hit the LLM twice.
  4. Warn at a configurable warn_fraction (default 80 %) of the daily budget.
  5. Expose mode property: "ai" | "rules_only" for the rest of Layer 5.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_BUDGET_INR    = 200.0        # ₹ per day
_INR_PER_USD           = 84.0        # approximate; override via env USD_INR_RATE
_CACHE_TTL_S           = 45.0        # seconds a prompt-hash stays cached
_WARN_FRACTION         = 0.80        # warn when spend reaches 80 % of budget
_STATE_FILE_ENV        = "AI_COST_STATE_PATH"
_DEFAULT_STATE_FILE    = "logs/ai_cost_state.json"

Mode = Literal["ai", "rules_only"]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    response: str
    direction: Optional[str]
    model_used: str
    cost_usd: float
    cached_at: float       # unix timestamp
    hits: int = 0


@dataclass
class BudgetSnapshot:
    date_iso: str
    spend_usd: float
    spend_inr: float
    budget_usd: float
    budget_inr: float
    pct_used: float
    mode: str
    cache_size: int
    total_saves: int       # number of cache hits today (API calls avoided)


# ── Main class ────────────────────────────────────────────────────────────────

class CostThrottle:
    """
    Usage pattern
    -------------
    throttle = CostThrottle()

    # 1. Before every AI call:
    if not throttle.can_call():
        return rules_based_result()

    key = throttle.make_key(prompt, context)
    cached = throttle.get_cached(key)
    if cached:
        return cached

    # 2. After a successful AI call:
    throttle.record(key, response_text, direction, model, cost_usd)
    """

    def __init__(
        self,
        daily_budget_inr: float = _DEFAULT_BUDGET_INR,
        cache_ttl_s: float      = _CACHE_TTL_S,
        warn_fraction: float    = _WARN_FRACTION,
        inr_per_usd: Optional[float] = None,
        state_path: Optional[str]    = None,
    ) -> None:
        self._inr_per_usd   = inr_per_usd or float(os.environ.get("USD_INR_RATE", _INR_PER_USD))
        self._budget_inr    = daily_budget_inr
        self._budget_usd    = daily_budget_inr / self._inr_per_usd
        self._cache_ttl     = cache_ttl_s
        self._warn_fraction = warn_fraction

        self._lock          = asyncio.Lock()
        self._cache: dict[str, CacheEntry]  = {}
        self._daily_spend: dict[str, float] = {}   # date_iso -> USD
        self._daily_saves: dict[str, int]   = {}   # date_iso -> count of cache hits
        self._warned_today  = False
        self._mode: Mode    = "ai"

        state_file = state_path or os.environ.get(_STATE_FILE_ENV, _DEFAULT_STATE_FILE)
        self._state_path = Path(state_file)
        self._load_state()

        logger.info(
            "CostThrottle ready | budget ₹%.0f (~$%.3f) | cache_ttl=%ss",
            self._budget_inr, self._budget_usd, self._cache_ttl,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def can_call(self) -> bool:
        """Return False when today's spend has reached the daily budget."""
        return self._mode == "ai"

    @property
    def mode(self) -> Mode:
        return self._mode

    @staticmethod
    def make_key(prompt: str, context: Any = None) -> str:
        """Deterministic hash for (prompt, context) pair."""
        raw = prompt + (json.dumps(context, sort_keys=True, default=str) if context else "")
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get_cached(self, key: str) -> Optional[CacheEntry]:
        """Return a live cache entry or None if absent/expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.cached_at > self._cache_ttl:
            del self._cache[key]
            return None
        entry.hits += 1
        today = date.today().isoformat()
        self._daily_saves[today] = self._daily_saves.get(today, 0) + 1
        logger.debug("Cache HIT key=%s  hits=%d  direction=%s", key, entry.hits, entry.direction)
        return entry

    async def record(
        self,
        key: str,
        response: str,
        direction: Optional[str],
        model: str,
        cost_usd: float,
    ) -> None:
        """Record a completed API call: update spend, cache result, check limits."""
        async with self._lock:
            today = date.today().isoformat()
            prev = self._daily_spend.get(today, 0.0)
            self._daily_spend[today] = prev + cost_usd

            # Cache the result for TTL seconds
            self._cache[key] = CacheEntry(
                response=response,
                direction=direction,
                model_used=model,
                cost_usd=cost_usd,
                cached_at=time.time(),
            )

            self._evict_expired()
            self._check_thresholds(today)
            self._persist_state()

        logger.debug(
            "Spend +$%.5f  today=$%.4f/₹%.2f  mode=%s",
            cost_usd,
            self._daily_spend.get(today, 0.0),
            self._daily_spend.get(today, 0.0) * self._inr_per_usd,
            self._mode,
        )

    def snapshot(self) -> BudgetSnapshot:
        today = date.today().isoformat()
        spend_usd = self._daily_spend.get(today, 0.0)
        spend_inr = spend_usd * self._inr_per_usd
        pct       = spend_usd / self._budget_usd if self._budget_usd else 0.0
        return BudgetSnapshot(
            date_iso   = today,
            spend_usd  = spend_usd,
            spend_inr  = spend_inr,
            budget_usd = self._budget_usd,
            budget_inr = self._budget_inr,
            pct_used   = round(pct * 100, 2),
            mode       = self._mode,
            cache_size = len(self._cache),
            total_saves= self._daily_saves.get(today, 0),
        )

    def reset_day(self) -> None:
        """Force-reset today's spend (testing / manual override)."""
        today = date.today().isoformat()
        self._daily_spend[today] = 0.0
        self._daily_saves[today] = 0
        self._warned_today = False
        self._mode = "ai"
        self._persist_state()
        logger.info("CostThrottle: daily spend manually reset")

    def invalidate_cache(self, key: Optional[str] = None) -> None:
        """Remove a specific key or flush the entire cache."""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_thresholds(self, today: str) -> None:
        spend = self._daily_spend.get(today, 0.0)
        pct   = spend / self._budget_usd if self._budget_usd else 0.0

        if pct >= 1.0 and self._mode == "ai":
            self._mode = "rules_only"
            logger.warning(
                "BUDGET EXHAUSTED — switching to rules-only mode | "
                "spend=$%.4f / ₹%.1f  budget=₹%.0f",
                spend, spend * self._inr_per_usd, self._budget_inr,
            )
        elif pct >= self._warn_fraction and not self._warned_today:
            self._warned_today = True
            remaining_inr = (self._budget_usd - spend) * self._inr_per_usd
            logger.warning(
                "BUDGET WARNING — %.0f%% used | ₹%.2f remaining today",
                pct * 100, remaining_inr,
            )

        # Auto-restore if a new calendar day started while mode was rules_only
        if self._mode == "rules_only" and pct < 1.0:
            self._mode = "ai"
            logger.info("CostThrottle: new day detected — restoring AI mode")

    def _evict_expired(self) -> None:
        now  = time.time()
        dead = [k for k, v in self._cache.items() if now - v.cached_at > self._cache_ttl]
        for k in dead:
            del self._cache[k]

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "daily_spend":  self._daily_spend,
                "daily_saves":  self._daily_saves,
                "updated_at":   datetime.utcnow().isoformat(),
            }
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(self._state_path)
        except Exception as exc:
            logger.debug("CostThrottle: state persist failed: %s", exc)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
            self._daily_spend = raw.get("daily_spend", {})
            self._daily_saves = raw.get("daily_saves", {})

            today = date.today().isoformat()
            spend = self._daily_spend.get(today, 0.0)
            pct   = spend / self._budget_usd if self._budget_usd else 0.0
            if pct >= 1.0:
                self._mode = "rules_only"
                logger.warning(
                    "CostThrottle loaded: budget already exhausted for %s ($%.4f spent)",
                    today, spend,
                )
            elif pct >= self._warn_fraction:
                self._warned_today = True

            logger.info(
                "CostThrottle loaded state from %s | today spend=$%.4f/₹%.2f (%.0f%%)",
                self._state_path,
                spend, spend * self._inr_per_usd, pct * 100,
            )
        except Exception as exc:
            logger.warning("CostThrottle: could not load state (%s) — starting fresh", exc)
