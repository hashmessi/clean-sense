"""Control loop: propose -> validate -> countdown -> act -> verify, bounded.

Three-voice log per event:
  proposed = what the planner asked for (may be garbage)
  decided  = what the validator allowed (rules)
  allowed  = what the hardware actually did
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

from planner import rules_planner
from rig import FakeRig, RigFault
from rules import Context, Verdict, validate, verify

Planner = Callable[[str, bool, Context], object]


@dataclass(frozen=True)
class Scenario:
    name: str
    mess_type: str
    sensitive: bool


@dataclass
class RunResult:
    scenario: str
    outcome: str  # done | human | aborted | cancelled
    attempts: int
    start_g: float
    end_g: float
    events: list[dict] = field(default_factory=list)


GENESIS = "0" * 64


def _canon(body: dict) -> str:
    return json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))


def _chain_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canon(body)).encode("utf-8")).hexdigest()


class AuditLog:
    """Hash-chained JSONL. Each line carries prev (hash of the line before) and h.

    Appending to an existing file continues its chain, so several runs in one file
    still verify as one log. An optional listener receives each event (dashboard).
    """

    def __init__(self, path: Optional[Path] = None,
                 listener: Optional[Callable[[dict], None]] = None) -> None:
        self.path = path
        self.events: list[dict] = []
        self._listener = listener
        self._last = GENESIS
        if path is not None and path.exists():
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                self._last = json.loads(lines[-1]).get("h", GENESIS)

    def write(self, voice: str, **data) -> None:
        body = {"t": round(time.time(), 3), "voice": voice, **data}
        h = _chain_hash(self._last, body)
        ev = {**body, "prev": self._last, "h": h}
        self._last = h
        self.events.append(ev)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        if self._listener is not None:
            self._listener(ev)


def verify_log(path: Path) -> tuple[bool, Optional[int]]:
    """Recompute every hash. Returns (ok, 1-based line number of the first bad line)."""
    prev = GENESIS
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    for i, line in enumerate(lines, 1):
        try:
            ev = json.loads(line)
            body = {k: v for k, v in ev.items() if k not in ("prev", "h")}
            if ev.get("prev") != prev or ev.get("h") != _chain_hash(prev, body):
                return False, i
        except (json.JSONDecodeError, AttributeError, TypeError):
            return False, i
        prev = ev["h"]
    return True, None


def _planned(decision) -> dict:
    """What the validator allowed, for the log. Both None when the plan was refused."""
    plan = getattr(decision, "plan", None)
    return {"rung": getattr(plan, "rung", None), "action": getattr(plan, "action", None)}


def run(
    scenario: Scenario,
    rig: FakeRig,
    planner: Planner = rules_planner,
    log: Optional[AuditLog] = None,
    temp_source: Optional[Callable[[], Optional[float]]] = None,
) -> RunResult:
    log = log or AuditLog()
    rig.park()
    rig.tare()
    start = rig.weigh().grams
    end = start
    ctx = Context(sensitive_surface=scenario.sensitive)
    log.write("sensor", start_g=start)

    # Already clean and camera agrees: nothing to do.
    if verify(start, start, rig.camera_clean(), 0) is Verdict.DONE:
        log.write("sensor", end_g=start, camera_clean=True, verdict=Verdict.DONE.value)
        return RunResult(scenario.name, "done", 0, start, start, log.events)

    llm_calls = 0
    while True:
        llm_calls += 1
        if temp_source is not None:  # read before every planning step; failure => None => rejected
            try:
                temp = temp_source()
            except Exception:  # noqa: BLE001
                temp = None
            log.write("sensor", temp_c=temp)
            ctx = replace(ctx, temp_c=temp, temp_required=True)
        try:
            raw = planner(scenario.mess_type, scenario.sensitive,
                          replace(ctx, llm_calls_used=llm_calls))
        except Exception as exc:  # planner failure is just a bad proposal
            raw = None
            # ai_offline comes from the breaker: a breaker opened by timeouts must still show OFFLINE.
            log.write("proposed", error=type(exc).__name__,
                      reason=getattr(exc, "reason", "error"),
                      ai_offline=bool(getattr(planner, "ai_offline", False)))
        else:
            log.write("proposed", plan=raw, provider=getattr(planner, "provider_used", None))

        decision = validate(raw, replace(ctx, llm_calls_used=llm_calls))
        if not decision.ok:
            log.write("decided", ok=False, reasons=decision.reasons, **_planned(decision), source="planner")
            # Fallback to the rules planner, which must also pass the validator.
            raw = rules_planner(scenario.mess_type, scenario.sensitive, ctx)
            decision = validate(raw, replace(ctx, llm_calls_used=llm_calls))
            log.write("decided", ok=decision.ok, reasons=decision.reasons, **_planned(decision), source="rules")
            if not decision.ok:
                return RunResult(scenario.name, "human", ctx.attempts_done, start, end, log.events)
        else:
            log.write("decided", ok=True, reasons=decision.reasons, **_planned(decision), source="planner")

        plan = decision.plan
        if plan.action == "human":
            return RunResult(scenario.name, "human", ctx.attempts_done, start, end, log.events)

        # Countdown: operator can cancel before any motion.
        if rig.cancel_requested():
            log.write("allowed", cancelled=True)
            return RunResult(scenario.name, "cancelled", ctx.attempts_done, start, end, log.events)

        try:
            rig.sweep(plan.rung)
        except RigFault as fault:
            log.write("allowed", fault=str(fault))
            return RunResult(scenario.name, "aborted", ctx.attempts_done, start, end, log.events)
        log.write("allowed", rung=plan.rung)

        end = rig.weigh().grams
        attempts = ctx.attempts_done + 1
        camera = rig.camera_clean()
        verdict = verify(start, end, camera, attempts)
        log.write("sensor", end_g=end, camera_clean=camera, verdict=verdict.value)
        ctx = replace(ctx, attempts_done=attempts, last_rung=plan.rung)

        if verdict is Verdict.DONE:
            return RunResult(scenario.name, "done", attempts, start, end, log.events)
        if verdict is Verdict.HUMAN:
            return RunResult(scenario.name, "human", attempts, start, end, log.events)
