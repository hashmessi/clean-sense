"""Accuracy gate: classify labelled photos with the configured provider. Reports N, accuracy, confusion.

    python eval_photos.py --labels labels.csv --out eval/photos.json [--image-root <folder>] [--provider gemini]

Keys come from .env or the environment, never from the command line. With no key it exits 2
and writes nothing that looks like a result. A photo the model cannot answer (error, bad JSON,
out-of-enum) counts as WRONG and is reported separately, so failures cannot hide.
Uses the FIRST configured provider only, one request per photo, no retry, so the number is one
provider's honest accuracy. Free-tier quotas are per day: keep the sample small and use --pause.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from planner_llm import MESS_TYPES, PlannerError, PlannerUnavailable

GATE_ACCURACY = 0.90
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def as_png(path: Path) -> bytes:
    """The provider layer sends PNG. Convert JPEG (and anything else OpenCV reads) first."""
    data = path.read_bytes()
    if data[:8] == PNG_MAGIC:
        return data
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"unreadable image {path}")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError(f"could not encode {path} as PNG")
    return buf.tobytes()


def score(rows: list[dict], predict: Callable[[bytes], dict],
          load: Callable[[Path], bytes] = _read_bytes, root: Optional[Path] = None) -> dict:
    labelled = [r for r in rows if r.get("label")]
    if len(labelled) < 25:
        raise ValueError(f"need at least 25 labelled photos, have {len(labelled)}")
    correct, errors = 0, 0
    confusion: Counter = Counter()
    for r in labelled:
        path = Path(r["path"])
        if root is not None and not path.is_absolute():
            path = root / path
        try:
            pred = predict(load(path))["mess_type"]
        except (PlannerError, PlannerUnavailable, Exception) as exc:  # noqa: BLE001
            errors += 1
            pred = f"error:{type(exc).__name__}"
        confusion[(r["label"], pred)] += 1
        if pred == r["label"]:
            correct += 1
    n = len(labelled)
    acc = correct / n
    return {
        "n": n,
        "accuracy": round(acc, 3),
        "errors": errors,
        "gate_accuracy": GATE_ACCURACY,
        "gate_passed": acc >= GATE_ACCURACY,
        "confusion": {f"{t}->{p}": c for (t, p), c in sorted(confusion.items())},
    }


def main() -> None:
    from llm_transport import build_transports, load_env_file
    from planner_llm import perceive_via_transports

    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--image-root", type=Path, default=None,
                    help="folder that relative paths in the labels CSV are under")
    ap.add_argument("--provider", default="", help="gemini | openrouter | anthropic (default: LLM_PROVIDER)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds after each request (rate limits)")
    args = ap.parse_args()

    load_env_file()
    transports = build_transports(args.provider or os.environ.get("LLM_PROVIDER", ""))
    if not transports:
        print("BLOCKED: no LLM provider key is set (GEMINI_API_KEY, OPENROUTER_API_KEY or "
              "ANTHROPIC_API_KEY). Accuracy gate not run.")
        sys.exit(2)
    primary = transports[0]

    with args.labels.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bad = [r["label"] for r in rows if r.get("label") and r["label"] not in MESS_TYPES]
    if bad:
        sys.exit(f"labels outside the mess-type enum: {sorted(set(bad))}")

    def predict(png: bytes) -> dict:
        try:
            return perceive_via_transports([primary], png)
        finally:
            time.sleep(args.pause)

    result = score(rows, predict, load=as_png, root=args.image_root)
    result["provider"] = primary.name
    result["model"] = getattr(primary, "model", "")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
