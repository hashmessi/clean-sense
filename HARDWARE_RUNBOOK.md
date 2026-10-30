# CleanSense: hardware and PC runbook (demo path only)

Scope: the shortest safe path from "parts on the table" to "five clean rehearsals of each kit".
Details and wiring diagrams stay in INTEGRATION.md (section numbers below point there). Do not skip a gate.
Stop rule: if a gate fails twice, stop and fix the cause (INTEGRATION.md section 9); do not retry blindly.

## What is cut for the demo
- ESP32 B, piezo and the `--offline --edge-port` run (INTEGRATION 3d, 7.4). Show "AI offline" by unsetting the key instead.
- The 8.3 finding (B sketch uncalibrated) is out of scope with B.

## Requirements
| Need | Detail |
|---|---|
| Rig | ESP32 A, HX711 + load cell (rated well above tile + mess; tile as light as practical), SG90 + wiper, cancel button, sensitive jumper, data-capable USB cable |
| E-stop | Mushroom button with NC + NO contacts. Servo supply: separate 5 V, 1 A or more, 100 uF across it, common GND |
| Bench | Multimeter, a second person for the pin-map sign-off, a known mass weighed on a trusted scale (50 g and 100 g ideal) |
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
| 1.1 | Wire per sections 2 and 3 (pins: DT 16, SCK 4, servo 18, e-stop 21, cancel 22, sensitive 23) | Pin map signed by the second person |
| 1.2 | **E-stop decision (8.1), recommended:** wire the button's NC contact in series with the servo +5 V line, so pressing it cuts motor power with no firmware involved. Wire the NO contact to GPIO 21 as already specified, so the firmware also reports `estop` | Pressing the button kills servo power; GPIO 21 reads LOW |
| 1.3 | Multimeter: no short 3V3-GND and 5V-GND; load-cell pair resistance in the datasheet range; continuity on 21, 22, 23 only when pressed or jumpered | All pass |
Why: the GPIO e-stop is fail-open (a broken wire reads healthy), so it is not a safety stop on its own. The power cut is. Firmware inversion (LOW = healthy) is an optional later change.

## Phase 2: Flash and scale, servo supply OFF (INTEGRATION 5; ~45 min)
| # | Step | Pass |
|---|---|---|
| 2.1 | `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/cleansense_a` then `upload -p <PORT> ...` | Compiles and uploads. First time on the real toolchain: if it fails, fix, then `bash verify.sh`, then reflash |
| 2.2 | `python bench_check.py --port <PORT> --step link` | PASS link |
| 2.3 | Known mass on the tile: `--step scale --not-empty`. With CAL_FACTOR still 1.0 the number is raw counts: R | Positive sign (if negative, swap A+ and A-) |
| 2.4 | Set `CAL_FACTOR = R / m` in `firmware/cleansense_a/cleansense_a.ino` line 17 (m = the known mass in grams). `bash verify.sh`, reflash | verify exit 0 |
| 2.5 | Tile empty: `--step scale`. Then 50 g and 100 g | std 0.2 g or less, empty median within 0.5 g, 50 g and 100 g read within 0.5 g. Record CAL_FACTOR in STATUS.md |
Order change versus INTEGRATION 7.1: CAL_FACTOR comes before `calibrate.py`, because the noise floor and the 0.5 g limit only mean something in grams.

## Phase 3: Motion, servo supply ON, wiper clear (INTEGRATION 6; ~30 min)
| # | Step | Pass |
|---|---|---|
| 3.1 | `python bench_check.py --port <PORT> --step sweep --confirm-clear-area` | One slow pass, head parks |
| 3.2 | E-stop mid-sweep | Motor stops at once (power cut); firmware reports `estop`. Re-park before the next run |
| 3.3 | Cancel during a countdown; unplug USB mid-motion; jumper on + medium speed | `cancelled`; head parks within 3 s; firmware refuses L2 |

## Phase 4: Calibrate, then the vision and voice chain (~45 min)
| # | Step | Pass |
|---|---|---|
| 4.1 | Tile empty, head parked, lamp on: `python calibrate.py --port <PORT> --camera <CAM> --out calibration/` | Repeatable within 0.2 g; `reference.npy` saved |
| 4.2 | Dry run, no camera: `python run_rig.py --port <PORT> --case pharma` | Ends `human` (no camera can never say DONE). Expected |
| 4.3 | **Look test, servo supply OFF:** spill in view, `python run_rig.py --port <PORT> --camera <CAM> --reference calibration/reference.npy --llm --case battery --mess liquid` | Live page "Camera sees" shows liquid with the frame; the warning is spoken; nothing sweeps (motor unpowered) |
| 4.4 | Frame timing: look at the frame on the card | The spill is visible in it. If not, move the camera or the spill, then repeat 4.3 |
| 4.5 | Same, servo ON, `--case pharma` with flour | Scale falls to ~0 g and the camera agrees: DONE |

## Phase 5: Rehearse (~60 to 90 min)
Run each kit 5 times (commands in DEMO_KITS.md), filling its rehearsal log. Include one run with the key unset
(AI offline banner, rules ladder) and one cancel. Record the backup video the same day (BACKUP_VIDEO.md).
Gate: every run ends `done` or `human` honestly, no unexplained fault, and you know each kit's typical outcome.

## Sign-off
INTEGRATION.md section 10, plus: Piper audible, one real spill classified, frame shows the spill,
5 runs per kit logged, wiring photo saved beside `calibration/`. Name and date.
