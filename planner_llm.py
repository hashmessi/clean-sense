"""LLM planner: proposes one plan as a JSON object. It never acts.

HTTP and provider choice live in llm_transport.py. This module owns the contract:
prompt, output schema, validation, retries, the call budget and the circuit breaker.
Output here is only a proposal. app.run() sends it through rules.validate().

Keys come from the environment (or the local .env). With no provider configured,
every call raises PlannerUnavailable and the breaker drops the run to rules (AI OFFLINE).
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llm_transport import (DEFAULT_ANTHROPIC_MODEL, AnthropicTransport, NotConfigured,
                           Reply, TransportError, build_transports, load_env_file)
from rules import LADDERS, Context, next_rung

DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL
TIMEOUT_S = 8.0                  # wall-clock budget for one planning call, its retries included
BREAKER_THRESHOLD = 2
MAX_RETRIES_PER_PROVIDER = 2     # transient errors only: timeout, 429, 5xx
BACKOFF_S = (0.5, 1.5)
MAX_TOKENS = 1024                # headroom for reasoning tokens on some Gemini models
# Hard cap on HTTP calls per run, counting repairs, retries and fail-overs. Refused, never sent.
MAX_HTTP_CALLS_PER_RUN = 12

SYSTEM_PROMPT = """You plan ONE cleaning step for a fixed rig. You do not move anything.
Reply with ONLY one JSON object, no prose, with exactly these keys:
  "mess_type": one of dry_debris, liquid, mixed, unknown, unsupported_hazard
  "action": "clean" or "human"
  "rung": "L1", "L2", "L3" or null
  "sensitive_surface": true or false
  "reason": plain words, at most 200 characters
Rules you must follow:
- Pick the rung that is NEXT on the ladder you are given. Never skip ahead.
- If the surface is sensitive, never choose L2.
- If the material is hazardous, unknown or you are unsure, use "action": "human" with rung null.
- Never claim the surface is clean. Clean is decided by the scale, not by you."""

MESS_TYPE_VALUES = ("dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard")

# Standard JSON Schema. Sent to OpenRouter as-is; converted for Gemini in llm_transport.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "mess_type": {"type": "string", "enum": list(MESS_TYPE_VALUES)},
        "action": {"type": "string", "enum": ["clean", "human"]},
        "rung": {"type": ["string", "null"], "enum": ["L1", "L2", "L3", None]},
        "sensitive_surface": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
    },
    "required": ["mess_type", "action", "rung", "sensitive_surface", "reason"],
    "additionalProperties": False,
}


class PlanReply(BaseModel):
    """The only shape a plan proposal may take. Extra keys are refused."""
    model_config = ConfigDict(extra="forbid")
    mess_type: Literal["dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard"]
    action: Literal["clean", "human"]
    rung: Optional[Literal["L1", "L2", "L3"]]
    sensitive_surface: bool
    reason: str = Field(max_length=200)


def _tok(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def latency_summary(entries: list[dict]) -> dict:
    """p50 and p95 by nearest rank over successful calls, plus totals. No guessing."""
    ok = sorted(e["ms"] for e in entries if e.get("ok"))

    def pct(q: float):
        if not ok:
            return None
        return ok[min(len(ok) - 1, max(0, math.ceil(q * len(ok)) - 1))]

    return {"calls": len(entries), "errors": sum(not e.get("ok") for e in entries),
            "p50_ms": pct(0.50), "p95_ms": pct(0.95),
            "in_tokens": sum(e.get("in_tok", 0) for e in entries),
            "out_tokens": sum(e.get("out_tok", 0) for e in entries)}


class PlannerUnavailable(Exception):
    """No provider, circuit open, call budget spent, or every provider failed.

    `reason` is a short code for the log and dashboard: not_configured, rate_limited,
    timeout, auth_error, provider_error, blocked, truncated, bad_request, cap, unavailable.
    """

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class PlannerError(Exception):
    """The model replied, but not with a usable plan."""
    reason = "invalid_output"


def parse_plan_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise PlannerError("no JSON object in reply")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise PlannerError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise PlannerError("reply is not a JSON object")
    return obj


def validate_plan_text(text: str) -> dict:
    """Parse, then check the exact plan shape. Rules validation still runs in app.py."""
    obj = parse_plan_json(text)
    try:
        return PlanReply.model_validate(obj).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "reply"
        raise PlannerError(f"schema: {where} {first.get('msg', 'invalid')}") from exc


def build_user_text(mess_type: str, sensitive: bool, ctx: Context) -> str:
    nxt = next_rung(mess_type, ctx.last_rung)
    return (
        f"Operator says the mess is: {mess_type}\n"
        f"Surface sensitive to moisture: {str(sensitive).lower()}\n"
        f"Ladder for this mess type: {LADDERS.get(mess_type, [])}\n"
        f"Next allowed rung: {nxt}\n"
        f"Attempts used so far: {ctx.attempts_done} of 3\n"
        "Reply with the JSON object only."
    )


class LLMPlanner:
    def __init__(self, client=None, model: Optional[str] = None,
                 timeout_s: float = TIMEOUT_S,
                 frame_png: Optional[Callable[[], Optional[bytes]]] = None,
                 max_http_calls: int = MAX_HTTP_CALLS_PER_RUN,
                 transports: Optional[list] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.model = model or os.environ.get("CLEANSENSE_MODEL", DEFAULT_MODEL)
        if transports is None:
            transports = [AnthropicTransport(client=client, model=self.model)] if client is not None else []
        self.transports = list(transports)
        self.timeout_s = timeout_s
        self.frame_png = frame_png
        self.max_http_calls = max_http_calls
        self._sleep = sleep
        self.calls = 0
        self.calls_log: list[dict] = []  # one entry per HTTP call: provider, latency, tokens, ok
        self.provider_used: Optional[str] = None

    @classmethod
    def from_env(cls, **kwargs) -> "LLMPlanner":
        load_env_file()
        primary = os.environ.get("LLM_PROVIDER", "")
        anthropic_model = os.environ.get("CLEANSENSE_MODEL", "").strip() or DEFAULT_MODEL
        transports = build_transports(primary, anthropic_model=anthropic_model)
        return cls(transports=transports, **kwargs)

    def _png(self) -> Optional[bytes]:
        return self.frame_png() if self.frame_png else None

    def _log(self, provider: str, t0: float, ok: bool, reply: Optional[Reply] = None) -> None:
        self.calls_log.append({
            "ms": round((time.perf_counter() - t0) * 1000, 1), "ok": ok, "provider": provider,
            "in_tok": _tok(getattr(reply, "in_tokens", 0)), "out_tok": _tok(getattr(reply, "out_tokens", 0)),
        })

    def _ask(self, text: str) -> Reply:
        """Try each provider in order. Retry only transient errors, inside one time budget."""
        if not self.transports:
            raise PlannerUnavailable(
                "no LLM provider configured (set GEMINI_API_KEY, OPENROUTER_API_KEY or ANTHROPIC_API_KEY)",
                reason="not_configured")
        deadline = time.monotonic() + self.timeout_s
        last: Optional[Exception] = None
        for transport in self.transports:
            attempt = 0
            while True:
                if self.calls >= self.max_http_calls:
                    raise PlannerUnavailable(f"HTTP call cap {self.max_http_calls} reached", reason="cap")
                remaining = deadline - time.monotonic()
                if attempt > 0 and remaining <= 0.5:
                    break
                timeout = self.timeout_s if attempt == 0 else max(1.0, remaining)
                self.calls += 1
                t0 = time.perf_counter()
                try:
                    reply = transport.complete(SYSTEM_PROMPT, text, self._png(), PLAN_SCHEMA, MAX_TOKENS, timeout)
                except NotConfigured as exc:
                    last = exc
                    break
                except TransportError as exc:
                    self._log(transport.name, t0, ok=False)
                    last = exc
                    if not exc.retryable or attempt >= MAX_RETRIES_PER_PROVIDER:
                        break
                    wait = exc.retry_after if exc.retry_after is not None else BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
                    if wait >= deadline - time.monotonic() - 0.5:
                        break  # the wait would blow the budget: fail over instead of sleeping
                    self._sleep(wait)
                    attempt += 1
                    continue
                except Exception as exc:  # unexpected: a provider failure, never a crash
                    self._log(transport.name, t0, ok=False)
                    last = exc
                    break
                self._log(transport.name, t0, ok=True, reply=reply)
                self.provider_used = transport.name
                return reply
        reason = getattr(last, "reason", "provider_error")
        raise PlannerUnavailable(f"{reason}: {last}", reason=reason)

    def __call__(self, mess_type: str, sensitive: bool, ctx: Context) -> dict:
        user = build_user_text(mess_type, sensitive, ctx)
        reply = self._ask(user)
        try:
            return validate_plan_text(reply.text)
        except PlannerError as first:
            # One repair with the validator's complaint. Transport failures are not repaired.
            reply = self._ask(user + f"\nYour last reply was invalid ({first}). Reply with only the JSON object.")
            return validate_plan_text(reply.text)


class CircuitBreaker:
    """After N consecutive failures, stop calling the model for this session.

    While open, every call raises PlannerUnavailable immediately, so app.run()
    uses the rules planner. `ai_offline` drives the AI OFFLINE banner.
    """

    def __init__(self, planner: Callable, threshold: int = BREAKER_THRESHOLD) -> None:
        self._planner = planner
        self._threshold = threshold
        self._failures = 0
        self.ai_offline = False

    @property
    def provider_used(self):
        """Which provider answered last, for the log and dashboard."""
        return getattr(self._planner, "provider_used", None)

    def __call__(self, mess_type: str, sensitive: bool, ctx: Context) -> dict:
        if self.ai_offline:
            raise PlannerUnavailable("circuit open: AI offline, using rules", reason="circuit_open")
        try:
            out = self._planner(mess_type, sensitive, ctx)
        except Exception:
            self._failures += 1
            if self._failures >= self._threshold:
                self.ai_offline = True
            raise
        self._failures = 0
        return out


PERCEIVE_PROMPT = """Look at this photo of a cleaning test tile. Classify the mess.
Reply with ONLY one JSON object with exactly these keys:
  "mess_type": one of dry_debris, liquid, mixed, unknown, unsupported_hazard
  "confidence": a number from 0 to 1
Use "unknown" if you cannot tell. Use "unsupported_hazard" for anything that is not an approved test material."""

MESS_TYPES = set(MESS_TYPE_VALUES)


def perceive_photo(client, png: bytes, model: Optional[str] = None,
                   timeout_s: float = TIMEOUT_S) -> dict:
    """Ask the model to classify one photo. Returns {'mess_type', 'confidence'}.

    Still Anthropic-only: the photo accuracy gate (eval_photos.py) is not yet routed
    through llm_transport. Raises PlannerUnavailable (no client), PlannerError (bad reply).
    """
    if client is None:
        raise PlannerUnavailable("no ANTHROPIC_API_KEY set", reason="not_configured")
    import base64
    b64 = base64.standard_b64encode(png).decode("ascii")
    msg = client.messages.create(
        model=model or os.environ.get("CLEANSENSE_MODEL", DEFAULT_MODEL),
        max_tokens=120,
        system=PERCEIVE_PROMPT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "Classify this photo. JSON only."},
        ]}],
        timeout=timeout_s,
    )
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    obj = parse_plan_json(text)
    if obj.get("mess_type") not in MESS_TYPES:
        raise PlannerError(f"mess_type out of enum: {obj.get('mess_type')!r}")
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        raise PlannerError("confidence must be a number from 0 to 1")
    return {"mess_type": obj["mess_type"], "confidence": float(conf)}


# ---- photo classification through the provider layer (accuracy gate input) ----

PERCEIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "mess_type": {"type": "string", "enum": list(MESS_TYPE_VALUES)},
        "confidence": {"type": "number"},
    },
    "required": ["mess_type", "confidence"],
    "additionalProperties": False,
}


class PerceiveReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mess_type: Literal["dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard"]
    confidence: float = Field(ge=0.0, le=1.0)


def perceive_via_transports(transports: list, png: bytes, timeout_s: float = TIMEOUT_S) -> dict:
    """Classify one photo with the FIRST configured provider only: one request, no retry, no fail-over.

    An accuracy measurement must not mix providers or hide errors, so this is strict on purpose.
    Returns {'mess_type', 'confidence'}. Raises PlannerUnavailable (no provider, or the request
    failed) or PlannerError (the reply is not a valid classification).
    """
    if not transports:
        raise PlannerUnavailable("no LLM provider configured", reason="not_configured")
    transport = transports[0]
    try:
        reply = transport.complete(PERCEIVE_PROMPT, "Classify this photo. JSON only.", png,
                                   PERCEIVE_SCHEMA, 120, timeout_s)
    except NotConfigured as exc:
        raise PlannerUnavailable(str(exc), reason="not_configured") from exc
    except TransportError as exc:
        raise PlannerUnavailable(f"{exc.reason}: {exc}", reason=exc.reason) from exc
    try:
        return PerceiveReply.model_validate(parse_plan_json(reply.text)).model_dump()
    except ValidationError as exc:
        raise PlannerError(f"classification invalid: {exc.errors()[0].get('msg', 'invalid')}") from exc
