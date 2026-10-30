"""Laptop side of the serial contract, tested against an in-memory firmware."""
import json
from collections import deque

import pytest

from app import Scenario, run
from rig import RigFault
from rules import RUNG_PARAMS
from serial_rig import SerialRig


class InMemoryFirmware:
    """Mimics the ESP32 contract. Replies are queued as the laptop writes."""

    def __init__(self, mass=10.0, fault=None, silent=False, cancel=False):
        self.mass = mass
        self.fault = fault
        self.silent = silent
        self.cancel = cancel
        self.out: deque[str] = deque()
        self.log: list[dict] = []

    def write(self, text: str) -> None:
        msg = json.loads(text)
        self.log.append(msg)
        if self.silent:
            return
        cmd = msg["cmd"]
        if self.fault and cmd == "sweep":
            self.out.append(json.dumps({"evt": "fault", "code": self.fault}))
            return
        if cmd == "park":
            self.out.append(json.dumps({"evt": "parked"}))
        elif cmd == "tare":
            self.out.append(json.dumps({"evt": "tared"}))
        elif cmd == "weigh":
            self.out.append(json.dumps({"evt": "mass", "g": self.mass, "std": 0.04, "settled": True}))
        elif cmd == "sweep":
            self.mass *= (1 - 0.5) ** msg["passes"]
            self.out.append(json.dumps({"evt": "motion_done", "id": msg["id"]}))
        if self.cancel:
            self.out.append(json.dumps({"evt": "cancel"}))
            self.cancel = False

    def readline(self) -> str:
        return self.out.popleft() + "\n" if self.out else ""


def test_weigh_and_tare_round_trip():
    rig = SerialRig(InMemoryFirmware(mass=7.5))
    rig.tare()
    r = rig.weigh()
    assert r.grams == 7.5 and r.settled


def test_sweep_sends_rung_params_and_waits_for_ack():
    fw = InMemoryFirmware()
    SerialRig(fw).sweep("L2")
    cmd = next(m for m in fw.log if m["cmd"] == "sweep")
    assert cmd["passes"] == 3 and cmd["speed"] == "med" and cmd["amp"] == "full"


def test_firmware_fault_raises_rigfault():
    with pytest.raises(RigFault, match="estop"):
        SerialRig(InMemoryFirmware(fault="estop")).sweep("L1")


def test_silent_firmware_times_out_fail_closed():
    with pytest.raises(RigFault, match="timeout"):
        SerialRig(InMemoryFirmware(silent=True)).weigh()


def test_camera_missing_means_never_done():
    # No camera => camera_clean() is False => verifier can never say DONE.
    fw = InMemoryFirmware(mass=0.1)  # scale says clean, but camera unavailable
    res = run(Scenario("dry", "dry_debris", False), SerialRig(fw))
    assert res.outcome != "done"


def test_full_run_over_serial_contract_with_camera():
    fw = InMemoryFirmware(mass=10.0)
    rig = SerialRig(fw, camera_clean=lambda: fw.mass <= 0.5)
    res = run(Scenario("dry", "dry_debris", False), rig)
    assert res.outcome in {"done", "human"}
    if res.outcome == "done":
        assert fw.mass <= 0.5


class PendingLink(InMemoryFirmware):
    """Firmware with unsolicited lines queued (e.g. a cancel press)."""

    @property
    def in_waiting(self):
        return len(self.out)


def test_cancel_press_seen_without_a_pending_command():
    fw = PendingLink()
    fw.out.append(json.dumps({"evt": "cancel"}))
    rig = SerialRig(fw)
    assert rig.cancel_requested() is True


def test_cancel_before_sweep_refuses_motion():
    fw = PendingLink()
    fw.out.append(json.dumps({"evt": "cancel"}))
    rig = SerialRig(fw)
    rig.cancel_requested()
    with pytest.raises(RigFault, match="cancelled_before_motion"):
        rig.sweep("L1")
    assert not any(m["cmd"] == "sweep" for m in fw.log)


class DeadLink:
    def write(self, text):
        raise OSError("cable pulled")

    def readline(self):
        raise OSError("cable pulled")


def test_disconnect_is_a_fault_not_a_crash():
    with pytest.raises(RigFault, match="link"):
        SerialRig(DeadLink()).weigh()


def test_bad_camera_is_never_a_clean_verdict():
    def camera():
        raise RuntimeError("usb glitch")

    assert SerialRig(InMemoryFirmware(), camera_clean=camera).camera_clean() is False
