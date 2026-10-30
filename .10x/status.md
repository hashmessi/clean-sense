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
| Camera-estimated weight (no load cell), `--weigh vision`, ADR-003 | Built, tested with fakes. Live provider accuracy, webcam framing and calibration untested. Dashboard unchanged. |
| Servo pin 13 (ADR-005), geometry home 0 and sweep 0 to 90 (ADR-006), CHECKLIST.md rewritten | Done in docs, firmware and tests (new host geometry test). Not built on ESP32 toolchain. |
| ESP32 toolchain build attempt (2026-10-30, cloud session) | BLOCKED by network policy, not by the code: `pio run` stops at `Platform Manager: Installing espressif32 @ 8.0.0` with `HTTPClientError:`; the proxy answers 403 to CONNECT for api.registry.platformio.org, and dl.platformio.org, dl.espressif.com and downloads.arduino.cc are unreachable. Both `platformio.ini` files parse (env esp32dev, pinned libs). Sketch never compiled for the ESP32. Must be built on the Windows PC. |
| Tests | 462 passed. verify.sh exit 0. |
