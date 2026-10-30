"""Results table generated from run logs (never typed by hand).

    python3 eval_table.py runs/*.jsonl

Splits each log into runs (a run starts with the sensor event carrying start_g)
and reports first-pass success, attempts, residue, validator rejections, rung
sweeps, AI-offline events and outcomes. Logs from real hardware are not labelled
with simulator truth, so false-clean rate is computed only by ablation.py.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def split_runs(events: list[dict]) -> list[list[dict]]:
    """A run starts at the sensor event carrying start_g. An "edge" event written just
    before it (run_rig logs the fused mess type first) belongs to the run it precedes."""
    runs: list[list[dict]] = []
    pending: list[dict] = []
    open_ = False
    for ev in events:
        if ev.get("voice") == "edge":
            pending, open_ = [ev], False
            continue
        if ev.get("voice") == "sensor" and "start_g" in ev:
            runs.append(pending)
            pending, open_ = [], True
        (runs[-1] if open_ else pending).append(ev)
    return runs


def run_facts(run: list[dict]) -> dict:
    verdicts = [e["verdict"] for e in run if e.get("voice") == "sensor" and "verdict" in e]
    sweeps = [e for e in run if e.get("voice") == "allowed" and "rung" in e]
    cancelled = any(e.get("voice") == "allowed" and e.get("cancelled") for e in run)
    fault = next((e["fault"] for e in run if e.get("voice") == "allowed" and "fault" in e), None)
    if verdicts:
        outcome = {"done": "done", "human": "human"}.get(verdicts[-1], verdicts[-1])
    elif cancelled:
        outcome = "cancelled"
    elif fault:
        outcome = "aborted"
    else:
        outcome = "human"
    ends = [e["end_g"] for e in run if e.get("voice") == "sensor" and "end_g" in e]
    start = next(e["start_g"] for e in run if e.get("voice") == "sensor" and "start_g" in e)
    return {
        "start_g": start,
        "end_g": ends[-1] if ends else start,
        "outcome": outcome,
        "attempts": len(verdicts),
        "first_pass": outcome == "done" and len(verdicts) == 1,
        "sweeps": len(sweeps),
        "validator_rejections": sum(1 for e in run if e.get("voice") == "decided" and e.get("ok") is False),
        "ai_offline_events": sum(1 for e in run if e.get("voice") == "proposed" and "error" in e),
        "edge": any(e.get("voice") == "edge" for e in run),
        "offline": any(e.get("voice") == "edge" and e.get("offline") for e in run),
    }


def table(paths: list[Path]) -> dict:
    facts: list[dict] = []
    for p in paths:
        events = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        for run in split_runs(events):
            facts.append(run_facts(run))
    n = len(facts)
    outcomes = Counter(f["outcome"] for f in facts)
    done = [f for f in facts if f["outcome"] == "done"]
    offline = [f for f in facts if f["offline"]]
    return {
        "runs": n,
        "edge_runs": sum(f["edge"] for f in facts),
        "offline_runs": len(offline),
        "offline_outcomes": dict(Counter(f["outcome"] for f in offline)),
        "outcomes": dict(outcomes),
        "first_pass_rate": round(sum(f["first_pass"] for f in facts) / n, 3) if n else None,
        "done_rate": round(len(done) / n, 3) if n else None,
        "mean_attempts": round(sum(f["attempts"] for f in facts) / n, 2) if n else None,
        "max_end_residue_g_when_done": max((f["end_g"] for f in done), default=None),
        "validator_rejections": sum(f["validator_rejections"] for f in facts),
        "ai_offline_events": sum(f["ai_offline_events"] for f in facts),
        "source": "logs only (SIMULATED or real, as logged)",
    }


if __name__ == "__main__":
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        sys.exit("usage: eval_table.py <log.jsonl> [...]")
    print(json.dumps(table(paths), indent=2))
