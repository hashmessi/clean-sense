# Handoff
- Built: camera perception at run start and spoken fixed announcements (ADR-002, tests/test_announce.py).
- Next on the PC: (1) pip install nothing new; install Piper, download a voice, set PIPER_CMD and PIPER_VOICE in .env;
  (2) run check_providers.py; (3) one run_rig.py --llm --camera <index> run with the phone pointed at a spill;
  confirm the Live page shows perceived class, announcement, and forced human when the operator says dry.
- Open: confirm the perception frame shows the spill before the head parks; Piper CLI flags on the PC;
  classifier accuracy on real liquid spills; coffee naming is out of scope.
- Safety notes unchanged: cancel is between sweeps; e-stop wiring decision (INTEGRATION.md 8.1) still open.
- Not committed (the project is not a git repo; commit only if asked).

## Tabletop industry demo (2026-10-30, built)
- Built: Live page "Camera sees" card + /frame.png, `--case` presets, DEMO_KITS.md. Decisions: sde/ and qa/ tabletop-industry-demo.md. No git; backups in ~/ship_backup/tabletop/.
- Next (hardware integration): resolve the E-stop wiring decision (INTEGRATION.md 8.1), set CAL_FACTOR, flash firmware, then calibrate.py and one run per kit; fill the rehearsal log in DEMO_KITS.md.
- On the PC: install Piper, set PIPER_CMD/PIPER_VOICE, run check_providers.py, then `run_rig.py --llm --camera <index> --case battery` with a coffee spill in view.
- Open: perception frame is grabbed before the head parks (confirm the spill is in view); classifier accuracy on real spills; SMT/battery end states on hardware.
- Execution order for the hardware and PC steps: HARDWARE_RUNBOOK.md (supersedes the order in INTEGRATION.md 7.1: CAL_FACTOR before calibrate.py; ESP32 B cut for the demo; E-stop recommendation = NC contact cutting servo power plus the existing GPIO signal; awaiting the user's decision).
