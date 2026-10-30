# SDE: vision-weight
Built: `vision_weight.py` (prompt, strict reply validation, provider failover via transports, VisionLooker, VisionWeighRig, VisionVerifier, compute_scale/load_scale), `calibrate_vision.py`, `rules.verify_vision` + constants, `app.run(verifier=)` and weigh-fault handling, `run_rig.py --weigh vision --vision-calibration`.
Not changed: `ui.py`, log schema, firmware, old `verify`.
Deviations: none from the approved design. Coverage/confidence are not logged (dashboard constraint), so the audit log cannot show them.
Debt: up to ~21 provider calls per run; 30-call cap. No live provider test yet.
