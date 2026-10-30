"""Integration: the REAL laptop serial code against the host-compiled REAL sketch.

Skipped when g++ is not installed. Only the Arduino core and the Servo class are host
stubs. Timing is real: a sweep takes several seconds, so this file is the slowest test.
"""
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "host_arduino"

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")


@pytest.fixture(scope="session")
def fw_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("fw") / "fw_host"
    subprocess.run(
        ["g++", "-std=gnu++17", "-Wall", "-Wextra", "-Werror",
         "-I", str(FW / "shim"), "-I", str(FW / "third_party"), "-I", str(ROOT / "firmware" / "cleansense_a"),
         str(FW / "host_main.cpp"), "-o", str(out), "-lpthread"],
        check=True,
    )
    return out


@pytest.fixture
def bridge(fw_binary):
    from firmware_bridge import FirmwareBridge

    b = FirmwareBridge(fw_binary)
    time.sleep(0.2)
    yield b
    b.close()


def _rig(bridge, heartbeat=False, motion_timeout_s=None):
    import serial

    from serial_rig import SerialRig

    link = serial.Serial(bridge.port, 115200, timeout=2.0)
    kwargs = {"heartbeat": heartbeat}
    if motion_timeout_s is not None:
        kwargs["motion_timeout_s"] = motion_timeout_s
    return SerialRig(link, **kwargs)


def test_park_tare_weigh_round_trip(bridge):
    rig = _rig(bridge, heartbeat=True)
    try:
        bridge.control("#hx 500000")
        rig.park()
        rig.tare()
        bridge.control("#hx 500100")
        r = rig.weigh()
        assert r.grams == pytest.approx(100.0, abs=0.5)
        assert r.settled is True
    finally:
        rig.close()


def test_bad_json_from_laptop_is_a_fault(bridge):
    from rig import RigFault

    rig = _rig(bridge)
    try:
        rig._link.write(b"{not json\n")
        with pytest.raises(RigFault, match="bad_json"):
            rig.park()
    finally:
        rig.close()


def test_estop_pressed_refuses_motion(bridge):
    from rig import RigFault

    rig = _rig(bridge, heartbeat=True)
    try:
        bridge.control("#pin 21 0")  # e-stop button pulls LOW
        with pytest.raises(RigFault, match="estop"):
            rig.sweep("L1")
    finally:
        rig.close()


def test_sensitive_jumper_refuses_l2(bridge):
    from rig import RigFault

    rig = _rig(bridge, heartbeat=True)
    try:
        bridge.control("#pin 23 0")  # jumper on = sensitive surface
        with pytest.raises(RigFault, match="refused"):
            rig.sweep("L2")
    finally:
        rig.close()


def test_l1_sweep_completes_with_heartbeat(bridge):
    """A real L1 pass takes several seconds. This is the bug the loopback found:
    a 2 s read timeout made the laptop report a timeout during a healthy motion."""
    rig = _rig(bridge, heartbeat=True)
    try:
        t0 = time.monotonic()
        rig.sweep("L1")
        assert 2.0 < time.monotonic() - t0 < 30.0
        rig.park()
    finally:
        rig.close()


def test_cancel_pressed_mid_sweep_stops_motion(bridge):
    from rig import RigFault

    rig = _rig(bridge, heartbeat=True)
    timer = threading.Timer(1.0, lambda: bridge.control("#pin 22 0"))
    try:
        timer.start()
        with pytest.raises(RigFault, match="cancelled"):
            rig.sweep("L2")  # long enough that the press lands mid-motion
    finally:
        timer.cancel()
        rig.close()


def test_silent_laptop_means_firmware_refuses_to_move(bridge):
    """No pings for 3 s: the firmware must refuse the sweep and report a timeout."""
    from rig import RigFault

    rig = _rig(bridge, heartbeat=False)
    try:
        time.sleep(3.5)
        with pytest.raises(RigFault, match="timeout"):
            rig.sweep("L1")
    finally:
        rig.close()


def test_open_serial_rig_handshakes_and_keeps_dtr_low(bridge):
    """The hardware factory path: DTR/RTS low (no reset on open), then a confirmed handshake."""
    from serial_rig import open_serial_rig

    rig = open_serial_rig(bridge.port, handshake_attempts=3)
    try:
        assert rig._link.dtr is False and rig._link.rts is False
        rig.park()
    finally:
        rig.close()


def test_no_firmware_fails_the_handshake_instead_of_hanging():
    """A port with nothing behind it must stop the run with a clear fault, not wait forever."""
    import os

    from rig import RigFault
    from serial_rig import open_serial_rig

    master, slave = os.openpty()
    try:
        import tty

        tty.setraw(slave)
        with pytest.raises(RigFault, match="no answer from firmware"):
            open_serial_rig(os.ttyname(slave), handshake_attempts=2)
    finally:
        os.close(master)
        os.close(slave)
