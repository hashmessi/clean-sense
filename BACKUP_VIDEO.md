# Backup video: shot list (record before the live demo)

Record one clean take of each scenario on the real rig, the same day as the demo.
Label the file with the date and the run's log ID. If the live demo fails, play
this, and say that it is a recording.

Before recording
- [ ] Tile empty, head parked, camera reference captured (`calibration/reference.npy`).
- [ ] E-stop tested: press it, head parks, release, the run stays stopped until restarted.
- [ ] Cancel button tested before the countdown ends.
- [ ] Dashboard open (`python3 ui.py`) and visible in frame, with the SIMULATED or
      PHYSICAL label shown truthfully on screen.

Shots (in order)
1. Empty tile, scale reads about 0 g. Say the start mass out loud.
2. Dry debris dropped on glass. Scale and camera both read the start state.
3. Run `run_rig.py --mess dry_debris --surface glass`. Show the planner's proposal
   (`proposed`) and the validator's decision (`decided`) on the dashboard.
4. Pass 1 (L1). Mass goes down. Verdict: RETRY or DONE, shown on screen.
5. If RETRY: pass 2 (L2) on the next rung. Show that the validator would refuse L3
   on this ladder.
6. DONE: residue at or under the limit and camera agreeing. Read the end mass.
7. Sensitive surface run (`--surface wood`): show that L2 is never chosen.
8. Cancel run: press cancel during the countdown. Show "cancelled" in the log.
9. AI OFFLINE: unset the key or disconnect the planner, show the banner and the
   rules planner taking over.

Narration rules
- Say what the AI proposed and what the code allowed. Never say the AI cleaned it.
- Say SIMULATED wherever the screen shows simulator output (ablation, demo).
- Do not show the API key or any file that contains it.
