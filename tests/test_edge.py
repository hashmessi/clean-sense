"""Edge vote, cautious fusion, offline runs and the V7 temperature rule. No hardware."""
import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import AuditLog, Scenario, run  # noqa: E402
from edge import MIN_CONF, EdgeLink, EdgeVote, fuse, parse_edge_vote  # noqa: E402
from eval_table import table  # noqa: E402
from rig import FakeRig  # noqa: E402
from rules import LADDERS, TEMP_LIMIT_C, Context, validate  # noqa: E402

DRY = Scenario("dry", "dry_debris", sensitive=False)
CLEAN_L1 = {"mess_type": "dry_debris", "action": "clean", "rung": "L1"}


# ---------- parsing ---------------------------------------------------------
@pytest.mark.parametrize("msg", [
    None, "dry", [], {"evt": "mass"}, {"evt": "edge_vote", "cls": "lava", "conf": 0.9},
    {"evt": "edge_vote", "cls": "dry", "conf": 1.5}, {"evt": "edge_vote", "cls": "dry", "conf": -0.1},
    {"evt": "edge_vote", "cls": "dry", "conf": True}, {"evt": "edge_vote", "cls": "dry", "conf": "0.9"},
    {"evt": "edge_vote", "cls": "dry"},
])
def test_bad_votes_are_no_vote(msg):
    assert parse_edge_vote(msg) is None


def test_valid_vote_parses():
    assert parse_edge_vote({"evt": "edge_vote", "cls": "liquid", "conf": 0.8}) == EdgeVote("liquid", 0.8)


class Lines:
    def __init__(self, lines, boom=False):
        self._lines, self._boom = list(lines), boom

    def readline(self):
        if self._boom:
            raise OSError("unplugged")
        return self._lines.pop(0) if self._lines else ""


def _line(cls, conf):
    return json.dumps({"evt": "edge_vote", "cls": cls, "conf": conf})


def test_link_returns_latest_valid_vote_and_skips_noise():
    link = EdgeLink(Lines(["not json", json.dumps({"evt": "mass", "g": 1}), _line("dry", 0.9),
                           "{bad", _line("liquid", 0.8)]))
    assert link.read_vote() == EdgeVote("liquid", 0.8)


def test_link_silence_and_errors_are_no_vote():
    assert EdgeLink(Lines([])).read_vote() is None
    assert EdgeLink(Lines([], boom=True)).read_vote() is None


def test_link_accepts_bytes_from_pyserial():
    assert EdgeLink(Lines([_line("hard", 0.95).encode()])).read_vote() == EdgeVote("hard", 0.95)


# ---------- cautious fusion --------------------------------------------------
OPERATORS = list(LADDERS)
VOTES = [None] + [EdgeVote(c, k) for c in ("dry", "liquid", "hard", "none") for k in (0.3, MIN_CONF, 0.99)]


@pytest.mark.parametrize("operator,vote", list(itertools.product(OPERATORS, VOTES)))
def test_fusion_never_adds_rungs(operator, vote):
    fused, _ = fuse(operator, vote)
    assert set(LADDERS[fused]) <= set(LADDERS[operator])


def test_fusion_cases():
    assert fuse("dry_debris", EdgeVote("dry", 0.9))[0] == "dry_debris"
    assert fuse("dry_debris", EdgeVote("liquid", 0.9))[0] == "unknown"
    assert fuse("liquid", EdgeVote("hard", 0.9))[0] == "unsupported_hazard"
    assert fuse("dry_debris", EdgeVote("hard", 0.5))[0] == "dry_debris"  # weak vote ignored
    assert fuse("mixed", EdgeVote("none", 0.99))[0] == "mixed"
    assert fuse("unsupported_hazard", EdgeVote("dry", 0.99))[0] == "unsupported_hazard"


def test_offline_fusion_uses_vote_or_unknown():
    assert fuse(None, EdgeVote("dry", 0.9))[0] == "dry_debris"
    assert fuse(None, EdgeVote("liquid", 0.9))[0] == "liquid"
    assert fuse(None, EdgeVote("hard", 0.9))[0] == "unsupported_hazard"
    assert fuse(None, EdgeVote("dry", 0.2))[0] == "unknown"
    assert fuse(None, None)[0] == "unknown"


# ---------- offline runs through the real loop (SIMULATED) -------------------
def _offline(vote, residue=10.0, seed=1):
    mess, _ = fuse(None, vote)
    rig = FakeRig(residue_g=residue, seed=seed)
    return rig, run(Scenario(f"{mess}_on_glass", mess, False), rig)  # no planner given: rules only


def test_offline_dry_vote_completes_with_no_llm():
    rig, res = _offline(EdgeVote("dry", 0.9))
    assert res.outcome == "done" and rig.sweeps


def test_offline_hard_vote_moves_nothing():
    rig, res = _offline(EdgeVote("hard", 0.95))
    assert res.outcome == "human" and rig.sweeps == []


def test_offline_without_any_vote_stays_gentle_then_human():
    rig, res = _offline(None)
    assert res.outcome == "human"
    assert set(rig.sweeps) == {"L1"}


# ---------- V7 temperature ----------------------------------------------------
@pytest.mark.parametrize("temp", [TEMP_LIMIT_C + 0.1, 90.0, None, float("nan")])
def test_hot_missing_or_nan_temperature_blocks_clean(temp):
    d = validate(CLEAN_L1, Context(temp_required=True, temp_c=temp))
    assert not d.ok and d.reasons[0].startswith("V7")


def test_temperature_ok_and_human_always_allowed():
    assert validate(CLEAN_L1, Context(temp_required=True, temp_c=25.0)).ok
    assert validate(CLEAN_L1, Context(temp_required=True, temp_c=TEMP_LIMIT_C)).ok
    hot_human = {"mess_type": "liquid", "action": "human"}
    assert validate(hot_human, Context(temp_required=True, temp_c=99.0)).ok


def test_no_probe_configured_means_rule_not_applied():
    assert validate(CLEAN_L1, Context()).ok


def test_hot_run_moves_nothing_and_logs_why():
    rig = FakeRig(residue_g=10.0, seed=1)
    res = run(DRY, rig, temp_source=lambda: 75.0)
    assert res.outcome == "human" and rig.sweeps == []
    assert any(str(e.get("reasons", "")).find("V7") >= 0 for e in res.events if e["voice"] == "decided")


def test_probe_failure_fails_closed():
    def boom():
        raise OSError("probe unplugged")

    rig = FakeRig(residue_g=10.0, seed=1)
    assert run(DRY, rig, temp_source=boom).outcome == "human" and rig.sweeps == []


def test_temperature_rise_mid_run_stops_further_sweeps():
    temps = iter([25.0, 60.0, 60.0, 60.0])
    rig = FakeRig(residue_g=10.0, seed=1)
    res = run(DRY, rig, temp_source=lambda: next(temps))
    assert res.outcome == "human" and len(rig.sweeps) == 1


def test_normal_temperature_run_completes():
    rig = FakeRig(residue_g=10.0, seed=1)
    assert run(DRY, rig, temp_source=lambda: 25.0).outcome == "done"


# ---------- results table knows edge/offline runs ----------------------------
def test_table_reports_offline_runs(tmp_path):
    p = tmp_path / "log.jsonl"
    for seed in range(2):
        mess, note = fuse(None, EdgeVote("dry", 0.9))
        log = AuditLog(p)
        log.write("edge", offline=True, operator=None, vote={"cls": "dry", "conf": 0.9}, fused=mess, note=note)
        run(Scenario("dry", mess, False), FakeRig(residue_g=10.0, seed=seed), log=log)
    # a normal run after them in the same file must not inherit the edge flag
    run(DRY, FakeRig(residue_g=10.0, seed=5), log=AuditLog(p))
    t = table([p])
    assert t["runs"] == 3 and t["offline_runs"] == 2 and t["edge_runs"] == 2
    assert t["offline_outcomes"] == {"done": 2}
