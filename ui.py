"""Dashboard for the rig: five pages, all read-only except POST /cancel.

    python3 ui.py --demo            # SIMULATED loop writes runs/sim_*.jsonl, serves on :8000
    open http://127.0.0.1:8000

Pages
    /               live run: workflow, verdict, mass, proposal, validator decision
    /runs           every run in runs/*.jsonl, with hash-chain status per log
    /offline        ESP32 B edge votes and the rule baseline behind them
    /results        results table generated from the logs by eval_table.py
    /calibration    scale and camera calibration state, and what is still a placeholder

The planner and validator never import this module. It only listens to the audit log
(live page) or reads the logs (other pages). Cancel sets a flag the run loop already
checks during the countdown, the same path as the hardware cancel button.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import threading
import time
from collections import Counter
from html import escape as esc
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response

from announce import TEMPLATES as ANNOUNCE_TEXT
from app import AuditLog, Scenario, run, verify_log
from cases import PERCEPTION_FRAME, case_title
from camera import DIFF_THRESHOLD as CAMERA_DIFF, MIN_AREA_FRAC as CAMERA_MIN_AREA
from runctl import LogFollower, request_cancel
import secrets
from fastapi import Header, HTTPException
from edge import EdgeVote, MIN_CONF, fuse
from eval_table import run_facts, split_runs, table
from rig import FakeRig
from rules import MAX_ATTEMPTS, RESIDUE_ABS_G, RESIDUE_FRAC

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = ROOT / "runs"
DEFAULT_CALIB_DIR = ROOT / "calibration"
FIRMWARE_A = ROOT / "firmware" / "cleansense_a" / "cleansense_a.ino"
# Mirror of firmware/cleansense_b/piezo_rules.h; tests/test_phase4.py checks they match the header.
PIEZO_HARD_PEAK, PIEZO_HARD_DECAY_MS, PIEZO_DRY_BURSTS = 2000, 20, 2

STEPS = ("sense", "propose", "validate", "act", "verify", "decide")
NAV = [("/", "Live"), ("/runs", "Runs"), ("/offline", "Offline tier"),
       ("/results", "Results"), ("/calibration", "Calibration")]
OUTCOME_LABEL = {"done": "Clean", "human": "Human takes over", "cancelled": "Cancelled", "aborted": "Aborted"}
OUTCOME_CLASS = {"done": "ok", "human": "off", "cancelled": "", "aborted": "warn"}


_MESS_CLASSES = {"dry_debris", "liquid", "mixed", "unknown", "unsupported_hazard"}


def _short(x, n: int = 160) -> Optional[str]:
    """Log text is data: keep only a short string, never anything else."""
    return x[:n] if isinstance(x, str) and x else None


def _unit(x) -> Optional[float]:
    return round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 else None


class DashboardState:
    def __init__(self, simulated: bool = True) -> None:
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.simulated = simulated
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.scenario: Optional[str] = None
            self.start_g: Optional[float] = None
            self.end_g: Optional[float] = None
            self.plan: Optional[str] = None
            self.verdict: Optional[str] = None
            self.ai_offline = False
            self.ai_reason: Optional[str] = None
            self.ai_provider: Optional[str] = None
            self.setup: Optional[dict] = None
            self.case: Optional[str] = None
            self.sense: Optional[dict] = None
            self.decisions: list[dict] = []
            self.edge: Optional[dict] = None
            self.camera_clean: Optional[bool] = None
            self.step: Optional[str] = None
            self.proposal: Optional[dict] = None
            self.decision: Optional[dict] = None
            self.readings: list[float] = []
            self.events: list[dict] = []
        self.cancel_event.clear()

    def on_event(self, ev: dict) -> None:
        with self._lock:
            self.events = (self.events + [ev])[-20:]
            voice = ev.get("voice")
            if voice == "setup":  # first event of a run: drop the previous run's details
                self.setup = {k: ev.get(k) for k in ("scale", "camera", "edge", "planner", "providers")}
                self.case, self.sense = case_title(ev.get("case")), None
                self.edge, self.decisions, self.camera_clean = None, [], None
                self.proposal, self.decision, self.verdict, self.plan = None, None, None, None
            if voice == "proposed":
                self.decisions = []
            if voice == "decided":
                self.decisions = (self.decisions + [{k: ev.get(k) for k in ("ok", "reasons", "source", "rung", "action")}])[-4:]
            if voice in ("perceived", "speaker", "announced", "announce_failed"):
                self._on_sense(voice, ev)
            if voice == "edge":
                self.edge = {k: ev.get(k) for k in ("operator", "vote", "fused", "note")}
            if voice == "sensor" and "start_g" in ev:
                self.start_g, self.end_g, self.verdict = ev["start_g"], None, None
                self.readings = [ev["start_g"]]
                self.step, self.proposal, self.decision = "sense", None, None
                self.camera_clean = None
            if voice == "sensor" and "end_g" in ev:
                self.end_g, self.verdict = ev["end_g"], ev.get("verdict")
                if "camera_clean" in ev:
                    self.camera_clean = bool(ev["camera_clean"])
                self.readings.append(ev["end_g"])
                self.step = "decide" if self.verdict else "verify"
            if voice == "allowed" and "rung" in ev:
                self.plan = ev["rung"]
                self.step = "act"
            if voice == "allowed" and ("cancelled" in ev or "fault" in ev):
                self.step = "decide"
                self.verdict = "cancelled" if "cancelled" in ev else "aborted"
                self.cancel_event.clear()  # the run acted on it; the next run starts clean
            if voice == "proposed":
                self.step = "propose"
                if "error" in ev:
                    self.proposal = {"error": ev["error"], "reason_code": ev.get("reason")}
                    self.ai_reason = ev.get("reason") or self.ai_reason
                    self.ai_offline = self.ai_offline or bool(ev.get("ai_offline")) \
                        or ev["error"] == "PlannerUnavailable"
                elif isinstance(ev.get("plan"), dict):
                    p = ev["plan"]
                    self.proposal = {k: p.get(k) for k in ("mess_type", "rung", "action", "reason")}
                    self.ai_provider = ev.get("provider") or self.ai_provider
                    self.ai_reason = None  # a good plan clears the last error
            if voice == "decided":
                self.step = "validate"
                self.decision = {"ok": bool(ev.get("ok")), "reasons": list(ev.get("reasons") or []),
                                 "source": ev.get("source")}

    def _on_sense(self, voice: str, ev: dict) -> None:
        """Camera look and spoken announcement, for the Sense card. Called with the lock held.
        The sentence shown is always a fixed template looked up by key, never text from a model."""
        n = self.sense = dict(self.sense or {})
        if voice == "perceived":
            n["looked"] = True
            seen = ev.get("mess_type")
            n["seen"] = seen if seen in _MESS_CLASSES else None
            n["provider"] = _short(ev.get("provider"), 40)
            n["confidence"] = _unit(ev.get("confidence"))
            n["error"] = _short(ev.get("reason") or ev.get("error"), 40) if "error" in ev else None
            n["frame"] = ev.get("frame") is True
            t = ev.get("t")
            n["frame_t"] = t if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) else 0
        elif voice == "speaker":
            n["voice_set"] = ev.get("spoken") is True
            n["speaker_note"] = _short(ev.get("note"))
        elif voice == "announced":
            n["sentence"] = ANNOUNCE_TEXT.get(ev.get("key")) if isinstance(ev.get("key"), str) else None
            n["spoken"] = ev.get("spoken") is True
        elif voice == "announce_failed":
            n["announce_error"] = _short(ev.get("reason"), 40) or "failed"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "scenario": self.scenario,
                "mass_start_g": self.start_g,
                "mass_now_g": self.end_g,
                "last_rung": self.plan,
                "verdict": self.verdict,
                "ai_offline": self.ai_offline,
                "ai_reason": self.ai_reason,
                "ai_provider": self.ai_provider,
                "setup": self.setup,
                "case": self.case,
                "sense": None if self.sense is None else dict(self.sense),
                "edge": self.edge,
                "camera_clean": self.camera_clean,
                "decisions": list(self.decisions),
                "cancel_requested": self.cancel_event.is_set(),
                "step": self.step,
                "steps": list(STEPS),
                "proposal": self.proposal,
                "decision": self.decision,
                "readings": list(self.readings),
                "recent_events": self.events[-10:],
                "SIMULATED": self.simulated,
            }


class CancelableRig:
    """Wraps a rig so the dashboard's cancel button reaches the run loop."""

    def __init__(self, inner, flag: threading.Event) -> None:
        self._inner = inner
        self._flag = flag

    def cancel_requested(self) -> bool:
        return self._flag.is_set() or self._inner.cancel_requested()

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------- log readers (read-only; the logs are the only source) ----------------
def log_files(log_dir: Path) -> list[Path]:
    return sorted(Path(log_dir).glob("*.jsonl")) if Path(log_dir).is_dir() else []


def is_simulated(path: Path) -> bool:
    return path.name.startswith("sim_")


def _events(path: Path) -> tuple[list[dict], Optional[str]]:
    try:
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()], None
    except (OSError, ValueError) as exc:
        return [], f"unreadable: {type(exc).__name__}"


def _chain(path: Path) -> tuple[bool, Optional[int], Optional[str]]:
    try:
        ok, bad = verify_log(path)
        return ok, bad, None
    except Exception as exc:  # a corrupt file is reported, never hidden
        return False, None, type(exc).__name__


def run_rows(log_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for p in log_files(log_dir):
        events, _ = _events(p)
        for run_events in split_runs(events):
            if not run_events:
                continue
            f = run_facts(run_events)
            edge = next((e for e in run_events if e.get("voice") == "edge"), None)
            plan = next((e["plan"] for e in run_events
                         if e.get("voice") == "proposed" and isinstance(e.get("plan"), dict)), None)
            mess = (edge or {}).get("fused") or (plan or {}).get("mess_type") or "not recorded"
            rows.append({**f, "file": p.name, "simulated": is_simulated(p), "t": run_events[0].get("t", 0),
                         "mess": mess, "edge": edge})
    rows.sort(key=lambda r: r["t"], reverse=True)
    return rows


def results_table(log_dir: Path) -> Optional[dict]:
    paths = log_files(log_dir)
    if not paths:
        return None
    try:
        return table(paths)
    except (OSError, ValueError, KeyError):
        return None


def camera_checks(log_dir: Path) -> Counter:
    """Counts (verdict, camera_clean) pairs from end-of-run sensor events."""
    c: Counter = Counter()
    for p in log_files(log_dir):
        events, _ = _events(p)
        for e in events:
            if e.get("voice") == "sensor" and "camera_clean" in e and "verdict" in e:
                c[(e["verdict"], bool(e["camera_clean"]), is_simulated(p))] += 1
    return c


def _when(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) + " UTC" if t else "not recorded"


def _pill(outcome: str) -> str:
    return f'<span class="tag {OUTCOME_CLASS.get(outcome, "")}">{esc(OUTCOME_LABEL.get(outcome, outcome))}</span>'


def _source(simulated: bool) -> str:
    return '<span class="tag sim">SIMULATED</span>' if simulated else '<span class="tag">Rig log</span>'


def _empty(text: str) -> str:
    return f'<div class="empty"><p>{text}</p></div>'


# ---------- shared page chrome ---------------------------------------------------
CSS = """
:root{--bg:#eef1ef;--surf:#f8faf9;--ink:#15201c;--mut:#53625c;--line:#c8d1cc;--todo:#5f6c66;
  --act:#2457a6;--act-t:#dae6f6;--clean:#1d7a4b;--clean-t:#d9eee2;--retry:#a85f00;--retry-t:#f9e6c8;
  --human:#b3261e;--human-t:#f6dcd9;--tint:#e3e8e5;color-scheme:light}
[hidden]{display:none!important}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:inherit}
.top{background:var(--surf);border-bottom:1px solid var(--line)}
.top-in{max-width:1080px;margin:0 auto;padding:0 16px;display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;min-height:60px}
.brand{font-weight:700;font-size:20px;letter-spacing:-.02em;text-decoration:none;margin-right:auto;padding-right:12px}
nav{display:flex;flex-wrap:wrap;gap:0 4px}
nav a{display:inline-flex;align-items:center;min-height:44px;padding:0 10px;color:var(--mut);text-decoration:none;font-weight:500;border-bottom:3px solid transparent}
nav a[aria-current=page]{color:var(--ink);font-weight:700;border-bottom-color:var(--act)}
nav a:hover{color:var(--ink)}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--act);outline-offset:3px}
main{max-width:1080px;margin:0 auto;padding:36px 16px 56px}
.intro{margin:0 0 28px}
.intro h1{font-size:clamp(30px,4.6vw,44px);font-weight:600;letter-spacing:-.02em;line-height:1.08;margin:0 0 10px}
.intro p{margin:0;color:var(--mut);max-width:60ch;font-size:17px}
.tag{display:inline-block;font-size:13px;font-weight:600;line-height:1;padding:6px 10px;border-radius:999px;background:var(--tint);color:var(--ink);vertical-align:middle;white-space:nowrap}
.tag.sim{background:transparent;border:1px solid var(--retry);color:var(--retry)}
.tag.ok{background:transparent;border:1px solid var(--clean);color:var(--clean)}
.tag.off{background:transparent;border:1px solid var(--human);color:var(--human)}
.tag.warn{background:transparent;border:1px solid var(--retry);color:var(--retry)}
.card{background:var(--surf);border:1px solid var(--line);border-radius:10px;padding:22px}
.grid{display:grid;gap:16px}
.g4{grid-template-columns:repeat(4,1fr)}.g2{grid-template-columns:1fr 1fr}
@media (max-width:820px){.g4{grid-template-columns:1fr 1fr}.g2{grid-template-columns:1fr}}
@media (max-width:480px){.g4{grid-template-columns:1fr}}
.stat{font-size:40px;font-weight:600;letter-spacing:-.03em;line-height:1;font-variant-numeric:tabular-nums}
.stat-label{color:var(--mut);font-size:15px;margin-top:10px}
h2{font-size:21px;font-weight:700;letter-spacing:-.015em;line-height:1.2;margin:0 0 6px}
.lede{color:var(--mut);margin:0 0 16px;font-size:15px}
.section{margin-top:32px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surf)}
table{width:100%;border-collapse:collapse;font-size:15px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:14px;background:var(--tint)}
tr:last-child td{border-bottom:0}
td.num{white-space:nowrap}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:13px}
.empty{border:1px dashed var(--todo);border-radius:10px;padding:24px;color:var(--mut)}
.empty p{margin:0}
.bar{height:12px;background:var(--tint);border-radius:999px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--ink);border-radius:999px}
.bar i.ok{background:var(--clean)}.bar i.off{background:var(--human)}.bar i.warn{background:var(--retry)}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.kv{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;margin:0;font-size:15px}
.kv dt{color:var(--mut)}.kv dd{margin:0;font-variant-numeric:tabular-nums}
.flow{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
.flow li{position:relative;background:var(--surf);border:1px solid var(--line);border-radius:10px;padding:16px;font-size:15px}
.flow li b{display:block;font-size:17px;margin-bottom:4px}
.flow li:not(:last-child)::after{content:"";position:absolute;right:-10px;top:50%;width:10px;height:2px;background:var(--todo)}
@media (max-width:820px){.flow{grid-template-columns:1fr}.flow li:not(:last-child)::after{display:none}}
.note{color:var(--mut);font-size:14px}
pre{white-space:pre-wrap;font:12px/1.5 "IBM Plex Mono",monospace;color:var(--mut);max-height:260px;overflow:auto;margin:12px 0 0}
details{margin-top:32px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--mut);font-size:15px;display:flex;align-items:center;min-height:44px;font-weight:600}
button{font:inherit;font-size:15px;font-weight:600;padding:0 16px;min-height:44px;border:2px solid var(--human);background:transparent;color:var(--human);border-radius:8px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
/* live page */
.live{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px;align-items:start}
@media (max-width:900px){.live{grid-template-columns:1fr}}
.status{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}
.verdict{font-size:clamp(34px,5.4vw,54px);font-weight:600;letter-spacing:-.025em;line-height:1.05;margin:0}
.verdict.done{color:var(--clean)}.verdict.human{color:var(--human)}.verdict.retry{color:var(--retry)}
.verdict.idle,.verdict.run{color:var(--ink)}
.why{color:var(--mut);margin:8px 0 0;max-width:62ch;font-size:17px}
.rail{list-style:none;display:flex;flex-wrap:wrap;gap:6px;padding:0;margin:20px 0 0}
.rail li{font-size:14px;font-weight:500;padding:6px 12px;border-radius:999px;border:1px solid var(--line);color:var(--mut);background:var(--surf)}
.rail li.done{color:var(--clean);border-color:var(--clean);background:var(--clean-t)}
.rail li.now{color:var(--act);border-color:var(--act);background:var(--act-t);font-weight:600}
.steps{list-style:none;padding:0;margin:28px 0 0;display:grid;gap:12px;counter-reset:s}
.step{background:var(--surf);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:10px;padding:18px 20px;counter-increment:s}
.step.ok{border-left-color:var(--clean)}.step.no{border-left-color:var(--human)}.step.mid{border-left-color:var(--retry)}.step.on{border-left-color:var(--act)}
.step-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 6px}
.step-head h2{font-size:17px;margin:0}
.step-head h2::before{content:counter(s) ". ";color:var(--mut);font-weight:500}
.by{font-size:14px;color:var(--mut)}
.claim{font-size:19px;font-weight:600;margin:0;letter-spacing:-.01em}
.quote{margin:10px 0 0;padding:10px 14px;background:var(--tint);border-radius:8px;color:var(--ink);font-size:15px}
.quote:empty{display:none}
.frame{display:block;max-width:100%;max-height:220px;margin-top:12px;border:1px solid var(--line);border-radius:8px;background:var(--tint)}
.sub{color:var(--mut);font-size:14px;margin:8px 0 0}
.rules{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:6px;font-size:15px}
.rules b{font-weight:600;margin-right:6px}
.meter{position:relative;height:14px;background:var(--tint);border-radius:999px;margin:14px 0 6px;overflow:hidden}
.meter i{position:absolute;inset:0 auto 0 0;background:var(--todo);border-radius:999px;transition:width .4s}
.meter i.ok{background:var(--clean)}.meter i.mid{background:var(--retry)}
.meter s{position:absolute;top:0;bottom:0;width:2px;background:var(--ink)}
.nums{display:flex;justify-content:space-between;gap:12px;font-size:14px;color:var(--mut);font-variant-numeric:tabular-nums}
.rig h2{font-size:17px;margin:0 0 10px}
.rig dl{display:grid;grid-template-columns:auto 1fr;gap:10px 14px;margin:0;font-size:15px}
.rig dt{color:var(--mut)}.rig dd{margin:0;overflow-wrap:anywhere}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:1px;background:var(--todo)}
.dot.ok{background:var(--clean)}.dot.no{background:var(--human)}.dot.mid{background:var(--retry)}
.act-row{margin-top:20px;display:grid;gap:10px}
.act-row button{width:100%}
.msg{font-size:14px;color:var(--mut);margin:0;min-height:1.4em}
@media (prefers-reduced-motion: reduce){.meter i{transition:none}}
.msg:empty{display:none}
#scenario::first-letter{text-transform:uppercase}
@media (max-width:900px){body{padding-bottom:84px}
  #cancel{position:fixed;left:16px;right:16px;bottom:16px;width:auto;z-index:5;background:var(--surf);box-shadow:0 2px 10px rgba(21,32,28,.18)}}
"""


def _shell(title: str, path: str, body: str, script: str = "") -> str:
    links = "".join(
        '<a href="{}"{}>{}</a>'.format(href, ' aria-current="page"' if href == path else "", esc(label))
        for href, label in NAV)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanSense: {esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body>
<header class="top"><div class="top-in">
  <a class="brand" href="/">CleanSense</a>
  <nav aria-label="Pages">{links}</nav>
</div></header>
<main>{body}</main>
{f'<script>{script}</script>' if script else ''}
</body></html>"""


# ---------- pages -----------------------------------------------------------------
LIVE_BODY = """
<div class="intro">
  <div class="status" aria-label="Status">
    <span class="tag sim" id="sim" hidden>Simulated rig</span>
    <span class="tag" id="ai">Planner: waiting</span>
    <span class="tag" id="case" hidden></span>
  </div>
  <h1 id="scenario">Live run</h1>
</div>

<div class="live">
  <div>
    <p id="verdict" class="verdict idle" aria-live="polite">Waiting for a run</p>
    <p class="why" id="why">Start a run with run_rig.py. This page follows its log.</p>
    <ol class="rail" id="rail" aria-label="Where the run is"></ol>

    <ol class="steps">
      <li class="step" id="c-sense">
        <div class="step-head"><h2>Camera sees</h2><span class="by" id="sense-by"></span></div>
        <p class="claim" id="sense-claim">No camera look yet.</p>
        <p class="quote" id="sense-say"></p>
        <p class="sub" id="sense-sub"></p>
        <img class="frame" id="sense-img" alt="The camera frame the look was made from" hidden>
      </li>
      <li class="step" id="c-prop">
        <div class="step-head"><h2>Proposed</h2><span class="by" id="prop-by"></span></div>
        <p class="claim" id="prop-claim">Nothing proposed yet.</p>
        <p class="quote" id="prop-why"></p>
        <p class="sub" id="prop-input"></p>
      </li>
      <li class="step" id="c-check">
        <div class="step-head"><h2>Checked by the rules</h2><span class="by" id="check-by"></span></div>
        <p class="claim" id="check-claim">Not checked yet.</p>
        <ul class="rules" id="check-rules"></ul>
      </li>
      <li class="step" id="c-meas">
        <div class="step-head"><h2>Measured</h2><span class="by">Scale and camera decide clean</span></div>
        <p class="claim" id="meas-claim">No reading yet.</p>
        <div class="meter" id="meter" hidden><i id="meter-bar"></i><s id="meter-limit"></s></div>
        <div class="nums" id="meter-nums" hidden><span id="n-start"></span><span id="n-limit"></span></div>
        <p class="sub" id="meas-sub"></p>
      </li>
    </ol>
  </div>

  <aside class="card rig" aria-label="Rig">
    <h2>Rig</h2>
    <dl id="rig"><dt>Status</dt><dd>No run yet</dd></dl>
    <div class="act-row">
      <button id="cancel" type="button">Cancel before the next sweep</button>
      <p class="msg" id="cancel-msg" aria-live="polite"></p>
      <p class="note">Cancel stops the run before its next sweep. The e-stop on the rig stops motion at once.</p>
    </div>
  </aside>
</div>

<details>
  <summary>Raw audit log</summary>
  <pre id="log"></pre>
</details>
"""

LIVE_SCRIPT = """
const $ = id => document.getElementById(id);
const set = (id, text) => { const el = $(id); if(el) el.textContent = text == null ? "" : text; };
const STEPS = {sense:"Sense", propose:"Propose", validate:"Check", act:"Sweep", verify:"Verify", decide:"Decide"};
const MESS = {dry_debris:"dry debris", liquid:"liquid", mixed:"mixed mess", unknown:"an unknown mess",
  unsupported_hazard:"a material it must not touch"};
const RUNG = {L1:"Gentle sweep (L1): one slow, narrow pass", L2:"Firm sweep (L2): three passes, full width",
  L3:"Absorbent pass (L3): one slow pass with the pad"};
const VERDICT = {done:["Clean","done","The scale is within the limit and the camera agrees."],
  retry:["Not clean yet","retry","Residue is above the limit, so the next rung is tried."],
  human:["A person takes over","human","The run stopped safely and needs a person."],
  cancelled:["Cancelled","retry","Cancelled before the sweep. Nothing moved."],
  aborted:["Stopped by a fault","human","The rig reported a fault, so the run stopped. See the raw log."]};
const PROVIDER = {gemini:"Gemini", openrouter:"OpenRouter", anthropic:"Claude"};
const AI_REASON = {not_configured:"no AI key is set",
  rate_limited:"the AI provider is rate-limiting", timeout:"the AI timed out",
  auth_error:"the AI key was rejected", provider_error:"the AI provider returned an error",
  blocked:"the AI reply was blocked by the provider", truncated:"the AI reply was cut off",
  bad_request:"the AI request was refused", cap:"the AI call budget for this run is used up",
  circuit_open:"the AI was switched off after repeated failures",
  invalid_output:"the AI reply was not a valid plan", unavailable:"the AI was unavailable"};
const RULE = {V0:"Internal check", V1:"Format", V2:"Ladder", V3:"Sensitive surface", V4:"Hazard",
  V5:"Budget", V6:"Override", V7:"Temperature"};

function words(mess){ return MESS[mess] || (mess ? mess.replace(/_/g, " ") : "this mess"); }

function tag(id, text, cls){ const el = $(id); if(!el) return; el.textContent = text; el.className = "tag " + (cls || ""); }

function drawRail(step, steps){
  const rail = $("rail"); if(!rail) return;
  const idx = (steps || []).indexOf(step);
  rail.replaceChildren(...(steps || []).map((s, i) => {
    const li = document.createElement("li");
    li.textContent = STEPS[s] || s;
    li.className = idx < 0 ? "" : i < idx ? "done" : i === idx ? "now" : "";
    if(i === idx) li.setAttribute("aria-current", "step");
    return li;
  }));
}

function drawProposal(s){
  const p = s.proposal, card = $("c-prop");
  const viaAI = !!s.ai_provider && p && !p.error && p.reason !== "rules ladder" && p.reason !== "no ladder";
  if(!p){ set("prop-claim", "Nothing proposed yet."); set("prop-why", ""); set("prop-by", ""); card.className = "step"; }
  else if(p.error){
    set("prop-by", "AI");
    set("prop-claim", "No usable AI proposal, so the rules ladder plans this step.");
    set("prop-why", "Why: " + (AI_REASON[p.reason_code] || AI_REASON.unavailable) + ".");
    card.className = "step mid";
  } else {
    set("prop-by", viaAI ? "by " + (PROVIDER[s.ai_provider] || s.ai_provider) : "by the rules ladder");
    set("prop-claim", p.action === "human" ? "Hand over to a person, no sweep."
      : (RUNG[p.rung] || "Sweep " + (p.rung || "")) + ", for " + words(p.mess_type) + ".");
    // Model text is untrusted: written with textContent only, never parsed as markup.
    set("prop-why", viaAI && p.reason ? "“" + p.reason + "”"
      : p.action === "human" ? "There is no safe sweep for " + words(p.mess_type) + "."
      : "Next rung on the " + words(p.mess_type) + " ladder.");
    card.className = "step on";
  }
  const e = s.edge;
  set("prop-input", e ? "Mess type " + words(e.fused) + ": " + (e.note || "") +
    (e.vote ? " (edge vote " + e.vote.cls + " " + Number(e.vote.conf).toFixed(2) + ")" : "") : "");
}

function ruleLine(text){
  const li = document.createElement("li");
  const m = /^(V\\d)\\s*([^:]*):\\s*(.*)$/.exec(text);
  const b = document.createElement("b");
  if(m){ b.textContent = RULE[m[1]] || m[1]; li.append(b, m[3]); }
  else if(/^ok:/.test(text)){ b.textContent = "All rules"; li.append(b, "next rung on the ladder, within the limits"); }
  else if(/^human:/.test(text)){ b.textContent = "Hand-over"; li.append(b, "always allowed"); }
  else { li.textContent = text; }
  return li;
}

function drawCheck(s){
  const card = $("c-check"), list = $("check-rules");
  let ds = (s.decisions && s.decisions.length) ? s.decisions : (s.decision ? [s.decision] : []);
  // A failed AI call is already explained on the Proposed card; skip its empty-plan refusal.
  if(s.proposal && s.proposal.error) ds = ds.filter((d, i) => !(i === 0 && !d.ok && d.source !== "rules"));
  if(!ds.length){ set("check-claim", "Not checked yet."); set("check-by", ""); list.replaceChildren(); card.className = "step"; return; }
  const last = ds[ds.length - 1], overridden = ds.length > 1 && !ds[0].ok;
  set("check-claim", overridden ? (last.ok ? "Refused, so a safer step runs instead" : "Refused, and the fallback was refused too")
    : last.ok ? "Allowed" : "Refused");
  set("check-by", "");
  const rows = [];
  ds.forEach(d => {
    if(!d.ok){ (d.reasons || []).slice(0, 3).forEach(t => { const li = ruleLine(t); li.prepend(Object.assign(document.createElement("b"), {textContent: "Refused:"})); rows.push(li); }); }
    else if(overridden){
      const li = document.createElement("li"); const b = document.createElement("b");
      b.textContent = "Runs instead:";
      li.append(b, (d.action === "human" ? "hand over to a person" : (RUNG[d.rung] || d.rung || "the next rung")) + ", from the rules ladder.");
      rows.push(li);
    } else { (d.reasons || []).slice(0, 3).forEach(t => rows.push(ruleLine(t))); }
  });
  list.replaceChildren(...rows);
  card.className = "step " + (overridden && last.ok ? "mid" : last.ok ? "ok" : "no");
}

function drawMeasure(s){
  const r = s.readings || [], card = $("c-meas");
  if(!r.length){ set("meas-claim", "No reading yet."); $("meter").hidden = $("meter-nums").hidden = true; set("meas-sub", ""); card.className = "step"; return; }
  const start = r[0], now = r[r.length - 1], limit = Math.min(0.5, 0.03 * start);
  const max = Math.max(start, now, 0.01);
  set("meas-claim", r.length > 1 ? start.toFixed(1) + " g to " + now.toFixed(1) + " g" : start.toFixed(1) + " g on the tile");
  $("meter").hidden = $("meter-nums").hidden = false;
  const bar = $("meter-bar");
  bar.style.width = (Math.max(0, now) / max * 100).toFixed(1) + "%";
  bar.className = now <= limit ? "ok" : r.length > 1 ? "mid" : "";
  $("meter-limit").style.left = (limit / max * 100).toFixed(2) + "%";
  set("n-start", "start " + start.toFixed(1) + " g");
  set("n-limit", "clean at or under " + limit.toFixed(2) + " g");
  const cam = s.camera_clean;
  set("meas-sub", (s.SIMULATED ? "Simulated scale. " : "") +
    (cam === true ? "Camera agrees the tile looks clean." : cam === false ? "Camera still sees mess." : "No camera verdict yet."));
  card.className = "step " + (s.verdict === "done" ? "ok" : s.verdict === "human" ? "no" : s.verdict ? "mid" : "on");
}

function drawSense(s){
  const card = $("c-sense"), n = s.sense || {}, img = $("sense-img");
  const prov = PROVIDER[n.provider] || n.provider;
  let claim, cls = "";
  if(n.looked && n.seen){
    const w = words(n.seen);
    claim = w.charAt(0).toUpperCase() + w.slice(1) + (n.confidence != null ? " (" + Math.round(n.confidence * 100) + "% sure)" : "");
    cls = n.seen === "liquid" || n.seen === "unsupported_hazard" ? "mid" : "on";
  } else if(n.looked && n.error){
    claim = "The camera look failed: " + (AI_REASON[n.error] || AI_REASON.unavailable) + ".";
    cls = "mid";
  } else if(n.looked){ claim = "The camera look gave no usable class."; cls = "mid"; }
  else { claim = s.SIMULATED ? "Simulated runs have no camera look."
    : s.step || s.verdict ? "No camera look in this run. It needs --llm and a camera." : "No camera look yet."; }
  set("sense-claim", claim);
  set("sense-by", n.looked && prov ? "Looked at by " + prov : "");
  set("sense-say", n.sentence ? "\u201c" + n.sentence + "\u201d" : "");
  const sub = [];
  if(n.sentence){ sub.push(n.spoken ? "Spoken on the PC speaker." : "Not spoken: " + (n.speaker_note || "no voice set") + "."); }
  if(n.announce_error){ sub.push("Speech failed (" + n.announce_error + "); the run went on."); }
  if(n.looked && n.seen && !s.SIMULATED){ sub.push("This look can only add caution; it never lowers a rule."); }
  set("sense-sub", sub.join(" "));
  if(n.frame){
    const src = "/frame.png?t=" + n.frame_t;
    if(img.dataset.src !== src){ img.dataset.src = src; img.src = src; }
    img.hidden = false;
  } else { img.hidden = true; img.removeAttribute("src"); img.dataset.src = ""; }
  card.className = "step " + cls;
}

function drawRig(s){
  const st = s.setup || {}, rows = [];
  const row = (k, v, cls) => rows.push([k, v, cls]);
  row("Source", s.SIMULATED ? "Simulated rig" : "Rig log", s.SIMULATED ? "mid" : "ok");
  if(!s.SIMULATED || st.scale){ row("Scale and wiper", st.scale || "not reported", st.scale ? "ok" : "mid"); }
  row("Camera", st.camera || (s.SIMULATED ? "simulated" : "none: a run cannot end clean"), st.camera || s.SIMULATED ? "ok" : "no");
  if(st.edge || s.edge){ row("Edge board", st.edge || "simulated vote", "ok"); }
  const prov = (st.providers || []).map(x => PROVIDER[x] || x);
  row("Planner", st.planner === "ai" ? "AI: " + (prov.join(", ") || "no provider") + ", rules as fallback" : "Rules ladder", st.planner === "ai" && !prov.length ? "no" : "ok");
  $("rig").replaceChildren(...rows.flatMap(([k, v, cls]) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); const dot = document.createElement("span");
    dot.className = "dot " + (cls || ""); dd.append(dot, v); return [dt, dd];
  }));
}

async function tick(){
  let s;
  try{ s = await (await fetch("/state")).json(); }
  catch(e){ set("why", "Lost contact with the dashboard server. Is ui.py still running?"); return; }
  $("sim").hidden = !s.SIMULATED;
  if(s.ai_offline){ tag("ai", "AI off: " + (AI_REASON[s.ai_reason] || AI_REASON.unavailable), "off"); }
  else if(s.ai_provider){ tag("ai", "AI: " + (PROVIDER[s.ai_provider] || s.ai_provider), "ok"); }
  else if(s.setup && s.setup.planner === "ai"){ tag("ai", "AI: waiting for the first call", ""); }
  else { tag("ai", s.setup ? "Rules ladder, no AI" : "Planner: waiting", ""); }
  set("scenario", s.scenario ? words(s.scenario.replace(/_on_/, " on ")) : "Live run");

  const v = VERDICT[s.verdict], vd = $("verdict");
  if(v){ vd.textContent = v[0]; vd.className = "verdict " + v[1]; set("why", v[2]); }
  else if(s.step){ vd.textContent = (STEPS[s.step] || "Working") + "…"; vd.className = "verdict run";
    set("why", s.cancel_requested ? "Cancel requested. The run stops before its next sweep." : "The run is in progress."); }
  else { vd.textContent = "Waiting for a run"; vd.className = "verdict idle";
    set("why", s.SIMULATED ? "The simulated loop starts the next run shortly." : "Start a run with run_rig.py. This page follows its log."); }

  const cs = $("case"); cs.hidden = !s.case; cs.textContent = s.case ? "Case: " + s.case : "";
  drawRail(s.step, s.steps); drawSense(s); drawProposal(s); drawCheck(s); drawMeasure(s); drawRig(s);
  set("log", (s.recent_events || []).map(e => JSON.stringify(e)).join("\\n"));
}

$("cancel").onclick = async () => {
  set("cancel-msg", "Sending…");
  try{
    const r = await fetch("/cancel", {method: "POST", headers: {"x-cancel-token": CANCEL_TOKEN}});
    set("cancel-msg", r.ok ? "Cancel requested. The run stops before its next sweep."
      : "Cancel refused: reload this page to get a fresh token.");
  }catch(e){ set("cancel-msg", "Cancel not sent: the dashboard server is not reachable."); }
  tick();
};
tick(); setInterval(tick, 700);
"""



def page_live(simulated: bool, token: str = "") -> str:
    body = LIVE_BODY  # one markup for both modes; the script reads SIMULATED from /state
    script = f"const CANCEL_TOKEN={json.dumps(token)};\n" + LIVE_SCRIPT
    return _shell("Live run", "/", body, script)


def page_runs(log_dir: Path) -> str:
    rows = run_rows(log_dir)
    files = log_files(log_dir)
    c = Counter(r["outcome"] for r in rows)
    stats = f"""<div class="grid g4" style="margin-bottom:8px">
  <div class="card"><div class="stat">{len(rows)}</div><div class="stat-label">Runs logged</div></div>
  <div class="card"><div class="stat">{c['done']}</div><div class="stat-label">Clean</div></div>
  <div class="card"><div class="stat">{c['human']}</div><div class="stat-label">Stopped for a person</div></div>
  <div class="card"><div class="stat">{c['cancelled'] + c['aborted']}</div><div class="stat-label">Cancelled or aborted</div></div>
</div>"""
    shown = rows[:12]
    if rows:
        trs = "".join(
            f"<tr><td class='num'>{_when(r['t'])}</td>"
            f"<td>{_source(r['simulated'])}</td>"
            f"<td>{esc(str(r['mess']))}</td>"
            f"<td>{_pill(r['outcome'])}</td>"
            f"<td class='num'>{r['start_g']:.1f} g to {r['end_g']:.1f} g</td>"
            f"<td class='num'>{r['attempts']} of {MAX_ATTEMPTS}</td>"
            f"<td class='num'>{r['validator_rejections']}</td></tr>"
            for r in shown)
        more = (f"<p class='note' style='margin:12px 0 0'>Showing the {len(shown)} newest of {len(rows)} runs. "
                "The full history is in the log files.</p>") if len(rows) > len(shown) else ""
        table_html = f"""{more}<div class="wrap"><table>
<thead><tr><th>Started</th><th>Source</th><th>Mess type</th><th>Outcome</th><th>Scale</th>
<th>Attempts</th><th>Rejected steps</th></tr></thead>
<tbody>{trs}</tbody></table></div>"""
    else:
        table_html = _empty("No runs logged yet. Start the simulated demo with <span class='mono'>python3 ui.py --demo</span>, "
                            "or run a rig session with <span class='mono'>run_rig.py</span>.")
    integrity = "".join(
        f"<li class='mono'>{esc(p.name)}: {'hash chain intact' if ok else ('chain broken at line ' + str(bad) if bad else 'could not verify (' + esc(err or '') + ')')}</li>"
        for p in files for ok, bad, err in [_chain(p)]) or "<li>No log files yet.</li>"
    body = f"""<div class="intro"><h1>Run history</h1>
<p>Every run in the audit logs, newest first. Each row is one run: what was proposed, what the rules
allowed, and what the scale and camera said at the end.</p></div>
{stats}
<section class="section"><h2>Runs</h2><p class="lede">Simulated runs are marked SIMULATED. Rig logs come from <span class="mono">run_rig.py</span>.</p>
{table_html}</section>
<section class="section"><h2>Log integrity</h2>
<p class="lede">Each log is a hash chain. A broken chain means the file was edited after the run.</p>
<ul class="note" style="padding-left:18px;margin:0">{integrity}</ul></section>"""
    return _shell("Run history", "/runs", body)


def _edge_text(edge: Optional[dict]) -> str:
    if not edge:
        return "none"
    vote = edge.get("vote")
    return f"{vote['cls']} {vote['conf']:.2f}" if vote else "no usable vote"


def page_offline(log_dir: Path) -> str:
    rows = [r for r in run_rows(log_dir) if r["edge"]]
    if rows:
        trs = ""
        for r in rows:
            e = r["edge"]
            trs += (f"<tr><td class='num'>{_when(r['t'])}</td><td>{_source(r['simulated'])}</td>"
                    f"<td>{esc(str(e.get('operator') or 'none'))}</td>"
                    f"<td class='num'>{esc(_edge_text(e))}</td>"
                    f"<td>{esc(str(e.get('fused') or 'not recorded'))}</td>"
                    f"<td>{esc(str(e.get('note') or ''))}</td>"
                    f"<td>{_pill(r['outcome'])}</td></tr>")
        votes_html = f"""<div class="wrap"><table>
<thead><tr><th>Started</th><th>Source</th><th>Operator said</th><th>Edge vote</th><th>Fused type</th>
<th>Why</th><th>Outcome</th></tr></thead><tbody>{trs}</tbody></table></div>"""
    else:
        votes_html = _empty("No edge votes logged yet. ESP32 B is not flashed on the rig yet, so no offline run has "
                            "happened. The demo writes simulated votes when started with <span class='mono'>--demo</span>.")
    body = f"""<div class="intro"><h1>Offline tier</h1>
<p>ESP32 B listens to the piezo on the tile and sends one vote. It needs no network and no LLM.
The vote can only make a run more careful. The scale and camera still decide whether the tile is clean.</p></div>

<ol class="flow" aria-label="Offline tier steps">
  <li><b>Piezo</b>An impact on the tile, sampled at 4 kHz.</li>
  <li><b>Features</b>Peak, decay and sub-impacts.</li>
  <li><b>Vote</b>Dry, hard or none. Never liquid.</li>
  <li><b>Fuse</b>A vote never adds motion.</li>
  <li><b>Check</b>Scale and camera decide clean.</li>
</ol>

<section class="section grid g2">
  <div class="card">
    <h2>Rule baseline</h2>
    <p class="lede">Uncalibrated thresholds from <span class="mono">piezo_rules.h</span>.</p>
    <dl class="kv">
      <dt>Hard</dt><dd>peak at least {PIEZO_HARD_PEAK}, decay at most {PIEZO_HARD_DECAY_MS} ms. Confidence 0.80</dd>
      <dt>Dry</dt><dd>at least {PIEZO_DRY_BURSTS} sub-impacts. Confidence 0.50</dd>
      <dt>Trust</dt><dd>a vote counts at confidence {MIN_CONF:.1f} or more (placeholder)</dd>
    </dl>
  </div>
  <div class="card">
    <h2>What the vote can do</h2>
    <dl class="kv">
      <dt>Agrees</dt><dd>the operator's type stands</dd>
      <dt>Disagrees</dt><dd>unknown: one gentle rung, then a person</dd>
      <dt>Hard</dt><dd>unsupported hazard: no motion, a person takes over</dd>
    </dl>
  </div>
</section>

<section class="section"><h2>Votes in the logs</h2>
<p class="lede">Each row is one edge event from a run. Simulated votes are marked SIMULATED.</p>
{votes_html}</section>

<p class="note" style="margin-top:24px">No accuracy is claimed for this tier. Its thresholds are not measured on labelled piezo data yet.</p>"""
    return _shell("Offline tier", "/offline", body)


def page_results(log_dir: Path) -> str:
    t = results_table(log_dir)
    if not t or not t["runs"]:
        body = f"""<div class="intro"><h1>Results</h1>
<p>Generated from the run logs by <span class="mono">eval_table.py</span>. Nothing on this page is typed by hand.</p></div>
{_empty("No runs to summarise yet. Results appear after the first logged run.")}"""
        return _shell("Results", "/results", body)
    n = t["runs"]
    outcomes = t["outcomes"]
    bars = ""
    for key in ("done", "human", "cancelled", "aborted"):
        cnt = outcomes.get(key, 0)
        pct = round(100 * cnt / n) if n else 0
        cls = OUTCOME_CLASS.get(key, "")
        bars += (f"<div style='margin-bottom:16px'><div class='row'><span>{esc(OUTCOME_LABEL[key])}</span>"
                 f"<span class='mono'>{cnt} of {n}</span></div>"
                 f"<div class='bar'><i class='{cls}' style='width:{pct}%'></i></div></div>")
    def pct(v):
        return "not measured" if v is None else f"{round(100 * v)}%"
    cards = [
        (str(n), "Runs in the logs"),
        (pct(t["done_rate"]), "Ended clean"),
        (pct(t["first_pass_rate"]), "Clean on the first pass"),
        ("not measured" if t["mean_attempts"] is None else f"{t['mean_attempts']:.2f}", "Mean attempts per run"),
        ("not measured" if t["max_end_residue_g_when_done"] is None else f"{t['max_end_residue_g_when_done']:.2f} g",
         "Most residue left on a clean tile"),
        (str(t["validator_rejections"]), "Steps the validator rejected"),
        (str(t["ai_offline_events"]), "Steps planned while AI was offline"),
        (str(t["offline_runs"]), "Runs on the offline tier"),
    ]
    cards_html = "".join(f'<div class="card"><div class="stat">{esc(v)}</div><div class="stat-label">{esc(l)}</div></div>'
                         for v, l in cards)
    sim_logs = sum(1 for p in log_files(log_dir) if is_simulated(p))
    rig_logs = len(log_files(log_dir)) - sim_logs
    body = f"""<div class="intro"><h1>Results</h1>
<p>Computed from {len(log_files(log_dir))} log file(s): {sim_logs} simulated, {rig_logs} from the rig.
{'These numbers are SIMULATED. They describe the software model, not the wiper.' if rig_logs == 0 else 'Rig numbers are real only where the source is a rig log.'}</p></div>
<div class="grid g4">{cards_html}</div>
<section class="section card"><h2>Outcomes</h2>
<p class="lede">Each run ends clean, with a person, cancelled, or aborted by a fault.</p>{bars}</section>
<section class="section"><h2>What this does not show</h2>
<p class="lede">False-clean rate needs the simulator's ground truth, so it is measured in <span class="mono">ablation.py</span>, not here.
The AI arm needs an API key and is not part of this table.</p></section>"""
    return _shell("Results", "/results", body)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def page_calibration(log_dir: Path, calib_dir: Path) -> str:
    cal = _read_json(calib_dir / "calibration.json")
    ref_ok = (calib_dir / "reference.npy").exists()
    if cal:
        mean, std = cal.get("mean_g"), cal.get("std_g")
        gate = (std is not None and std <= 0.2) and (mean is not None and abs(mean) <= 0.5)
        scale_html = f"""<dl class="kv">
<dt>Readings</dt><dd>{esc(str(cal.get('n', '?')))}</dd>
<dt>Mean, empty tile</dt><dd>{esc(str(mean))} g</dd>
<dt>Spread (std)</dt><dd>{esc(str(std))} g</dd>
<dt>Bench gate</dt><dd><span class="tag {'ok' if gate else 'off'}">{'Passes' if gate else 'Does not pass'}</span>
 std at most 0.2 g and mean within 0.5 g of zero</dd></dl>"""
    else:
        scale_html = _empty("Not calibrated yet. With the tile empty and the head parked, run "
                            "<span class='mono'>calibrate.py</span>.")
    fw = FIRMWARE_A.read_text(encoding="utf-8") if FIRMWARE_A.exists() else ""
    m = re.search(r"CAL_FACTOR\s*=\s*([0-9.]+)f?", fw)
    cal_factor = m.group(1) if m else "not found"
    placeholder = cal_factor == "1.0"
    checks = camera_checks(log_dir)
    if checks:
        rows = "".join(
            f"<tr><td>{esc(v)}</td><td>{'yes' if cam else 'no'}</td><td class='num'>{n}</td>"
            f"<td>{_source(sim)}</td></tr>"
            for (v, cam, sim), n in sorted(checks.items()))
        cam_html = f"""<div class="wrap"><table><thead><tr><th>Scale verdict</th><th>Camera says clean</th>
<th>Count</th><th>Source</th></tr></thead><tbody>{rows}</tbody></table></div>"""
    else:
        cam_html = _empty("No camera checks logged yet. Camera checks need a reference frame of the empty tile.")
    body = f"""<div class="intro"><h1>Calibration and camera</h1>
<p>What the scale and the camera were checked against, and which numbers are still placeholders.
Nothing here is measured on the rig until the calibration file exists.</p></div>

<div class="grid g2">
  <section class="card"><h2>Scale</h2>{scale_html}</section>
  <section class="card"><h2>Reference frame</h2>
    <p class="lede">The empty-tile image the camera compares against.</p>
    <p style="margin:0"><span class="tag {'ok' if ref_ok else 'warn'}">{'Saved' if ref_ok else 'Not captured'}</span></p>
  </section>
</div>

<section class="section grid g2">
  <div class="card">
    <h2>Scale factor</h2>
    <dl class="kv">
      <dt>CAL_FACTOR</dt><dd class="mono">{esc(cal_factor)}</dd>
      <dt>Status</dt><dd>{'Placeholder. Readings are raw counts until a known-mass test sets it.' if placeholder else 'Set in firmware'}</dd>
    </dl>
  </div>
  <div class="card">
    <h2>Camera rules</h2>
    <dl class="kv">
      <dt>Mess pixel</dt><dd>grey level differs by more than {CAMERA_DIFF} from the empty tile</dd>
      <dt>Visually empty</dt><dd>under {CAMERA_MIN_AREA * 100:.1f}% of the tile covered</dd>
      <dt>Scale rule</dt><dd>clean when the residue is at most the smaller of {RESIDUE_ABS_G} g and {RESIDUE_FRAC * 100:.0f}% of the start mass</dd>
      <dt>Camera rule</dt><dd>clean when the mess covers at most the larger of {RESIDUE_FRAC * 100:.0f}% of the start area and {CAMERA_MIN_AREA * 100:.1f}% of the tile</dd>
      <dt>Camera error</dt><dd>never counts as clean</dd>
    </dl>
  </div>
</section>

<section class="section"><h2>Camera agreement in the logs</h2>
<p class="lede">Where the scale and camera disagree, the run goes to a person.</p>{cam_html}</section>"""
    return _shell("Calibration", "/calibration", body)


# ---------- app and demo ---------------------------------------------------------
def make_app(state: DashboardState, log_dir: Path = DEFAULT_LOG_DIR,
             calib_dir: Path = DEFAULT_CALIB_DIR, token_arg: Optional[str] = None) -> FastAPI:
    token = token_arg or secrets.token_urlsafe(16)  # embedded in the live page only; other origins cannot read it
    api = FastAPI(title="CleanSense dashboard")

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page_live(state.simulated, token)

    @api.get("/state")
    def get_state() -> dict:
        return state.snapshot()

    @api.get("/frame.png")
    def frame() -> Response:
        path = log_dir / PERCEPTION_FRAME  # one fixed file name: no user-supplied path
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no camera frame yet")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @api.post("/cancel")
    def cancel(x_cancel_token: Optional[str] = Header(default=None)) -> dict:
        if not secrets.compare_digest(x_cancel_token or "", token):
            raise HTTPException(status_code=403, detail="cancel token missing or wrong")
        state.cancel_event.set()
        request_cancel(log_dir)  # reaches a run in another process
        return {"cancel_requested": True}

    @api.get("/runs", response_class=HTMLResponse)
    def runs_page() -> str:
        return page_runs(log_dir)

    @api.get("/offline", response_class=HTMLResponse)
    def offline_page() -> str:
        return page_offline(log_dir)

    @api.get("/results", response_class=HTMLResponse)
    def results_page() -> str:
        return page_results(log_dir)

    @api.get("/calibration", response_class=HTMLResponse)
    def calibration_page() -> str:
        return page_calibration(log_dir, calib_dir)

    return api


# (name, mess, sensitive, operator, edge vote or None). The offline cases cover the
# agree, weak-vote, hazard and no-operator paths. Every vote is SIMULATED.
DEMO_CYCLE = [
    ("dry_debris_on_glass", "dry_debris", False, None, None),
    ("offline_dry_vote_no_operator", None, False, None, EdgeVote("dry", 0.80)),
    ("offline_weak_vote", None, False, "dry_debris", EdgeVote("dry", 0.50)),
    ("offline_hard_strike", None, False, "dry_debris", EdgeVote("hard", 0.80)),
]


def demo_loop(state: DashboardState, stop: threading.Event, log_dir: Path = DEFAULT_LOG_DIR,
              pause_s: float = 3.0) -> None:
    """Runs SIMULATED scenarios forever. Each process writes its own sim_*.jsonl log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"sim_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    seed = 0
    while not stop.is_set():
        name, mess, sensitive, operator, vote = DEMO_CYCLE[seed % len(DEMO_CYCLE)]
        state.reset()
        state.scenario = name
        log = AuditLog(path, listener=state.on_event)
        log.write("setup", scale="simulated", camera="simulated", edge="simulated" if vote is not None else None,
                  planner="rules", providers=[])
        if vote is not None or operator is not None:
            fused, note = fuse(operator, vote)
            mess = fused
            log.write("edge", offline=True, operator=operator,
                      vote=None if vote is None else {"cls": vote.cls, "conf": vote.conf},
                      fused=fused, note=note)
        rig = CancelableRig(FakeRig(residue_g=10.0, seed=seed, camera_error=0.02), state.cancel_event)
        run(Scenario(name, mess, sensitive), rig, log=log)
        seed += 1
        stop.wait(pause_s)


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="drive a SIMULATED run loop (writes runs/sim_*.jsonl)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--logs", default=str(DEFAULT_LOG_DIR), help="run log folder (default runs/)")
    ap.add_argument("--calibration", default=str(DEFAULT_CALIB_DIR), help="calibration folder")
    args = ap.parse_args()
    state = DashboardState(simulated=args.demo)
    if args.demo:
        threading.Thread(target=demo_loop, args=(state, threading.Event(), Path(args.logs)),
                         daemon=True).start()
    if not args.demo:
        LogFollower(state, Path(args.logs)).start()  # live view of CLI runs, via their log
    uvicorn.run(make_app(state, Path(args.logs), Path(args.calibration)), host="127.0.0.1", port=args.port)
