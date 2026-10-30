"""Ablation: rules-only vs other planners on IDENTICAL simulated inputs (same seeds).

The rules arm is always runnable. An AI arm is passed in by the caller and is only
run when a key is present (see demo at the bottom). Every arm sees the same
FakeRig seed and the same operator mess label, so the only difference is the planner.

False clean = the run said DONE while the simulator's true residue was still above
0.5 g. Simulator truth is never shown to a planner.
"""
from __future__ import annotations

import json
from typing import Callable

from app import Scenario, run
from planner import rules_planner
from rig import FakeRig

Planner = Callable[..., dict]
FALSE_CLEAN_G = 0.5


def run_arm(scenario: Scenario, residue: float, seeds: range, planner: Planner,
            camera_error: float = 0.02) -> dict:
    outcomes, attempts, false_clean, rejections = {}, [], 0, 0
    for seed in seeds:
        rig = FakeRig(residue_g=residue, seed=seed, camera_error=camera_error)
        res = run(scenario, rig, planner=planner)
        outcomes[res.outcome] = outcomes.get(res.outcome, 0) + 1
        attempts.append(res.attempts)
        rejections += sum(1 for e in res.events if e.get("voice") == "decided" and e.get("ok") is False)
        if res.outcome == "done" and rig.true_residue_g > FALSE_CLEAN_G:
            false_clean += 1
    n = len(seeds)
    return {
        "n": n,
        "outcomes": outcomes,
        "done_rate": round(outcomes.get("done", 0) / n, 3),
        "mean_attempts": round(sum(attempts) / n, 2),
        "false_clean": false_clean,
        "validator_rejections": rejections,
    }


def compare(scenario: Scenario, residue: float, arms: dict[str, Planner],
            seeds: range = range(30)) -> dict:
    return {name: run_arm(scenario, residue, seeds, p) for name, p in arms.items()}


def detector_table(scenario: Scenario, residue: float, seeds: range,
                   planner: Planner = rules_planner, camera_error: float = 0.02) -> dict:
    """Which judge is safe to trust? Each judge answers 'clean?' at the END of a run.

    camera_only: the camera says clean.  scale_only: the scale rule says clean.
    both_agree: the shipped AND-gate. False clean = judge says clean while the
    simulator's true residue is above 0.5 g. SIMULATED.
    """
    from rules import Verdict, verify

    counts = {"camera_only": 0, "scale_only": 0, "both_agree": 0}
    n = 0
    for seed in seeds:
        rig = FakeRig(residue_g=residue, seed=seed, camera_error=camera_error)
        res = run(scenario, rig, planner=planner)
        n += 1
        truly_clean = rig.true_residue_g <= FALSE_CLEAN_G
        cam = rig.camera_clean()
        scale = verify(res.start_g, res.end_g, True, 0) is Verdict.DONE
        votes = {"camera_only": cam, "scale_only": scale, "both_agree": cam and scale}
        for name, says_clean in votes.items():
            if says_clean and not truly_clean:
                counts[name] += 1
    return {"SIMULATED": True, "n": n, "false_clean": counts}


if __name__ == "__main__":
    scenario = Scenario("dry_debris_on_glass", "dry_debris", sensitive=False)
    print(json.dumps({"scenario": scenario.name, "SIMULATED": True,
                      "arms": compare(scenario, 10.0, {"rules_only": rules_planner})}, indent=2))
    human = lambda m, sens, ctx: {"mess_type": m, "action": "human", "sensitive_surface": sens, "reason": "x"}
    print(json.dumps({"judges_end_of_run_human_stop": detector_table(scenario, 5.0, range(30), human, 0.5)}, indent=2))
    print("AI arm: not run here. Add it on the rig or with ANTHROPIC_API_KEY set (see README).")
