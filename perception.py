"""Camera perception for one run: which fixed announcement to make, and when the camera forces a person.

The camera can only add caution. A run's plan is never widened by what the camera sees:
  - a perceived `liquid` or `unsupported_hazard` that differs from the run's mess type sends the run to a person;
  - every other perceived class leaves the plan exactly as the operator and the rules set it.
No perception (no camera, no provider, or a failed look) changes nothing and announces nothing.
"""
from __future__ import annotations

from typing import Callable, Optional

# Camera classes that force a human hand-over when the run's mess type is different.
FORCE_HUMAN = frozenset({"liquid", "unsupported_hazard"})

# Classes that have a fixed announcement (announce.TEMPLATES keys are built from these).
ANNOUNCED = frozenset({"dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard"})


def forced_human(run_mess: str, perceived: Optional[str]) -> bool:
    """True when the camera's class must send this run to a person."""
    return perceived in FORCE_HUMAN and perceived != run_mess


def template_key(perceived: Optional[str], forced: bool) -> Optional[str]:
    """Which fixed announcement to make, or None when nothing was perceived."""
    if perceived is None:
        return None
    if perceived == "liquid":
        return "liquid_human" if forced else "liquid_try"
    if perceived == "unsupported_hazard":
        return "hazard_human"
    if perceived in ANNOUNCED:
        return f"{perceived}_try"
    return None


def guarded_planner(inner: Callable, forced: bool, perceived: Optional[str], run_mess: str) -> Callable:
    """Planner for this run. When forced, it returns a human hand-over and never calls `inner`
    (so no LLM call and no rules ladder step can run). Otherwise it is `inner`, unchanged."""
    if not forced:
        return inner
    reason = f"camera saw {perceived}, run mess is {run_mess}: human only"

    def planner(mess_type, sensitive, ctx):  # same call shape as rules_planner
        return {"mess_type": mess_type, "action": "human",
                "sensitive_surface": sensitive, "reason": reason}

    return planner
