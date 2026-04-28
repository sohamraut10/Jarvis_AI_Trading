"""
AIRouter — Layer 5 primary inference gateway.

Routes prompts to Claude (primary) with GPT-4o fallback.
Supports three call modes:
  primary   — Claude only, fallback to GPT-4o on error or latency > 3 s
  fallback  — GPT-4o only
  consensus — both models called concurrently; returns result only when
              both agree on the extracted `direction` field
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# ── Pricing table (USD per 1 000 tokens, as of 2025-Q2) ───────────────────────
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":           {"in": 0.015,  "out": 0.075},
    "claude-sonnet-4-6":         {"in": 0.003,  "out": 0.015},
    "claude-haiku-4-5-20251001": {"in": 0.00025,"out": 0.00125},
    "gpt-4o":                    {"in": 0.005,  "out": 0.015},
    "gpt-4o-mini":               {"in": 0.00015,"out": 0.0006},
}

_FALLBACK_LATENCY_S = 3.0   # switch to GPT-4o if Claude takes longer than this
_DEFAULT_MAX_TOKENS = 512


def _calc_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model, {"in": 0.005, "out": 0.015})
    return (tokens_in * p["in"] + tokens_out * p["out"]) / 1000.0


@dataclass
class CallRecord:
    ts: float
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    mode: str
    success: bool
    error: Optional[str] = None


@dataclass
class RouterResponse:
    response: str
    model_used: str
    latency_ms: float
    cost_usd: float
    consensus: bool             # True when consensus mode matched
    direction: Optional[str]    # extracted from JSON response if present
    tokens_in: int = 0
    tokens_out: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class AIRouter:
    """
    Stateful router that maintains daily cost and call-log.
    Thread/task-safe for concurrent callers; accumulator uses asyncio.Lock.
    """

    DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
    DEFAULT_GPT_MODEL    = "gpt-4o"

    def __init__(
        self,
        *,
        claude_model: str = DEFAULT_CLAUDE_MODEL,
        gpt_model: str    = DEFAULT_GPT_MODEL,
        max_tokens: int   = _DEFAULT_MAX_TOKENS,
        system_prompt: str = (
            "You are JARVIS, an autonomous quantitative trading AI. "
            "Always respond in valid JSON. Include a 'direction' field: "
            "'long', 'short', or 'flat'. Be concise and decisive."
        ),
    ) -> None:
        self.claude_model   = claude_model
        self.gpt_model      = gpt_model
        self.max_tokens     = max_tokens
        self.system_prompt  = system_prompt

        self._lock          = asyncio.Lock()
        self._call_log: list[CallRecord] = []
        self._daily_cost: dict[date, float] = {}

        # Lazy-initialised SDK clients
        self._anthropic_client = None
        self._openai_client    = None

    # ── Public interface ──────────────────────────────────────────────────────

    async def call(
        self,
        prompt: str,
        context: dict[str, Any],
        mode: Literal["primary", "fallback", "consensus"] = "primary",
    ) -> RouterResponse:
        """
        Make an inference call.

        mode="primary"   → Claude; auto-fallback to GPT-4o on error / slow
        mode="fallback"  → GPT-4o only
        mode="consensus" → Claude + GPT-4o concurrently; agree or raise
        """
        full_prompt = self._build_prompt(prompt, context)

        if mode == "fallback":
            return await self._call_gpt(full_prompt, mode)

        if mode == "consensus":
            return await self._consensus(full_prompt)

        # mode == "primary"
        return await self._call_claude_with_fallback(full_prompt)

    @property
    def daily_cost_usd(self) -> float:
        return self._daily_cost.get(date.today(), 0.0)

    @property
    def call_log(self) -> list[CallRecord]:
        return list(self._call_log)

    def cost_summary(self) -> dict[str, Any]:
        today = date.today()
        today_records = [r for r in self._call_log if date.fromtimestamp(r.ts) == today]
        return {
            "date":          today.isoformat(),
            "total_cost_usd": self._daily_cost.get(today, 0.0),
            "total_calls":   len(today_records),
            "by_model": {
                m: {
                    "calls": sum(1 for r in today_records if r.model == m),
                    "cost_usd": sum(r.cost_usd for r in today_records if r.model == m),
                }
                for m in {r.model for r in today_records}
            },
        }

    # ── Claude call ───────────────────────────────────────────────────────────

    async def _call_claude(self, prompt: str, mode: str) -> RouterResponse:
        client = self._get_anthropic_client()
        t0 = time.perf_counter()
        try:
            msg = await asyncio.wait_for(
                client.messages.create(
                    model=self.claude_model,
                    max_tokens=self.max_tokens,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=_FALLBACK_LATENCY_S + 0.5,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            text       = msg.content[0].text
            tok_in     = msg.usage.input_tokens
            tok_out    = msg.usage.output_tokens
            cost       = _calc_cost(self.claude_model, tok_in, tok_out)

            await self._record(CallRecord(
                ts=time.time(), model=self.claude_model,
                tokens_in=tok_in, tokens_out=tok_out,
                cost_usd=cost, latency_ms=latency_ms,
                mode=mode, success=True,
            ))

            return RouterResponse(
                response=text, model_used=self.claude_model,
                latency_ms=latency_ms, cost_usd=cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out,
                raw={"model": self.claude_model, "text": text},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=self.claude_model,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, success=False,
                error=str(exc),
            ))
            raise

    async def _call_claude_with_fallback(self, prompt: str) -> RouterResponse:
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self._call_claude(prompt, mode="primary"),
                timeout=_FALLBACK_LATENCY_S,
            )
            return resp
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.warning("Claude latency >%.0f ms — falling back to GPT-4o", elapsed_ms)
        except Exception as exc:
            logger.warning("Claude error (%s) — falling back to GPT-4o", exc)

        return await self._call_gpt(prompt, mode="primary_fallback")

    # ── GPT-4o call ───────────────────────────────────────────────────────────

    async def _call_gpt(self, prompt: str, mode: str) -> RouterResponse:
        client = self._get_openai_client()
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.gpt_model,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user",   "content": prompt},
                    ],
                ),
                timeout=10.0,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            text       = resp.choices[0].message.content
            tok_in     = resp.usage.prompt_tokens
            tok_out    = resp.usage.completion_tokens
            cost       = _calc_cost(self.gpt_model, tok_in, tok_out)

            await self._record(CallRecord(
                ts=time.time(), model=self.gpt_model,
                tokens_in=tok_in, tokens_out=tok_out,
                cost_usd=cost, latency_ms=latency_ms,
                mode=mode, success=True,
            ))

            return RouterResponse(
                response=text, model_used=self.gpt_model,
                latency_ms=latency_ms, cost_usd=cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out,
                raw={"model": self.gpt_model, "text": text},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=self.gpt_model,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, success=False,
                error=str(exc),
            ))
            raise

    # ── Consensus mode ────────────────────────────────────────────────────────

    async def _consensus(self, prompt: str) -> RouterResponse:
        claude_task = asyncio.create_task(self._call_claude(prompt, mode="consensus"))
        gpt_task    = asyncio.create_task(self._call_gpt(prompt, mode="consensus"))

        results = await asyncio.gather(claude_task, gpt_task, return_exceptions=True)
        claude_resp, gpt_resp = results

        claude_ok = isinstance(claude_resp, RouterResponse)
        gpt_ok    = isinstance(gpt_resp,    RouterResponse)

        if not claude_ok and not gpt_ok:
            raise RuntimeError(
                f"Both models failed — Claude: {claude_resp}; GPT: {gpt_resp}"
            )

        if claude_ok and gpt_ok:
            c_dir = claude_resp.direction
            g_dir = gpt_resp.direction
            agree = c_dir is not None and c_dir == g_dir
            if agree:
                # merge: prefer Claude text, sum costs
                return RouterResponse(
                    response=claude_resp.response,
                    model_used=f"{self.claude_model}+{self.gpt_model}",
                    latency_ms=max(claude_resp.latency_ms, gpt_resp.latency_ms),
                    cost_usd=claude_resp.cost_usd + gpt_resp.cost_usd,
                    consensus=True,
                    direction=c_dir,
                    tokens_in=claude_resp.tokens_in + gpt_resp.tokens_in,
                    tokens_out=claude_resp.tokens_out + gpt_resp.tokens_out,
                    raw={"claude": claude_resp.raw, "gpt": gpt_resp.raw},
                )
            else:
                raise ValueError(
                    f"Consensus disagreement — Claude: {c_dir!r}, GPT: {g_dir!r}"
                )

        # One model failed — return the successful one, consensus=False
        good: RouterResponse = claude_resp if claude_ok else gpt_resp  # type: ignore[assignment]
        logger.warning(
            "Consensus: one model failed (%s); returning single-model result (consensus=False)",
            claude_resp if not claude_ok else gpt_resp,
        )
        return good

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(prompt: str, context: dict[str, Any]) -> str:
        ctx_json = json.dumps(context, default=str, indent=2)
        return f"CONTEXT:\n{ctx_json}\n\nTASK:\n{prompt}"

    @staticmethod
    def _extract_direction(text: str) -> Optional[str]:
        try:
            data = json.loads(text)
            d = str(data.get("direction", "")).lower().strip()
            return d if d in ("long", "short", "flat") else None
        except (json.JSONDecodeError, AttributeError):
            # Try a naive keyword scan
            lower = text.lower()
            for kw in ("long", "short", "flat"):
                if f'"direction": "{kw}"' in lower or f"direction: {kw}" in lower:
                    return kw
            return None

    async def _record(self, record: CallRecord) -> None:
        async with self._lock:
            self._call_log.append(record)
            day = date.fromtimestamp(record.ts)
            self._daily_cost[day] = self._daily_cost.get(day, 0.0) + record.cost_usd
        logger.debug(
            "AI call | model=%-28s mode=%-18s latency=%6.0f ms  cost=$%.5f  ok=%s%s",
            record.model, record.mode, record.latency_ms, record.cost_usd,
            record.success, f"  err={record.error}" if record.error else "",
        )

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            import anthropic  # noqa: PLC0415
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise EnvironmentError(
                    "ANTHROPIC_API_KEY not set. Export it or add to .env."
                )
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._anthropic_client

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI  # noqa: PLC0415
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise EnvironmentError(
                    "OPENAI_API_KEY not set. Export it or add to .env."
                )
            self._openai_client = AsyncOpenAI(api_key=api_key)
        return self._openai_client
