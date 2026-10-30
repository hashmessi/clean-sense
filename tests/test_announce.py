"""Camera perception and spoken announcements.

Covers every row of the decision table, the rule that the camera can only add caution, the fixed
speech path (fake speaker, never audio hardware), and what reaches the audit log. No provider calls.
"""
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from announce import ANNOUNCE_WAIT_S, TEMPLATES, Announcer, NullSpeaker, announce_for_run, speaker_from_env
from app import AuditLog, Scenario, run, verify_log
from perception import ANNOUNCED, FORCE_HUMAN, forced_human, guarded_planner, template_key
from planner import rules_planner
from rig import FakeRig

DRY = Scenario("dry_debris_on_glass", "dry_debris", False)


class FakeSpeaker:
    def __init__(self, fail=None):
        self.said = []
        self.fail = fail

    def speak(self, text):
        if self.fail is not None:
            raise self.fail
        self.said.append(text)


# --- decision table: (run mess, camera class) -> forced human, announcement key ---------------------

@pytest.mark.parametrize("run_mess,perceived,forced,key", [
    ("dry_debris", "liquid", True, "liquid_human"),      # operator dry, camera liquid: human only
    ("mixed", "liquid", True, "liquid_human"),
    ("unknown", "liquid", True, "liquid_human"),
    ("liquid", "liquid", False, "liquid_try"),           # agreed: gentle liquid clean runs
    ("liquid", "dry_debris", False, "dry_debris_try"),   # camera milder: plan unchanged
    ("dry_debris", "dry_debris", False, "dry_debris_try"),
    ("dry_debris", "mixed", False, "mixed_try"),
    ("dry_debris", "unknown", False, "unknown_try"),
    ("dry_debris", "unsupported_hazard", True, "hazard_human"),
    ("liquid", "unsupported_hazard", True, "hazard_human"),
    ("unsupported_hazard", "unsupported_hazard", False, "hazard_human"),
])
def test_decision_table(run_mess, perceived, forced, key):
    assert forced_human(run_mess, perceived) is forced
    assert template_key(perceived, forced_human(run_mess, perceived)) == key


def test_no_perception_means_no_caution_and_no_announcement():
    assert forced_human("dry_debris", None) is False
    assert template_key(None, False) is None


def test_camera_never_forces_on_a_match_or_a_milder_class():
    for run_mess in ANNOUNCED:
        for perceived in ANNOUNCED:
            if perceived in FORCE_HUMAN and perceived != run_mess:
                assert forced_human(run_mess, perceived)
            else:
                assert not forced_human(run_mess, perceived)


def test_every_announced_class_has_a_fixed_template():
    for perceived in ANNOUNCED:
        for forced in (False, True):
            key = template_key(perceived, forced)
            assert key in TEMPLATES, (perceived, forced)
    for text in TEMPLATES.values():
        assert "{" not in text and "}" not in text  # fixed text, no interpolation


def test_liquid_messages_match_the_plan():
    assert TEMPLATES["liquid_try"].endswith("The robot will try a gentle clean.")
    assert TEMPLATES["liquid_human"].endswith("A person will clean it.")
    assert TEMPLATES["hazard_human"].endswith("A person will handle it.")


# --- planner guard: when forced, the LLM and the rules ladder never run -----------------------------

def test_forced_planner_returns_human_and_never_calls_inner():
    calls = []

    def inner(*args):
        calls.append(args)
        return {"mess_type": "dry_debris", "action": "clean", "rung": "L1",
                "sensitive_surface": False, "reason": "should not run"}

    plan = guarded_planner(inner, True, "liquid", "dry_debris")("dry_debris", False, None)
    assert calls == []
    assert plan["action"] == "human"
    assert "camera saw liquid" in plan["reason"]


def test_unforced_planner_is_the_inner_planner():
    def inner(*args):
        return {}

    assert guarded_planner(inner, False, "liquid", "liquid") is inner


def test_forced_run_makes_no_sweep_and_ends_with_a_person():
    rig = FakeRig(residue_g=10.0, seed=1)
    planner = guarded_planner(rules_planner, True, "liquid", "dry_debris")
    res = run(DRY, rig, planner=planner, log=AuditLog())
    assert res.outcome == "human"
    assert rig.sweeps == []


def test_unforced_run_still_sweeps():
    rig = FakeRig(residue_g=10.0, seed=1)
    planner = guarded_planner(rules_planner, False, "dry_debris", "dry_debris")
    run(DRY, rig, planner=planner, log=AuditLog())
    assert rig.sweeps  # the existing rules ladder is unchanged


# --- speech: fixed text only, bounded wait, errors recorded not raised ------------------------------

def test_announcer_speaks_the_template_text_only():
    sp = FakeSpeaker()
    a = Announcer(sp)
    a.announce("liquid_try")
    assert a.wait(5) is True
    assert sp.said == [TEMPLATES["liquid_try"]]


def test_unknown_key_fails_fast():
    with pytest.raises(KeyError):
        Announcer(FakeSpeaker()).announce("model_says_something")


def test_speaker_error_is_recorded_not_raised():
    a = Announcer(FakeSpeaker(fail=OSError("no audio device")))
    a.announce("dry_debris_try")
    assert a.wait(5) is True
    assert a.error == "OSError"


def test_wait_times_out_without_raising():
    class Slow:
        def speak(self, text):
            time.sleep(0.4)

    a = Announcer(Slow())
    a.announce("hazard_human")
    assert a.wait(0.01) is False
    assert a.wait(3) is True  # let it finish so the thread does not outlive the test


def test_announce_for_run_logs_key_only_not_the_sentence(tmp_path):
    path = tmp_path / "a.jsonl"
    announce_for_run("liquid_try", FakeSpeaker(), AuditLog(path), wait_s=ANNOUNCE_WAIT_S)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["voice"] == "announced" and ev["key"] == "liquid_try" and ev["spoken"] is True
    assert TEMPLATES["liquid_try"] not in lines[0]
    assert verify_log(path) == (True, None)


def test_announce_for_run_with_no_key_writes_nothing(tmp_path):
    path = tmp_path / "b.jsonl"
    announce_for_run(None, FakeSpeaker(), AuditLog(path))
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_announce_for_run_logs_speaker_failure(tmp_path):
    path = tmp_path / "c.jsonl"
    announce_for_run("dry_debris_try", FakeSpeaker(fail=RuntimeError("boom")), AuditLog(path))
    events = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert [e["voice"] for e in events] == ["announced", "announce_failed"]
    assert events[1]["reason"] == "RuntimeError"


def test_silent_speaker_logs_spoken_false(tmp_path):
    path = tmp_path / "d.jsonl"
    announce_for_run("liquid_human", NullSpeaker(), AuditLog(path))
    ev = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert ev["spoken"] is False


# --- speaker selection: silent unless fully configured ----------------------------------------------

def test_no_voice_configured_is_silent():
    sp, note = speaker_from_env({})
    assert isinstance(sp, NullSpeaker)
    assert "PIPER_VOICE" in note


def test_missing_voice_file_is_silent(tmp_path):
    sp, _ = speaker_from_env({"PIPER_VOICE": str(tmp_path / "missing.onnx")})
    assert isinstance(sp, NullSpeaker)


def test_missing_piper_binary_is_silent(tmp_path):
    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"")
    sp, note = speaker_from_env({"PIPER_VOICE": str(voice), "PIPER_CMD": "no-such-piper-binary-xyz"})
    assert isinstance(sp, NullSpeaker)
    assert "PIPER_CMD" in note


# --- camera perception step: one frame, one call, failures change nothing ---------------------------

class _FakeCam:
    def grab_bgr(self):
        return np.zeros((4, 4, 3), dtype=np.uint8)


class _FakeTransport:
    name = "fake"

    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return SimpleNamespace(text=self._text)


def _llm(transport):
    return SimpleNamespace(transports=[transport])


def test_perceive_logs_the_class_and_provider(tmp_path):
    from run_rig import _perceive

    t = _FakeTransport(text='{"mess_type": "liquid", "confidence": 0.91}')
    path = tmp_path / "p.jsonl"
    got = _perceive(_FakeCam(), _llm(t), AuditLog(path))
    assert got == "liquid"
    assert t.calls == 1  # exactly one request, no retry
    ev = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert ev["voice"] == "perceived" and ev["provider"] == "fake" and ev["mess_type"] == "liquid"


def test_perceive_failure_returns_none_and_logs_error_class(tmp_path):
    from planner_llm import PlannerUnavailable
    from run_rig import _perceive

    t = _FakeTransport(error=PlannerUnavailable("timeout", reason="timeout"))
    path = tmp_path / "q.jsonl"
    assert _perceive(_FakeCam(), _llm(t), AuditLog(path)) is None
    ev = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert ev["voice"] == "perceived" and ev["error"] == "PlannerUnavailable"
    assert "mess_type" not in ev


def test_perceive_invalid_reply_returns_none(tmp_path):
    from run_rig import _perceive

    t = _FakeTransport(text='{"mess_type": "coffee", "confidence": 0.9}')  # not a known class
    assert _perceive(_FakeCam(), _llm(t), AuditLog(tmp_path / "r.jsonl")) is None


# --- dashboard tolerates the new events and keeps its verdict ----------------------------------------

def test_dashboard_ignores_new_events_without_crashing():
    from ui import DashboardState

    st = DashboardState(simulated=False)
    for ev in ({"voice": "perceived", "mess_type": "liquid", "provider": "fake"},
               {"voice": "announced", "key": "liquid_human", "spoken": True},
               {"voice": "announce_failed", "reason": "OSError"},
               {"voice": "speaker", "spoken": False, "note": "no voice"}):
        st.on_event(ev)
    assert st.verdict != "clean"  # new events never claim a clean run


def test_any_unexpected_camera_failure_returns_none(tmp_path):
    from run_rig import _perceive

    class BrokenCam:
        def grab_bgr(self):
            raise ValueError("driver fault")

    path = tmp_path / "s.jsonl"
    assert _perceive(BrokenCam(), _llm(_FakeTransport(text="{}")), AuditLog(path)) is None
    ev = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert ev["error"] == "ValueError"
