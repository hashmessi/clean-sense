# CleanSense Lite

A small wiper on a load-cell tile that cleans a test spill in the least force that
the scale says is enough. An AI planner proposes the next step. Code decides whether
it may run, and the scale plus a camera decide whether the tile is clean.

**Status (2026-10-29):** software complete and tested without hardware. Physical
gates (calibration, e-stop, first real run) and the AI accuracy gate are not yet run.
See [STATUS.md](STATUS.md) for the gate table and open items.

> **SIMULATED vs PHYSICAL.** Everything in the test suite, `demo.py`, `ablation.py`,
> `red_team_replay.py` and the dashboard's default mode uses `FakeRig`, a software
> model of the scale, wiper and camera. Its efficiency numbers are assumptions, not
> measurements. Only `run_rig.py` and `calibrate.py` touch hardware, and they have
> not been run on the rig yet. Any result that says SIMULATED is not evidence about
> the physical wiper.

## Design

Three voices write one audit log, and each event says who made it:

| Voice | Who | What it may do |
|---|---|---|
| `proposed` | Planner (LLM, or rules when AI is offline) | Propose JSON: mess type, rung, sensitive flag, reason. Never says "clean". |
| `decided` | Validator (`rules.py`) | Accept or reject. Fail-closed: any exception is a rejection. |
| `allowed` | Hardware (`firmware/`) | Refuse motion outside its own limits (angle, passes, speed, e-stop, heartbeat). |

The `sensor` events (scale mass, camera) are the only source of "clean".

- **Ladder.** Each mess type has an ordered list of rungs: `dry_debris` L1→L2,
  `liquid` L1→L3, `mixed` L1→L2→L3, `unknown` L1 only, `unsupported_hazard` none
  (a human handles it). The validator only accepts the next rung.
- **Bounded run.** At most 3 attempts and 12 LLM calls per run.
- **Verifier.** A run is DONE when the residue is ≤ min(0.5 g, 3% of the start mass)
  *and* the camera agrees. If the start was under 0.5 g, it is already clean. A
  negative reading, disagreement, or 3 failed attempts sends it to a human.
- **Sensitive surface.** The validator never allows L2 (the strongest pass) on a
  sensitive surface, and the firmware refuses more than one pass or medium speed.
- **Circuit breaker.** Two consecutive planner failures take the AI offline for the
  session. The rules planner takes over, and the dashboard shows AI OFFLINE.
- **Serial contract.** JSON lines between laptop and ESP32 A: `ping` (1 s heartbeat;
  firmware parks after 3 s silence), `tare`, `weigh`, `sweep`, `park`, plus `fault`,
  `cancel` and `estop` events from the firmware.

## Offline tier (add-on, advisory edge vote)

A third perception tier needs no network and no LLM. ESP32 B reads the piezo, computes
features in firmware and sends `{"evt":"edge_vote","cls":"dry|liquid|hard|none","conf":0..1}`.
The laptop fuses it with the operator's mess type (`edge.py`):

- The vote can only make a run more careful: the fused type's ladder is always a subset of
  the operator's. Disagreement becomes `unknown` (one gentle rung, then a human). A "hard"
  vote becomes `unsupported_hazard` (no motion, human).
- Weak (< 0.7) or `none` votes carry no information. A bad, missing or unparsable vote is
  no vote. With no operator and no usable vote, the type is `unknown`.
- `python3 run_rig.py --port <A> --edge-port <B> --offline` runs with no key and no network.
  The scale and camera still decide "clean".
- Rule V7 (temperature): when a probe is configured, a reading above `TEMP_LIMIT_C` (40 C,
  a bench placeholder), a missing reading or NaN means no motion and a human. The probe is
  not wired to any firmware yet; `app.run(temp_source=...)` takes any callable.

Status: the plumbing is tested with fake votes (SIMULATED). There is no trained model and no
measured accuracy yet. The firmware feature code and the data collection are still to do.

## Safety rules (what must stay true)

1. The AI never declares a surface clean. Only the scale and camera do.
2. Camera errors and a missing camera are never "clean".
3. An LLM plan can only move one rung forward, never skip, and cannot raise intensity
   after an override (override is reduce-only).
4. Hazard and unknown materials go to a human, not to the wiper.
5. E-stop (pin 21, HIGH = healthy) and heartbeat loss park the head in firmware, without
   laptop help.
6. No test, no key and no hardware is needed for the checks below. Nothing commits or
   sets an API key from these scripts.

## Run the checks (no hardware, no key)

```bash
bash verify.sh
```

This runs the pytest suite (rules, runs, serial, LLM wiring, camera, Phase 4), the
firmware safety limits compiled on the host, and the gate-1 simulation. Last run:
see STATUS.md.

Individual pieces:

```bash
python3 demo.py                 # 10 seeded runs per scenario (SIMULATED)
python3 ablation.py             # rules-only arm on identical seeds (AI arm needs a key, see below)
python3 red_team_replay.py      # 15 hostile plans through the validator; exit 1 if any accepted
python3 ui.py --demo            # dashboard on http://127.0.0.1:8000 (SIMULATED loop;
                                #   writes runs/sim_*.jsonl so the pages have history)
```

Dashboard pages (all read the logs; nothing is typed by hand):

| Page | Shows |
|---|---|
| `/` | One live run: workflow, verdict, mass, AI proposal, validator decision |
| `/runs` | Every run in `runs/*.jsonl`, with hash-chain status per log |
| `/offline` | ESP32 B edge votes, the rule baseline, and how a vote is fused |
| `/results` | Results table computed from the logs |
| `/calibration` | Scale and camera calibration state, and which values are still placeholders |

Results table from logs (never typed by hand):

```bash
python3 eval_table.py runs/*.jsonl
```

## Run on the rig (physical, not yet run)

```bash
python3 calibrate.py --port /dev/ttyUSB0 --camera 0 --out calibration/
python3 run_rig.py --port /dev/ttyUSB0 --camera 0 --reference calibration/reference.npy \
    --mess dry_debris --surface glass
```

For the tabletop industry demo, `--case pharma|battery|smt` presets the mess type and surface (explicit
`--mess` / `--surface` still win). Kits, commands and the rehearsal sheet are in `DEMO_KITS.md`.

Calibrate with the tile empty and the head parked. `run_rig.py` writes JSONL logs to
`runs/`. Without `--camera`, no run can say DONE. Add `--llm` for the AI planner
(needs `ANTHROPIC_API_KEY`); the default is rules-only.

## AI accuracy gate (needs a key and labelled photos)

```bash
python3 dataset_import.py --folder <photos> --out eval/labels.csv   # labels stay empty
# fill labels.csv by hand: dry_debris, liquid, mixed, unknown, unsupported_hazard
export ANTHROPIC_API_KEY=...        # never commit it
python3 eval_photos.py --labels eval/labels.csv --out eval/photos.json
```

- Without a key the script prints `BLOCKED: ANTHROPIC_API_KEY is not set` and exits 2.
- At least 25 labelled photos are required, and the gate is 90% accuracy. Errors count
  as wrong.
- The public waste-object dataset labels whole objects, not spills on a tile. Use it
  for material vocabulary only. Do not map it to rig mess types as ground truth.

## Limits (say these out loud)

- The e-stop is fail-open in the current wiring; see INTEGRATION.md §8.1. Do not connect a motor until that is decided.

- `FakeRig` efficiency values are assumptions. The ablation shows the planner comparison
  under those assumptions, not the real wiper.
- The 0.5 g and 3% thresholds are not trusted until calibration repeatability passes.
- Firmware is host-tested only; it has not been compiled for the ESP32 or flashed. ESP32 B has a rule-baseline sketch (`firmware/cleansense_b/cleansense_b.ino`), host-tested, with uncalibrated thresholds and no liquid class. There is no trained model.
- `CAL_FACTOR` is a placeholder (1.0) until a known-mass test sets it.
- Temperature is not a parameter of the Anthropic SDK version in use, so the model
  is not deterministic. Determinism comes from enum-only output plus the validator.
- No hash chain on the audit log yet.

## Files

| File | Purpose |
|---|---|
| `rules.py` | Validator, ladders, verifier (pure, tested) |
| `app.py` | Run loop, audit log, planner fallback |
| `planner.py`, `planner_llm.py` | Rules planner; LLM planner, photo classifier, circuit breaker |
| `rig.py`, `serial_rig.py` | Simulated rig; laptop side of serial contract |
| `camera.py`, `calibrate.py` | Camera check against an empty-tile reference; calibration |
| `run_rig.py` | Hardware run entry point |
| `demo.py`, `ablation.py`, `red_team_replay.py`, `eval_table.py` | Simulation, comparison, red team, results table |
| `ui.py` | Dashboard (FastAPI): live run, run history, offline tier, results, calibration and camera |
| `dataset_import.py`, `eval_photos.py` | Labelled-photo pipeline and accuracy gate |
| `edge.py` | Edge vote parsing from ESP32 B and cautious fusion (offline tier) |
| `firmware/cleansense_b/piezo_features.h` | Piezo impact features for ESP32 B, integer math, host-tested |
| `firmware/` | ESP32 A (sweeper + load cell), `limits.h` safety rules, host test |
| `BACKUP_VIDEO.md` | Shot list for the backup video |
| `INTEGRATION.md` | Hardware teammate checklist: pins, wiring, bench, motion, sign-off |
| `bench_check.py` | One-step bench checks (link, scale, park, sweep) |
