"""Run the Gate 1 simulation: 10 seeded runs per scenario, rules-only, no LLM.

    python3 demo.py
"""
from collections import Counter

from app import Scenario, run
from rig import FakeRig

SCENARIOS = [
    (Scenario("dry_debris_on_glass", "dry_debris", sensitive=False), 10.0),
    (Scenario("liquid_on_sensitive_wood", "liquid", sensitive=True), 8.0),
]

for scenario, residue in SCENARIOS:
    outcomes, attempts, residues = Counter(), Counter(), []
    for seed in range(10):
        res = run(scenario, FakeRig(residue_g=residue, seed=seed, camera_error=0.02))
        outcomes[res.outcome] += 1
        attempts[res.attempts] += 1
        residues.append(res.end_g)
    print(f"{scenario.name}: {dict(outcomes)}  attempts={dict(attempts)}  "
          f"max_residue_g={max(residues):.2f}  (SIMULATED)")
