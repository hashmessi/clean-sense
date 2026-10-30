# CleanSense Lite — Software Build Plan (4 phases, each gated by verification)
Date 2026-10-29. Companion to PROJECT.md (decisions) — this file is the order of work. Hour figures are estimates (~20 h total, 1–3 people). A phase is DONE only when its gate passes; otherwise fix before moving on.

## Tech stack
| Layer | Choice | Why / note |
|---|---|---|
| Laptop core | Python 3.12, `transitions` (state machine), Pydantic v2, `instructor` (validated LLM output + retry), pyserial | MIT libs; typed contracts end to end |
| Dashboard | FastAPI + WebSocket + one static HTML page | Live scale, plan card, countdown, cancel |
| Vision/measure | OpenCV (frame grab, ROI crop, pixel-area), numpy | Crop-to-region before the VLM (FailBench: biggest gain) |
| Firmware | Arduino-ESP32 (PlatformIO), HX711 lib in firmware, ESP32Servo, JSON lines over USB serial | No Wi-Fi on ESP32; do NOT use PyPI `hx711` (RPi-only) |
| Logging | JSONL, hash-chained, frames + scale trace per run | Feeds eval table |
| Tests | pytest; FakeESP32 via pyserial `loop://` | Whole pipeline runs with no hardware |

## AI models in the workflow
| Job | Model | Role | Status |
|---|---|---|---|
| Plan/adapt (vision, tool loop) | Hosted Claude vision (`claude-sonnet-5-5`; `claude-haiku-5-5` for parsing/narration), temp 0, enum-only JSON | Proposes; never acts | Core. Measure latency in Phase 3 |
| Override parse / report | Haiku-class | Reduce-only edits; narration | Buttons first, text override if time |
| Decision gate | **Laya** (Apache-2.0, `pip install laya`) on a GPU (Colab T4) | 2nd calibrated vote: choice (rung), noul (surface conflict), act-vs-escalate on a text/JSON state | Go/no-go spike in Phase 3; text-only, needs recalibration |
| 2nd perception | SigLIP 2 closed-set head | Agreement gate with VLM; disagree → unknown → human | Stretch |
| Offline resilience | Qwen3-VL-2B/4B via Ollama (`format` = JSON schema) | Only if time; time it first | Optional |
| Fallback | Rules-only planner (no model) | AI-offline fallback AND ablation baseline | Core |
Not used: JEPA (future work), TinyML classifier as a decision-maker.

## TinyML on the spare ESP32 (scoped honestly)
1. Scale-settled detector: rolling std of HX711 stream → `settled` flag (not ML, 20 min, **Phase 2, core**).
2. Piezo-RMS jam/contact telemetry vs per-servo-angle baseline (30–40 min, Phase 4, optional).
3. Dry/liquid/hard "edge vote": emlearn random forest (sklearn → C) with a hand-tuned threshold tree as the baseline. **2 h hard cap, Phase 4 stretch.** Only ship the forest if it beats the threshold tree on a held-out session; else call it an "edge heuristic vote". Put both mess types on both tiles (class is otherwise confounded with tile material); ADC1 only, bias piezo to mid-rail, 100 nF cap, Wi-Fi off, split by session.
Edge vote is advisory telemetry shown as a 4th voice in the log; it never gates motion.

## Phase 1 — Spine on a fake rig (≈0–5 h)
Build: FakeESP32 + `serial_link` (JSONL protocol), hash-chained `audit`, `validator` V1–V6 (fail-closed), `verifier` (≤0.5 g and ≤3%, camera/scale disagree = not clean, 3 fails → human), `rules_planner`, `orchestrator` state machine, two scripted scenarios (dry debris, liquid on sensitive surface).
**Gate 1:** pytest green incl. ≥15 red-team cases (injected "ignore limits", hostile override, bad enums, validator exception ⇒ reject); 10 consecutive fake runs per scenario end verified-clean or human; log chain verifies; killing the LLM changes nothing (rules path completes).

## Phase 2 — Real rig + firmware (≈5–10 h)
Build: firmware (named motions, angle clamps, 3 s heartbeat → park, e-stop sense, jumper cap, HX711 stream + settled flag), calibration script, real serial link replacing the fake, camera capture.
**Gate 2:** measured scale noise floor and settle time recorded (sets the 0.5 g / 3% thresholds with data); tare→weigh repeatability within ±0.2 g over 10 trials with servos moving; e-stop cuts motion (observed); unplug USB → head parks within ~3 s; jumper=sensitive caps head in firmware even if laptop commands more; first real end-to-end run with the **rules planner**.

## Phase 3 — AI layer (≈10–16 h)
Build: LLM planner (schema, `inspect_region`, `request_human`), crop-then-classify, circuit breaker (2 fails → tier 2), button overrides (cancel / gentler / skip region), 25–30 labelled photos (incl. 5 held-out "mystery"), **Laya spike (1 h, go/no-go)**, optional SigLIP head.
**Gate 3:** type accuracy ≥90% on labelled set (report N); schema-valid ≥90% first try, ≥98% after one repair; **0 unsafe plans after validator**; plan latency p50 ≤4 s / p95 ≤8 s (targets — measure); API disabled mid-run → tier-2 banner and run completes; Laya kept only if it adds measurable value (disagreement catches ≥1 real error or calibrated ECE ok), otherwise dropped and noted in README.

## Phase 4 — Proof, polish, rehearsal (≈16–22 h)
Build: dashboard (plan card, countdown, cancel, scale, verdict, AI-OFFLINE banner), `eval/replay.py` table (first-pass success, attempts, residue g, false-clean count, validator rejections, p50/p95, cost; VLM-only vs scale-only vs AND), AI-vs-rules ablation on identical non-LLM inputs with alternating order (report a tie/loss honestly), README (design, safety, physical vs SIMULATED), backup video; stretch: piezo telemetry, edge vote, rehearsal playbook (~30 rows, held-out reported separately).
**Gate 4:** 10 consecutive demo runs per scenario with no intervention; eval table generated from logs, not typed by hand; 5 timed rehearsals of the demo script; backup video recorded; claims in README match numbers ("0 failures in N=30 ⇒ ≤~10% at 95%", never "0%"). Feature freeze at hackathon hour ~16.

## Risks that break first (watch in gates)
HX711 noise from servo vibration vs 0.5 g · swept mass still on the scale · liquid evaporation drift · camera/scale disagree on first live run (explain on screen) · venue Wi-Fi/API latency · squeegee on fine debris untested → test in Phase 2.
