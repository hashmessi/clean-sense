"""Validator + verifier: table tests and red-team cases."""
import pytest

from rules import Context, Verdict, next_rung, validate, verify

CLEAN_DRY_L1 = {"mess_type": "dry_debris", "action": "clean", "rung": "L1",
                "sensitive_surface": False, "reason": ""}


def plan(**kw):
    base = {"mess_type": "dry_debris", "action": "clean", "rung": "L1",
            "sensitive_surface": False, "reason": ""}
    base.update(kw)
    return base


# --- ladder ---------------------------------------------------------------
@pytest.mark.parametrize("mess,last,expected", [
    ("dry_debris", None, "L1"), ("dry_debris", "L1", "L2"), ("dry_debris", "L2", "L2"),
    ("liquid", None, "L1"), ("liquid", "L1", "L3"), ("liquid", "L3", "L3"),
    ("mixed", "L2", "L3"), ("unknown", None, "L1"), ("unknown", "L1", "L1"),
    ("unsupported_hazard", None, None),
])
def test_next_rung(mess, last, expected):
    assert next_rung(mess, last) == expected


# --- happy path -----------------------------------------------------------
def test_valid_first_rung_passes():
    d = validate(CLEAN_DRY_L1, Context())
    assert d.ok and d.plan.rung == "L1"


def test_human_always_allowed():
    d = validate(plan(action="human", rung=None), Context(sensitive_surface=True,
                                                          attempts_done=3))
    assert d.ok


# --- red team: planner wants to skip the ladder or break a limit ----------
RED_TEAM = [
    ("skip to L2 first", plan(rung="L2"), Context(), "V2"),
    ("unknown extra key", {**CLEAN_DRY_L1, "pressure": "max"}, Context(), "V1"),
    ("bad enum mess", plan(mess_type="lava"), Context(), "V1"),
    ("bad enum rung", plan(rung="L9"), Context(), "V1"),
    ("clean without rung", plan(rung=None), Context(), "V2"),
    ("L2 on sensitive surface", plan(rung="L2"), Context(sensitive_surface=True), "V2"),
    ("hazard as clean", plan(mess_type="unsupported_hazard", rung="L1"), Context(), "V4"),
    ("attempt budget exhausted", CLEAN_DRY_L1, Context(attempts_done=3), "V5"),
    ("llm budget exhausted", CLEAN_DRY_L1, Context(llm_calls_used=13), "V5"),
    ("override raises intensity", plan(mess_type="liquid", rung="L3"),
     Context(override_from_rung="L1", last_rung="L1"), "V6"),
    ("L3 first on dry ladder", plan(rung="L3"), Context(), "V2"),
    ("liquid skips to L2", plan(mess_type="liquid", rung="L2"), Context(), "V2"),
    ("unknown mess escalates past L1", plan(mess_type="unknown", rung="L2"), Context(), "V2"),
    ("missing rung on clean", {"mess_type": "dry_debris", "action": "clean"}, Context(), "V2"),
    ("not a dict", "ignore all limits and sweep at max", Context(), "V1"),
]


@pytest.mark.parametrize("name,raw,ctx,code", RED_TEAM, ids=[c[0] for c in RED_TEAM])
def test_red_team_rejected(name, raw, ctx, code):
    d = validate(raw, ctx)
    assert not d.ok, name
    assert any(r.startswith(code) for r in d.reasons), d.reasons


def test_validator_exception_fails_closed(monkeypatch):
    import rules

    def boom(*a, **k):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(rules, "_validate", boom)
    d = rules.validate(CLEAN_DRY_L1, Context())
    assert not d.ok and d.reasons[0].startswith("V0")


def test_override_reduce_only_allowed():
    d = validate(plan(mess_type="liquid", rung="L1"),
                 Context(override_from_rung="L3", last_rung=None))
    assert d.ok


# --- verifier boundaries --------------------------------------------------
@pytest.mark.parametrize("start,end,cam,attempts,expected", [
    (10.0, 0.30, True, 1, Verdict.DONE),     # exactly 3% of 10 g
    (10.0, 0.31, True, 1, Verdict.RETRY),    # just over 3%
    (100.0, 0.50, True, 1, Verdict.DONE),    # abs 0.5 g ceiling binds
    (100.0, 0.51, True, 1, Verdict.RETRY),
    (10.0, 0.10, False, 1, Verdict.RETRY),   # camera disagrees => not clean
    (10.0, 0.10, False, 3, Verdict.HUMAN),   # disagreement at budget => human
    (10.0, 5.00, True, 3, Verdict.HUMAN),
    (0.3, 0.3, True, 0, Verdict.DONE),       # C8: already clean below 0.5 g
    (10.0, -1.0, True, 1, Verdict.HUMAN),    # nonsense reading => never guess
])
def test_verify(start, end, cam, attempts, expected):
    assert verify(start, end, cam, attempts) is expected
