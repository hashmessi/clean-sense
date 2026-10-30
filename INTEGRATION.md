# CleanSense: hardware integration checklist

For the hardware teammate. Use this to wire, bench-test and connect the parts,
one step at a time. Each step has a pass check. Do not go to the next step
until the current one passes.

Scope: ESP32 A (sweeper, load cell, e-stop, cancel, surface jumper), the laptop
link, and ESP32 B (piezo, advisory only). The laptop software is already built and
tested without hardware (`bash verify.sh`).

---

## 0. Rules that apply to every step

1. **Power off before you wire.** Unplug USB and the servo supply. Wire, then check, then power.
2. **One wire per hole, short leads.** Keep wires off the ESP32 antenna area, and keep the
   piezo wires away from the servo and its power wires.
3. **Common ground.** ESP32 GND, HX711 GND, servo supply GND and the piezo GND all join at one point.
4. **Never 5 V into a GPIO.** ESP32 pins are 3.3 V only. Any 5 V line must reach a GPIO through a level shifter or divider.
5. **Never power the servo from the ESP32 3V3 pin.** The servo draws peaks above 500 mA.
6. **Color code.** Red = +, black or brown = GND, and one colour per signal. Write the pin number on each wire end.
7. **Do not connect a motor until step 6 passes.** Bench power-on checks (steps 1 to 5) need no motion.

---

## 1. Parts and where they plug in

| Part | Connects to | Power |
|---|---|---|
| ESP32 "A" dev board | Laptop USB (serial), HX711, servo signal, e-stop, cancel, jumper | USB 5 V |
| HX711 + load cell | ESP32 A pins below | 3V3 if the breakout works at 3.3 V (see §3); else 5 V with level shifting on DT |
| SG90 servo + wiper | ESP32 A `PIN_SERVO` (signal) | Separate 5 V supply, ≥1 A, common GND |
| E-stop button | ESP32 A `PIN_ESTOP` | none (switch to GND) |
| Cancel button | ESP32 A `PIN_CANCEL` | none (switch to GND) |
| Surface jumper | ESP32 A `PIN_SENSITIVE` | none (jumper to GND) |
| Piezo disc (27 mm) | ESP32 B `PIEZO_ADC` (bias network, §3) | none |
| ESP32 "B" dev board | Laptop USB (serial), piezo | USB 5 V |

---

## 2. Pin map (agreed; the firmware and `tests/test_bench_and_pins.py` both check this)

| Signal | Board | GPIO | Direction | Notes |
|---|---|---|---|---|
| `PIN_HX_DT` | A | 16 | input | HX711 data out |
| `PIN_HX_SCK` | A | 4 | output | HX711 clock |
| `PIN_SERVO` | A | 18 | PWM output | SG90 orange wire |
| `PIN_ESTOP` | A | 21 | input, pull-up | LOW = button pressed (see §8, safety finding) |
| `PIN_CANCEL` | A | 22 | input, pull-up | LOW = pressed |
| `PIN_SENSITIVE` | A | 23 | input, pull-up | LOW = jumper on (sensitive surface) |
| `PIEZO_ADC` | B | 34 | ADC1, input only | piezo through the bias network (not yet in firmware) |

Avoid on this board: GPIO 6 to 11 (flash), 0, 2, 12, 15 (boot strapping), 1 and 3 (USB serial), and never drive GPIO 34 to 39 as outputs.

---

## 3. Wiring by part

Confirm every colour against your own part's datasheet. Colours differ between suppliers.

### 3a. HX711 and load cell (ESP32 A)

```
Load cell (4 wires)            HX711 breakout             ESP32 A
  excitation +  ─────────────►  E+
  excitation −  ─────────────►  E−
  signal +      ─────────────►  A+
  signal −      ─────────────►  A−
                                 VCC  ◄── 3V3 (or 5 V with DT level shift)
                                 GND  ◄── common GND
                                 DT   ─────────────►  GPIO 16
                                 SCK  ◄─────────────  GPIO 4
```

- Do not swap E+ and E−. That can damage the cell or the breakout.
- If the reading goes **down** when you add weight, swap A+ and A−. Do not swap E.
- The HX711 gain is 128 by default. Leave the RATE pin as the breakout ships.

### 3b. SG90 servo and wiper (ESP32 A)

```
Servo supply (5 V, ≥1 A) +  ─────►  servo red
Servo supply GND            ─────►  servo brown  ─── common GND
ESP32 A GPIO 18             ─────►  servo orange (signal)
100 µF electrolytic across the servo supply, close to the servo
```

- The wiper arm must be **clear of the tile and the scale** during bench checks.
- Set the servo horn to the home position by hand before you power it.

### 3c. Buttons and jumper (ESP32 A)

```
GPIO 21 ── e-stop button ── GND        (button pulls LOW when pressed)
GPIO 22 ── cancel button ── GND        (button pulls LOW when pressed)
GPIO 23 ── jumper ── GND               (jumper on = sensitive surface)
```

All three use the ESP32's internal pull-up (firmware sets `INPUT_PULLUP`). Do not add
external resistors.

### 3d. Piezo and bias network (ESP32 B, not yet in firmware)

```
3V3 ── 100 kΩ ──┬── 100 kΩ ── GND         (bias to mid-rail, about 1.65 V)
                │
            GPIO 34 (ADC1_CH6)
                │
piezo + ────────┼── 1 MΩ ── GND           (bleed)
                ├── 100 nF ── GND         (ADC noise filter, as close to the pin as possible)
                ├── clamp diode to 3V3    (1N4148 or BAT54S)
                └── clamp diode to GND    (same type, reversed)
piezo − ─────────── GND
```

- A hard impact can produce a voltage far above 3.3 V. The clamp diodes are required.
- Keep the piezo leads short and twisted. Do not run them alongside the servo wires.

---

## 4. Bench checks before connecting the laptop (no motion)

| # | Check | How | Pass |
|---|---|---|---|
| 4.1 | No shorts | Multimeter on continuity between 3V3 and GND, and between 5 V and GND, with USB and the servo supply unplugged | No beep, resistance above 10 kΩ |
| 4.2 | Pin map | Compare every wire with §2 and §3, with a second person | Second person signs off |
| 4.3 | Load cell polarity | Probe E+ to E− with the meter in resistance mode; then A+ to A− | Resistance matches the cell datasheet (usually 350 to 1,000 Ω across each pair) |
| 4.4 | Button wiring | Continuity GPIO 21, 22 and 23 to GND with each switch | Continuity only when pressed (21, 22) or jumper on (23) |

---

## 5. Power-on bring-up (no motion)

Power the board by USB only. Keep the servo supply **off** for this section.

| # | Step | Command | Pass |
|---|---|---|---|
| 5.1 | Flash A | `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/cleansense_a` then `arduino-cli upload -p <PORT> --fqbn esp32:esp32:esp32 firmware/cleansense_a` (install the ESP32Servo and ArduinoJson v6 libraries first). The same sketch already compiles on a PC against the real ArduinoJson 6.21.5 (`bash verify.sh`), so only the ESP32 toolchain step is new | Compiles with no errors; upload succeeds |
| 5.1a | Opening the port does not reset the board | The laptop holds DTR and RTS low when it opens the port (`open_serial_rig`). If the board still resets, the handshake retries and the first run still waits for boot | Log shows a park reply within about 3 s |
| 5.2 | Link alive | `python3 bench_check.py --port <PORT> --step link` | `PASS link` |
| 5.3 | Scale reads | With the tile **empty**: `python3 bench_check.py --port <PORT> --step scale` | `PASS scale`, std ≤ 0.2 g, |median| ≤ 0.5 g |
| 5.4 | Scale responds | Put a known mass (for example 50 g) on the tile, then run `--step scale --not-empty` | Reading changes by about the known mass; sign is positive |
| 5.5 | Heartbeat | Unplug USB for 5 s, then replug | Board is still responsive after replug (`PASS link`) |

If 5.3 fails, stop and go to §7.

---

Software bugs the loopback test found and fixed before this round (no hardware needed):
- The weigh reply silently lost the `settled` field (document too small). Fixed in the sketch.
- Commands were sent as text, which pyserial rejects. Fixed to bytes.
- A sweep longer than 2 s was reported as a timeout. Fixed with a deadline wait (15 s limit).

## 6. Motion checks (servo supply ON, wiper clear of everything)

Power the servo supply only now. Keep one hand on its switch.

| # | Step | Command | Pass |
|---|---|---|---|
| 6.1 | Servo moves | `python3 bench_check.py --port <PORT> --step sweep --confirm-clear-area` | `PASS sweep`: one slow L1 pass, head parked after |
| 6.2 | E-stop | Press e-stop during step 6.1 (repeat the command, press mid-sweep) | Head stops and parks; the run reports `estop`, not `PASS` |
| 6.3 | Cancel | Press cancel during the countdown of a dry run (see 7.2) | Run reports `cancelled`, no sweep happens |
| 6.4 | Unplug | Unplug USB during a motion check | Head parks within 3 s (heartbeat lost) |
| 6.5 | Sensitive jumper | Put the jumper on, run a medium-speed check | Firmware refuses; the run never sweeps at L2 |

---

## 7. First run and integration with the laptop

| # | Step | Command | Pass |
|---|---|---|---|
| 7.1 | Calibrate | `python3 calibrate.py --port <PORT> --out calibration/` (tile empty, head parked) | Repeatable within ±0.2 g; set `CAL_FACTOR` in the firmware from a known mass before 7.2 |
| 7.2 | Dry run, no camera | `python3 run_rig.py --port <PORT> --mess dry_debris --surface glass` | The run finishes with `human` (no camera means it can never say DONE). That is the expected safe result |
| 7.3 | Camera | Add `--camera 0 --reference calibration/reference.npy` after capturing the empty-tile reference | Verdicts agree with what you see |
| 7.4 | Offline run | Add `--offline --edge-port <B>` only after ESP32 B is flashed | Completes with no network |

Note the outcome of every run in the log folder, `runs/`. Do not delete logs.

---

## 8. Safety findings: decide before connecting a motor

1. **E-stop logic is fail-open.** The firmware treats HIGH as healthy. A broken wire or a
   disconnected button also reads HIGH, so the system would look healthy. A safe wiring
   would make "healthy" the **LOW** state with a normally-closed contact, so a broken wire
   reads as stop. This needs a firmware decision before the motor is connected. Until then,
   the e-stop is not a verified safety stop. Do not rely on it alone.
2. **CAL_FACTOR is 1.0.** The mass reading is in raw counts until step 7.1 sets it.
3. **ESP32 B sketch exists but is host-tested only** (`firmware/cleansense_b/cleansense_b.ino`, rule baseline in `piezo_rules.h`). It has not been compiled for the ESP32 or flashed. Its thresholds are uncalibrated, and it never reports liquid. Do not treat its votes as measurements.

---

## 9. Failure triage

| Symptom | Likely cause | Check |
|---|---|---|
| `link or firmware fault: timeout` | Wrong port, board not flashed, USB charge-only cable | Port name, re-flash, use a data cable |
| Scale reads a steady negative or huge number | A+ and A− swapped, or the cell is not seated | §3a sign note; re-seat the cell |
| Scale reading drifts or jumps | Loose wire, shared ground missing, supply noise | Wiggle-test each wire; check the common ground |
| Servo jitters or resets the board | Servo drawing from the ESP32 or USB | Use the separate supply and the 100 µF capacitor |
| `fault: estop` on a healthy rig | e-stop wire to GND loose, or button stuck pressed | §4.4 continuity, then §8.1 |
| `fault: refused` on sweep | Sensitive jumper on, or medium speed requested | Remove the jumper for a normal-surface test |
| Piezo reads a constant offset | Bias network missing or wrong values | §3d; check the mid-rail voltage with the meter |

---

## 10. Sign-off

- [ ] §4 bench checks all pass (second person checked the pin map)
- [ ] §5 power-on checks 5.1 to 5.5 pass
- [ ] §6 motion checks 6.1 to 6.5 pass, with the e-stop finding in §8.1 decided
- [ ] §7.1 calibration recorded (`calibration/calibration.json`), `CAL_FACTOR` set
- [ ] §7.2 dry run finishes with the expected safe result
- [ ] Wiring photo taken and saved beside `calibration/`
- [ ] Name and date: ____________________
