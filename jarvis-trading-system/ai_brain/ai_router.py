"""
AIRouter — Layer 5 primary inference gateway.

Supports Claude via Anthropic direct API or Amazon Bedrock, GPT-4o (OpenAI),
and Gemini (Google).  Routing per pipeline step is driven by config/ai_models.yaml.

Amazon Bedrock
──────────────
  Set bedrock.enabled: true in ai_models.yaml (or USE_BEDROCK=true env var) to
  route all Claude calls through AWS Bedrock instead of the Anthropic direct API.
  Requires: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION.
  Uses anthropic.AsyncAnthropicBedrock — same message interface, different client.
  Model IDs are mapped via bedrock.model_ids in ai_models.yaml.

Call modes
──────────
  primary   — use step routing; auto-fallback on error / latency breach
  fallback  — use the step's fallback model directly
  consensus — Claude + GPT concurrently; return only when direction agrees

Pipeline steps and their routing (from ai_models.yaml)
───────────────────────────────────────────────────────
  scan       gemini-2.5-flash-lite  → haiku fallback
  shortlist  gemini-2.5-flash       → haiku fallback
  analyse    claude-sonnet-4-6      → gemini-2.5-pro (if >3 s) → gpt-4o fallback
  decide     claude-sonnet-4-6      → gpt-4o fallback  (Gemini never used)
  monitor    gemini-2.5-flash       → haiku fallback

Gemini notes
────────────
  • Uses google.generativeai SDK with system_instruction= (not messages system role)
  • Context caching (CachedContent) for scan / shortlist / monitor system prompts;
    TTL 300 s; gracefully degrades to uncached on any cache error
  • Cost tokens read from response.usage_metadata:
      prompt_token_count, candidates_token_count, cached_content_token_count
  • GEMINI_FREE_TIER=true in .env skips cost recording (paper trading phase)

Backward compatibility
──────────────────────
  call(prompt, context, mode) still works unchanged (step defaults to None →
  original Claude-primary / GPT-fallback / consensus behaviour).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, Optional

import yaml

logger = logging.getLogger(__name__)

# ── Config loading ────────────────────────────────────────────────────────────

def _load_ai_config() -> dict:
    cfg_path = pathlib.Path(__file__).parent.parent / "config" / "ai_models.yaml"
    try:
        return yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as exc:
        logger.warning("ai_models.yaml not loaded (%s) — using built-in defaults", exc)
        return {}


# ── Fallback pricing (USD per million tokens) ─────────────────────────────────
# Used when ai_models.yaml is absent.  Mirrors the YAML values exactly.
_BUILTIN_COSTS: dict[str, dict[str, float]] = {
    "claude-opus-4-7":           {"in": 15.00,  "out": 75.00},
    "claude-sonnet-4-6":         {"in": 3.00,   "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 0.25,   "out": 1.25},
    "gpt-4o":                    {"in": 5.00,   "out": 15.00},
    "gpt-4o-mini":               {"in": 0.15,   "out": 0.60},
    "gemini-2.5-flash-lite":     {"in": 0.10,   "out": 0.40,  "cache_read": 0.01},
    "gemini-2.5-flash":          {"in": 0.30,   "out": 2.50,  "cache_read": 0.03},
    "gemini-2.5-pro":            {"in": 1.25,   "out": 10.00, "cache_read": 0.125},
    "gemini-3.1-pro":            {"in": 2.00,   "out": 12.00, "cache_read": 0.20},
}

_DEFAULT_ROUTING: dict[str, dict] = {
    "scan":      {"primary": "gemini-2.5-flash-lite", "fallback": "claude-haiku-4-5-20251001"},
    "shortlist": {"primary": "gemini-2.5-flash",      "fallback": "claude-haiku-4-5-20251001"},
    "analyse":   {"primary": "claude-sonnet-4-6", "secondary": "gemini-2.5-pro", "fallback": "gpt-4o"},
    "decide":    {"primary": "claude-sonnet-4-6", "fallback": "gpt-4o"},
    "monitor":   {"primary": "gemini-2.5-flash",  "fallback": "claude-haiku-4-5-20251001",
                  "throttle_override": True},
}

_DEFAULT_MAX_TOKENS   = 512
_PRIMARY_TIMEOUT_S    = 3.0
_HARD_TIMEOUT_S       = 10.0
_GEMINI_CACHE_TTL_S   = 300
_GEMINI_CACHE_STEPS   = {"scan", "shortlist", "monitor"}


# ── Cost calculation ──────────────────────────────────────────────────────────

def _calc_cost(
    pricing: dict[str, dict[str, float]],
    model: str,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int = 0,
) -> float:
    """Cost in USD. tokens_cached uses the cheaper cache_read rate."""
    p = pricing.get(model, {"in": 5.00, "out": 15.00})
    regular_in = max(0, tokens_in - tokens_cached)
    cost = (
        regular_in   * p["in"]
        + tokens_out * p["out"]
        + tokens_cached * p.get("cache_read", p["in"])
    ) / 1_000_000
    return cost


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    ts: float
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cost_usd: float
    latency_ms: float
    mode: str
    step: str
    success: bool
    error: Optional[str] = None


@dataclass
class RouterResponse:
    response: str
    model_used: str
    latency_ms: float
    cost_usd: float
    consensus: bool
    direction: Optional[str]
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# ── AIRouter ──────────────────────────────────────────────────────────────────

class AIRouter:
    """
    Stateful router with per-step model routing, Gemini support, and
    context caching.  Daily cost accumulator is asyncio.Lock-guarded.
    """

    DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
    DEFAULT_GPT_MODEL    = "gpt-4o"

    def __init__(
        self,
        *,
        claude_model: str  = DEFAULT_CLAUDE_MODEL,
        gpt_model: str     = DEFAULT_GPT_MODEL,
        max_tokens: int    = _DEFAULT_MAX_TOKENS,
        system_prompt: str = (
            "You are JARVIS, an autonomous quantitative trading AI. "
            "Always respond in valid JSON. Include a 'direction' field: "
            "'long', 'short', or 'flat'. Be concise and decisive."
        ),
        config: Optional[dict] = None,
    ) -> None:
        self.claude_model  = claude_model
        self.gpt_model     = gpt_model
        self.max_tokens    = max_tokens
        self.system_prompt = system_prompt

        cfg = config if config is not None else _load_ai_config()
        raw_costs = cfg.get("costs_usd_per_million") or {}
        self._pricing: dict[str, dict[str, float]] = {
            **_BUILTIN_COSTS,
            **raw_costs,
        }
        self._routing: dict[str, dict] = {
            **_DEFAULT_ROUTING,
            **(cfg.get("routing") or {}),
        }
        latency_cfg          = cfg.get("latency") or {}
        self._primary_timeout = float(latency_cfg.get("primary_timeout", _PRIMARY_TIMEOUT_S))
        self._hard_timeout    = float(latency_cfg.get("hard_timeout", _HARD_TIMEOUT_S))

        cache_cfg             = cfg.get("gemini_cache") or {}
        self._cache_steps     = set(cache_cfg.get("enabled_steps", list(_GEMINI_CACHE_STEPS)))
        self._cache_ttl_s     = int(cache_cfg.get("ttl_seconds", _GEMINI_CACHE_TTL_S))

        free_tier_var         = (cfg.get("budget") or {}).get("free_tier_env_var", "GEMINI_FREE_TIER")
        self._gemini_free     = os.environ.get(free_tier_var, "").lower() == "true"

        # ── Amazon Bedrock ────────────────────────────────────────────────────
        bedrock_cfg           = cfg.get("bedrock") or {}
        self._use_bedrock: bool = (
            bedrock_cfg.get("enabled", False)
            or os.environ.get("USE_BEDROCK", "").lower() == "true"
        )
        self._bedrock_region: str = (
            os.environ.get("AWS_DEFAULT_REGION")
            or bedrock_cfg.get("region", "us-east-1")
        )
        # Logical model name → Bedrock cross-region inference profile ID
        self._bedrock_model_map: dict[str, str] = {
            "claude-opus-4-7":           "us.anthropic.claude-opus-4-5-20250514-v1:0",
            "claude-sonnet-4-6":         "us.anthropic.claude-sonnet-4-5-20250514-v1:0",
            "claude-haiku-4-5-20251001": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            **(bedrock_cfg.get("model_ids") or {}),
        }

        self._lock            = asyncio.Lock()
        self._call_log: list[CallRecord] = []
        self._daily_cost: dict[date, float] = {}

        # Lazy SDK clients
        self._anthropic_client = None
        self._bedrock_client   = None   # AsyncAnthropicBedrock
        self._openai_client    = None
        self._gemini_client    = None   # google.generativeai module handle

        # Gemini context cache: (model, prompt_hash) → (CachedContent, expires_at)
        self._gemini_prompt_cache: dict[tuple[str, str], tuple[Any, float]] = {}

        if self._use_bedrock:
            logger.info(
                "AIRouter: Bedrock mode ENABLED  region=%s", self._bedrock_region
            )

    # ── Public interface ──────────────────────────────────────────────────────

    async def call(
        self,
        prompt: str,
        context: dict[str, Any],
        mode: Literal["primary", "fallback", "consensus"] = "primary",
        step: Optional[str] = None,
    ) -> RouterResponse:
        """
        Make an inference call.

        step (optional) selects per-step routing from ai_models.yaml.
        When step is None the original Claude-primary / GPT-fallback
        / consensus behaviour is preserved.
        """
        full_prompt = self._build_prompt(prompt, context)

        if mode == "consensus":
            return await self._consensus(full_prompt, step)

        if step:
            return await self._call_for_step(full_prompt, step, mode)

        # ── Legacy path (no step) ─────────────────────────────────────────────
        if mode == "fallback":
            return await self._call_gpt(full_prompt, self.gpt_model, "fallback", "legacy")
        return await self._claude_with_fallback(full_prompt, self.claude_model, self.gpt_model, "legacy")

    @property
    def daily_cost_usd(self) -> float:
        return self._daily_cost.get(date.today(), 0.0)

    @property
    def use_bedrock(self) -> bool:
        return self._use_bedrock

    @property
    def call_log(self) -> list[CallRecord]:
        return list(self._call_log)

    def cost_summary(self) -> dict[str, Any]:
        today = date.today()
        recs  = [r for r in self._call_log if date.fromtimestamp(r.ts) == today]
        return {
            "date":            today.isoformat(),
            "total_cost_usd":  self._daily_cost.get(today, 0.0),
            "total_calls":     len(recs),
            "by_model": {
                m: {
                    "calls":    sum(1 for r in recs if r.model == m),
                    "cost_usd": sum(r.cost_usd for r in recs if r.model == m),
                }
                for m in {r.model for r in recs}
            },
        }

    # ── Step-based routing ────────────────────────────────────────────────────

    async def _call_for_step(
        self, prompt: str, step: str, mode: str
    ) -> RouterResponse:
        cfg      = self._routing.get(step, {})
        primary  = cfg.get("primary",  self.claude_model)
        fallback = cfg.get("fallback", self.gpt_model)

        if mode == "fallback":
            return await self._dispatch(prompt, fallback, f"{step}_fallback", step)

        # Special case: analyse step races primary vs secondary on slow response
        if step == "analyse":
            return await self._call_analyse(prompt, cfg)

        # Standard: try primary, fall back on error or timeout
        try:
            return await asyncio.wait_for(
                self._dispatch(prompt, primary, f"{step}_primary", step),
                timeout=self._primary_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("step=%s primary=%s timed out → fallback=%s", step, primary, fallback)
        except Exception as exc:
            logger.warning("step=%s primary=%s error (%s) → fallback=%s", step, primary, exc, fallback)

        return await self._dispatch(prompt, fallback, f"{step}_fallback", step)

    async def _call_analyse(self, prompt: str, cfg: dict) -> RouterResponse:
        """
        Analyse step: start Claude; if it takes > primary_timeout,
        fire Gemini secondary in parallel.  Use whichever finishes first.
        Falls back to GPT-4o if both fail.
        """
        primary   = cfg.get("primary",   self.claude_model)
        secondary = cfg.get("secondary")     # e.g. gemini-2.5-pro
        fallback  = cfg.get("fallback",  self.gpt_model)

        primary_task = asyncio.create_task(
            self._dispatch(prompt, primary, "analyse_primary", "analyse")
        )
        secondary_task: Optional[asyncio.Task] = None

        try:
            return await asyncio.wait_for(
                asyncio.shield(primary_task),
                timeout=self._primary_timeout,
            )
        except asyncio.TimeoutError:
            logger.info(
                "analyse: %s >%.0f s — racing with secondary %s",
                primary, self._primary_timeout, secondary,
            )
        except Exception as exc:
            logger.warning("analyse: primary %s failed (%s)", primary, exc)
            primary_task.cancel()

        # Fire secondary if configured
        if secondary:
            secondary_task = asyncio.create_task(
                self._dispatch(prompt, secondary, "analyse_secondary", "analyse")
            )

        tasks = [t for t in (primary_task, secondary_task) if t and not t.done() and not t.cancelled()]
        if not tasks:
            return await self._dispatch(prompt, fallback, "analyse_fallback", "analyse")

        remaining = self._hard_timeout - self._primary_timeout
        done, pending = await asyncio.wait(tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        for t in done:
            exc = t.exception()
            if exc is None:
                return t.result()

        logger.warning("analyse: both primary and secondary failed — GPT fallback")
        return await self._dispatch(prompt, fallback, "analyse_fallback", "analyse")

    # ── Dispatch by model family ──────────────────────────────────────────────

    async def _dispatch(
        self, prompt: str, model: str, mode: str, step: str
    ) -> RouterResponse:
        if model.startswith("gemini"):
            return await self._call_gemini(prompt, model, mode, step)
        if model.startswith("claude"):
            if self._use_bedrock:
                return await self._call_bedrock(prompt, model, mode, step)
            return await self._call_claude(prompt, model, mode, step)
        return await self._call_gpt(prompt, model, mode, step)

    # ── Amazon Bedrock (Claude via AWS) ──────────────────────────────────────

    async def _call_bedrock(
        self, prompt: str, model: str, mode: str, step: str
    ) -> RouterResponse:
        """Call Claude through AWS Bedrock using AsyncAnthropicBedrock."""
        bedrock_id = self._bedrock_model_map.get(model, model)
        client = self._get_bedrock_client()
        t0 = time.perf_counter()
        try:
            msg = await asyncio.wait_for(
                client.messages.create(
                    model=bedrock_id,
                    max_tokens=self.max_tokens,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self._hard_timeout,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            text       = msg.content[0].text
            tok_in     = msg.usage.input_tokens
            tok_out    = msg.usage.output_tokens
            cost       = _calc_cost(self._pricing, model, tok_in, tok_out)

            await self._record(CallRecord(
                ts=time.time(), model=f"bedrock/{bedrock_id}",
                tokens_in=tok_in, tokens_out=tok_out, tokens_cached=0,
                cost_usd=cost, latency_ms=latency_ms,
                mode=mode, step=step, success=True,
            ))
            return RouterResponse(
                response=text, model_used=f"bedrock/{bedrock_id}",
                latency_ms=latency_ms, cost_usd=cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out,
                raw={"model": bedrock_id, "text": text, "via": "bedrock"},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=f"bedrock/{bedrock_id}",
                tokens_in=0, tokens_out=0, tokens_cached=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, step=step,
                success=False, error=str(exc),
            ))
            raise

    # ── Claude (Anthropic direct API) ────────────────────────────────────────

    async def _call_claude(
        self, prompt: str, model: str, mode: str, step: str
    ) -> RouterResponse:
        client = self._get_anthropic_client()
        t0 = time.perf_counter()
        try:
            msg = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=self.max_tokens,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self._hard_timeout,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            text       = msg.content[0].text
            tok_in     = msg.usage.input_tokens
            tok_out    = msg.usage.output_tokens
            cost       = _calc_cost(self._pricing, model, tok_in, tok_out)

            await self._record(CallRecord(
                ts=time.time(), model=model,
                tokens_in=tok_in, tokens_out=tok_out, tokens_cached=0,
                cost_usd=cost, latency_ms=latency_ms,
                mode=mode, step=step, success=True,
            ))
            return RouterResponse(
                response=text, model_used=model,
                latency_ms=latency_ms, cost_usd=cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out,
                raw={"model": model, "text": text},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=model,
                tokens_in=0, tokens_out=0, tokens_cached=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, step=step,
                success=False, error=str(exc),
            ))
            raise

    # ── GPT ───────────────────────────────────────────────────────────────────

    async def _call_gpt(
        self, prompt: str, model: str, mode: str, step: str
    ) -> RouterResponse:
        client = self._get_openai_client()
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user",   "content": prompt},
                    ],
                ),
                timeout=self._hard_timeout,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            text       = resp.choices[0].message.content
            tok_in     = resp.usage.prompt_tokens
            tok_out    = resp.usage.completion_tokens
            cost       = _calc_cost(self._pricing, model, tok_in, tok_out)

            await self._record(CallRecord(
                ts=time.time(), model=model,
                tokens_in=tok_in, tokens_out=tok_out, tokens_cached=0,
                cost_usd=cost, latency_ms=latency_ms,
                mode=mode, step=step, success=True,
            ))
            return RouterResponse(
                response=text, model_used=model,
                latency_ms=latency_ms, cost_usd=cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out,
                raw={"model": model, "text": text},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=model,
                tokens_in=0, tokens_out=0, tokens_cached=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, step=step,
                success=False, error=str(exc),
            ))
            raise

    # ── Gemini ────────────────────────────────────────────────────────────────

    async def _call_gemini(
        self, prompt: str, model: str, mode: str, step: str
    ) -> RouterResponse:
        genai = self._get_gemini_client()
        t0    = time.perf_counter()
        try:
            gen_model = await self._gemini_model(genai, model, step)
            loop = asyncio.get_running_loop()
            resp = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: gen_model.generate_content(
                        prompt,
                        generation_config=genai.GenerationConfig(
                            response_mime_type="application/json",
                            max_output_tokens=self.max_tokens,
                        ),
                    ),
                ),
                timeout=self._hard_timeout,
            )

            latency_ms    = (time.perf_counter() - t0) * 1000
            text          = resp.text
            usage         = resp.usage_metadata
            tok_in        = getattr(usage, "prompt_token_count",      0) or 0
            tok_out       = getattr(usage, "candidates_token_count",  0) or 0
            tok_cached    = getattr(usage, "cached_content_token_count", 0) or 0
            cost          = _calc_cost(self._pricing, model, tok_in, tok_out, tok_cached)

            # Free-tier: skip cost recording for Gemini during paper phase
            if not self._gemini_free:
                await self._record(CallRecord(
                    ts=time.time(), model=model,
                    tokens_in=tok_in, tokens_out=tok_out, tokens_cached=tok_cached,
                    cost_usd=cost, latency_ms=latency_ms,
                    mode=mode, step=step, success=True,
                ))
            else:
                logger.debug(
                    "Gemini FREE TIER — skipping cost record  model=%s  tok_in=%d  tok_out=%d",
                    model, tok_in, tok_out,
                )

            return RouterResponse(
                response=text, model_used=model,
                latency_ms=latency_ms, cost_usd=0.0 if self._gemini_free else cost,
                consensus=False, direction=self._extract_direction(text),
                tokens_in=tok_in, tokens_out=tok_out, tokens_cached=tok_cached,
                raw={"model": model, "text": text, "cached_tokens": tok_cached},
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            await self._record(CallRecord(
                ts=time.time(), model=model,
                tokens_in=0, tokens_out=0, tokens_cached=0, cost_usd=0.0,
                latency_ms=latency_ms, mode=mode, step=step,
                success=False, error=str(exc),
            ))
            raise

    async def _gemini_model(self, genai, model: str, step: str):
        """Return a GenerativeModel, using CachedContent for eligible steps."""
        use_cache = step in self._cache_steps

        if use_cache:
            cache_key = (model, self._prompt_hash(self.system_prompt))
            cached, expires = self._gemini_prompt_cache.get(cache_key, (None, 0.0))
            if cached is not None and time.time() < expires:
                try:
                    return genai.GenerativeModel.from_cached_content(cached)
                except Exception as exc:
                    logger.debug("Gemini: stale cache for %s (%s) — recreating", model, exc)
                    self._gemini_prompt_cache.pop(cache_key, None)

            # Create / refresh cache
            try:
                loop = asyncio.get_running_loop()
                new_cache = await loop.run_in_executor(
                    None,
                    lambda: genai.caching.CachedContent.create(
                        model=f"models/{model}",
                        system_instruction=self.system_prompt,
                        ttl=timedelta(seconds=self._cache_ttl_s),
                        contents=[],
                    ),
                )
                self._gemini_prompt_cache[cache_key] = (
                    new_cache,
                    time.time() + self._cache_ttl_s,
                )
                logger.debug("Gemini CachedContent created for step=%s model=%s", step, model)
                return genai.GenerativeModel.from_cached_content(new_cache)
            except Exception as exc:
                logger.debug(
                    "Gemini: CachedContent creation failed (%s) — using uncached model", exc
                )

        # Uncached model
        return genai.GenerativeModel(
            model_name=model,
            system_instruction=self.system_prompt,
        )

    # ── Consensus ─────────────────────────────────────────────────────────────

    async def _consensus(self, prompt: str, step: Optional[str]) -> RouterResponse:
        # For consensus: always use Claude + GPT regardless of step routing
        claude_model = self._routing.get(step or "", {}).get("primary", self.claude_model)
        if not claude_model.startswith("claude"):
            claude_model = self.claude_model

        claude_task = asyncio.create_task(
            self._call_claude(prompt, claude_model, "consensus", step or "legacy")
        )
        gpt_task = asyncio.create_task(
            self._call_gpt(prompt, self.gpt_model, "consensus", step or "legacy")
        )

        results = await asyncio.gather(claude_task, gpt_task, return_exceptions=True)
        claude_resp, gpt_resp = results

        claude_ok = isinstance(claude_resp, RouterResponse)
        gpt_ok    = isinstance(gpt_resp,    RouterResponse)

        if not claude_ok and not gpt_ok:
            raise RuntimeError(f"Both models failed — Claude: {claude_resp}; GPT: {gpt_resp}")

        if claude_ok and gpt_ok:
            c_dir = claude_resp.direction
            g_dir = gpt_resp.direction
            if c_dir is not None and c_dir == g_dir:
                return RouterResponse(
                    response=claude_resp.response,
                    model_used=f"{claude_model}+{self.gpt_model}",
                    latency_ms=max(claude_resp.latency_ms, gpt_resp.latency_ms),
                    cost_usd=claude_resp.cost_usd + gpt_resp.cost_usd,
                    consensus=True, direction=c_dir,
                    tokens_in=claude_resp.tokens_in + gpt_resp.tokens_in,
                    tokens_out=claude_resp.tokens_out + gpt_resp.tokens_out,
                    raw={"claude": claude_resp.raw, "gpt": gpt_resp.raw},
                )
            raise ValueError(f"Consensus disagreement — Claude: {c_dir!r}, GPT: {g_dir!r}")

        good: RouterResponse = claude_resp if claude_ok else gpt_resp  # type: ignore[assignment]
        logger.warning("Consensus: one model failed; returning single-model (consensus=False)")
        return good

    # ── Legacy helpers kept for backwards compat ──────────────────────────────

    async def _claude_with_fallback(
        self, prompt: str, claude_model: str, gpt_model: str, step: str
    ) -> RouterResponse:
        try:
            return await asyncio.wait_for(
                self._call_claude(prompt, claude_model, "primary", step),
                timeout=self._primary_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Claude latency >%.0f s — falling back to GPT-4o", self._primary_timeout)
        except Exception as exc:
            logger.warning("Claude error (%s) — falling back to GPT-4o", exc)
        return await self._call_gpt(prompt, gpt_model, "primary_fallback", step)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(prompt: str, context: dict[str, Any]) -> str:
        ctx_json = json.dumps(context, default=str, indent=2)
        return f"CONTEXT:\n{ctx_json}\n\nTASK:\n{prompt}"

    @staticmethod
    def _prompt_hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    @staticmethod
    def _extract_direction(text: str) -> Optional[str]:
        try:
            data = json.loads(text)
            d = str(data.get("direction", "")).lower().strip()
            return d if d in ("long", "short", "flat") else None
        except (json.JSONDecodeError, AttributeError):
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
            "AI call | model=%-28s step=%-10s mode=%-20s "
            "latency=%6.0f ms  cost=$%.5f  cached=%d  ok=%s%s",
            record.model, record.step, record.mode,
            record.latency_ms, record.cost_usd, record.tokens_cached,
            record.success, f"  err={record.error}" if record.error else "",
        )

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            import anthropic  # noqa: PLC0415
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise EnvironmentError("ANTHROPIC_API_KEY not set. Export it or add to .env.")
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
        return self._anthropic_client

    def _get_bedrock_client(self):
        if self._bedrock_client is None:
            from anthropic import AsyncAnthropicBedrock  # noqa: PLC0415
            access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
            if not access_key or not secret_key:
                raise EnvironmentError(
                    "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set for Bedrock mode."
                )
            self._bedrock_client = AsyncAnthropicBedrock(
                aws_access_key=access_key,
                aws_secret_key=secret_key,
                aws_region=self._bedrock_region,
            )
        return self._bedrock_client

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI  # noqa: PLC0415
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise EnvironmentError("OPENAI_API_KEY not set. Export it or add to .env.")
            self._openai_client = AsyncOpenAI(api_key=api_key)
        return self._openai_client

    def _get_gemini_client(self):
        if self._gemini_client is None:
            import google.generativeai as genai  # noqa: PLC0415
            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                raise EnvironmentError("GOOGLE_API_KEY not set. Export it or add to .env.")
            genai.configure(api_key=api_key)
            self._gemini_client = genai
        return self._gemini_client
