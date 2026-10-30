# CleanSense Lite — build status (2026-10-29, late)

Run `./verify.sh` for every check that works without hardware or an API key. Last run: 266 Python tests (dashboard pages added: 10), plus the host build of firmware A and the loopback, firmware limits host test, Gate-1 sim, rules-only ablation, 15/15 red-team replay, photo eval blocked without key: all passed (exit 0).

| Phase | Gate | Status |
|---|---|---|
| 1 Spine on fake rig | pytest; ≥15 red-team; 10 fake runs ×2 scenarios; LLM-off completes | **PASS** (sim) |
| 2 Real link + firmware | noise floor measured; ±0.2 g repeatability; e-stop observed; unplug → park; jumper cap | **Software WRITTEN, hardware gate OPEN.** Laptop link, calibration, camera check and firmware are built; the firmware sketch is NOT compiled (needs ESP32Servo + ArduinoJson and a board); the firmware safety header IS host-tested |
| 3 AI layer | ≥90% type acc on 25–30 photos; 0 unsafe after rules; AI-off run completes | **Wiring WRITTEN, API key NOT set (by request). Accuracy gate OPEN** — needs labelled photos. Tested with fake clients: JSON parse, repair retry, timeout, circuit breaker, ladder-skipping proposal rejected, no-key run completes on rules |
| 4a Ship batch (this round) | T4 hash chain · T14 frame to planner · T19 latency log · T21 judge comparison · ESP32 B piezo features | **Built and tested.** T4: `verify_log` detects edit, delete, reorder and tail tamper; appends continue the chain. T14: camera PNG goes to the planner as an image block (fake client); `inspect_region` tool NOT built. T19: per-call latency and tokens, p50/p95 nearest rank, written to `runs/llm_calls.json`. T21: camera-only vs scale-only vs AND-gate false-clean counts (SIMULATED). Piezo: `firmware/cleansense_b/piezo_features.h` integer-only, host-tested; B sketch now written (see 3c); no trained model |
| 4 Proof & polish | 10 demo runs/scenario on rig; generated results table; rehearsals; README | **Software WRITTEN (sim-only).** Results table generated from logs (`eval_table.py`); rules-only ablation on identical seeds (`ablation.py`, 0 false-clean in sim); red-team replay (`red_team_replay.py`, 15/15); dashboard (`ui.py`, FastAPI, read-only except cancel); README; backup-video shot list. **Rig rehearsals and the AI-vs-rules ablation arm are OPEN** (need the rig and key) |
| 3c Offline edge tier (add-on) | Edge vote fused cautiously; works with no key; hot/unreadable temp blocks motion | **Plumbing WRITTEN and tested with fake votes (SIMULATED).** `edge.py`, `run_rig.py --offline --edge-port`, rule V7, edge/offline rows in `eval_table.py`. **ESP32 B rule baseline WRITTEN and host-tested** (`firmware/cleansense_b/cleansense_b.ino` + `piezo_rules.h`): the real sketch compiles on the host with -Werror; `hard` / `dry` / `none` only (never `liquid`); confidences capped below MIN_CONF unless `hard`; 6 serial tests over the pty bridge (`tests/test_firmware_b_loopback.py`) and rule boundary tests (`firmware/tests/test_piezo_rules.cpp`). **Thresholds are uncalibrated placeholders** (HARD_PEAK 2000, HARD_DECAY_MS 20, DRY_MIN_BURSTS 2). **OPEN:** the sketch is NOT flashed or compiled for the ESP32 toolchain; labelled drops (≥40/class, both tiles, ≥3 sessions); measured accuracy; piezo bias wiring; calibrated thresholds |
| 3b Labelled-photo pipeline | ≥90% on 25–30 labelled rig photos | **Pipeline WRITTEN** (`dataset_import.py`, `eval_photos.py`, `perceive_photo` in `planner_llm.py`). Prints BLOCKED and exits 2 without a key. **Labels and key OPEN.** Public waste dataset is whole-object classification: material vocabulary only, not ground truth |

## Files
- rules.py — validator V1–V6 (fail-closed), verifier, ladders
- firmware/host_arduino/ — host build of the REAL cleansense_a.ino (ArduinoJson 6.21.5 real; Arduino core and Servo stubbed); tests/firmware_bridge.py and tests/test_firmware_loopback.py run the laptop serial code against it over a pseudo-terminal
- bench_check.py — one-step bench checks: link, scale, park, sweep (sweep needs --confirm-clear-area)
- INTEGRATION.md — hardware teammate checklist: pin map, wiring, bench, motion, first run, sign-off
- edge.py — edge vote parse + cautious fusion (offline tier)
- firmware/cleansense_b/piezo_features.h — impact features (peak, decay, zero crossings, band share, sub-impacts), integer math
- ui.py — FastAPI dashboard (GET /, GET /state, POST /cancel); SIMULATED loop with `--demo`
- eval_table.py — results table generated from JSONL logs
- ablation.py — rules-only arm on identical FakeRig seeds; AI arm added with a key
- red_team_replay.py — 15 hostile plans through the validator (exit 1 on any accepted)
- dataset_import.py — folder → labels CSV; unmapped classes skipped and reported
- hd10k_import.py — HD10K (gaussianopensource/dl_active_cleaning) → labels CSV: solid boxes → dry_debris, liquid mask → liquid, both (test split only) → mixed. Internal evaluation only (no licence stated). Domain is mall-floor robot views: a sanity check for dry vs liquid, NOT the accuracy gate. Run: `python3 hd10k_import.py --root <HD10K folder> --out eval/hd10k_labels.csv`
- eval_photos.py — accuracy gate (≥25 labelled, 90%, errors count as wrong); BLOCKED without key
- README.md, BACKUP_VIDEO.md — design, safety, SIMULATED vs PHYSICAL, backup video shot list
- rig.py — FakeRig (SIMULATED), RigFault
- serial_rig.py — JSON-lines laptop side; heartbeat thread; cancel drain; disconnect → fault; camera missing or erroring ⇒ never DONE
- camera.py — empty-tile reference check (pure functions tested; OpenCV capture class untested without a camera)
- calibrate.py — noise floor + repeatability summary; saves reference image
- run_rig.py — one real session (rules by default, `--llm` for AI)
- planner.py — rules-only planner (fallback + ablation)
- planner_llm.py — Claude planner: JSON-only, one repair, 8 s timeout, CircuitBreaker (2 failures → AI OFFLINE)
- app.py — bounded control loop, three-voice log
- firmware/cleansense_a/limits.h — motion gate, angle clamps, heartbeat (host-tested)
- firmware/cleansense_a/cleansense_a.ino — ESP32 sketch (NOT compiled)
- firmware/tests/test_limits.cpp — host test for limits.h
- demo.py — Gate-1 sim, 10 seeds per scenario
- tests/ — rules, runs, serial rig, LLM wiring, camera/calibration, phase 4 (table, ablation, dataset, photo eval, dashboard)

## Known gaps (honest)
- **Firmware not compiled or flashed.** HX711 scale factor `CAL_FACTOR` is 1.0 and must come from a known-mass test. E-stop wiring assumed: button pulls pin 21 LOW, HIGH = healthy. Confirm with the teammate who wires it.
- **Cancel:** the press is caught during a sweep (firmware flag) and between messages (laptop drains the serial buffer). Verify on the bench.
- **Temperature not set in the API call:** the installed Anthropic SDK version has no `temperature` parameter. Output is constrained by the JSON-only prompt and the validator, not by temperature 0. Decide whether to use another method when you add the key.
- **Budget counting (CLOSED, QA):** LLMPlanner now hard-caps HTTP calls at `MAX_HTTP_CALLS_PER_RUN` = 12, counting the repair retry. Calls beyond the cap are refused with `PlannerUnavailable` and never sent. Tests: `tests/test_qa_gaps.py`.
- **Image input (CLOSED, QA):** `run_rig.py --camera` passes the camera PNG to the planner as an image block (`CameraCheck.png`). Covered by the frame-to-planner tests. Not yet exercised with a real camera.
- **Camera calibration** requires a reference image of the empty tile (calibrate.py --camera).
- **No git repo here:** nothing committed (you did not ask for commits).
- **Simulator efficiencies are assumptions** (`FakeRig.EFFICIENCY`). Ablation and demo results are SIMULATED evidence only.
- **Ablation AI arm not run:** `ablation.py` prints that the AI arm is skipped until a key is set.
- **Audit log hash chain** covers local tampering only; it is not signed and not externally anchored.
- **`inspect_region` tool** is not built; the planner gets the frame and proposes plans only.
- **T18 Laya spike** is not started (optional, needs a notebook).
- **Edge vote:** piezo assumed on ESP32 B. The rule baseline is uncalibrated and never reports liquid (nothing in the piezo features separates liquid). Placeholder `MIN_CONF` 0.7 and `TEMP_LIMIT_C` 40 C. No trained model or measured accuracy. Temperature probe has no firmware message yet.
- **Host firmware stubs:** the Arduino core and Servo are host stubs, so timing, PWM and electrical behaviour are NOT verified. Only the sketch's logic and the laptop protocol are.
- **Boot reset:** the laptop holds DTR/RTS low on open and handshakes before any command. Not yet confirmed on your board.
- **E-STOP IS FAIL-OPEN:** HIGH = healthy, so a broken wire reads as healthy. Decide the inverted (normally-closed) logic in firmware before any motor is connected. See INTEGRATION.md §8.1.
- **Not certified:** nothing here is rated for hot liquid, mines or hazardous areas; bench-scale demonstration only.
- **Dashboard AI OFFLINE (CLOSED, QA):** the `proposed` event now carries the breaker state (`ai_offline`), so a breaker opened by any error class (for example a timeout) shows the banner. Before this fix, only a `PlannerUnavailable` error did. Tests: `tests/test_qa_gaps.py`.

## QA triage of the gaps (2026-10-29, before integration)

| Gap | Can software close it tonight? | Status |
|---|---|---|
| Budget counting / repair retry | Yes | CLOSED (hard HTTP cap) |
| Image input to planner | Already wired | CLOSED (verified by test) |
| Dashboard AI OFFLINE | Yes | CLOSED (breaker state in event) |
| E-stop fail-open (§8.1) | No: a safety wiring decision | **OPEN, decide before any motor connects** |
| CAL_FACTOR 1.0 | No: needs a known-mass test | OPEN, step 7.1 |
| Boot reset not confirmed on a real board | No: hardware | OPEN, step 5.1a |
| Cancel press not verified on the bench | No: hardware | OPEN, step 6.3 |
| arduino-cli compile on the team machine | No: needs the ESP32 toolchain | OPEN, step 5.1 |
| Host firmware stubs (timing, PWM) | No: needs the board | OPEN, known limit |
| Edge placeholders `MIN_CONF` 0.7, `TEMP_LIMIT_C` 40 | No: needs measured data | OPEN, placeholder |
| AI ablation arm and photo gate (≥90%) | No: needs the key and 25–30 rig photos | OPEN, by request |
| ESP32 B rule baseline (offline tier) | Written, host-tested; not flashed | Needs step E3–E5 on the rig |
| `inspect_region`, camera snapshot, T18 spike | Not built | Deferred, not needed for tomorrow's dry run |
| Temperature parameter absent in SDK | No change needed: output is constrained by the JSON-only prompt and the validator | Accepted |

## To finish Phase 2 (needs the rig)
1. Flash firmware/cleansense_a/cleansense_a.ino (install ESP32Servo + ArduinoJson).
2. `python3 calibrate.py --port <port> --camera <idx>` → check repeatable_within_target is true; set CAL_FACTOR.
3. `python3 run_rig.py --port <port> --camera <idx> --mess dry_debris --surface glass` → first real run.

## To finish Phase 3 (needs your key + photos)
1. `export ANTHROPIC_API_KEY=...` (never commit it).
2. Label 25–30 rig photos (dry_debris, liquid, mixed, unknown, unsupported_hazard) in a CSV.
   The waste-object dataset is not ground truth for rig mess types.
3. `python3 eval_photos.py --labels <labels.csv> --out eval/photos.json`; record N, accuracy and latency.
4. Re-run `python3 ablation.py` with the AI arm enabled, then `python3 eval_table.py runs/*.jsonl`.
