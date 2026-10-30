"""HD10K importer on a synthetic tree with the README's layout. No real data is used."""
import csv
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

cv2 = pytest.importorskip("cv2")

from hd10k_import import build_rows, write_csv  # noqa: E402


def _img(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((8, 8, 3), 128, np.uint8))


def _mask(path: Path, liquid: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    m = np.zeros((8, 8, 3), np.uint8)  # BGR
    if liquid:
        m[2:4, 2:4] = (0, 0, 255)  # red in RGB
    cv2.imwrite(str(path), m)


def _boxes(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def hd10k(tmp_path):
    root = tmp_path / "HD10K"
    # train/solid: one with a box (dry), one empty (no_mess), one without annotation (missing)
    t = root / "train" / "solid_dirts"
    _img(t / "images" / "scene_0" / "a.png"); _boxes(t / "solid_dirts_bboxes" / "scene_0" / "a.txt", "0 0.5 0.5 0.1 0.1\n")
    _img(t / "images" / "scene_0" / "b.png"); _boxes(t / "solid_dirts_bboxes" / "scene_0" / "b.txt", "")
    _img(t / "images" / "scene_0" / "c.png")
    # train/liquid: one with liquid (liquid), one with an empty mask (no_mess)
    l = root / "train" / "liquid_dirts"
    _img(l / "images" / "scene_1" / "d.png"); _mask(l / "liquid_dirts_masks" / "scene_1" / "d.png", True)
    _img(l / "images" / "scene_1" / "e.png"); _mask(l / "liquid_dirts_masks" / "scene_1" / "e.png", False)
    # test: solid only, liquid only, both (mixed), neither
    s = root / "test"
    _img(s / "images" / "scene_2" / "f.png"); _boxes(s / "solid_dirts_bboxes" / "scene_2" / "f.txt", "0 0.5 0.5 0.1 0.1\n")
    _mask(s / "liquid_dirts_masks" / "scene_2" / "f.png", False)
    _img(s / "images" / "scene_2" / "g.png"); _mask(s / "liquid_dirts_masks" / "scene_2" / "g.png", True)
    _boxes(s / "solid_dirts_bboxes" / "scene_2" / "g.txt", "")
    _img(s / "images" / "scene_2" / "h.png"); _mask(s / "liquid_dirts_masks" / "scene_2" / "h.png", True)
    _boxes(s / "solid_dirts_bboxes" / "scene_2" / "h.txt", "0 0.2 0.2 0.1 0.1\n")
    _img(s / "images" / "scene_2" / "i.png"); _mask(s / "liquid_dirts_masks" / "scene_2" / "i.png", False)
    _boxes(s / "solid_dirts_bboxes" / "scene_2" / "i.txt", "")
    return root


def _labels(rows):
    return {Path(r["path"]).name + ":" + r["split"]: r["label"] for r in rows}


def test_labels_come_from_annotations(hd10k):
    rows, _ = build_rows(hd10k)
    got = _labels(rows)
    assert got["a.png:train"] == "dry_debris"
    assert got["d.png:train"] == "liquid"
    assert got["f.png:test"] == "dry_debris"
    assert got["g.png:test"] == "liquid"
    assert got["h.png:test"] == "mixed"
    assert "b.png:train" not in got and "e.png:train" not in got and "i.png:test" not in got


def test_skips_are_counted_not_labelled(hd10k):
    _, stats = build_rows(hd10k)
    assert stats["missing_annotation"] == 1  # train c.png has no box file
    assert stats["no_mess"] == 3             # b (empty boxes), e (empty mask), i (nothing)


def test_no_hazard_or_unknown_labels_ever_come_from_hd10k(hd10k):
    rows, _ = build_rows(hd10k)
    assert {r["label"] for r in rows} <= {"dry_debris", "liquid", "mixed"}


def test_csv_has_the_columns_eval_photos_needs(hd10k, tmp_path):
    rows, _ = build_rows(hd10k)
    out = tmp_path / "labels.csv"
    write_csv(rows, out)
    with out.open(newline="") as f:
        reader = csv.DictReader(f)
        assert {"path", "label"} <= set(reader.fieldnames)
        assert len(list(reader)) == len(rows)


def test_macos_metadata_files_are_ignored(hd10k):
    s = hd10k / "test"
    _img(s / "images" / "scene_2" / "._junk.png")  # AppleDouble copy, not a real image
    rows, stats = build_rows(hd10k)
    assert not any("._" in r["path"] for r in rows)
    assert stats["missing_annotation"] == 1  # unchanged: junk file is not counted as a sample


def test_bad_box_line_is_an_error_not_a_silent_skip(tmp_path):
    root = tmp_path / "HD10K"
    _img(root / "test" / "images" / "scene_0" / "x.png")
    _mask(root / "test" / "liquid_dirts_masks" / "scene_0" / "x.png", False)
    _boxes(root / "test" / "solid_dirts_bboxes" / "scene_0" / "x.txt", "0 0.5 0.5\n")
    with pytest.raises(ValueError, match="bad box line"):
        build_rows(root)
