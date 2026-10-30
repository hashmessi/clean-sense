"""Rig interface + simulated rig.

SIMULATED: FakeRig models the scale, the wiper and the camera in software.
Phase 2 adds SerialRig with the same methods, talking JSON lines to the ESP32.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from rules import RUNG_PARAMS


class RigFault(Exception):
    """Hardware-level stop: e-stop, timeout, clamp. Always aborts the run."""


@dataclass(frozen=True)
class Reading:
    grams: float
    settled: bool


class FakeRig:
    """Simulated rig. Residue on the scale is removed by the wiper per pass."""

    # per-pass removal efficiency by rung (sim parameter, not a claim about the real wiper)
    EFFICIENCY = {"L1": 0.5, "L2": 0.6, "L3": 0.85}

    def __init__(
        self,
        residue_g: float = 10.0,
        noise_g: float = 0.05,
        seed: int = 0,
        camera_error: float = 0.0,
        estop_on_sweep: bool = False,
        cancel_at_countdown: bool = False,
    ) -> None:
        self._rng = random.Random(seed)
        self._residue = residue_g
        self._noise = noise_g
        self._camera_error = camera_error
        self._estop_on_sweep = estop_on_sweep
        self._cancel = cancel_at_countdown
        self.sweeps: list[str] = []  # what the hardware actually did (allowed voice)
        self.estop_latched = False

    # --- hardware-facing API (same shape as SerialRig) -----------------
    def park(self) -> None:
        pass  # head up / parked. Real rig sends {"cmd":"park"}.

    def tare(self) -> None:
        pass  # sim scale already zeroed; real rig re-zeroes with head lifted.

    def weigh(self) -> Reading:
        g = max(0.0, self._residue + self._rng.gauss(0.0, self._noise))
        return Reading(grams=round(g, 3), settled=True)

    def sweep(self, rung: str) -> None:
        if self.estop_latched or self._estop_on_sweep:
            self.estop_latched = True
            raise RigFault("estop")
        p = RUNG_PARAMS[rung]
        eff = self.EFFICIENCY[rung]
        self._residue *= (1.0 - eff) ** p["passes"]
        self.sweeps.append(rung)

    def camera_clean(self) -> bool:
        truly_clean = self._residue <= 0.5
        if self._rng.random() < self._camera_error:
            return not truly_clean  # camera disagrees with reality
        return truly_clean

    def cancel_requested(self) -> bool:
        return self._cancel

    @property
    def true_residue_g(self) -> float:
        """SIMULATOR TRUTH. Not available on real hardware; used only by evals."""
        return self._residue
