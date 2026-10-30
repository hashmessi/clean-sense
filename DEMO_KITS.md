# Tabletop demo kits and rehearsal sheet

One rig, three scene kits. Each kit is a preset (`--case`) plus stand-in material. The presets only set
the operator's mess type and the surface setting; every rule, ladder and firmware limit is unchanged, and
the scale and camera still decide clean. Say "stand-in material" out loud. We have not run real API powder,
electrolyte or solder paste.

## Kits
| Kit | Stand-in | Command (add `--llm --camera 0` for the vision look and spoken warning) | What to expect |
|---|---|---|---|
| Pharma | a pinch of flour + 3 to 5 lentils | `python run_rig.py --port COM3 --case pharma` | dry debris on glass; gentle then firmer pass; scale to ~0 g; camera agrees |
| Battery, powder | a light dusting of cocoa | `python run_rig.py --port COM3 --case battery` | sensitive surface: never L2. One gentle pass may finish it; if not, a person takes over |
| Battery, coffee | one teaspoon of coffee | `python run_rig.py --port COM3 --case battery --mess liquid` | camera sees liquid; spoken "Liquid spill detected. Keep people back. The robot will try a gentle clean."; gentle then absorbent pass |
| Battery, camera adds caution | cocoa preset, then pour the coffee | `python run_rig.py --port COM3 --case battery --llm --camera 0` | operator said dry, camera sees liquid: spoken "A person will clean it."; no sweep at all |
| SMT | rice + sesame + a dab of toothpaste | `python run_rig.py --port COM3 --case smt` | mixed mess on a protected surface: one gentle pass, then the rules bar the firm rung and a person takes over |

Explicit flags win over the preset: `--mess` and `--surface` override `--case`.

Fake-rig result, SIMULATED (8 g residue model, one seed, not the hardware): pharma clean after 3 passes;
battery powder hands over after 1 pass; battery liquid clean after 3 passes; SMT hands over after 1 pass.
Your real amounts will differ. Rehearse each kit and write down what it actually does.

## What the judges see on the Live page (http://127.0.0.1:8000)
Camera sees (class, confidence, the camera frame, the fixed spoken sentence) > Proposed (AI rung and reason)
> Checked by the rules (allowed or refused, with the rule) > Measured (start g, limit, camera agrees, verdict).
The "Camera sees" card needs `--llm` and `--camera`; without them it says so. The camera frame it shows is
saved as `runs/perception_latest.png` and overwritten each run; it is served only on 127.0.0.1. Delete it if
people were in view.

## Before every session
- [ ] E-stop pressed: head parks; released: the run stays stopped until restarted.
- [ ] Cancel pressed during the countdown: log shows "cancelled".
- [ ] Empty tile reads ~0.00 g; camera reference captured (`calibration/reference.npy`); lamp on, no shadows.
- [ ] `python check_providers.py` ok; `.env` has the keys (never paste them anywhere).
- [ ] Piper test sentence is audible; speaker volume set.
- [ ] Phone or webcam shows the whole tile. The look is taken before the head parks, so the spill must be in view then.
- [ ] Dashboard shows the truthful PHYSICAL or SIMULATED label. Kits labelled "stand-in material".
- [ ] Backup video recorded the same day (BACKUP_VIDEO.md).

## Rehearsal log (5 runs per kit)
| Kit | Run | Mess g | Camera class | Outcome | Passes | End g | Note |
|---|---|---|---|---|---|---|---|
| Pharma | 1 | | | | | | |
| Battery powder | 1 | | | | | | |
| Battery coffee | 1 | | | | | | |
| Battery caution | 1 | | | | | | |
| SMT | 1 | | | | | | |
(copy the rows for runs 2 to 5)

## Judge question: where does the scale sit?
Under the tile. The tile is the weigh zone, on one HX711 load cell; the mess lands on it, the camera looks
down at it, the wiper clears it, and the scale reads the residue. It is a fixed single-tile weigh zone, not
the whole floor. For a larger floor the same loop applies with a carry-under tray or swab-and-weigh, which
we have designed but not built.

## Honest limits
Classification is into 5 fixed classes (dry debris, liquid, mixed, unknown, hazard), not material
identification. The AI proposes from a fixed list; code validates; hardware vetoes; the scale decides clean.
Hardware gates still open: E-stop wiring decision (INTEGRATION.md 8.1), CAL_FACTOR, firmware flash.
