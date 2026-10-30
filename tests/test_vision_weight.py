"""Camera-estimated weight (ADR-003). No hardware, no provider calls: every provider answer is scripted."""
import json
import math

import pytest

from app import AuditLog, Scenario, run
from llm_transport import NotConfigured, RateLimited, Reply
from planner import rules_planner
from rig import FakeRig, Reading, RigFault
from rules import Verdict, verify, verify_vision
import vision_weight as vw


def good(grams=5.0, cov=10.0, conf=0.9):
    return {"est_grams": grams, "coverage_pct": cov, "confidence": conf}


# --- reply validation ------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {}, {"est_grams": 5}, "text", None, 7, [],
    {**good(), "extra": 1},
    good(grams=-1), good(grams=201), good(grams=float("nan")), good(grams=float("inf")),
    good(cov=-0.1), good(cov=100.1), good(cov=float("nan")),
    good(conf=-0.1), good(conf=1.1), good(conf=float("nan")),
    {"est_grams": "5", "coverage_pct": 1, "confidence": 0.9},
    {"est_grams": True, "coverage_pct": 1, "confidence": 0.9},
])
def test_hostile_replies_are_unreadable(bad):
    with pytest.raises(vw.Unreadable):
        vw.parse_reply(json.dumps(bad, allow_nan=True))


def test_non_json_text_is_unreadable():
    for text in ("", "I think about 5 grams", "```json\n[1]\n```", "{"):
        with pytest.raises(vw.Unreadable):
            vw.parse_reply(text)


def test_good_reply_parses_and_fenced_json_is_accepted():
    r = vw.parse_reply(json.dumps(good(5.5, 12.0, 0.8)))
    assert (r["est_grams"], r["coverage_pct"], r["confidence"]) == (5.5, 12.0, 0.8)
    r = vw.parse_reply("```json\n" + json.dumps(good()) + "\n```")
    assert r["est_grams"] == 5.0


# --- provider order (fake transports; nothing leaves the process) -----------------------------------

class T:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, 0

    def complete(self, system, text, png, schema, max_tokens, timeout_s):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return Reply(text=self.result)


def test_failover_to_second_provider_on_transport_error():
    a, b = T("gemini", RateLimited("429")), T("openrouter", json.dumps(good(4.0)))
    r = vw.estimate_frame([a, b], b"png", timeout_s=1)
    assert r["est_grams"] == 4.0 and a.calls == 1 and b.calls == 1


def test_not_configured_and_all_failed_are_unreadable():
    with pytest.raises(vw.Unreadable):
        vw.estimate_frame([], b"png", timeout_s=1)
    with pytest.raises(vw.Unreadable):
        vw.estimate_frame([T("a", NotConfigured("no key")), T("b", RateLimited("x"))], b"png", timeout_s=1)


def test_invalid_reply_from_first_provider_tries_the_next():
    a, b = T("a", "not json"), T("b", json.dumps(good(3.0)))
    assert vw.estimate_frame([a, b], b"png", timeout_s=1)["est_grams"] == 3.0


# --- one look: 3 frames, median, agreement, confidence, call cap -------------------------------------

def looker(replies, scale=1.0, max_calls=30, frames=3, grab=None):
    it = iter(replies)
    return vw.VisionLooker(grab or (lambda: b"png"), [], scale=scale, frames=frames, max_calls=max_calls,
                           ask=lambda transports, png, timeout_s: next(it))


def test_look_is_the_median_times_scale():
    lk = looker([good(5.0, 10), good(5.4, 12), good(4.8, 11)], scale=1.5)
    look = lk.look()
    assert look.grams == pytest.approx(5.0 * 1.5)
    assert look.coverage_pct == 11 and look.confidence == 0.9


def test_low_confidence_in_any_frame_is_unreadable():
    with pytest.raises(vw.Unreadable) as e:
        looker([good(conf=0.9), good(conf=0.2), good(conf=0.9)]).look()
    assert e.value.reason == "low_confidence"


def test_frames_that_disagree_are_unreadable():
    with pytest.raises(vw.Unreadable) as e:
        looker([good(2.0), good(9.0), good(2.1)]).look()
    assert e.value.reason == "frames_disagree"


def test_camera_failure_is_unreadable():
    def boom():
        raise RuntimeError("camera read failed")
    with pytest.raises(vw.Unreadable) as e:
        looker([], grab=boom).look()
    assert e.value.reason == "camera"


def test_call_cap_makes_later_looks_unreadable():
    lk = looker([good()] * 10, max_calls=4)
    lk.look()  # 3 calls
    with pytest.raises(vw.Unreadable) as e:
        lk.look()  # would need calls 4,5,6 but only 1 left
    assert e.value.reason == "call_cap"


def test_look_retries_once_then_gives_up():
    seq = [good(2.0), good(9.0), good(2.1)] + [good(5.0)] * 3
    assert looker(seq).look_with_retry().grams == 5.0
    seq = [good(2.0), good(9.0), good(2.1)] * 2
    with pytest.raises(vw.Unreadable):
        looker(seq).look_with_retry()


# --- calibration ------------------------------------------------------------------------------------

def test_compute_scale_clamps_and_refuses():
    assert vw.compute_scale(5.0, 4.0) == pytest.approx(1.25)
    for known, est in ((5.0, 0.0), (5.0, float("nan")), (0.0, 4.0), (-1.0, 4.0), (5.0, 100.0), (100.0, 1.0)):
        with pytest.raises(ValueError):
            vw.compute_scale(known, est)


def test_load_scale_needs_a_valid_file(tmp_path):
    p = tmp_path / "v.json"
    with pytest.raises(ValueError):
        vw.load_scale(p)
    p.write_text("{not json")
    with pytest.raises(ValueError):
        vw.load_scale(p)
    for bad in (0, -1, 50, "x", None, float("nan")):
        p.write_text(json.dumps({"scale": bad}))
        with pytest.raises(ValueError):
            vw.load_scale(p)
    p.write_text(json.dumps({"scale": 1.25}))
    assert vw.load_scale(p) == 1.25


# --- verifier rule table ----------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,cam,att,cov,want", [
    (10.0, 0.9, True, 1, 1.0, Verdict.DONE),     # 9 percent of start, coverage low, camera agrees
    (10.0, 1.0, True, 1, 1.0, Verdict.DONE),     # exactly the limit
    (10.0, 1.1, True, 1, 1.0, Verdict.RETRY),    # over 10 percent
    (10.0, 0.5, False, 1, 1.0, Verdict.RETRY),   # camera disagrees
    (10.0, 0.5, True, 1, 2.5, Verdict.RETRY),    # coverage above 2 percent
    (10.0, 5.0, True, 3, 1.0, Verdict.HUMAN),    # out of attempts
    (0.4, 0.6, True, 0, 0.5, Verdict.DONE),      # below the 1 g floor
    (-1.0, 0.0, True, 0, 0.0, Verdict.HUMAN),
    (10.0, -0.1, True, 1, 0.0, Verdict.HUMAN),
    (10.0, float("nan"), True, 1, 0.0, Verdict.HUMAN),
    (10.0, 0.5, True, 1, None, Verdict.HUMAN),
    (10.0, 0.5, True, 1, float("nan"), Verdict.HUMAN),
])
def test_verify_vision_table(start, end, cam, att, cov, want):
    assert verify_vision(start, end, cam, att, cov) is want


def test_old_verify_is_unchanged():
    assert verify(10.0, 0.2, True, 1) is Verdict.DONE
    assert verify(10.0, 0.4, True, 1) is Verdict.RETRY  # 0.3 g limit (3 percent)


# --- the rig wrapper --------------------------------------------------------------------------------

class SpyInner:
    def __init__(self):
        self.calls = []

    def park(self): self.calls.append("park")
    def tare(self): self.calls.append("tare")
    def weigh(self): self.calls.append("weigh"); return Reading(1.0, True)
    def sweep(self, rung): self.calls.append(f"sweep:{rung}")
    def camera_clean(self): self.calls.append("camera"); return True
    def cancel_requested(self): return False
    def close(self): self.calls.append("close")


def test_wrapper_never_sends_tare_or_weigh_to_the_firmware():
    inner = SpyInner()
    rig = vw.VisionWeighRig(inner, looker([good(5.0)] * 3))
    rig.park(); rig.tare()
    assert rig.weigh().grams == 5.0
    rig.sweep("L1"); assert rig.camera_clean() is True; rig.close()
    assert inner.calls == ["park", "sweep:L1", "camera", "close"]


def test_unreadable_look_becomes_a_rig_fault():
    rig = vw.VisionWeighRig(SpyInner(), looker([good(conf=0.1)] * 6))
    with pytest.raises(RigFault):
        rig.weigh()


# --- whole runs on the simulated rig, camera estimate follows the simulator truth ---------------------

def sim(fake, conf=0.9, scale=1.0):
    """A looker whose provider 'sees' the simulator's true residue. SIMULATED."""
    ask = lambda transports, png, timeout_s: good(fake.true_residue_g, min(100.0, fake.true_residue_g * 0.5), conf)
    return vw.VisionLooker(lambda: b"png", [], scale=scale, ask=ask)


def vision_run(fake, lk=None, mess="dry_debris", sensitive=False):
    rig = vw.VisionWeighRig(fake, lk or sim(fake))
    log = AuditLog()
    res = run(Scenario("t", mess, sensitive), rig, planner=rules_planner, log=log,
              verifier=vw.VisionVerifier(rig))
    return res, log, rig


def test_dry_debris_run_finishes_on_the_camera_estimate():
    fake = FakeRig(residue_g=8.0, noise_g=0.0)
    res, log, _ = vision_run(fake)
    assert res.outcome == "done" and res.attempts == 2
    assert fake.sweeps == ["L1", "L2"]
    assert res.start_g == pytest.approx(8.0)


def test_log_shows_only_grams_nothing_new_for_the_dashboard():
    fake = FakeRig(residue_g=8.0, noise_g=0.0)
    _, log, _ = vision_run(fake)
    assert {e["voice"] for e in log.events} <= {"sensor", "proposed", "decided", "allowed"}
    blob = json.dumps(log.events).lower()
    assert "vision" not in blob and "coverage" not in blob and "estimat" not in blob
    assert any("start_g" in e for e in log.events) and any("end_g" in e for e in log.events)


def test_unreadable_start_look_aborts_with_no_motion():
    fake = FakeRig(residue_g=8.0)
    res, log, _ = vision_run(fake, lk=sim(fake, conf=0.1))
    assert res.outcome == "aborted" and fake.sweeps == []


def test_unreadable_look_after_a_sweep_aborts():
    fake = FakeRig(residue_g=8.0, noise_g=0.0)
    state = {"n": 0}

    def ask(transports, png, timeout_s):
        state["n"] += 1
        # first look (3 frames) fine; afterwards the model is unsure
        return good(fake.true_residue_g, 4.0, 0.9 if state["n"] <= 3 else 0.1)

    lk = vw.VisionLooker(lambda: b"png", [], ask=ask)
    res, _, _ = vision_run(fake, lk=lk)
    assert res.outcome == "aborted" and fake.sweeps == ["L1"]


def test_model_overclaiming_clean_cannot_beat_the_pixel_check():
    class DirtyCamera(SpyInner):
        def camera_clean(self): return False  # the pixel check keeps seeing a mess

    inner = DirtyCamera()
    lk = vw.VisionLooker(lambda: b"png", [], ask=lambda t, p, timeout_s: good(0.0, 0.0, 0.95))
    rig = vw.VisionWeighRig(inner, lk)
    res = run(Scenario("t", "dry_debris", False), rig, planner=rules_planner, log=AuditLog(),
              verifier=vw.VisionVerifier(rig))
    assert res.outcome == "human"  # the model said 0 g every time; it still never finishes


def test_second_look_must_also_be_clean():
    fake = FakeRig(residue_g=8.0, noise_g=0.0)
    seq = {"n": 0}

    def ask(transports, png, timeout_s):
        seq["n"] += 1
        look_no = (seq["n"] - 1) // 3
        # look 0: start 8 g. look 1: 0.2 g (clean). look 2 (the confirmation): back to 3 g.
        grams = {0: 8.0, 1: 0.2, 2: 3.0}.get(look_no, 0.1)
        return good(grams, 0.1 if grams < 1 else 20.0, 0.9)

    lk = vw.VisionLooker(lambda: b"png", [], ask=ask)
    res, _, _ = vision_run(fake, lk=lk)
    assert res.outcome != "done" or res.attempts >= 2  # the first clean look alone did not finish the run
    assert res.attempts >= 2


def test_unexpected_provider_exception_is_unreadable_not_a_crash():
    def boom(transports, png, timeout_s):
        raise ConnectionResetError("boom")
    lk = vw.VisionLooker(lambda: b"png", [], ask=boom)
    with pytest.raises(vw.Unreadable) as e:
        lk.look()
    assert e.value.reason == "provider_error"


# --- run_rig --weigh vision (fake serial rig, fake camera, fake provider) -----------------------------

import sys
import numpy as np


class _FakeCamera:
    def __init__(self, *a, **k): pass
    def grab_gray(self): return np.zeros((8, 8), dtype=np.uint8)
    def grab_bgr(self): return np.zeros((8, 8, 3), dtype=np.uint8)
    def close(self): pass


class _ClosableFake(FakeRig):
    closed = False

    def close(self):
        type(self).closed = True


def _setup_main(monkeypatch, tmp_path, fake, *, calibrated=True, transports=True, extra=()):
    import camera
    import llm_transport
    import run_rig

    monkeypatch.setattr(camera, "Camera", _FakeCamera)
    monkeypatch.setattr(camera, "encode_png", lambda frame: b"png")
    monkeypatch.setattr(run_rig, "load_env_file", lambda *a, **k: None)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.setattr(run_rig, "open_serial_rig", lambda port, camera_clean=None: fake)
    ref = tmp_path / "reference.npy"
    np.save(ref, np.zeros((8, 8), dtype=np.uint8))
    cal = tmp_path / "vision_weight.json"
    if calibrated:
        cal.write_text(json.dumps({"scale": 1.0}))
    truth = lambda: json.dumps(good(fake.true_residue_g, min(100.0, fake.true_residue_g * 0.5), 0.9))
    tr = [type("T", (), {"name": "fake", "complete": lambda self, *a: Reply(text=truth())})()] if transports else []
    monkeypatch.setattr(llm_transport, "build_transports", lambda *a, **k: tr)
    monkeypatch.setattr(sys, "argv", ["run_rig.py", "--port", "FAKE", "--camera", "0", "--weigh", "vision",
                                      "--reference", str(ref), "--vision-calibration", str(cal),
                                      "--log", str(tmp_path / "runs"), *extra])


def test_run_rig_vision_run_finishes_and_adds_nothing_new_to_the_log(tmp_path, monkeypatch, capsys):
    import run_rig
    fake = _ClosableFake(residue_g=8.0, noise_g=0.0)
    _setup_main(monkeypatch, tmp_path, fake)
    run_rig.main()
    out = json.loads(capsys.readouterr().out.split("\n  [")[0])
    assert out["outcome"] == "done" and out["start_g"] == pytest.approx(8.0)
    events = [json.loads(ln) for ln in (tmp_path / "runs" / "dry_debris_on_glass.jsonl").read_text().splitlines()]
    assert set(events[0]) - {"t", "prev", "h"} == {"voice", "scale", "camera", "edge", "case", "planner", "providers"}
    assert "vision" not in json.dumps(events).lower()  # the setup event and every other event: no new claim
    assert _ClosableFake.closed


def test_run_rig_vision_refuses_without_calibration(tmp_path, monkeypatch):
    import run_rig
    fake = _ClosableFake(residue_g=8.0)
    _ClosableFake.closed = False
    _setup_main(monkeypatch, tmp_path, fake, calibrated=False)
    with pytest.raises(SystemExit):
        run_rig.main()
    assert fake.sweeps == [] and _ClosableFake.closed  # nothing moved, port released


def test_run_rig_vision_refuses_without_a_provider(tmp_path, monkeypatch):
    import run_rig
    fake = _ClosableFake(residue_g=8.0)
    _setup_main(monkeypatch, tmp_path, fake, transports=False)
    with pytest.raises(SystemExit):
        run_rig.main()
    assert fake.sweeps == []


def test_run_rig_vision_needs_a_camera(tmp_path, monkeypatch):
    import run_rig
    monkeypatch.setattr(sys, "argv", ["run_rig.py", "--port", "FAKE", "--weigh", "vision", "--log", str(tmp_path)])
    with pytest.raises(SystemExit):
        run_rig.main()


# --- calibrate_vision.py (fake camera, fake provider) -------------------------------------------------

def _cal_main(monkeypatch, tmp_path, argv, reply):
    import camera
    import calibrate_vision
    import llm_transport

    monkeypatch.setattr(camera, "Camera", _FakeCamera)
    monkeypatch.setattr(camera, "encode_png", lambda frame: b"png")
    monkeypatch.setattr(llm_transport, "load_env_file", lambda *a, **k: None)
    tr = [type("T", (), {"name": "fake", "complete": lambda self, *a: Reply(text=json.dumps(reply))})()]
    monkeypatch.setattr(llm_transport, "build_transports", lambda *a, **k: tr)
    monkeypatch.setattr(sys, "argv", ["calibrate_vision.py", *argv, "--out", str(tmp_path)])
    calibrate_vision.main()


def test_calibrate_reference_saves_an_empty_tile_frame(tmp_path, monkeypatch):
    _cal_main(monkeypatch, tmp_path, ["reference"], good())
    assert (tmp_path / "reference.npy").exists()


def test_calibrate_dose_saves_a_scale_the_run_accepts(tmp_path, monkeypatch):
    _cal_main(monkeypatch, tmp_path, ["dose", "--grams", "5"], good(4.0))
    assert vw.load_scale(tmp_path / "vision_weight.json") == pytest.approx(1.25)


def test_calibrate_dose_refuses_an_unsure_model(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        _cal_main(monkeypatch, tmp_path, ["dose", "--grams", "5"], good(4.0, conf=0.1))
    assert not (tmp_path / "vision_weight.json").exists()


def test_calibrate_dose_needs_grams(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        _cal_main(monkeypatch, tmp_path, ["dose"], good())
