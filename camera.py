"""Camera check: does the tile look clean, measured against an empty-tile reference.

The camera never declares success alone. verify() still needs the scale, and
the two must agree. Disagreement is treated as NOT CLEAN.

Pure functions (testable with synthetic images) + a thin OpenCV capture class.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from rules import RESIDUE_FRAC

DIFF_THRESHOLD = 25          # grey-level difference that counts as "mess"
MIN_AREA_FRAC = 0.002        # below this the tile counts as visually empty


def mess_area_fraction(reference: np.ndarray, frame: np.ndarray,
                       roi: Optional[tuple[int, int, int, int]] = None) -> float:
    """Fraction of ROI pixels that differ from the empty-tile reference."""
    if reference.shape != frame.shape:
        raise ValueError("reference and frame must have the same shape")
    ref = reference.astype(np.int16)
    cur = frame.astype(np.int16)
    if roi is not None:
        x, y, w, h = roi
        ref = ref[y:y + h, x:x + w]
        cur = cur[y:y + h, x:x + w]
    mask = np.abs(cur - ref) > DIFF_THRESHOLD
    return float(mask.mean())


def camera_clean(reference: np.ndarray, start_frame: np.ndarray,
                 now_frame: np.ndarray,
                 roi: Optional[tuple[int, int, int, int]] = None) -> bool:
    """Clean if remaining mess area is within the same 3% rule the scale uses.

    If the start frame showed almost no mess, the absolute floor applies.
    """
    start_area = mess_area_fraction(reference, start_frame, roi)
    now_area = mess_area_fraction(reference, now_frame, roi)
    limit = max(RESIDUE_FRAC * start_area, MIN_AREA_FRAC)
    return now_area <= limit


class Camera:
    """OpenCV capture. Not used in tests (no camera in CI)."""

    def __init__(self, index: int = 0) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(f"camera {index} not available")

    def grab_bgr(self) -> np.ndarray:
        """One colour frame (BGR) for classification. Same failure rule as grab_gray."""
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise RuntimeError("camera read failed")
        return frame

    def grab_gray(self) -> np.ndarray:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise RuntimeError("camera read failed")
        return self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)

    def close(self) -> None:
        self._cap.release()


def encode_png(frame: np.ndarray) -> bytes:
    """PNG bytes for the provider layer. Raises when encoding fails."""
    import cv2

    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("png encode failed")
    return buf.tobytes()
