# CleanSense: ordered execution checklist

Source of truth: `STATUS.md` (state), `INTEGRATION.md` (hardware steps), `verify.sh` (software gate).
Rule: do not start a step until the one above it has its pass check. If a step fails, stop, go to
the triage table in `INTEGRATION.md` §9, and record the result in `STATUS.md` before retrying.
Never delete logs in `runs/`.

Legend: [SW] software, you can run it alone · [HW] needs the rig · [DEC] needs a decision from the team · [KEY] needs the API key (not set; not part of tomorrow's gate)

---

## A. Before anyone touches hardware (laptop only)

- [ ] A1 [SW] `bash verify.sh` exits 0 (expect 250 tests, all checks pass). Pass: "ALL CHECKS PASSED".
- [ ] A2 [SW] Read `git status`-equivalent: confirm no unexpected file changes (project is not a git repo, so run `ls -la` and check `STATUS.md` / `CHECKLIST.md` only changed as intended).
- [ ] A3 [DEC] **E-stop decision (INTEGRATION §8.1).** Choose one, record it in `STATUS.md`:
      - (a) keep HIGH = healthy (fail-open, as built) and accept the risk, **or**
      - (b) switch to normally-closed logic (LOW = healthy, broken wire = stop), then do A4.
      Must be decided before step C1 (any motor connected).
- [ ] A4 [SW] Only if A3(b): change the e-stop logic in `firmware/cleansense_a/cleansense_a.ino` and its host test, rebuild, run `verify.sh`. Pass: loopback `estop` test updated and green.
- [ ] A5 [SW] Print `INTEGRATION.md` §2 pin table and §3 wiring diagrams for the teammate. Pass: teammate has the paper copy.
- [ ] A6 [SW] Decide the CAL_FACTOR path: calibrate on the rig (step C4). Until then CAL_FACTOR = 1.0 and results are raw counts. Record this in `STATUS.md`.

## B. Hardware bench, power off → USB only (INTEGRATION §0–§5)

- [ ] B1 [HW] §0 rules read aloud with the teammate (power off before wiring, common ground, no 5 V into GPIO, servo not on 3V3).
- [ ] B2 [HW] §3a–§3c wire HX711 + load cell, SG90 supply (not yet connected to servo signal), buttons, jumper. Keep servo supply OFF.
- [ ] B3 [HW] §4.1 no shorts (continuity 3V3–GND, 5 V–GND, with everything unplugged). Pass: no beep, > 10 kΩ.
- [ ] B4 [HW] §4.2 pin map compared wire-by-wire with §2, **second person signs**.
- [ ] B5 [HW] §4.3 load cell resistance matches datasheet (usually 350–1,000 Ω per pair).
- [ ] B6 [HW] §4.4 button continuity on GPIO 21, 22 and 23.
- [ ] B7 [HW] §5.1 flash firmware A with arduino-cli (ESP32Servo + ArduinoJson v6 installed). Pass: compile and upload succeed. **First time the sketch is compiled for the real ESP32 toolchain.** If compile fails, fix in the sketch, then `bash verify.sh` again, before re-flashing.
- [ ] B8 [HW] §5.1a open the port (laptop holds DTR/RTS low). Pass: park reply within about 3 s, no reset loop.
- [ ] B9 [HW] §5.2 `python3 bench_check.py --port <PORT> --step link` → `PASS link`.
- [ ] B10 [HW] §5.3 empty tile: `--step scale` → `PASS scale` (std ≤ 0.2 g, |median| ≤ 0.5 g). If it fails, §9 and stop.
- [ ] B11 [HW] §5.4 known mass (e.g. 50 g): `--step scale --not-empty`. Pass: reading rises by about the mass, positive sign. If negative, swap A+ and A− (not E).
- [ ] B12 [HW] §5.5 unplug USB 5 s, replug. Pass: `PASS link` again.
- [ ] B13 [SW] Record bench results in `STATUS.md` (date, port, pass/fail per step).

## C. Motion (servo supply ON, wiper clear of tile and scale) (INTEGRATION §6)

Requires A3 decided and B1–B13 passed. Hand on the servo supply switch the whole time.

- [ ] C1 [HW] §6.1 `python3 bench_check.py --port <PORT> --step sweep --confirm-clear-area` → `PASS sweep`, head parked after.
- [ ] C2 [HW] §6.2 press e-stop mid-sweep. Pass: head stops and parks; run reports `estop`, not PASS.
- [ ] C3 [HW] §6.3 press cancel during countdown of a dry run. Pass: run reports `cancelled`, no sweep.
- [ ] C4 [HW] §6.4 unplug USB during a motion check. Pass: head parks within 3 s.
- [ ] C5 [HW] §6.5 sensitive jumper on, medium-speed request. Pass: firmware refuses; never sweeps at L2.
- [ ] C6 [SW] Record results in `STATUS.md`. If any motion check fails, do not continue to D; go to §9.

## D. Calibration and first run (INTEGRATION §7)

- [ ] D1 [HW] §7.1 `python3 calibrate.py --port <PORT> --out calibration/` (tile empty, head parked). Pass: repeatable within ±0.2 g.
- [ ] D2 [HW] Set `CAL_FACTOR` in firmware from the known-mass test, rebuild (B7 path), re-run B10–B11. Pass: 50 g reads 50 g ± 0.5 g.
- [ ] D3 [HW] §7.2 dry run, no camera: `python3 run_rig.py --port <PORT> --mess dry_debris --surface glass`. Expected: finishes with `human` (safe result, no camera means never DONE).
- [ ] D4 [HW] §7.3 capture empty-tile reference, then run with `--camera 0 --reference calibration/reference.npy`. Pass: verdicts match what you see on the tile.
- [ ] D5 [SW] Check `runs/` contains one log per run, with the hash chain intact (`verify_log` passes).

## E. Offline tier, ESP32 B (INTEGRATION §7.4 — BLOCKED until E1–E2 done)

ESP32 B has no sketch and no trained model yet. Do not wire the piezo until E1–E2 are done.

- [ ] E1 [SW] Decide scope for tonight: (a) rule baseline on the existing features (a few thresholds, host-tested), plus an ESP32 B sketch that sends `edge_vote`; or (b) skip the offline tier for tomorrow and say so in the demo.
- [x] E2 [SW] DONE: ESP32 B sketch + rule baseline, host-compiled with -Werror, in `verify.sh`; 266 tests green (after the dashboard pages were added). Still to do on the board: E2b [HW] compile and flash the ESP32 B sketch with arduino-cli (same toolchain step as B7), and confirm `{"evt":"boot","mid":...}` appears.
- [ ] E3 [HW] Piezo bias network per INTEGRATION §3d. Check the mid-rail voltage (~1.65 V) with the meter before connecting the piezo. Pass: within 0.1 V.
- [ ] E4 [HW] Trigger the piezo by hand, confirm `edge_vote` arrives on the laptop. Pass: parsed by `edge.py`.
- [ ] E5 [HW] §7.4 `python3 run_rig.py ... --offline --edge-port <B>`. Pass: completes with no network. Expect the MIN_CONF placeholder to be marked in the log, not claimed as accuracy.
- [ ] E6 [SW] Record in `STATUS.md`: "piezo edge vote: rule baseline, not measured accuracy".

## F. Software items that do not need the rig (run any time, safe to skip for tomorrow)

- [ ] F1 [SW] HD10K: import the test split (done for test: 1,867 labelled rows). Use for dry vs liquid sanity only. Pass: `pytest tests/test_hd10k_import.py` green.
- [ ] F2 [KEY] AI ablation arm and the ≥90% photo gate. Blocked until the key is set and 25–30 rig photos are labelled. Do not set the key without the user asking.
- [ ] F3 [SW] Re-run `ablation.py` and `eval_table.py runs/*.jsonl` after the rig runs, to publish the results table.

## G. Close-out

- [ ] G1 [SW] Run `bash verify.sh` once more. Pass: exit 0.
- [ ] G2 [SW] Update `STATUS.md`: each step in B–E with date and result. Move any open item to "Known gaps" with a reason.
- [ ] G3 [HW] Sign-off (INTEGRATION §10): §4 bench, §5 power-on, §6 motion, §7.1 calibration recorded, §7.2 dry run safe result, wiring photo saved beside `calibration/`, name and date.
- [ ] G4 [DEC] Decide whether to commit (not done so far; only on request).

---

## Stop conditions (go straight to triage, do not "push through")
- Any motion check in C fails → stop motion work until §9 is resolved.
- Scale reads negative after swap, or repeatability outside ±0.2 g → stop calibration.
- Any `fault: estop` on a healthy rig → check the button and the wire (§4.4) before anything else.
- Any unexpected reset of the board during a run → stop, re-do B8.
