"""Deterministic safety core. Pure functions only: no I/O, no model calls.

The LLM (or rules planner) proposes a plan dict. validate() decides whether
it may reach the hardware. verify() decides whether the mess is gone. Any
exception inside validate() is a rejection (fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

MessType = Literal["dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard"]
Action = Literal["clean", "human"]
Rung = Literal["L1", "L2", "L3"]
Speed = Literal["slow", "med"]
Area = Literal["narrow", "full"]
Pressure = Literal["light", "normal"]

# Ladder per mess type. Escalate along the list; the top rung may be retried.
LADDERS: dict[str, list[str]] = {
    "dry_debris": ["L1", "L2"],
    "liquid": ["L1", "L3"],
    "mixed": ["L1", "L2", "L3"],
    "unknown": ["L1"],
    "unsupported_hazard": [],
}

# Parameters are fixed per rung. The planner picks a rung, never raw params.
RUNG_PARAMS: dict[str, dict] = {
    "L1": {"passes": 1, "speed": "slow", "area": "narrow", "pressure": "light"},
    "L2": {"passes": 3, "speed": "med", "area": "full", "pressure": "normal"},
    "L3": {"passes": 1, "speed": "slow", "area": "full", "pressure": "light"},
}

# Intensity order for reduce-only overrides (higher = more aggressive).
INTENSITY = {"L1": 1, "L3": 2, "L2": 3}

MAX_ATTEMPTS = 3
MAX_LLM_CALLS = 12
RESIDUE_ABS_G = 0.5
RESIDUE_FRAC = 0.03
TEMP_LIMIT_C = 40.0  # BENCH PLACEHOLDER: set from the probe and a safe margin before any real use


class Plan(BaseModel):
    """Strict schema. Extra keys are rejected, so a model cannot smuggle fields."""

    model_config = {"extra": "forbid"}

    mess_type: MessType
    action: Action
    rung: Optional[Rung] = None
    sensitive_surface: bool = False
    reason: str = Field(default="", max_length=200)  # logged only, never parsed


@dataclass(frozen=True)
class Context:
    attempts_done: int = 0
    llm_calls_used: int = 0
    last_rung: Optional[str] = None
    sensitive_surface: bool = False
    override_from_rung: Optional[str] = None  # set when user overrides
    temp_c: Optional[float] = None  # latest probe reading, if a probe is configured
    temp_required: bool = False  # True when a probe is configured: no reading => no motion


@dataclass(frozen=True)
class Decision:
    ok: bool
    plan: Optional[Plan]
    reasons: tuple[str, ...]


def next_rung(mess_type: str, last_rung: Optional[str]) -> Optional[str]:
    """Next rung on the ladder. Top rung repeats on retry. None if no ladder."""
    ladder = LADDERS.get(mess_type, [])
    if not ladder:
        return None
    if last_rung is None or last_rung not in ladder:
        return ladder[0]
    idx = ladder.index(last_rung)
    return ladder[min(idx + 1, len(ladder) - 1)]


def _reject(reasons: list[str]) -> Decision:
    return Decision(ok=False, plan=None, reasons=tuple(reasons))


def _validate(raw: object, ctx: Context) -> Decision:
    reasons: list[str] = []

    # V1 schema and enums
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        return _reject([f"V1 schema: {exc.errors()[0]['msg']}"])

    # Human is always allowed: it is the safe exit.
    if plan.action == "human":
        return Decision(ok=True, plan=plan, reasons=("human: always allowed",))

    # V7 temperature: too hot, unreadable or NaN means no motion (fail-closed)
    if ctx.temp_required:
        if not (ctx.temp_c is not None and ctx.temp_c <= TEMP_LIMIT_C):
            return _reject([f"V7 temperature: {ctx.temp_c} C not within limit {TEMP_LIMIT_C} C, human only"])

    # V5 budget
    if ctx.attempts_done >= MAX_ATTEMPTS:
        return _reject(["V5 budget: attempts exhausted, must go to human"])
    if ctx.llm_calls_used > MAX_LLM_CALLS:
        return _reject(["V5 budget: LLM call budget exhausted"])

    # V4 hazard / unknown
    if plan.mess_type == "unsupported_hazard":
        return _reject(["V4 hazard: unsupported material, human only"])

    # V2 rung must be the next rung on this mess type's ladder
    if plan.rung is None:
        return _reject(["V2 ladder: clean action needs a rung"])
    expected = next_rung(plan.mess_type, ctx.last_rung)
    if expected is None or plan.rung != expected:
        return _reject([f"V2 ladder: rung {plan.rung} is not next (expected {expected})"])

    # V3 sensitive surface: light pressure only, no L2
    sensitive = ctx.sensitive_surface or plan.sensitive_surface
    if sensitive:
        if RUNG_PARAMS[plan.rung]["pressure"] != "light" or plan.rung == "L2":
            return _reject([f"V3 sensitive surface: rung {plan.rung} not allowed"])

    # V6 override may only reduce intensity
    if ctx.override_from_rung is not None:
        if INTENSITY[plan.rung] > INTENSITY[ctx.override_from_rung]:
            return _reject(["V6 override: would increase intensity"])

    reasons.append(f"ok: {plan.mess_type} rung {plan.rung}")
    return Decision(ok=True, plan=plan, reasons=tuple(reasons))


def validate(raw: object, ctx: Context) -> Decision:
    """Fail-closed wrapper: any unexpected error is a rejection."""
    try:
        return _validate(raw, ctx)
    except Exception as exc:  # noqa: BLE001 - deliberate fail-closed
        return _reject([f"V0 internal error, rejected: {type(exc).__name__}"])


class Verdict(str, Enum):
    DONE = "done"
    RETRY = "retry"
    HUMAN = "human"


def verify(start_g: float, end_g: float, camera_clean: bool, attempts_done: int) -> Verdict:
    """Scale decides. Camera must agree. Model never declares clean."""
    if end_g < 0 or start_g < 0:
        return Verdict.HUMAN  # nonsense reading: never guess
    if start_g < RESIDUE_ABS_G:
        scale_clean = True  # already clean on the scale (C8 shortcut)
    else:
        residue_limit = min(RESIDUE_ABS_G, RESIDUE_FRAC * start_g)
        scale_clean = end_g <= residue_limit
    if scale_clean and camera_clean:
        return Verdict.DONE
    if attempts_done >= MAX_ATTEMPTS:
        return Verdict.HUMAN
    return Verdict.RETRY
