"""Planners. Each takes (mess_type, sensitive, ctx) and returns a raw plan dict.

rules_planner is the offline fallback AND the ablation baseline. It gets the
same non-LLM inputs as the AI planner, so the comparison is fair.
"""
from __future__ import annotations

from rules import Context, next_rung


def rules_planner(mess_type: str, sensitive: bool, ctx: Context) -> dict:
    rung = next_rung(mess_type, ctx.last_rung)
    if rung is None:
        return {"mess_type": mess_type, "action": "human",
                "sensitive_surface": sensitive, "reason": "no ladder"}
    return {"mess_type": mess_type, "action": "clean", "rung": rung,
            "sensitive_surface": sensitive, "reason": "rules ladder"}
