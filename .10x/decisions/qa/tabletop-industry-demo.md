# QA: tabletop industry demo gaps (2026-10-30)
- tests/test_demo_gaps.py: 23 tests (presets table, overrides, offline, main() with a fake rig, Sense state incl. hostile log values, frame route serves only the fixed file, frame saving and failure paths). verify.sh exit 0, 398 passed (was 375).
- Browser check (Playwright, desktop 1280 and phone 390 px): Sense card renders, frame loads, no page errors, no horizontal scroll. Font requests blocked by the sandbox proxy only.
- Fake-rig outcomes (SIMULATED, 8 g residue model, one seed): pharma done (3 passes); battery powder human after 1 pass; battery liquid done (3 passes); smt human after 1 pass. Not hardware.
- Hostile review of the full diff: 2 fixes applied (finite timestamp; simulated-mode wording), 1 doc line (frame stays on disk).
- NOT verified: real phone/webcam frame, live provider class, Piper audio, forced-human path on hardware, the frame being taken before the head parks.
