# SDE: spoken announcements and camera perception

## Built
- perception.py: FORCE_HUMAN, forced_human, template_key, guarded_planner.
- announce.py: TEMPLATES (6 fixed sentences), NullSpeaker, PiperSpeaker (piper CLI, winsound/aplay),
  speaker_from_env (silent unless PIPER_VOICE and PIPER_CMD resolve), Announcer, announce_for_run.
- camera.py: Camera.grab_bgr, encode_png.
- run_rig.py: _perceive (one frame, one provider call, any failure -> None), perception step after the
  edge event, speaker event, announcement, planner guard, bounded wait before run().
- .env.example: PIPER_CMD, PIPER_VOICE (empty by default).
- tests/test_announce.py: 35 tests (decision table, guard, run with no sweep, speech, logging, speaker
  selection, perception failures, dashboard tolerance).

## Deviations from the approved design
- Announcements wait up to 10 s before motion (design said background only). Reason: people must hear the
  warning before the robot moves. Timeout is logged and the run continues.
- Perception failure handling broadened to any Exception at the boundary, so a camera-library fault can never
  stop a run before motion.
- No new Python dependency: Piper runs through its command line; playback uses stdlib winsound on Windows.

## Tech debt
- Piper CLI flags unverified on this machine (PIPER_CMD may need adjusting per release).
- Perception frame is taken before run() parks the head; confirm on the rig that the spill is in view.
- Coffee is not named; announcements say "liquid".
