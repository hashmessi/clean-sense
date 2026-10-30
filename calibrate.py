"""Phase 2 calibration: measure the scale's noise floor and capture the empty-tile reference.

    python3 calibrate.py --port /dev/ttyUSB0 --camera 0 --out calibration/

Run with the tile EMPTY and the head parked. Writes calibration.json and
reference.npy. Gate 2 needs the repeatability result below before the 0.5 g
and 3% thresholds are trusted.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPEAT_TARGET_G = 0.2   # gate: tare->weigh repeatability within +/-0.2 g


def summarize(readings_g: list[float]) -> dict:
    if len(readings_g) < 5:
        raise ValueError("need at least 5 readings")
    mean = statistics.fmean(readings_g)
    std = statistics.stdev(readings_g)
    span = max(readings_g) - min(readings_g)
    return {
        "n": len(readings_g),
        "mean_g": round(mean, 3),
        "std_g": round(std, 3),
        "span_g": round(span, 3),
        "repeatable_within_target": span / 2 <= REPEAT_TARGET_G,
    }


def main() -> None:
    import numpy as np

    from serial_rig import open_serial_rig

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--out", default="calibration")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    camera = None
    if args.camera is not None:
        from camera import Camera
        camera = Camera(args.camera)

    rig = open_serial_rig(args.port, camera_clean=None)
    try:
        rig.park()
        readings = []
        for _ in range(args.samples):
            rig.tare()
            readings.append(rig.weigh().grams)
        result = summarize(readings)
        if camera is not None:
            frames = np.stack([camera.grab_gray() for _ in range(10)])
            reference = frames.mean(axis=0).astype(np.uint8)
            np.save(out / "reference.npy", reference)
            result["reference_saved"] = True
        (out / "calibration.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        rig.close()
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
