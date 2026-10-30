"""Camera pixel check and calibration summary, on synthetic images."""
import numpy as np
import pytest

from calibrate import summarize
from camera import camera_clean, mess_area_fraction


def tile(value=200):
    return np.full((100, 100), value, dtype=np.uint8)


def with_mess(base, pixels):
    img = base.copy()
    img[:pixels, :] = 40  # dark band = mess
    return img


def test_area_zero_for_identical_frames():
    t = tile()
    assert mess_area_fraction(t, t) == 0.0


def test_area_counts_changed_pixels():
    t = tile()
    assert mess_area_fraction(t, with_mess(t, 10)) == pytest.approx(0.10)


def test_camera_clean_when_mess_removed():
    ref = tile()
    start = with_mess(ref, 30)
    assert camera_clean(ref, start, ref) is True


def test_camera_not_clean_when_mess_remains():
    ref = tile()
    start = with_mess(ref, 30)
    assert camera_clean(ref, start, with_mess(ref, 10)) is False


def test_camera_floor_for_visually_empty_start():
    ref = tile()
    assert camera_clean(ref, ref, ref) is True
    assert camera_clean(ref, ref, with_mess(ref, 5)) is False


def test_shape_mismatch_raises():
    with pytest.raises(ValueError):
        mess_area_fraction(tile(), np.zeros((50, 50), dtype=np.uint8))


def test_calibration_summary_flags_repeatability():
    ok = summarize([10.0, 10.05, 9.98, 10.02, 10.1])
    assert ok["repeatable_within_target"] is True
    bad = summarize([10.0, 10.6, 9.3, 10.2, 9.9])
    assert bad["repeatable_within_target"] is False


def test_calibration_needs_five_samples():
    with pytest.raises(ValueError):
        summarize([1.0, 2.0])
