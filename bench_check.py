"""Bench bring-up checks for the hardware bench. Run one step at a time, never all at once.

    python3 bench_check.py --port /dev/ttyUSB0 --step link
    python3 bench_check.py --port /dev/ttyUSB0 --step scale            # tile EMPTY
    python3 bench_check.py --port /dev/ttyUSB0 --step sweep --confirm-clear-area

Each step prints PASS or FAIL with the reason. `sweep` moves the wiper, so it needs
--confirm-clear-area (the tile and scale must be clear of anything you care about).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from typing import Optional

from rig import RigFault

STD_LIMIT_G = 0.2     # repeatability with the tile empty (same gate as calibrate.py)
EMPTY_LIMIT_G = 0.5   # an empty tile should read close to zero after tare
STEPS = ("link", "scale", "park", "sweep")


def check_link(rig) -> tuple[bool, str]:
    rig.park()  # any reply proves the serial link and the firmware are alive
    return True, "firmware answered park"


def check_scale(rig, samples: int = 5, expect_empty: bool = True) -> tuple[bool, str]:
    rig.tare()
    grams = [rig.weigh().grams for _ in range(samples)]
    if any(not math.isfinite(g) for g in grams):
        return False, f"non-finite reading: {grams}"
    std = statistics.pstdev(grams)
    mid = statistics.median(grams)
    detail = f"median {mid:.2f} g, std {std:.3f} g over {samples} reads"
    if std > STD_LIMIT_G:
        return False, detail + f": std above {STD_LIMIT_G} g, check wiring and the load-cell mount"
    if expect_empty and abs(mid) > EMPTY_LIMIT_G:
        return False, detail + f": tile should be empty, |median| above {EMPTY_LIMIT_G} g"
    return True, detail


def check_park(rig) -> tuple[bool, str]:
    rig.park()
    g = rig.weigh().grams
    return (math.isfinite(g), f"parked, scale still reads {g:.2f} g")


def check_sweep(rig, confirmed: bool) -> tuple[bool, str]:
    if not confirmed:
        return False, "refused: pass --confirm-clear-area (the wiper will move)"
    try:
        rig.sweep("L1")  # one pass, slow, narrow: the gentlest rung
    except RigFault as fault:
        try:
            rig.park()
        except RigFault:
            pass
        return False, f"firmware fault: {fault}"
    rig.park()
    return True, "one L1 pass completed and the head parked"


def run_steps(rig, steps: list[str], confirmed: bool = False,
              samples: int = 5, expect_empty: bool = True) -> list[dict]:
    results = []
    for step in steps:
        try:
            if step == "link":
                ok, detail = check_link(rig)
            elif step == "scale":
                ok, detail = check_scale(rig, samples, expect_empty)
            elif step == "park":
                ok, detail = check_park(rig)
            elif step == "sweep":
                ok, detail = check_sweep(rig, confirmed)
            else:
                ok, detail = False, f"unknown step {step}"
        except RigFault as fault:
            ok, detail = False, f"link or firmware fault: {fault}"
        results.append({"step": step, "pass": ok, "detail": detail})
        if not ok:
            break  # stop at the first failure; later steps depend on this one
    return results


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--not-empty", action="store_true", help="skip the empty-tile check")
    ap.add_argument("--confirm-clear-area", action="store_true")
    args = ap.parse_args(argv)

    if args.step == "sweep" and not args.confirm_clear_area:
        print(json.dumps([{"step": "sweep", "pass": False,
                           "detail": "refused: pass --confirm-clear-area"}], indent=2))
        return 1

    from serial_rig import open_serial_rig

    rig = open_serial_rig(args.port, heartbeat=True)
    try:
        results = run_steps(rig, [args.step], args.confirm_clear_area,
                            args.samples, expect_empty=not args.not_empty)
    finally:
        rig.close()
    for r in results:
        print(("PASS " if r["pass"] else "FAIL ") + f"{r['step']}: {r['detail']}")
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
