"""Tabletop industry cases for the demo. Each case is only a preset of the operator's mess type and the
surface setting that run_rig.py already takes. It adds no new motion, rung or rule: the validator, the
ladders and the firmware limits are unchanged, and the scale and camera still decide clean.

The materials named here are stand-ins for the demo table, not real industrial materials.
"""
from __future__ import annotations

from typing import Optional

# Written by run_rig.py at run start, served by the dashboard at /frame.png (one fixed name, no path input).
PERCEPTION_FRAME = "perception_latest.png"

CASES: dict[str, dict[str, str]] = {
    "pharma": {"title": "Pharma packaging", "mess": "dry_debris", "surface": "glass",
               "stand_in": "flour and lentils"},
    "battery": {"title": "Battery cell", "mess": "dry_debris", "surface": "wood",
                "stand_in": "cocoa powder; add --mess liquid for the coffee spill"},
    "smt": {"title": "Electronics SMT", "mess": "mixed", "surface": "wood",
            "stand_in": "rice, sesame and a dab of toothpaste"},
}


def case_title(key: Optional[str]) -> Optional[str]:
    """Display title for a logged case key. Unknown or missing keys give None (never raises)."""
    entry = CASES.get(key) if isinstance(key, str) else None
    return entry["title"] if entry else None


def resolve(case: Optional[str], mess: Optional[str], surface: Optional[str],
            offline: bool = False) -> tuple[Optional[str], str]:
    """Operator mess type and surface for a run. Explicit --mess / --surface always win over the case.
    In --offline mode the case never supplies a mess type (the edge vote is the only perception there)."""
    preset = CASES[case] if case else {}
    if mess is None and not offline:
        mess = preset.get("mess")
    return mess, surface or preset.get("surface") or "glass"
