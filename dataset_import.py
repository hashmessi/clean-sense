"""Build a labelled manifest from a folder of photos (for the accuracy gate).

    python3 dataset_import.py --folder <photos> --out labels.csv [--mapping mapping.json]

Folder layout: <folder>/<class_name>/*.jpg  (the class folder name is the source label).
--mapping is a JSON object {"source_class": "rig_mess_type"}. Without it, rows are
written with an empty label, so nothing is silently assumed. Rows whose class is not
in the mapping are skipped and reported.

IMPORTANT: public waste-object datasets (e.g. a 6-class cardboard/glass/metal/paper/
plastic/trash set) label whole objects, not scattered debris or spills on a tile.
Mapping them to rig mess types would be a guess. Use them for material vocabulary
checks only, and label the rig photos yourself for the accuracy gate.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def build_manifest(folder: Path, mapping: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    skipped: list[str] = []
    for class_dir in sorted(p for p in folder.iterdir() if p.is_dir()):
        if class_dir.name.startswith("__"):
            continue  # macOS archive metadata
        source = class_dir.name
        label = "" if mapping is None else mapping.get(source, "")
        if mapping is not None and not label:
            skipped.append(source)
            continue
        for img in sorted(class_dir.iterdir()):
            if img.suffix.lower() in IMAGE_EXT:
                rows.append({"path": str(img), "source_class": source, "label": label})
    return rows, skipped


def write_manifest(rows: list[dict], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "source_class", "label"])
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mapping", type=Path, default=None)
    args = ap.parse_args()
    mapping = json.loads(args.mapping.read_text()) if args.mapping else None
    rows, skipped = build_manifest(args.folder, mapping)
    write_manifest(rows, args.out)
    print(f"wrote {len(rows)} rows to {args.out}; skipped classes: {skipped or 'none'}")


if __name__ == "__main__":
    main()
