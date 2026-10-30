# CleanSense: ordered execution checklist (current plan)

Plan: camera-estimated weight (no load cell, ADR-003), servo benched with `CleanSense_Servo_Handoff` then the run
firmware on the same pin (GPIO13, ADR-005), no ESP32 B. Details and pass checks: `HARDWARE_RUNBOOK.md`.
Wiring and triage reference: `INTEGRATION.md`. State: `STATUS.md`. Software gate: `bash verify.sh`.
Rule: do not start a step until the one above it has its pass check. Two failures of a gate: stop and find the cause.
Never delete logs in `runs/`. Never print or paste API keys.

Legend: [SW] software, laptop only · [HW] needs the rig · [DEC] decision · [KEY] needs an API key in `.env`

## A. Laptop only (can run while someone wires)
- [ ] A1 [SW] `bash verify.sh` exits 0 (see STATUS.md for the count). Pass: "ALL CHECKS PASSED".
- [ ] A2 [DEC] **E-stop (INTEGRATION 8.1):** recommended = NC contact in series with the servo +5 V (power cut) plus the NO contact to GPIO 21. Record the choice in `STATUS.md`. Must be decided before any motor power (C1).
- [ ] A3 [KEY] `python check_providers.py`: both keys list usable models (needed for vision weight, classification and the planner).
- [ ] A4 [SW] Camera index probe; note `<CAM>`. Tile fully in view, lamp on.
- [ ] A5 [SW] Piper installed, `PIPER_CMD` and `PIPER_VOICE` set; the speech one-liner (runbook 0.4) is audible.

## B. Wire and bench, power off (runbook Phase 1, 1b)
- [ ] B1 [HW] Rules read aloud (INTEGRATION section 0): power off before wiring, common ground, servo never on 3V3 or 5V pin.
- [ ] B2 [HW] Wire: servo signal D13 (GPIO13), servo supply with a 470 to 1000 uF capacitor, E-stop NC in series with servo +, cancel 22, sensitive jumper 23. Load-cell pins 16 and 4 stay unconnected. Pin map signed by a second person.
- [ ] B3 [HW] Multimeter: no short 3V3-GND or 5V-GND; continuity on 21, 22, 23 only when pressed or jumpered.
- [ ] B4 [HW] Bench build in `CleanSense_Servo_Handoff`: `python -m platformio run`, then `python -m platformio run -t upload` (BOOT if stuck), then `python -m platformio device monitor -b 115200`. Servo supply OFF while flashing.
- [ ] B5 [HW] Bench test, horn off: `s`, `mode gentle`, `sweep`. Pass: slow smooth sweep, no ESP32 reset. Only `mode gentle`; never `jump`, `mode power` or `range`.
- [ ] B6 [HW] `home` (0 degrees), fit the horn so the arm rests clear of the tile, then `mode gentle` and `sweep`: the arm goes 0 to 90 and back without hitting the tile edge or a hard stop. Never command above 90 degrees.

## C. Run firmware and motion (runbook Phase 2, 3)
- [ ] C0 [HW] On the Windows PC (the cloud session cannot reach the ESP32 toolchain servers): `cd firmware/cleansense_a` then `python -m platformio run`. Build only; no board needed. Pass: `SUCCESS`. On failure copy the full error text.
- [ ] C1 [HW] Close the monitor. In `firmware/cleansense_a`: `python -m platformio run` and `python -m platformio run -t upload`. **First build of this sketch on the ESP32 toolchain**: on a compile error, fix, `bash verify.sh`, reflash.
- [ ] C2 [HW] `python bench_check.py --port <PORT> --step link` prints `PASS link`; port opens with no reset loop. (Do NOT run `--step scale`: no load cell.)
- [ ] C3 [HW] `bench_check.py --step sweep --confirm-clear-area`: one slow pass, head parks.
- [ ] C4 [HW] E-stop mid-sweep: motor power cut, run reports `estop`.
- [ ] C5 [HW] Cancel during a countdown: `cancelled`. Unplug USB mid-motion: head parks within 3 s. Jumper on plus medium speed: firmware refuses.
- [ ] C6 [SW] Record B and C results in `STATUS.md`. Any motion failure: stop, INTEGRATION 9.

## D. Camera calibration and first runs (runbook Phase 4)
- [ ] D1 [HW] `python calibrate_vision.py reference --camera <CAM>` (tile empty; do not move camera or lamp after).
- [ ] D2 [HW] Weigh about 5 g of flour on a kitchen scale, place it with a marker in frame: `python calibrate_vision.py dose --camera <CAM> --grams <G>`. Scale between 0.2 and 5.
- [ ] D3 [HW] Look test, servo supply OFF: `python run_rig.py --port <PORT> --camera <CAM> --reference calibration/reference.npy --weigh vision --llm --case battery --mess liquid`. Pass: Live page shows liquid, a gram figure and the frame; the warning is spoken; nothing sweeps.
- [ ] D4 [HW] Frame shows the spill at the moment of the look. If not, move camera or spill and repeat D3.
- [ ] D5 [HW] Servo ON, `--case pharma` with flour: estimate falls, pixel check agrees twice: `done`.
- [ ] D6 [HW] Repeat the 5 g dose three times; start grams within about +-40 percent.
- [ ] D7 [SW] `runs/` has one log per run and `verify_log` passes.

## E. Rehearse (runbook Phase 5)
- [ ] E1 [HW] Five runs per kit in `DEMO_KITS.md`, rehearsal log filled; one with the key unset (AI offline banner); one cancel.
- [ ] E2 [HW] Backup video recorded the same day (`BACKUP_VIDEO.md`).

## F. Close-out
- [ ] F1 [SW] `bash verify.sh` exit 0. `STATUS.md` updated: every step above with date and result; open items moved to Known gaps.
- [ ] F2 [HW] Sign-off (INTEGRATION 10, load-cell items excluded): wiring photo saved beside `calibration/`, name and date.
- [ ] F3 [DEC] Commit and push only when asked.

## Cut from the demo (do not do)
Load cell, HX711 wiring, `CAL_FACTOR`, `calibrate.py`, `bench_check.py --step scale`, ESP32 B and `--offline --edge-port`.
Their code and docs stay in the repo for later.

## Stop conditions
- Any motion check in C fails: stop motion work until INTEGRATION 9 is resolved.
- `run_rig.py` or `bench_check.py --step scale` started without `--weigh vision` on this rig: unplug USB; the firmware is waiting on the missing chip and is not watching the E-stop.
- Unexpected board reset during a run: stop, redo C1 and C2.
- Any `fault: estop` on a healthy rig: check the button wiring before anything else.
