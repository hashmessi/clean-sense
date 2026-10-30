# Architect: spoken announcements and camera perception

Implements ADR-002 (see .10x/adrs/002-camera-perception-and-spoken-announcements.md).

- Units: perception.py (pure decision rules), announce.py (fixed templates, speaker, bounded wait), camera.py
  (grab_bgr, encode_png), run_rig.py (one perception step before run(), planner guard, announcement).
- Authority: the camera can only add caution. forced_human is true only for a perceived liquid or
  unsupported_hazard that differs from the run's fused mess type. Forced runs never call the LLM or the ladder.
- Speech: fixed TEMPLATES only. Background thread, the run waits at most ANNOUNCE_WAIT_S (10 s) before motion.
- Failure rule: any perception or speech failure is logged by error class and changes nothing else.
- Boundaries not touched: validator, rules, e-stop, cancel, firmware, dashboard logic (only tolerates new events).
- Revisit if: perception should widen a plan (needs a new ADR), coffee should be named (needs a coffee test set).
