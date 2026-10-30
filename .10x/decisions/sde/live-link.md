# Live link: what was built
- runctl.py: cancel flag helpers, FileCancelRig, LogFollower, newest_log.
- ui.py: /cancel requires x-cancel-token (403 otherwise); writes flag; token embedded in live page;
  LogFollower started in live mode (not --demo); page sends the token.
- run_rig.py: clears stale flag, wraps rig in FileCancelRig.
- tests/test_live_link.py (8 tests); tests/test_phase4.py cancel test updated for the token and a temp log folder.
- Deviation: cancel checks run before each sweep, not during motion.
