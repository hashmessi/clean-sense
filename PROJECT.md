# CleanSense Lite — PROJECT.md (locked 2026-10-29)

PS4 "CleanSense AI". Rule of the build: **cut, measure, claim less.**
Claim we make: *composition of known safety patterns + a physical ground-truth verifier.* We do NOT claim "zero hallucination" or a new safety technique.

## 1. Locked decisions
| Area | Decision |
|---|---|
| Rig | ESP32 + HX711 load cell under tile; 2 servos (sweep + lift) squeegee; piezo + temp probe as telemetry; e-stop cuts servo 5V; cancel button; surface jumper; laptop webcam |
| Link | USB serial, JSON lines, no Wi-Fi on ESP32. Firmware = named motions, angle clamps, 3 s heartbeat (park on silence), jumper caps head |
| Verifier | Code only. DONE iff residue <= 0.5 g AND <= 3% of start; camera must agree with scale else NOT CLEAN; 3 failed attempts -> human. Model never declares success |
| Ladders (code) | dry_debris L1>L2>human; liquid L1>L3>human; mixed L1>L2>L3>human; unknown L1>human |
| Planner | Hosted vision LLM, temp 0, enum-only JSON via `instructor`/Pydantic. **Tools: `inspect_region`, `request_human` only** (ask_user/propose_plan folded into output schema) |
| Validator | **6 rules** (fail-CLOSED: any exception = reject + abstain): V1 schema/enums; V2 next-rung-in-ladder; V3 sensitive surface caps pressure=light, rung L1/L3 only; V4 unknown/hazard/temp>40C = no contact; V5 region inside safe zone + budgets (<=3 attempts, <=15 s motion, <=12 LLM calls); V6 override may only reduce intensity/area |
| Fallback | ONE rules-only planner (operator taps dry/liquid) = AI-offline fallback AND ablation baseline. Circuit breaker after 2 failures. Banner "AI OFFLINE" |
| Override | v1 = buttons (cancel / gentler / skip region). Typed NL override only if time remains (reduce-only parser) |
| Log | JSONL, hash-chained, three voices: AI proposed / rules decided / hardware allowed; frames + scale trace per run |
| Dashboard | Minimal FastAPI + WebSocket: plan card, countdown, cancel, live scale, verdict, banner |
| Eval | Offline harness replays 20-30 logged trials -> table: first-pass success, attempts, residue g, false-clean count, validator rejections, p50/p95 latency, cost |

## 2. Cut list (from hostile review)
Cut: live judge-chosen ablation panel (use offline table), typed NL override (button first), ask_user + propose_plan tools, validator rules 9->6, conformal guarantees, simulator-in-the-loop, TinyML classifier, JEPA, local VLM (optional resilience only).

## 3. Honest AI claim (use verbatim)
"No free-text model output ever reaches a motor. The model proposes from closed enums; code validates; hardware vetoes; the scale decides 'clean'. We report counts with N (0 failures in 30 trials => <= ~10% at 95%, rule of three), abstention rate and false-clean count."

## 4. Novelty ledger
- Table stakes (don't claim): validator, hardware veto, tool loop, JSONL log, rule fallback (SafeGate 2604.05427, RoboGuard 2503.07885, ROSA 2410.06472).
- Defensible (no prior art found in a few searches — "not found" != "novel"): load-cell mass as the only "clean" verifier; camera/scale disagreement = not clean; reduce-only override; measured false-clean/risk-coverage (VLM-only vs scale-only vs AND); red-team set.
- Why scale decides: VLM judges are success-biased (FailBench arXiv 2609.03611: best 0.77 balanced acc.; more reasoning, top-3 voting didn't fix).

## 5. Upgrades, in priority order
1. Reliable clean-verify loop + measured scale noise floor (tare per run, weigh only head-lifted, settle detector = rolling std).
2. Offline eval harness.
3. False-clean count + risk-coverage chart (3 points).
4. Red-team set (10-15 injections: text in image, hostile override, "ignore limits") + hash-chained log.
5. Rehearsal playbook (~30 JSON rows), report held-out vs rehearsed separately.
6. Stretch: SigLIP 2 closed-set head as 2nd perception path, agreement-gated with VLM (disagree -> unknown -> human).
7. Stretch: active probe (low-energy pass reads scale to disambiguate unknown).
8. Last/optional: local VLM (Qwen3-VL-2B/4B via Ollama `format` schema; time it first), piezo-RMS jam telemetry. TinyML classifier = skip (class confounded with tile material; if tried: emlearn RF, threshold-tree baseline, split by session, both mess types on both tiles).
**CORRECTION (2026-10-29 18:30):** "Jev" and "Laya" are real, not typos. Jev = TypeSafe AI's closed "System One" typed-decision model (API, ~236-276 ms, ~$0.042/1M input tokens, third-party figures). Laya = Apache-2.0 open alternative (Convai Innovations, released 2026-09-18, `pip install laya`, ModernBERT-large 421M + decision head; Hugging Face convaiinnovations/laya). Non-generative: input = text/JSON state + typed questions (choice / score / noul yes-no P(true)); output = probabilities + confidence + act-vs-escalate gate, never prose. TEXT-ONLY (no images). Zero-shot weak (0.362 typed-decisions acc. < 0.461 majority baseline), ships overconfident (ECE 0.466 -> 0.081 only after per-question temperature refit), choice <~20 options, needs a GPU (T4 32.8 ms; CPU-only 4 vCPU ~49 s), fine-tune needs a few thousand labelled examples. Earlier JEPA/LLaVA reading was wrong; JEPA still = future work.
**Laya spike (optional, 1 h, go/no-go):** perception stays VLM/sensors -> text/JSON state -> Laya answers closed questions (choice: rung in ladder; noul: surface conflict; act-vs-escalate) as a 2nd independent calibrated vote beside the LLM planner; disagree or low confidence -> human. Run on Colab T4 or any GPU; measure on the ~30 rehearsal states; drop if not clearly useful. Keep the LLM planner: PS4 requires the LLM to drive decisions, Laya is an encoder not an LLM. Claim only "bounded, calibrated, abstaining" - never "zero hallucination".

## 6. Repo layout
```
cleansense/
  orchestrator.py   # `transitions` state machine
  planner.py        # LLM call, Pydantic schema, 2 tools
  rules_planner.py  # fallback + ablation baseline
  validator.py      # V1-V6, fail-closed
  verifier.py       # scale + camera AND-gate
  override.py       # buttons (+ optional NL)
  serial_link.py    # JSONL <-> ESP32 ; FakeESP32 via pyserial loop://
  audit.py          # hash-chained JSONL
  server.py         # FastAPI + WS dashboard
  eval/replay.py    # trial table
  tests/            # validator, verifier, redteam
firmware/           # named motions, clamps, heartbeat, HX711 stream
README.md           # design, safety, physical vs SIMULATED
```
Reuse: `transitions` (MIT), `instructor` (MIT), pyserial loop://, OpenCV, FastAPI/Pydantic. Do NOT use PyPI `hx711` (RPi-only); stream raw HX711 from firmware. Sensor twins tagged SIMULATED.

## 7. Build order today (software)
1. FakeESP32 + serial_link + audit log (1.5 h)
2. validator V1-V6 + unit tests incl. red-team (2 h)
3. verifier + scale settle/noise-floor tooling (1.5 h)
4. rules_planner + orchestrator end-to-end on fake rig (2 h) -> FIRST E2E RUN
5. LLM planner + schema + 2 tools, then prompt-tune on real photos (3 h)
6. dashboard + buttons override (2 h)
7. eval replay table (1.5 h) ; then upgrades 4-6 as time allows
Freeze features ~hour 16 of the hackathon; last 3-4 h = demo script + 5 rehearsals + backup video. Estimates are unmeasured (total ~18-22 h).

## 8. Fallback tiers
0 normal; 1 reject -> one repair; 2 AI >8 s/offline -> rules planner; 3 camera down -> weight-only; 4 scale unusable -> camera pixel-area verification; 5 backup video.

## 9. Judge questions to pre-answer
Why 0.5 g / 3%? (measure noise floor, settle time — data needed) · Is confidence calibrated / is abstention real? (risk-coverage) · Is the LLM just a lookup on a classifier label? (ablation: same non-LLM inputs for both arms, alternate order, show a case AI ties/loses; N stated) · Generalises beyond 5 messes? (held-out set) · What if API dies? (tier 2 live).

## 10. Teardown (3 h, optional before build)
Six-gene sheet (hook, relatable task, closed loop, proof number, recovery, persona), cell score 0-3 with evidence link. Repos: `git clone` into empty dir, never run code; `git log` hourly buckets, `git log -S`, gitingest (`env -u GITHUB_TOKEN`). Videos: ffmpeg scene detect + contact sheet. Closest neighbour: DeskPartner (github.com/aarochu/DeskPartner). Provenance block in README for anything reused. Stop line = last thing a precedent shows before hand-waving (ours: a measured result).

Full evidence: reports/CleanSense AI novelty hostile review.md
