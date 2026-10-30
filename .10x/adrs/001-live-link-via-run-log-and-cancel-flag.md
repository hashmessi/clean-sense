# ADR-001: Live link between a rig run and the dashboard

**Status:** Accepted
**Date:** 2026-10-30
**Feature:** live-link
**Author:** 10x-Team (Architect + Staff Engineer), approved by the operator

## Context
The CLI run (run_rig.py) and the dashboard (ui.py) are separate processes. The dashboard only
received events from runs inside its own process, so a real run never appeared on the live page,
and its Cancel button did nothing for a real run. Cancel also had no authentication.

## Decision
1. The dashboard follows the newest runs/*.jsonl as lines are appended (LogFollower). The
   hash-chained log is the only shared state. Only complete lines are consumed.
2. POST /cancel writes runs/CANCEL. A run checks it before every sweep (FileCancelRig), the same
   checkpoint as the hardware cancel button. A run clears a stale flag when it starts.
3. POST /cancel requires a random per-start token, embedded only in the live page. Browsers block
   other origins from reading that page (no CORS), so other pages cannot cancel runs.

## Alternatives Considered
| Alternative | Pros | Cons | Why Not |
|---|---|---|---|
| Start runs from the dashboard | Real product flow | Needs auth, hardware lock, crash handling | Out of scope for now |
| Socket or IPC between processes | Instant | New moving parts on a safety path | Log already is the record |
| Leave Cancel as is | No change | Button does nothing for real runs | Broken contract |

## Consequences
- Positive: no new server coupling; works for CLI and demo runs; no database needed.
- Negative: live view lags by up to 0.5 s; cancel takes effect before the next sweep, not mid-motion.
- Risks: a cancel pressed before a run starts is discarded when the run clears the flag.
