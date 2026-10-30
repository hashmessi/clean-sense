"""Live page wiring: same markup in both modes, honest decision story, safe model text. No hardware."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

import ui
from app import AuditLog, Scenario, run
from llm_transport import GeminiTransport
from planner_llm import CircuitBreaker, LLMPlanner
from rig import FakeRig
from runctl import LogFollower

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_IDS = set(re.findall(r'\$\("([a-z-]+)"\)', ui.LIVE_SCRIPT)) | set(re.findall(r'set\("([a-z-]+)"', ui.LIVE_SCRIPT))


def _page(tmp_path, simulated):
    st = ui.DashboardState(simulated=simulated)
    return TestClient(ui.make_app(st, tmp_path / "runs", tmp_path / "cal", token_arg="t")).get("/").text


def test_every_element_the_script_touches_exists_in_both_modes(tmp_path):
    """Regression: rig mode used to lack #sim and #airsn, so the script crashed and the page stayed blank."""
    for simulated in (True, False):
        html = _page(tmp_path, simulated)
        missing = sorted(i for i in SCRIPT_IDS if f'id="{i}"' not in html)
        assert not missing, (simulated, missing)


def test_model_text_is_never_written_as_html():
    assert "innerHTML" not in ui.LIVE_SCRIPT


def _refusing_gemini():
    def opener(req, timeout):
        plan = {"mess_type": "liquid", "action": "clean", "rung": "L2", "sensitive_surface": True,
                "reason": "firm pass is faster"}
        return 200, {}, {"candidates": [{"content": {"parts": [{"text": json.dumps(plan)}]}, "finishReason": "STOP"}]}
    return CircuitBreaker(LLMPlanner(transports=[GeminiTransport(api_key="k", model="m", opener=opener)]))


def test_refused_ai_plan_and_the_rules_step_that_replaced_it_are_both_logged(tmp_path):
    log = AuditLog(tmp_path / "r.jsonl")
    run(Scenario("liquid_on_wood", "liquid", True), FakeRig(residue_g=6.0, seed=3, camera_error=0.0),
        planner=_refusing_gemini(), log=log)
    decided = [e for e in log.events if e["voice"] == "decided"]
    assert decided[0]["ok"] is False and decided[0]["rung"] is None and decided[0]["source"] == "planner"
    assert decided[1]["ok"] is True and decided[1]["rung"] == "L1" and decided[1]["source"] == "rules"


def test_dashboard_keeps_the_refusal_not_only_the_last_decision(tmp_path):
    st = ui.DashboardState()
    log = AuditLog(tmp_path / "r.jsonl", listener=st.on_event)
    run(Scenario("liquid_on_wood", "liquid", True), FakeRig(residue_g=6.0, seed=3, camera_error=0.0),
        planner=_refusing_gemini(), log=log)
    ds = st.snapshot()["decisions"]
    assert ds[0]["ok"] is False and ds[-1]["ok"] is True
    assert st.snapshot()["ai_provider"] == "gemini"


def test_setup_event_starts_a_fresh_run_view():
    st = ui.DashboardState()
    st.on_event({"voice": "edge", "operator": "dry_debris", "vote": None, "fused": "dry_debris", "note": "x"})
    st.on_event({"voice": "decided", "ok": True, "reasons": [], "source": "planner"})
    st.on_event({"voice": "setup", "scale": "ESP32 A on COM5", "camera": "camera index 1", "edge": None,
                 "planner": "ai", "providers": ["gemini"]})
    snap = st.snapshot()
    assert snap["edge"] is None and snap["decisions"] == [] and snap["decision"] is None
    assert snap["setup"]["camera"] == "camera index 1" and snap["setup"]["providers"] == ["gemini"]


def test_cancelled_run_shows_cancelled_and_clears_the_request():
    st = ui.DashboardState()
    st.cancel_event.set()
    st.on_event({"voice": "allowed", "cancelled": True})
    snap = st.snapshot()
    assert snap["verdict"] == "cancelled" and snap["cancel_requested"] is False


def test_fault_shows_aborted_not_a_person():
    st = ui.DashboardState()
    st.on_event({"voice": "allowed", "fault": "estop"})
    assert st.snapshot()["verdict"] == "aborted"


def test_followed_demo_log_is_labelled_simulated_even_on_the_rig_server(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    run(Scenario("dry", "dry_debris", False), FakeRig(residue_g=10.0, seed=1), log=AuditLog(logs / "sim_x.jsonl"))
    st = ui.DashboardState(simulated=False)
    LogFollower(st, logs).poll_once()
    assert st.snapshot()["SIMULATED"] is True


def test_rig_runner_logs_setup_with_provider_names_only():
    src = (ROOT / "run_rig.py").read_text(encoding="utf-8")
    setup = src[src.index('log.write("setup"'):src.index("if args.offline or edge is not None")]
    assert "providers=[t.name for t in llm.transports]" in setup
    assert "KEY" not in setup.upper().replace("KEYS NEVER", "")
