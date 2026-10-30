# Tabletop industry demo: design (gaps 4.1 to 4.4 built 2026-10-30; kits in DEMO_KITS.md)

Goal: one table, one rig, a ~2:45 live demo that makes the industry claim visible in four stages:
trigger, sense, act, verify. Judges must SEE the vision detection, the waste classification, the AI
reasoning, and the scale-verified clean. Stand-in materials only; no new firmware.

## 1. Rig on the table (existing hardware)
- Tile on the HX711 load cell = the weigh zone (answer to "where is the scale": under the tile, not the floor).
- Phone or laptop webcam on a stand above the tile, desk lamp for steady light.
- Servo wiper at one tile edge, plain catch tray beside it. E-stop and cancel within the presenter's reach.
- PC runs run_rig.py + ui.py + Piper speaker. Second screen shows the SVG case board.

## 2. Three scene kits (swap about 30 s)
| Kit | Stand-in mess | Mess class | Surface | Expected story |
|---|---|---|---|---|
| Pharma | flour + lentils (burst capsule) | dry_debris | glass | L1 sweep, scale to ~0 g, camera agrees, line resumes |
| Battery | cocoa powder, then a spoon of coffee | dry_debris, then liquid (`--mess liquid`) | wood (sensitive) | powder: never L2, one gentle pass may finish it or a person takes over; coffee: camera says liquid, spoken warning, gentle then absorbent pass |
| SMT | rice + sesame + toothpaste dab | mixed | wood (sensitive) | one gentle pass, validator bars the firm rung, person takes over (confirmed on the fake rig, SIMULATED; confirm on hardware) |
Kits are labelled "stand-in material". No real electrolyte, API powder or solder paste.

## 3. Four stages and the evidence on screen
| Stage | Judge sees | Source | Built? |
|---|---|---|---|
| Trigger | mess dropped, scale jumps off 0.00 g | live mass | yes |
| Sense: vision detection | camera class (liquid / dry / mixed / unknown / hazard) | `perceived` event | logged, NOT on the Live page |
| Sense: waste classification | AI mess_type from the fixed list, plus the spoken sentence | `proposed`, `announced` events | proposal shown; spoken sentence NOT shown |
| AI reasoning | Proposed card (rung + reason) -> Check card (allowed/refused with rule) -> hardware allowed | `proposed`, `decided`, `allowed` | yes |
| Act | wiper pass named by rung | `allowed` | yes |
| Verify | start g -> end g, limit, camera agrees, verdict Clean / Human takes over | sensor events | yes |
Claim wording: classification is into 5 fixed classes (dry debris, liquid, mixed, unknown, hazard),
not material identification. The AI proposes; the scale and camera decide clean.

## 4. Gaps to build (small, each with tests, after approval)
1. Live page "Sense" card: perceived class and the announced sentence, tolerant of missing events.
2. Optional: latest camera frame thumbnail on the Live page so the vision stage is literally visible.
3. `--case pharma|battery|smt` preset in run_rig.py: sets mess + surface, shows the case name on the Live page.
4. Rehearsal script and kit checklist (printed case cards, plate labels).

## 5. Run of show (~2:45)
0:00 empty plate, state the rule | 0:20 Pharma live | 1:10 Battery powder, then coffee + spoken warning |
2:00 SMT refusal + human hand-off | 2:35 audit log, PHYSICAL/SIMULATED label. SVG boards are the fallback.

## 6. Risks and gates (unchanged, still open)
E-stop wiring decision (INTEGRATION.md 8.1), CAL_FACTOR placeholder, firmware flash, Piper install,
live provider check, perception frame taken before the head parks, SMT end state unverified.
Rehearse each kit 5 times; record the backup video the same day (BACKUP_VIDEO.md).

## 7. Out of scope
New firmware, real hazardous materials, three physical stations, material identification, any claim of
validated industrial deployment.
