# Live link: architecture
- Run process writes hash-chained JSONL to runs/. Dashboard process reads it (LogFollower, runctl.py).
- Cancel: flag file runs/CANCEL, checked before each sweep. Hardware cancel and E-stop unchanged.
- Auth: token on POST /cancel only. Reads are localhost-only and unauthenticated (accepted).
- Boundary: ui.py and run_rig.py import runctl.py; no provider or hardware code in runctl.py.
