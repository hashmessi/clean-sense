"""Tabletop demo gaps: --case presets, the Live page Sense card state, the camera frame route.
No hardware, no provider calls, no audio."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import ui
from announce import TEMPLATES
from app import AuditLog
from cases import CASES, PERCEPTION_FRAME, case_title, resolve
from rig import FakeRig

RUN_RIG_MESS = {"dry_debris", "liquid", "mixed", "unknown"}


# --- --case presets ---------------------------------------------------------------------------------

@pytest.mark.parametrize("case,mess,surface", [
    ("pharma", "dry_debris", "glass"),
    ("battery", "dry_debris", "wood"),
    ("smt", "mixed", "wood"),
])
def test_case_sets_mess_and_surface(case, mess, surface):
    assert resolve(case, None, None) == (mess, surface)


def test_explicit_flags_win_over_the_case():
    assert resolve("battery", "liquid", None) == ("liquid", "wood")  # the coffee-spill run
    assert resolve("pharma", None, "wood") == ("dry_debris", "wood")
    assert resolve("smt", "unknown", "glass") == ("unknown", "glass")


def test_no_case_keeps_the_old_defaults():
    assert resolve(None, None, None) == (None, "glass")  # run_rig then defaults the mess to dry_debris
    assert resolve(None, "liquid", "wood") == ("liquid", "wood")


def test_offline_never_takes_a_mess_type_from_the_case():
    assert resolve("smt", None, None, offline=True) == (None, "wood")
    assert resolve("smt", "dry_debris", None, offline=True) == ("dry_debris", "wood")


def test_every_case_uses_values_run_rig_accepts():
    assert set(CASES) == {"pharma", "battery", "smt"}
    for key, c in CASES.items():
        assert c["mess"] in RUN_RIG_MESS and c["surface"] in ("glass", "wood"), key
        assert case_title(key) == c["title"]


def test_case_title_never_raises_on_bad_input():
    for bad in (None, "", "nope", 7, ["pharma"], {"a": 1}):
        assert case_title(bad) is None


class _ClosableFakeRig(FakeRig):
    def close(self):  # the real serial rig has one; main() calls it
        pass


def test_main_with_a_case_runs_on_the_preset_and_logs_it(tmp_path, monkeypatch):
    import run_rig

    monkeypatch.setattr(run_rig, "open_serial_rig",
                        lambda port, camera_clean=None: _ClosableFakeRig(residue_g=4.0, seed=1, camera_error=0.0))
    monkeypatch.setattr(run_rig, "load_env_file", lambda *a, **k: None)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_rig.py", "--port", "FAKE", "--case", "smt", "--log", str(tmp_path)])
    run_rig.main()
    path = tmp_path / "mixed_on_wood.jsonl"  # the smt preset: mixed mess on the sensitive surface
    events = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["voice"] == "setup" and events[0]["case"] == "smt"
    assert any(e["voice"] == "decided" for e in events)
    # the sensitive surface still bars L2: no allowed motion may use a firm sweep
    assert not [e for e in events if e["voice"] == "allowed" and e.get("rung") == "L2"]


# --- Sense card state -------------------------------------------------------------------------------

def _state(*events):
    st = ui.DashboardState(simulated=False)
    for ev in events:
        st.on_event(ev)
    return st.snapshot()


def test_sense_collects_the_look_and_the_fixed_sentence():
    s = _state({"voice": "setup", "case": "battery", "planner": "ai"},
               {"voice": "perceived", "provider": "gemini", "mess_type": "liquid", "confidence": 0.9234,
                "frame": True, "t": 12.5},
               {"voice": "speaker", "spoken": True, "note": "piper ready"},
               {"voice": "announced", "key": "liquid_try", "spoken": True})
    n = s["sense"]
    assert s["case"] == "Battery cell"
    assert (n["seen"], n["provider"], n["confidence"], n["frame"], n["frame_t"]) == ("liquid", "gemini", 0.92, True, 12.5)
    assert n["sentence"] == TEMPLATES["liquid_try"] and n["spoken"] is True and n["voice_set"] is True


def test_sense_is_none_until_something_happens_and_cleared_by_a_new_run():
    assert _state()["sense"] is None
    s = _state({"voice": "setup"}, {"voice": "perceived", "mess_type": "mixed", "confidence": 0.5, "frame": False})
    assert s["sense"]["seen"] == "mixed"
    st = ui.DashboardState()
    st.on_event({"voice": "setup", "case": "pharma"})
    st.on_event({"voice": "perceived", "mess_type": "mixed", "confidence": 0.5})
    st.on_event({"voice": "setup"})  # next run
    snap = st.snapshot()
    assert snap["sense"] is None and snap["case"] is None


def test_sense_failed_look_keeps_the_reason_and_no_class():
    n = _state({"voice": "perceived", "error": "PlannerUnavailable", "reason": "timeout", "frame": True})["sense"]
    assert n["seen"] is None and n["error"] == "timeout" and n["frame"] is True


def test_sense_treats_log_text_as_untrusted():
    n = _state({"voice": "perceived", "mess_type": "<script>alert(1)</script>", "confidence": "high",
                "provider": "x" * 500, "frame": "yes", "t": "now"},
               {"voice": "speaker", "note": "n" * 500, "spoken": "true"},
               {"voice": "announced", "key": "<b>not a template</b>", "spoken": 1},
               {"voice": "announce_failed", "reason": {"nested": 1}})["sense"]
    assert n["seen"] is None and n["confidence"] is None and len(n["provider"]) == 40
    assert n["frame"] is False and n["frame_t"] == 0
    assert len(n["speaker_note"]) == 160 and n["voice_set"] is False
    assert n["sentence"] is None and n["spoken"] is False and n["announce_error"] == "failed"


def test_sense_timestamp_must_be_finite_so_state_stays_valid_json():
    for bad in (float("nan"), float("inf"), True, None, "1"):
        n = _state({"voice": "perceived", "mess_type": "liquid", "frame": True, "t": bad})["sense"]
        assert n["frame_t"] == 0
    st = ui.DashboardState()
    st.on_event({"voice": "perceived", "mess_type": "liquid", "frame": True, "t": float("nan")})
    json.dumps(st.snapshot()["sense"], allow_nan=False)  # the new fields are always strict-JSON safe


def test_sense_confidence_must_be_a_fraction():
    for bad in (-0.1, 1.5, True, None, float("nan")):
        assert _state({"voice": "perceived", "mess_type": "liquid", "confidence": bad})["sense"]["confidence"] is None


def test_sense_events_never_set_a_verdict():
    s = _state({"voice": "perceived", "mess_type": "liquid", "confidence": 1.0},
               {"voice": "announced", "key": "liquid_try", "spoken": True})
    assert s["verdict"] is None and s["step"] is None


def test_unknown_case_key_shows_no_case():
    assert _state({"voice": "setup", "case": "cooking"})["case"] is None


# --- frame route and page markup ---------------------------------------------------------------------

def _client(tmp_path):
    st = ui.DashboardState(simulated=False)
    (tmp_path / "runs").mkdir(exist_ok=True)
    return TestClient(ui.make_app(st, tmp_path / "runs", tmp_path / "cal", token_arg="t"))


def test_frame_route_404_until_a_frame_exists(tmp_path):
    assert _client(tmp_path).get("/frame.png").status_code == 404


def test_frame_route_serves_only_the_fixed_file(tmp_path):
    c = _client(tmp_path)
    (tmp_path / "runs" / PERCEPTION_FRAME).write_bytes(b"\x89PNG-demo")
    (tmp_path / "runs" / "secret.png").write_bytes(b"other")
    r = c.get("/frame.png", params={"name": "secret.png", "path": "../secret.png"})
    assert r.status_code == 200 and r.content == b"\x89PNG-demo"
    assert r.headers["content-type"] == "image/png" and "no-store" in r.headers["cache-control"]
    assert c.get("/frame.png/../secret.png").content != b"other"


def test_live_page_has_the_sense_card_and_case_tag(tmp_path):
    html = _client(tmp_path).get("/").text
    for i in ("c-sense", "sense-claim", "sense-say", "sense-sub", "sense-img", "sense-by", "case"):
        assert f'id="{i}"' in html, i
    assert "innerHTML" not in ui.LIVE_SCRIPT


# --- saving the frame --------------------------------------------------------------------------------

class _Cam:
    def grab_bgr(self):
        return np.zeros((4, 4, 3), dtype=np.uint8)


class _T:
    name = "fake"

    def __init__(self, text=None, error=None):
        self._text, self._error = text, error

    def complete(self, *a, **k):
        if self._error:
            raise self._error
        return SimpleNamespace(text=self._text)


def _llm(t):
    return SimpleNamespace(transports=[t])


def test_perceive_saves_the_frame_and_logs_it(tmp_path):
    from run_rig import _perceive

    frame = tmp_path / PERCEPTION_FRAME
    log = AuditLog(tmp_path / "p.jsonl")
    got = _perceive(_Cam(), _llm(_T('{"mess_type": "liquid", "confidence": 0.9}')), log, frame)
    assert got == "liquid" and frame.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert log.events[0]["frame"] is True
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp file


def test_perceive_saves_the_frame_even_when_the_provider_fails(tmp_path):
    from run_rig import _perceive

    frame = tmp_path / PERCEPTION_FRAME
    log = AuditLog(tmp_path / "p.jsonl")
    assert _perceive(_Cam(), _llm(_T(error=TimeoutError())), log, frame) is None
    assert frame.is_file() and log.events[0]["error"] == "TimeoutError" and log.events[0]["frame"] is True


def test_perceive_without_a_frame_path_or_with_a_bad_one_still_works(tmp_path):
    from run_rig import _perceive

    ok = _T('{"mess_type": "mixed", "confidence": 0.8}')
    a = AuditLog(tmp_path / "a.jsonl")
    assert _perceive(_Cam(), _llm(ok), a) == "mixed" and a.events[0]["frame"] is False
    b = AuditLog(tmp_path / "b.jsonl")
    assert _perceive(_Cam(), _llm(ok), b, tmp_path / "missing_dir" / PERCEPTION_FRAME) == "mixed"
    assert b.events[0]["frame"] is False
