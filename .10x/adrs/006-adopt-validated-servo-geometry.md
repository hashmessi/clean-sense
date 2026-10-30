# ADR-006: The run firmware adopts the hardware-validated servo geometry (home 0, sweep 0 to 90)

**Status:** Accepted (supersedes the geometry part of ADR-004)
**Date:** 2026-10-30
**Feature:** hardware-integration
**Author:** 10x-Team (Principal Architect)

## Context
The user asked that the run firmware fit the handoff sketch's `home` (0 degrees) and `sweep` (0 to 90 degrees to 0),
which were validated on the real servo and arm. `cleansense_a` had home 90 with passes about the centre
(+-20 narrow, +-45 full, clamp 30 to 150), which no one had mounted or tested.

## Decision
- `SERVO_HOME_DEG = 0`, `SERVO_MIN_DEG = 0`, `SERVO_MAX_DEG = 90` (a hard ceiling, tighter than the handoff's 0 to 180).
- A pass goes home -> home+reach -> home. `reachDegrees(FULL) = 90` (the validated sweep), `reachDegrees(NARROW) = 40`
  (the same 40 degree span as before). Passes per rung, speeds (1 degree per 25 ms slow, 10 ms medium) and the
  sensitive-surface rule are unchanged, so rung durations are unchanged (L2 about 5.4 s, inside the 15 s motion timeout).
- Heartbeat park, E-stop check and cancel are unchanged; parking now means 0 degrees.
- Tests: `firmware/tests/test_limits.cpp` (constants), new `firmware/tests/test_geometry.cpp` (the real sketch on the host
  shim sweeps 0 to 90 to 0 and 0 to 40 to 0, never outside), both in `verify.sh`.

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| Keep centre-90 geometry, re-mount the arm | Symmetric reach | Untested mounting; differs from the validated arm | User asked for the validated geometry |
| Allow 0 to 180 like the handoff | Full compatibility | A bad command could swing past the validated range | Hard ceiling at 90 is safer |

## Consequences
### Positive
- Bench and run use the same pin and the same arm motion; `home` and `sweep` in the bench console are now safe with the arm fitted.
### Negative
- Reach is one-sided: the sweep covers only the 0 to 90 side of the arm's pivot.
- The servo writes 0 at boot, so the horn must already be at 0 when the ESP32 resets.
### Risks
- The arm's reach over the tile at 0 to 90 is assumed from the validated mounting, not measured here.

## Dependencies
- ADR-004, ADR-005.
