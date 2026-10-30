# SDE: tabletop industry demo gaps (2026-10-30)
Built (spec: .10x/specs/2026-10-30-tabletop-industry-demo-design.md, section 4):
- ui.py: DashboardState keeps `case` and `sense` (perceived, speaker, announced, announce_failed events); Live page "Camera sees" card (class, confidence, fixed spoken sentence, speech status, camera frame); Case tag; GET /frame.png serves one fixed file.
- run_rig.py: `--case pharma|battery|smt` preset (explicit --mess/--surface win; --offline never takes a mess from the case); `_perceive` saves the frame atomically to <log>/perception_latest.png and logs frame=true/false; setup event carries `case`.
- cases.py (new): presets, PERCEPTION_FRAME name, resolve(), case_title().
- DEMO_KITS.md (new): kits, commands, pre-session checklist, rehearsal log, judge answer. README points to it.
Not changed: rules, ladders, validator, firmware, announce.py, perception.py, camera.py, .env.
Safety: sentence shown is always a fixed template looked up by key; all log text is length-capped and class-checked; the page uses textContent only; /frame.png has no path input.
Deviations: none from the approved design. Debt: recent_events (raw log events) in /state would still carry a NaN if a log held one; our logger cannot write one.
Backups: ~/ship_backup/tabletop/.
