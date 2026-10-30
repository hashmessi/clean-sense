"""Gate 1: end-to-end on the fake rig."""
import pytest

from app import AuditLog, Scenario, run
from rig import FakeRig
from rules import Context, validate

DRY = Scenario("dry_on_glass", "dry_debris", sensitive=False)
LIQUID = Scenario("liquid_on_wood", "liquid", sensitive=True)


def garbage_planner(mess, sensitive, ctx):
    return {"mess_type": mess, "action": "clean", "rung": "L2",
            "sensitive_surface": sensitive}  # always wants the aggressive rung


def exploding_planner(mess, sensitive, ctx):
    raise TimeoutError("API down")


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("scenario,residue", [(DRY, 10.0), (LIQUID, 8.0)])
def test_gate1_ten_runs_per_scenario_end_verified_or_human(seed, scenario, residue):
    rig = FakeRig(residue_g=residue, seed=seed, camera_error=0.02)
    res = run(scenario, rig)
    assert res.outcome in {"done", "human"}
    if res.outcome == "done":
        assert res.end_g <= 0.5 and res.end_g <= 0.03 * res.start_g + 0.5
    assert res.attempts <= 3


@pytest.mark.parametrize("scenario,residue", [(DRY, 10.0), (LIQUID, 8.0)])
def test_rules_only_completes_clean_path(scenario, residue):
    res = run(scenario, FakeRig(residue_g=residue, seed=1))
    assert res.outcome == "done"


def test_bad_llm_plans_are_rejected_then_rules_take_over():
    rig = FakeRig(residue_g=10.0, seed=2)
    res = run(DRY, rig, planner=garbage_planner)
    planner_decisions = [e for e in res.events if e["voice"] == "decided" and e.get("source") == "planner"]
    assert planner_decisions[0]["ok"] is False  # first L2 proposal skips the ladder
    assert res.outcome == "done"
    # hardware only ever did rungs the validator allowed, starting from L1
    assert rig.sweeps[0] == "L1"


def test_api_down_still_completes_offline():
    res = run(DRY, FakeRig(residue_g=10.0, seed=3), planner=exploding_planner)
    assert res.outcome == "done"


def test_sensitive_surface_never_gets_L2():
    rig = FakeRig(residue_g=8.0, seed=4)
    run(LIQUID, rig, planner=garbage_planner)
    assert "L2" not in rig.sweeps


def test_cancel_before_motion_moves_nothing():
    rig = FakeRig(residue_g=10.0, seed=5, cancel_at_countdown=True)
    res = run(DRY, rig)
    assert res.outcome == "cancelled" and rig.sweeps == []


def test_estop_aborts_and_latches():
    rig = FakeRig(residue_g=10.0, seed=6, estop_on_sweep=True)
    res = run(DRY, rig)
    assert res.outcome == "aborted" and rig.estop_latched


def test_already_clean_moves_nothing():
    rig = FakeRig(residue_g=0.2, seed=7)
    res = run(DRY, rig)
    assert res.outcome == "done" and rig.sweeps == []


def test_three_voices_are_logged(tmp_path):
    log = AuditLog(tmp_path / "run.jsonl")
    run(DRY, FakeRig(residue_g=10.0, seed=8), log=log)
    voices = {e["voice"] for e in log.events}
    assert {"sensor", "proposed", "decided", "allowed"} <= voices
    assert (tmp_path / "run.jsonl").read_text().count("\n") == len(log.events)
