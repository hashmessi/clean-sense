# ADR-003: Camera-estimated weight replaces the load cell for the demo

**Status:** Accepted
**Date:** 2026-10-30
**Feature:** vision-weight
**Author:** 10x-Team (Architect + Staff Engineer)

## Context
The HX711 load cell is not available. The verifier's "scale decides" rule needs a gram figure. The user approved a
camera-estimated gram figure as the replacement, shown on the dashboard as plain grams. The dashboard is not
changed and no new mode flag appears in the log. A camera cannot measure grams; it estimates from coverage.
Expected error is about +-20 to 40 percent.

## Decision
- New `vision_weight.py`: asks the vision provider for fixed JSON (coverage_pct, est_grams, confidence), 3 frames per
  look, median, valid only if the frames agree and confidence is high enough. Provider calls use the existing
  transports in `llm_transport.py` (Gemini, then OpenRouter); no provider-specific code outside that file.
- New `VisionWeighRig` wraps the serial rig: `weigh()` is the camera look; `tare()` is a no-op. It never sends
  `tare` or `weigh` to the ESP32, because the firmware blocks forever on a missing HX711 (which would also stall
  the heartbeat park). `park`, `sweep`, cancel and E-stop pass through unchanged.
- New `verify_vision` in `rules.py` plus a `VisionVerifier`: DONE needs estimated residue <= max(1 g, 10 percent of
  start), coverage <= 2 percent, the pixel-diff camera check clean, and a second look right after the first also
  clean. An unreadable look is never clean. `run()` gets an optional `verifier` argument (default: old `verify`)
  and turns a weigh fault into an aborted run before any further motion.
- The AI never declares clean. Deterministic code decides from validated numbers.
- Per-run cap of 30 vision calls; beyond it looks are unreadable.
- One-point calibration (`calibrate_vision.py`): a known dose gives a multiplicative scale clamped to 0.2..5.
  `run_rig.py --weigh vision` refuses to start without that file.
- `ui.py` and the log schema are unchanged. Coverage and confidence stay internal (not logged), so the dashboard
  shows only grams.

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| HX711 primary, vision fallback | Scale stays ground truth | Hardware is missing | User chose vision for now |
| Vision only, delete scale code | Simplest | Loses the hardware path | Scale code stays dormant |
| Reuse the 0.5 g / 3 percent limits | No new rule | Unreachable for a camera | Separate vision limits |

## Consequences
### Positive
- Demo runs with no load cell. Safety paths unchanged. Existing tests keep passing.
### Negative
- Gram figure is an estimate; transparent liquids may read near zero.
- Up to about 21 provider calls per run; free-tier limits may bite.
- Coverage and confidence are not in the audit log (kept off the dashboard on purpose).
### Risks
- Model over-reports clean: pixel-diff check, 2 percent coverage rule and two looks all must agree.
- Provider outage mid-run: the look is unreadable, so the run aborts and a person takes over.

## Dependencies
- ADR-002 (camera and providers). A future load cell returns through the default `verify`.
