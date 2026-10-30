# CleanSense: hardware and PC runbook (demo path only)

Scope: the shortest safe path from "parts on the table" to "five clean rehearsals of each kit".
Details and wiring diagrams stay in INTEGRATION.md (section numbers below point there). Do not skip a gate.
Stop rule: if a gate fails twice, stop and fix the cause (INTEGRATION.md section 9); do not retry blindly.

## Weight source: camera estimate, no load cell (ADR-003)
There is no HX711. Grams come from the vision model: an estimate, roughly +-20 to 40 percent, never a measurement.
Always run with `--weigh vision`. **Never run `run_rig.py` or `bench_check.py --step scale` without it on a rig that
has no load cell:** the firmware's `tare` and `weigh` wait forever for the missing chip, and while waiting the
firmware does not watch the E-stop or the heartbeat. (This is one more reason the E-stop must cut servo power
in hardware, step 1.2.) The load-cell steps (2.3 to 2.5, `calibrate.py`) are skipped. When a load cell returns, use
the old steps and drop `--weigh vision`.

## What is cut for the demo
- ESP32 B, piezo and the `--offline --edge-port` run (INTEGRATION 3d, 7.4). Show "AI offline" by unsetting the key instead.
- The 8.3 finding (B sketch uncalibrated) is out of scope with B.

## Requirements
| Need | Detail |
|---|---|
| Rig | ESP32 A, SG90 + wiper (no load cell), a few grams each of the demo materials plus a kitchen scale to weigh one calibration dose, cancel button, sensitive jumper, data-capable USB cable |
| E-stop | Mushroom button with NC + NO contacts. Servo supply: separate 5 V, 1 A or more, 100 uF across it, common GND |
| Bench | Multimeter, a second person for the pin-map sign-off, a coin or printed marker for scale in frame |
| Camera | Webcam, or a phone-as-webcam app that shows up as a normal camera index. Desk lamp. Tile fully in view |
| PC | Python deps from requirements.txt, arduino-cli with the ESP32 core, ESP32Servo and ArduinoJson v6 libraries |
| AI | `.env` with GEMINI_API_KEY and OPENROUTER_API_KEY (never print or paste them) |
| Voice | Piper (`pip install piper-tts`), one voice (.onnx + .json), speaker. Check the voice's licence on its model page |
| Kits | The stand-in materials in DEMO_KITS.md, printed case cards |

## Phase 0: PC-only, no hardware (do this while someone wires; ~30 min)
| # | Step | Pass |
|---|---|---|
| 0.1 | `python check_providers.py` | Both keys list usable models, including the ones named in `.env` |
| 0.2 | Camera index: `python -c "import cv2; [print(i, cv2.VideoCapture(i).isOpened()) for i in range(5)]"` with the phone app running | One index opens; note it as `<CAM>` |
| 0.3 | Piper: `piper --help` shows `--model` and `--output_file`. Set `PIPER_CMD` and `PIPER_VOICE` (full path to the .onnx) in `.env` | See 0.4 |
| 0.4 | Speech test: `python -c "from llm_transport import load_env_file; load_env_file(); import os; from announce import speaker_from_env, TEMPLATES; s,n=speaker_from_env(os.environ); print(n); s.speak(TEMPLATES['liquid_try'])"` | Prints `piper` and you hear the warning. If the CLI flags differ, only `PiperSpeaker.speak` in `announce.py` changes |
| 0.5 | `bash verify.sh` | exit 0 (baseline: 398 tests) |

## Phase 1: Wire and bench checks, power off (INTEGRATION 4; ~45 min)
| # | Step | Pass |
|---|---|---|
| 1.1 | Wire per sections 2 and 3, skipping the load cell (pins: servo 13 (D13), e-stop 21, cancel 22, sensitive 23; DT 16 and SCK 4 stay unconnected) | Pin map signed by the second person |
| 1.2 | **E-stop decision (8.1), recommended:** wire the button's NC contact in series with the servo +5 V line, so pressing it cuts motor power with no firmware involved. Wire the NO contact to GPIO 21 as already specified, so the firmware also reports `estop` | Pressing the button kills servo power; GPIO 21 reads LOW |
| 1.3 | Multimeter: no short 3V3-GND and 5V-GND; continuity on 21, 22, 23 only when pressed or jumpered | All pass |
Why: the GPIO e-stop is fail-open (a broken wire reads healthy), so it is not a safety stop on its own. The power cut is. Firmware inversion (LOW = healthy) is an optional later change.

## Phase 1b: Servo bench with the handoff sketch (do this before Phase 2) (`CleanSense_Servo_Handoff/`; ~20 min, servo supply OFF while flashing)
That folder is a typed-command console (ASCII) whose servo wiring was validated on the real parts. It is NOT the run
firmware: no JSON, no heartbeat, no hardware E-stop, and `stop` is only a typed command. Use it to prove the servo,
supply and wiper travel; `cleansense_a` uses the same servo pin (GPIO13), so no wire moves. Flash `cleansense_a` over
it (step 2.1) before any `run_rig.py`. (The validation is the handoff author's; it is not repeated here.)
| # | Step | Pass |
|---|---|---|
| B.1 | PowerShell in `CleanSense_Servo_Handoff`: `python -m platformio run` then `python -m platformio run -t upload` (hold BOOT if it stalls at `Connecting....`). Servo supply switch OFF | Builds and uploads. First real build: if it fails, send the error, do not edit blindly |
| B.2 | `python -m platformio device monitor -b 115200` (close other serial programs first). Switch servo supply ON after the command list appears. Horn OFF or arm clear. Type `s`, `mode gentle`, `sweep` | Status shows `sg90`, `DETACHED`; one slow smooth sweep; no ESP32 reset |
| B.3 | Park first: `home` (0 degrees), then fit the horn so the arm rests clear of the tile. Then `mode gentle`, `sweep` with the arm fitted: it goes 0 to 90 and back | The arm covers the tile area and stops at 90 without hitting the tile edge, the scale frame or a hard stop. This is exactly the geometry `cleansense_a` uses |
| B.4 | Close the monitor (only one program can hold the port). Leave the signal wire on D13, then do 2.1 | Pin map matches `tests/test_bench_and_pins.py` |
Rules: use `mode gentle` only. Never use `jump`, `mode power` or `range` on the rig, and never command above 90 degrees
(our firmware caps motion at 100 deg/s and refuses anything outside 0 to 90). `home` and `sweep` are the same moves the run
firmware makes, so they are safe with the arm fitted. Horn at 0 degrees before powering: `cleansense_a` writes 0 at boot.
The servo supply "switch" in the handoff wiring becomes the E-stop's NC contact in series with that switch (step 1.2).
Supply: SG90 needs 5 to 6 V and 1 A or more; the 3 A figure in the handoff README is for the larger servos.

## Phase 2: Flash, servo supply OFF (INTEGRATION 5; ~15 min)
| # | Step | Pass |
|---|---|---|
| 2.1 | Same toolchain as the bench build: `cd firmware/cleansense_a` then `python -m platformio run` and `python -m platformio run -t upload` (hold BOOT if it stalls at `Connecting....`). Alternative: `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/cleansense_a` then `upload -p <PORT> ...` | Compiles and uploads. First time on the real toolchain: if it fails, fix, then `bash verify.sh`, then reflash |
| 2.2 | `python bench_check.py --port <PORT> --step link` | PASS link |
CAL_FACTOR, the 50 g and 100 g checks and the noise floor do not apply without a load cell.

## Phase 3: Motion, servo supply ON, wiper clear (INTEGRATION 6; ~30 min)
| # | Step | Pass |
|---|---|---|
| 3.1 | `python bench_check.py --port <PORT> --step sweep --confirm-clear-area` | One slow pass, head parks |
| 3.2 | E-stop mid-sweep | Motor stops at once (power cut); firmware reports `estop`. Re-park before the next run |
| 3.3 | Cancel during a countdown; unplug USB mid-motion; jumper on + medium speed | `cancelled`; head parks within 3 s; firmware refuses L2 |

## Phase 4: Calibrate the camera, then the vision and voice chain (~45 min)
| # | Step | Pass |
|---|---|---|
| 4.1 | Tile empty, lamp on, camera fixed in its demo position: `python calibrate_vision.py reference --camera <CAM>` | `calibration/reference.npy` saved. Do not move the camera or the lamp afterwards; redo if you do |
| 4.2 | Weigh a dry dose (about 5 g of flour) on the kitchen scale, put it on the tile with the marker in frame: `python calibrate_vision.py dose --camera <CAM> --grams <G>` | Prints the model's estimate and a scale between 0.2 and 5. Outside that range: fix framing and light, repeat |
| 4.3 | **Look test, servo supply OFF:** liquid spill in view, `python run_rig.py --port <PORT> --camera <CAM> --reference calibration/reference.npy --weigh vision --llm --case battery --mess liquid` | Live page shows liquid with the frame and a gram figure; the warning is spoken; nothing sweeps (motor unpowered) |
| 4.4 | Frame timing: look at the frame on the card | The spill is visible in it. If not, move the camera or the spill, then repeat 4.3 |
| 4.5 | Same, servo ON, `--case pharma` with flour | Estimate falls to about 0 g, coverage low, the pixel check agrees on two looks: DONE |
| 4.6 | Repeatability: three runs with the same 5 g dose, note start grams | Within about +-40 percent of each other. Wider: fix light and framing before rehearsing |

## Phase 5: Rehearse (~60 to 90 min)
Run each kit 5 times (commands in DEMO_KITS.md), filling its rehearsal log. Include one run with the key unset
(AI offline banner, rules ladder) and one cancel. Record the backup video the same day (BACKUP_VIDEO.md).
Gate: every run ends `done` or `human` honestly, no unexplained fault, and you know each kit's typical outcome.

## Sign-off
INTEGRATION.md section 10 (load-cell items excluded), plus: Piper audible, one real spill classified, frame shows the spill, a gram figure that tracks the dose,
5 runs per kit logged, wiring photo saved beside `calibration/`. Name and date.
