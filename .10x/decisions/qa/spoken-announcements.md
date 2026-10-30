# QA: spoken announcements and camera perception

## Gate results (this session)
- pytest: 375 passed (340 existing + 35 new).
- verify.sh: exit 0, ALL CHECKS PASSED (firmware host builds, rule baselines, gate-1 simulation, ablation,
  red-team replay 15/15 blocked, photo eval blocked without key).

## Covered
- Every row of the decision table; the "camera never widens" property over all class pairs.
- Every announced class and forced state maps to a fixed template with no placeholders.
- Forced runs: zero sweeps, outcome human. Unforced runs still sweep.
- Speech: template text only; unknown key fails fast; speaker error recorded; wait timeout does not raise.
- Logs: announced event carries the key, never the sentence; hash chain verifies.
- Perception: one call, no retry; failed or invalid or unexpected look returns None and logs the error class.

## Not covered (needs the PC and the rig)
- Real Piper voice and real speakers (tests use fakes; no audio hardware here).
- Real phone webcam frame and real provider classification (no live calls from this session).
- Timing of the 10 s speech wait on the real rig.

## Quality risks
- Classifier accuracy for "liquid" on real coffee spills is unmeasured.
