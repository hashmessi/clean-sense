# ADR-002: Camera perception that can only add caution, and fixed spoken announcements

**Status:** Accepted (hackathon build; user approved design 2026-10-30)
**Date:** 2026-10-30
**Feature:** spoken-announcements
**Author:** 10x-Team (Architect + Staff Engineer)

## Context
The run loop never looked at the spill. The mess type came from the operator's `--mess` flag, or the
edge vote, and the photo classifier only ran in the offline eval. The user wants: point the camera at a
spill, the system classifies it, and the PC speaks a short message to people nearby, with a next step.

Forces:
- The rules and the validator are the safety boundary. An AI or camera result must not widen what the
  robot may do.
- The spoken message must match what the robot will actually attempt. Saying "the robot will try a
  gentle clean" and then sending the robot to a person (or the reverse) is a false statement to people.
- Hackathon: 24 hours, one PC, no new hardware. Piper is open-source and offline, but the project is not
  verified against a live Piper install from here.

## Decision
1. **Camera perception, one call per run.** `run_rig.py` grabs one colour frame at run start and
   classifies it through the first configured provider (`perceive_via_transports`: one request, no retry,
   no fail-over, timeout = `TIMEOUT_S`). A failed look means no announcement and no extra caution.
2. **Camera can only add caution.** `perception.forced_human(run_mess, perceived)` is true when the
   perceived class is `liquid` or `unsupported_hazard` and differs from the run's (fused) mess type. Then
   the planner is wrapped so it returns a human hand-over plan and never calls the LLM or the rules ladder.
   Every other perceived class leaves the plan exactly as the rules and operator set it.
3. **Fixed announcements only.** `announce.TEMPLATES` maps keys to fixed sentences. Model text never reaches
   the speaker. The announcement says what the robot will *try*, which is true for every runnable class.
   The rules can still hand a run to a person later; "try" is the honest wording for that.
4. **Speech is announce-only and bounded.** Speech runs on a background thread. The run waits up to
   `ANNOUNCE_WAIT_S` (10 s) before motion so people hear it first. A timeout or device error is logged and
   the run continues; it never raises into the run loop.
5. **Audit.** Events: `perceived` (provider name, class, confidence, or error class), `announced` (template
   key and whether it was spoken, never the sentence text), `announce_failed` (reason), `speaker` (note).
   The planner's human plan is already logged through `proposed`/`decided` with the reason.
6. **Piper behind an interface.** `PiperSpeaker` calls the `piper` command line (`PIPER_CMD`, default `piper`)
   with `PIPER_VOICE` as the `.onnx` model. Playback: `winsound` on Windows, `aplay` elsewhere. Missing voice
   file or binary falls back to `NullSpeaker` with a note. No new Python dependency.

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| Camera result sets the run's mess type (liquid ladder) | Plan and message always match the camera | Lets an AI/vision result widen the robot's ladder; a wrong read sweeps a hazard | Widens authority; the agreed rule is "can only add caution" |
| Announce-only, plan unchanged | Simplest | Speech and robot disagree on a liquid spill | Message would be false for the operator's type |
| Speak model-written text | Richer wording | Model output reaches speakers; injection and off-script claims | Fixed templates only, agreed |
| Piper Python package API | No subprocess | API differs across Piper releases; cannot verify here | CLI is stable and easy to swap; verify on PC |
| Speak on a background thread with no wait | Run never delayed | Robot could move while people are still hearing the warning | Wait is bounded at 10 s, acceptable |

## Consequences
### Positive
- A camera error can never make the robot do more than the rules allow; it can only send the run to a person.
- Every spoken sentence is one of six reviewed strings.
- The audit log records what was perceived, what was said (by key), and why a plan was forced.

### Negative
- One extra camera frame and one provider call per run (latency up to `TIMEOUT_S`).
- Perception needs `--llm` and a provider key; rules-only and offline runs do not announce.
- A run where the camera says `dry_debris` while the operator says `liquid` uses the liquid ladder with a
  "gentle clean" announcement, which is accurate but relies on the operator's type.

### Risks
- Piper CLI flags or voice file format may differ by release. Mitigation: `PIPER_CMD` override, tested with
  a fake speaker; a live check on the PC is still required.
- Speech device hang. Mitigation: bounded wait and 30 s subprocess timeout.
- Classifier misses a real spill. Consequence is the same as today (operator type drives the run). Coffee
  is not identified; the message says "liquid", as agreed.

## Dependencies
- Depends on: `planner_llm.perceive_via_transports`, `planner.rules_planner`, `app.run`, `AuditLog`.
- Constrains: any future change that lets perception widen a plan needs a new ADR that supersedes this one.
- Does not touch: e-stop, cancel, validator rules, firmware.
