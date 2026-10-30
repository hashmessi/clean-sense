# ADR-005: The run firmware uses the hardware-validated servo pin GPIO13

**Status:** Accepted (supersedes the servo-pin part of ADR-004)
**Date:** 2026-10-30
**Feature:** hardware-integration
**Author:** 10x-Team (Principal Architect)

## Context
The user states the `CleanSense_Servo_Handoff` sketch and its wiring were validated against the real servo hardware
(servo signal on GPIO13, separate 5 to 6 V supply, common ground, 470 to 1000 uF capacitor). Our `cleansense_a` was
host-tested only and had the servo on GPIO18 (never wired).

## Decision
Move `PIN_SERVO` in `cleansense_a` to 13, so bench and run use the same validated wire. GPIO13 is not a boot-strapping
pin and is free in our pin map (16, 4 unused, 21, 22, 23). Everything else in ADR-004 stands: the handoff sketch stays a
bench console, and its `jump`, `mode power`, `range`, 0 to 180 degree travel and 0 degree home are not ported.
Mount the horn at 90 degrees (our home). PlatformIO becomes the primary build path (same pins as the validated build).

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| Keep GPIO18 and rewire | Pin map untouched | Moves a wire that is already validated | Adds risk for no gain |
| Port the handoff firmware as the run firmware | Fully validated sketch | No JSON, heartbeat, E-stop gate or host tests | Breaks the safety case |

## Consequences
### Positive
- No rewiring between bench and run; pin map has one source (INTEGRATION.md section 2, checked by tests).
### Negative
- Validation covers the wiring and servo, not our firmware's JSON loop or limits on the real board.
### Risks
- The user's validation claim is taken as given. Our sketch is still unbuilt on the ESP32 toolchain.

## Dependencies
- ADR-004, ADR-003.
