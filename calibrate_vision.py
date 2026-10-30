"""Calibration for camera-estimated weight (ADR-003). No scale and no ESP32 needed.

    python3 calibrate_vision.py reference --camera 0
        Tile EMPTY, lit as in the demo. Saves calibration/reference.npy (what "clean" looks like).

    python3 calibrate_vision.py dose --camera 0 --grams 5
        Put a known dose on the tile (weigh it on a trusted kitchen scale first). The model
        estimates it; the ratio is saved as a multiplier in calibration/vision_weight.json.

Needs a provider key in .env for `dose`. Keys are never printed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vision_weight import Unreadable, VisionLooker, compute_scale


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["reference", "dose"])
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--grams", type=float, default=None, help="true mass of the dose (dose step)")
    ap.add_argument("--out", default="calibration")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from camera import Camera, encode_png

    camera = Camera(args.camera)
    try:
        if args.step == "reference":
            import numpy as np

            np.save(out / "reference.npy", camera.grab_gray())
            print(f"saved {out / 'reference.npy'}: keep the tile empty and the light unchanged")
            return
        if args.grams is None:
            raise SystemExit("dose step needs --grams (the true mass of the dose)")
        from llm_transport import build_transports, load_env_file
        import os

        load_env_file()
        transports = build_transports(os.environ.get("LLM_PROVIDER", ""))
        if not transports:
            raise SystemExit("no provider configured: set a key in .env (see check_providers.py)")
        looker = VisionLooker(lambda: encode_png(camera.grab_bgr()), transports)  # same checks as a real run
        try:
            est = looker.look_with_retry().grams
        except Unreadable as exc:
            raise SystemExit(f"the model's estimate was unusable ({exc.reason}): fix framing and light, repeat")
        try:
            scale = compute_scale(args.grams, est)
        except ValueError as exc:
            raise SystemExit(str(exc))
        (out / "vision_weight.json").write_text(
            json.dumps({"scale": round(scale, 4), "known_g": args.grams, "estimated_g": est}, indent=2),
            encoding="utf-8")
        print(f"model said {est:g} g for a {args.grams:g} g dose -> scale {scale:.3f} saved")
    finally:
        camera.close()


if __name__ == "__main__":
    main()
