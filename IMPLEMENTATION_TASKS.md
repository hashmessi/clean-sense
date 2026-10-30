# CleanSense Lite — Software Implementation Tasks (hardware-confirmed 2026-10-29)

## Confirmed hardware → what changes in software
| Confirmed | Effect |
|---|---|
| ESP32 "A" (wireless dev board, breadboard) | Actuator + HX711 streamer. Laptop link = USB serial JSONL (Wi-Fi optional later) |
| ESP32 "B" (second, "for AI model") | Edge-AI vote only (advisory). Gets weight-trace windows from laptop, returns class + confidence |
| HX711 load cell | Sole ground truth for "clean" |
| Piezo (added) | Impact signature at landing (kHz) + sweep-contact vibration. Feeds the edge vote on ESP32 B and a contact/jam telemetry. Advisory only; never decides "clean" |
| 1× SG90 + wiper | One axis only. Ladder rungs = parameters (passes, speed, amplitude), not different tools. No lift → weigh only when sweep is parked and settled |
| (not listed) temp probe, 2nd servo | Drop temp rule (old C5). Surface flag becomes a UI/jumper input |
Piezo notes for firmware: ADC1 pin only (not ADC2), bias to mid-rail + clamp + 100 nF, Wi-Fi off while sampling, sample ~4 kHz into a 512-sample window after a trigger, send **features computed in firmware C** (peak, decay time, zero-crossings, band-energy ratio, sub-impact count) so training and runtime match. Which ESP32 the piezo wires to is an open item: if B, B computes the vote itself and laptop only reads it; if A, A streams features and laptop forwards to B.
Open items to confirm with teammates (software depends only on these messages): hardware e-stop + cancel button wiring; where the sweep pushes debris (tray off-scale?); camera = laptop webcam.

## Serial contract (agree with firmware teammates TODAY; JSON lines, one object per line)
```
laptop→A  {"cmd":"ping"}                         every 1 s heartbeat; A parks if silent >3 s
laptop→A  {"cmd":"tare"}   {"cmd":"weigh"}       → {"evt":"mass","g":12.31,"std":0.04,"settled":true}
laptop→A  {"cmd":"sweep","id":7,"passes":2,"speed":"slow|med","amp":"narrow|full"}
A→laptop  {"evt":"motion_done","id":7} | {"evt":"fault","code":"estop|timeout|clamp"}
laptop→A  {"cmd":"park"}
A→laptop  {"evt":"mass_stream","g":12.3,"t":123456}   ~10 Hz
A→laptop  {"evt":"estop","state":true} {"evt":"cancel"} {"evt":"surface","sensitive":true}
A or B→laptop {"evt":"impact","feat":{"peak":..,"decay_ms":..,"zc":..,"band_ratio":..,"n_sub":..},"t":..}
B→laptop  {"evt":"edge_vote","cls":"dry|liquid|hard|none","conf":0.8}   (from piezo features + load-cell step)
A→laptop  {"evt":"contact","rms":..,"angle":..}   sweep vibration vs per-angle baseline (jam/contact telemetry)
```
Rung map (firmware clamps authoritative): L1 = 1 pass, slow, narrow · L2 = 3 passes, med, full · L3 = 1 pass, slow, full with absorbent pad. Sensitive surface ⇒ firmware refuses L2.

## Tasks (ID · task · depends · acceptance test)
### Phase 1 — Spine on fake rig (0–5 h)
- T1 Repo scaffold, pyproject, pytest, ruff · – · `pytest` runs green on empty suite
- T2 Pydantic models: Observation, Decision, Plan, Verdict, LogEvent · T1 · schema round-trip tests
- T3 `serial_link` + `FakeRig` (mass sim with noise, sweep removes mass probabilistically, faults, heartbeat) · T2 · 10 scripted runs on fake
- T4 `audit` hash-chained JSONL + verify tool · T2 · tamper one line ⇒ verify fails
- T5 `validator` V1–V6, fail-closed · T2 · table-driven tests + ≥15 red-team cases; exception ⇒ reject
- T6 `verifier` (≤0.5 g and ≤3% of start; camera/scale disagree ⇒ not clean; 3 fails ⇒ human) · T2 · boundary tests (0.5 g, 3%)
- T7 `rules_planner` (operator taps dry/liquid/mixed) · T5 · deterministic ladder tests
- T8 `orchestrator` (`transitions`): IDLE→PERCEIVE→PLAN→VALIDATE→COUNTDOWN→ACT→SETTLE→VERIFY→{DONE|RETRY|HUMAN|ABORT} · T3–T7 · **Gate 1**: 10 consecutive fake runs ×2 scenarios; LLM disabled ⇒ still completes
### Phase 2 — Real link (5–10 h, runs in parallel with teammates' firmware)
- T9 Wire real serial (same interface as FakeRig) + heartbeat + reconnect · T3 + firmware · unplug USB ⇒ park, UI banner
- T10 Calibration/noise-floor tool: N tares, std, settle time → writes `thresholds.json` · T9 · repeatability ±0.2 g over 10 trials (servo moving)
- T11 Camera capture + ROI crop + saved frames per run · T1 · frame ≤1 s, stored with run id
- T12 Camera/scale agreement check (pixel-area before/after vs mass delta) · T6, T11 · disagreement ⇒ not clean test
- T13 First real E2E with rules planner · T8–T12 · **Gate 2** incl. e-stop observed, jumper cap
### Phase 3 — AI layer (10–16 h)
- T14 LLM planner: system prompt, enum JSON, `instructor` retry, tools `inspect_region`/`request_human`, temp 0, cached prompt · T2, T11 · schema-valid ≥90% first try, ≥98% after repair
- T15 Label 25–30 photos (+5 held-out mystery) + `eval/` runner · T11 · type acc ≥90% (state N)
- T16 Circuit breaker + tier-2 fallback + AI-OFFLINE banner · T7, T14 · kill network mid-run ⇒ completes
- T17 Override: buttons (cancel/gentler/skip region), reduce-only (V6) · T8 · tests: cannot increase intensity
- T18 Laya spike (1 h, Colab T4): state→typed questions, compare to ladder on rehearsal states · T15 · keep only if it catches ≥1 real error or ECE acceptable
- T19 Latency/cost log per call · T14 · p50 ≤4 s, p95 ≤8 s target
### Phase 4 — Proof & polish (16–22 h)
- T24a Piezo contact/jam telemetry: per-angle baseline, log as 4th voice (servo EMI check: capture with servo moving, no mess) · T9 · shows in dashboard; never gates
- T20 Dashboard FastAPI+WS (plan card, countdown, cancel, live mass, three-voice log, verdict) · T8 · phone/laptop view works
- T21 `eval/replay.py` table: first-pass, attempts, residue g, false-clean, rejections, latency, cost; VLM-only vs scale-only vs AND · T4, T15 · table regenerated from logs
- T22 Ablation: rules-only vs AI, same non-LLM inputs, alternating order · T7, T14, T21 · report ties/losses
- T23 Red-team replay (text-in-image, hostile override, "ignore limits") · T5, T14 · 0 limit violations
- T24 Edge vote (ESP32 B, **stretch, 2 h cap**): piezo features + load-cell step; collect labelled drops (4 classes: none/dry/liquid/hard, ≥40 each, **both mess types on both tiles**, ≥3 sessions, split by session) → threshold tree first, emlearn RF second → run on B · T9 · ship RF only if it beats tree on held-out session; else "edge heuristic vote"; advisory only
- T25 README (design, safety, physical vs SIMULATED), backup video, 5 timed rehearsals · all · **Gate 4**: 10 consecutive runs per scenario

## Parallelisation
Dev 1: T1–T8 core. Dev 2: T9–T13 once link contract agreed (fake rig lets Dev 1 proceed). AI dev: T14–T19 can start at T2 using saved photos. Edge-AI: T24 data collection can start as soon as A streams mass. Freeze features ~hour 16.
