# ADR-004: The servo handoff sketch is a bench tool, not the run firmware

**Status:** Accepted; the servo-pin decision is superseded by ADR-005 (pin 13) and the geometry decision by ADR-006 (home 0, sweep 0 to 90). The rest stands.
**Date:** 2026-10-30
**Feature:** hardware-integration
**Author:** 10x-Team (Principal Architect + Staff Engineer)

## Context
A friend's `CleanSense_Servo_Handoff/` offers a typed-command ESP32 servo console (PlatformIO, GPIO13, SG90 profile,
easing, duty-cycle and stall guards). Our `cleansense_a` speaks JSON lines to `run_rig.py`, with heartbeat park,
E-stop, cancel, sensitive-surface gate and host-tested limits (30 to 150 degrees, home 90, at most 100 deg/s).
The two differ: pin (13 vs 18), geometry (home 0 and sweep 0 to 90 vs home 90 and +-20/45), speed (up to 500 deg/s
and an unbounded `jump` vs 100 deg/s), protocol, and safety (typed `stop` only vs hardware-checked E-stop).

## Decision
Keep them separate. Use the handoff sketch only to bench the servo, supply and wiper travel (runbook Phase 1b),
then flash `cleansense_a` for every run. Do not port `jump`, `mode power`, `range`, 0 to 180 travel or the 0 degree home.
Keep our pin map frozen at servo GPIO18: the signal wire moves from D13 to 18. Add a PlatformIO file for
`cleansense_a` with the same toolchain pins as the handoff (unbuilt here; arduino-cli stays documented).
The handoff's supply "switch" becomes the E-stop NC contact in series (INTEGRATION 8.1).

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| Replace our firmware with the handoff | One fewer sketch | Loses JSON, heartbeat, E-stop gate, host tests; unsafe speeds | Breaks the safety case |
| Merge its easing and guards into cleansense_a | Smoother motion | Touches host-tested limits; not needed at 100 deg/s | Later, only if motion looks harsh |
| Change our servo pin to 13 | No rewire | Changes the signed-off pin map, tests and docs | One jumper is cheaper |

## Consequences
### Positive
- Safety case and 460 tests untouched. A known-good servo bench exists before the run firmware.
### Negative
- Two sketches to keep straight; the wrong one flashed makes `run_rig.py` time out (fail-closed: handshake fails).
### Risks
- Arm fitted with the handoff's `home` or `sweep` could hit a stop: runbook forbids it.
- Handoff sketch and PlatformIO files are not built in this session.

## Dependencies
- ADR-003 (no load cell). INTEGRATION 8.1 (E-stop wiring).
