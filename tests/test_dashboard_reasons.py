"""Reason codes reach the dashboard; the OpenRouter pin is safe and documented. No network."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

import ui
from app import AuditLog, Scenario, run
from llm_transport import OpenRouterTransport, build_transports
from planner import rules_planner
from planner_llm import CircuitBreaker, PlannerError, PlannerUnavailable
from rig import FakeRig

ROOT = Path(__file__).resolve().parent.parent
DRY = Scenario("dry", "dry_debris", sensitive=False)
REASON_CODES = ["not_configured", "rate_limited", "timeout", "auth_error", "provider_error",
                "blocked", "truncated", "bad_request", "cap", "circuit_open", "invalid_output", "unavailable"]


class _Failing:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, *args):
        raise self.exc


class _Good:
    provider_used = "gemini"

    def __call__(self, mess, sensitive, ctx):
        return rules_planner(mess, sensitive, ctx)


def _offline_event(reason="rate_limited"):
    return {"voice": "proposed", "error": "PlannerUnavailable", "reason": reason, "ai_offline": True}


def test_dashboard_maps_every_reason_code():
    for code in REASON_CODES:
        assert f"{code}:" in ui.LIVE_SCRIPT, code


def test_state_records_failure_reason_when_offline():
    st = ui.DashboardState()
    st.on_event(_offline_event("rate_limited"))
    snap = st.snapshot()
    assert snap["ai_offline"] is True
    assert snap["ai_reason"] == "rate_limited"
    assert snap["proposal"]["reason_code"] == "rate_limited"


def test_state_records_provider_on_success():
    st = ui.DashboardState()
    st.on_event({"voice": "proposed", "provider": "gemini",
                 "plan": {"mess_type": "dry_debris", "rung": "L1", "action": "clean", "reason": "x"}})
    snap = st.snapshot()
    assert snap["ai_provider"] == "gemini"
    assert snap["ai_offline"] is False


def test_reset_clears_reason_and_provider():
    st = ui.DashboardState()
    st.on_event(_offline_event("timeout"))
    st.reset()
    snap = st.snapshot()
    assert snap["ai_reason"] is None and snap["ai_provider"] is None


def test_state_endpoint_exposes_reason(tmp_path):
    st = ui.DashboardState()
    st.on_event(_offline_event("timeout"))
    client = TestClient(ui.make_app(st, tmp_path / "runs", tmp_path / "calibration"))
    assert client.get("/state").json()["ai_reason"] == "timeout"


def test_live_page_has_the_ai_status_tag(tmp_path):
    client = TestClient(ui.make_app(ui.DashboardState(), tmp_path / "runs", tmp_path / "calibration"))
    assert 'id="ai"' in client.get("/").text  # the reason code is written into this tag


def test_planner_failure_is_logged_with_its_reason(tmp_path):
    path = tmp_path / "run.jsonl"
    run(DRY, FakeRig(residue_g=10.0, seed=3),
        planner=_Failing(PlannerUnavailable("rate limited", reason="rate_limited")),
        log=AuditLog(path))
    proposed = [e for e in ui._events(path)[0] if e.get("voice") == "proposed"]
    assert proposed and proposed[0]["reason"] == "rate_limited"


def test_success_is_logged_with_provider(tmp_path):
    path = tmp_path / "run.jsonl"
    run(DRY, FakeRig(residue_g=10.0, seed=4), planner=_Good(), log=AuditLog(path))
    proposed = [e for e in ui._events(path)[0] if e.get("voice") == "proposed"]
    assert proposed and proposed[0]["provider"] == "gemini"


def test_breaker_passes_provider_through():
    assert CircuitBreaker(_Good()).provider_used == "gemini"


def test_invalid_output_has_reason_code():
    assert PlannerError.reason == "invalid_output"


def test_env_example_pins_a_specific_openrouter_model():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^OPENROUTER_MODEL=(\S*)$", text, re.M)
    assert m and m.group(1).endswith(":free") and m.group(1) != "openrouter/free"


def test_random_router_is_refused_as_pinned_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/free")
    assert build_transports("openrouter") == []


def test_pinned_model_is_used(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free")
    (transport,) = build_transports("openrouter")
    assert isinstance(transport, OpenRouterTransport)
    assert transport.model == "nvidia/nemotron-3.5-super-vl:free"


def test_success_clears_last_error_reason():
    st = ui.DashboardState()
    st.on_event({"voice": "proposed", "error": "PlannerUnavailable", "reason": "timeout"})
    assert st.snapshot()["ai_reason"] == "timeout"
    st.on_event({"voice": "proposed", "provider": "openrouter",
                 "plan": {"mess_type": "dry_debris", "rung": "L1", "action": "clean", "reason": "x"}})
    assert st.snapshot()["ai_reason"] is None
