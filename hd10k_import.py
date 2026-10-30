"""Turn the HD10K dirt dataset (gaussianopensource/dl_active_cleaning) into a labelled CSV.

    python3 hd10k_import.py --root /path/to/HD10K --out eval/hd10k_labels.csv

Image-level labels, derived from the annotations (never guessed):
  solid_dirts with >=1 bounding box          -> dry_debris
  liquid_dirts mask with liquid pixels       -> liquid
  test image with BOTH boxes and liquid      -> mixed   (only detectable in test, where
                                                          both annotations share a folder)
  no annotation or an empty one              -> skipped, counted as "no_mess"
  image without its annotation file          -> skipped, counted as "missing_annotation"
HD10K has no hazard, unknown or hard-surface class, so those labels never come from here.

LICENCE: the dataset page states no licence. Keep the data out of this repo, use it for
internal evaluation only, and ask the authors before publishing results built on it.
Domain: robot-camera floor views in shopping malls, not a close-up of a test tile. Use it
for the dry-vs-liquid sanity check; the rig gate still needs our own photos.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg"}
LIQUID_RGB_BGR = (0, 0, 255)  # masks are RGB [255,0,0] for liquid, read as BGR by OpenCV


def has_liquid(mask_path: Path) -> bool:
    import cv2
    import numpy as np

    m = cv2.imread(str(mask_path), cv2.IMREAD_COLOR)
    if m is None:
        raise ValueError(f"unreadable mask {mask_path}")
    b, g, r = m[..., 0], m[..., 1], m[..., 2]
    return bool(np.any((r > 200) & (g < 60) & (b < 60)))


def has_boxes(bbox_path: Path) -> bool:
    lines = [ln for ln in bbox_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for ln in lines:
        parts = ln.split()
        if len(parts) != 5:
            raise ValueError(f"bad box line in {bbox_path}: {ln!r}")
    return bool(lines)


def _images(folder: Path):
    if not folder.is_dir():
        return []
    # skip macOS metadata ("._name.jpg") that zip/copy tools leave beside real files
    return sorted(p for p in folder.rglob("*")
                  if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))


def build_rows(root: Path) -> tuple[list[dict], Counter]:
    rows: list[dict] = []
    stats: Counter = Counter()

    # train: solid and liquid images are listed separately, each with its own annotation
    for img in _images(root / "train" / "solid_dirts" / "images"):
        scene = img.parent.name
        ann = root / "train" / "solid_dirts" / "solid_dirts_bboxes" / scene / (img.stem + ".txt")
        if not ann.exists():
            stats["missing_annotation"] += 1
            continue
        if has_boxes(ann):
            rows.append(_row(img, "train", scene, "solid", "dry_debris"))
        else:
            stats["no_mess"] += 1

    for img in _images(root / "train" / "liquid_dirts" / "images"):
        scene = img.parent.name
        mask = root / "train" / "liquid_dirts" / "liquid_dirts_masks" / scene / (img.stem + ".png")
        if not mask.exists():
            stats["missing_annotation"] += 1
            continue
        if has_liquid(mask):
            rows.append(_row(img, "train", scene, "liquid", "liquid"))
        else:
            stats["no_mess"] += 1

    # test: one image folder, both annotation kinds for the same frame
    for img in _images(root / "test" / "images"):
        scene = img.parent.name
        ann = root / "test" / "solid_dirts_bboxes" / scene / (img.stem + ".txt")
        mask = root / "test" / "liquid_dirts_masks" / scene / (img.stem + ".png")
        if not ann.exists() or not mask.exists():
            stats["missing_annotation"] += 1
            continue
        solid, liquid = has_boxes(ann), has_liquid(mask)
        if solid and liquid:
            rows.append(_row(img, "test", scene, "solid+liquid", "mixed"))
        elif solid:
            rows.append(_row(img, "test", scene, "solid", "dry_debris"))
        elif liquid:
            rows.append(_row(img, "test", scene, "liquid", "liquid"))
        else:
            stats["no_mess"] += 1

    return rows, stats


def _row(img: Path, split: str, scene: str, source: str, label: str) -> dict:
    return {"path": str(img), "split": split, "scene": scene, "source": source, "label": label}


def write_csv(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "split", "scene", "source", "label"])
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path, help="folder that contains train/ and test/")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    rows, stats = build_rows(args.root)
    write_csv(rows, args.out)
    counts = Counter(r["label"] for r in rows)
    print(f"wrote {len(rows)} labelled rows to {args.out}")
    print(f"labels: {dict(counts)}")
    print(f"skipped: {dict(stats)}")


if __name__ == "__main__":
    main()
