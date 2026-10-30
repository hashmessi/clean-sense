# Architect: vision-weight
- Seam is `rig.weigh()`; wrapper pattern (like FileCancelRig) keeps planner, ladders, firmware and E-stop untouched. See ADR-003.
- `run()` gets `verifier` (default `verify`) and stops cleanly when a weigh raises RigFault (before or after motion).
- Failure modes: unreadable look -> RigFault -> `aborted` (no further motion). Provider outage -> same. Model over-claims clean -> pixel check, coverage rule and the second look must all agree.
- Firmware hazard: `tare`/`weigh` block on a missing HX711 and starve the heartbeat/E-stop checks; the wrapper never sends them.
