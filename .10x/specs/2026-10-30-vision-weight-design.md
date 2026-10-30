# Camera-estimated weight (no load cell) - design

Approved by the user 2026-10-30. Constraint added by the user: do not change the dashboard, do not claim a
"vision" mode anywhere; the dashboard shows only the detected grams.

- Source of grams: vision provider, fixed JSON (est_grams, coverage_pct, confidence), 3 frames, median.
- Rig seam: `rig.weigh() -> Reading(grams, settled)` unchanged. `VisionWeighRig` wraps the serial rig;
  `tare`/`weigh` are never forwarded to the firmware (it blocks on a missing HX711).
- Verifier: `verify_vision` + `VisionVerifier` (second look). DONE needs residue <= max(1 g, 10 percent of start),
  coverage <= 2 percent, pixel-diff camera clean, two looks. Unreadable = not clean = stop.
- Calibration: `calibrate_vision.py reference|dose`; `run_rig.py --weigh vision` refuses without it.
- Out of scope: any ui.py change, new log events or fields, deleting the load-cell code or firmware.
- Limits stated to judges: estimate, +-20 to 40 percent; transparent liquids may read near zero.
