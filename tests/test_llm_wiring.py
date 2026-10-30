"""Phase 3 wiring, tested with fake clients. No API key or network needed."""
import json
from types import SimpleNamespace

import pytest

from app import Scenario, run
from planner import rules_planner
from planner_llm import (BREAKER_THRESHOLD, CircuitBreaker, LLMPlanner,
                         PlannerError, PlannerUnavailable, parse_plan_json)
from rig import FakeRig
from rules import Context

DRY = Scenario("dry", "dry_debris", sensitive=False)
GOOD = json.dumps({"mess_type": "dry_debris", "action": "clean", "rung": "L1",
                   "sensitive_surface": False, "reason": "dry debris, gentle first"})


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=r)])


def client_with(*replies):
    m = FakeMessages(replies)
    return SimpleNamespace(messages=m), m


def test_parse_accepts_prose_around_json():
    assert parse_plan_json("Sure! " + GOOD + " Done.")["rung"] == "L1"


def test_parse_rejects_non_json():
    with pytest.raises(PlannerError):
        parse_plan_json("I think you should sweep hard")


def test_valid_reply_returns_plan_and_uses_timeout_and_model():
    client, m = client_with(GOOD)
    p = LLMPlanner(client=client, model="claude-sonnet-5-5")
    assert p("dry_debris", False, Context())["rung"] == "L1"
    assert m.calls[0]["timeout"] == 8.0 and m.calls[0]["model"] == "claude-sonnet-5-5"


def test_one_repair_then_success():
    client, m = client_with("not json at all", GOOD)
    p = LLMPlanner(client=client)
    assert p("dry_debris", False, Context())["rung"] == "L1"
    assert len(m.calls) == 2
    assert "invalid" in m.calls[1]["messages"][0]["content"]


def test_two_bad_replies_raise():
    client, _ = client_with("nope", "still nope")
    with pytest.raises(PlannerError):
        LLMPlanner(client=client)("dry_debris", False, Context())


def test_no_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = LLMPlanner.from_env()
    with pytest.raises(PlannerUnavailable):
        p("dry_debris", False, Context())


def test_system_prompt_forbids_clean_claims():
    from planner_llm import SYSTEM_PROMPT
    assert "Never claim the surface is clean" in SYSTEM_PROMPT


def test_image_is_attached_when_provider_gives_png():
    client, m = client_with(GOOD)
    p = LLMPlanner(client=client, frame_png=lambda: b"\x89PNG fake")
    p("dry_debris", False, Context())
    content = m.calls[0]["messages"][0]["content"]
    assert isinstance(content, list) and content[0]["type"] == "image"


def test_breaker_opens_after_two_failures_and_stops_calling_model():
    calls = {"n": 0}

    def broken(*a):
        calls["n"] += 1
        raise TimeoutError("slow")

    b = CircuitBreaker(broken, threshold=BREAKER_THRESHOLD)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            b("dry_debris", False, Context())
    assert b.ai_offline
    with pytest.raises(PlannerUnavailable):
        b("dry_debris", False, Context())
    assert calls["n"] == 2  # no third call once open


def test_breaker_success_resets_failure_count():
    seq = iter([TimeoutError(), {"ok": 1}, TimeoutError()])

    def flaky(*a):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    b = CircuitBreaker(flaky)
    with pytest.raises(TimeoutError):
        b("dry_debris", False, Context())
    b("dry_debris", False, Context())
    with pytest.raises(TimeoutError):
        b("dry_debris", False, Context())
    assert not b.ai_offline  # never two in a row


def test_llm_proposal_that_skips_ladder_is_rejected_and_run_completes():
    skip = json.dumps({"mess_type": "dry_debris", "action": "clean", "rung": "L2",
                       "sensitive_surface": False, "reason": "go hard"})
    client, _ = client_with(skip, skip, skip)
    rig = FakeRig(residue_g=10.0, seed=11)
    res = run(DRY, rig, planner=CircuitBreaker(LLMPlanner(client=client)))
    assert res.outcome == "done"
    assert rig.sweeps[0] == "L1"


def test_no_key_run_completes_on_rules_with_breaker_open():
    breaker = CircuitBreaker(LLMPlanner(client=None))
    res = run(DRY, FakeRig(residue_g=10.0, seed=12), planner=breaker)
    assert res.outcome == "done"
    assert breaker.ai_offline
