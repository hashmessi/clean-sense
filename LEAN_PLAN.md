# CleanSense Lite — LEAN hackathon plan (supersedes task detail where they conflict)
Principle: build the thinnest thing that is **safe, verified, and demoable**. No production polish. ~10–12 h of software, not 20.

## Lean architecture: 5 files + firmware
```
app.py        # state machine as plain if/else steps (no `transitions` lib), ~150 lines
planner.py    # one LLM call (vision, enum JSON) + rules_planner() in same file
rules.py      # validator (5 rules) + verifier + rung map — pure functions, unit-tested
rig.py        # serial JSONL: RealRig / FakeRig (same class interface)
ui.py         # FastAPI + one HTML page (WebSocket); logs to runs/<ts>.jsonl
eval.py       # replay logs -> one results table
```
Skip: hash-chain (plain JSONL is enough; add chain only if 20 spare min), `instructor` (use Pydantic + one retry by hand), state-machine lib, reconnect logic, auth, config system, packaging, CI, type-perfect code.

## Models — just 3 slots
| Slot | Pick | Rule |
|---|---|---|
| Planner | One hosted Claude vision call, temp 0, enum JSON, **no tool loop** (tools = `request_human` as an enum action only) | 1 retry on bad JSON, else rules planner |
| Fallback | rules_planner (operator taps dry/liquid) | also the ablation baseline |
| Edge vote | Piezo threshold tree on ESP32 B (RF only if time) | advisory |
Cut: Laya spike (only if everything else is green and a GPU is at hand), SigLIP, local VLM, NL override, conformal, JEPA.

## Rules — keep 5 (all enforced in code, fail-closed)
1. Schema/enums valid, else reject.
2. Rung must be next on the ladder for the mess type (dry L1>L2>human; liquid L1>L3>human; mixed L1>L2>L3; unknown L1>human).
3. Sensitive surface ⇒ pressure light, no L2 (firmware also clamps).
4. Budget: ≤3 attempts, ≤12 LLM calls; unknown/hazard ⇒ gentle or human.
5. Override can only reduce intensity/area.
Verifier: DONE iff residue ≤0.5 g AND ≤3% of start AND camera agrees; else retry/human. Model never says "clean".

## Build order (hours, estimates)
1. rig.py FakeRig + rules.py + tests incl. ~10 red-team cases (2 h) → **gate: pytest green**
2. app.py loop with rules_planner on FakeRig, 2 scenarios (1.5 h) → **gate: 10 fake runs each**
3. Plug real serial with teammates' firmware; measure noise floor, set thresholds (2 h) → **gate: first real run, e-stop works**
4. planner.py LLM call + 25 labelled photos accuracy check; AI-offline switch (2 h) → **gate: ≥90% type acc, 0 unsafe after rules, run completes with network off**
5. ui.py minimal: plan card, countdown, cancel, live mass, 3-voice log (1.5 h)
6. eval.py table + ablation (rules-only vs AI, same inputs, report a tie) (1 h)
7. Piezo edge vote threshold tree, 1 h cap, then rehearse ×5 + backup video (rest)
Freeze features at hour ~16 of the hackathon.

## Demo must-haves (everything else is optional)
Two scenarios (dry debris; liquid on sensitive surface refused → gentle/human) · countdown + cancel · hardware e-stop · scale-verified result · AI-offline fallback shown once · results table · README (design, safety, physical vs SIMULATED).

## Honest claim
"Model proposes from closed options; code validates; hardware vetoes; the scale decides 'clean'." Never "zero hallucination"; report counts with N.
