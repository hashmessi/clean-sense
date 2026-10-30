# CleanSense status (10x-Team)
Phase: Tabletop demo gaps built and verified (Sense card, camera frame, --case presets, DEMO_KITS.md). Hardware and live provider checks still open.

| Item | State |
|---|---|
| Camera perception at run start (one frame, one provider call) | Built, tested with fakes. Live check pending on PC. |
| Camera can only add caution (liquid or hazard vs operator type -> human) | Built, tested (decision table). |
| Spoken fixed announcements (6 templates, Piper behind an interface) | Built, tested with fake speaker. Piper install and voice licence pending. |
| ADR-002 | Accepted. |
| AI providers (Gemini primary, OpenRouter failover) | Built, tested offline. Live calls not run from this session. |
| Live dashboard (reason codes, redesign, light theme) | Built, tested. |
| Hardware gates (phases 2 to 4) | Open. Need the rig. |
| Live page Sense card + camera frame (/frame.png) | Built, tested, browser-checked. Real frame and provider untested. |
| `--case pharma/battery/smt` presets, DEMO_KITS.md | Built, tested on the fake rig (SIMULATED). Rehearse on hardware. |
| Tests | 398 passed. verify.sh exit 0. |
