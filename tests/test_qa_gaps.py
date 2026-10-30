"""QA gap fixes before integration: breaker must show AI OFFLINE for any error class,
and the LLM planner must never send more HTTP calls than the cap (repair retry included).
No hardware, no key."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import AuditLog, Scenario, run  # noqa: E402
from planner_llm import CircuitBreaker, LLMPlanner, PlannerError, PlannerUnavailable  # noqa: E402
from rig import FakeRig  # noqa: E402
from rules import Context  # noqa: E402
from ui import DashboardState  # noqa: E402

DRY = Scenario("dry", "dry_debris", sensitive=False)


class APITimeoutError(Exception):
    """Named like the SDK's timeout error; the banner must not depend on this name."""


def _raising(*_a, **_k):
    raise APITimeoutError("model timed out")


def test_breaker_opened_by_timeouts_still_shows_ai_offline(tmp_path):
    breaker = CircuitBreaker(_raising, threshold=2)
    result = run(DRY, FakeRig(residue_g=10.0, seed=1), planner=breaker,
                 log=AuditLog(tmp_path / "log.jsonl"))
    errors = [e for e in result.events if e.get("voice") == "proposed" and "error" in e]
    assert errors, "the run must reach the planner"
    assert errors[0]["error"] == "APITimeoutError"
    assert breaker.ai_offline is True

    state = DashboardState()
    for ev in result.events:
        state.on_event(ev)
    assert state.ai_offline is True, "banner must follow the breaker, not the error name"


def test_breaker_not_open_does_not_show_offline(tmp_path):
    state = DashboardState()
    state.on_event({"voice": "proposed", "error": "APITimeoutError", "ai_offline": False})
    assert state.ai_offline is False


class _FakeMessages:
    def __init__(self, text):
        self.calls = 0
        self._text = text

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._text)], usage=None)


def _client(text="not json at all"):
    m = _FakeMessages(text)
    return SimpleNamespace(messages=m), m


def test_http_cap_counts_repair_retry_and_refuses_beyond_it():
    client, fake = _client()
    planner = LLMPlanner(client=client, max_http_calls=3)
    ctx = Context(sensitive_surface=False)
    # attempt 1: call + repair = 2 HTTP calls, then the parser gives up
    with pytest.raises(PlannerError):
        planner("dry_debris", False, ctx)
    assert fake.calls == 2
    # attempt 2: one more allowed call (3rd), its repair is refused by the cap
    with pytest.raises(PlannerUnavailable):
        planner("dry_debris", False, ctx)
    assert fake.calls == 3
    # nothing further is ever sent
    with pytest.raises(PlannerUnavailable):
        planner("dry_debris", False, ctx)
    assert fake.calls == 3


def test_default_cap_is_twelve_and_is_a_planner_attribute():
    client, _ = _client()
    assert LLMPlanner(client=client).max_http_calls == 12
