"""Laptop side of the USB-serial JSON-lines contract (see IMPLEMENTATION_TASKS.md).

`link` is any object with write(str), readline()->str and optional in_waiting:
  - hardware: pyserial Serial (open_serial_rig below)
  - tests:    InMemoryFirmware in tests/test_serial_rig.py

Fail-closed rules:
  - timeout, fault, disconnect or unexpected reply  -> RigFault (run aborts)
  - camera missing                                  -> camera_clean() is False,
                                                       so the run can never say DONE
  - heartbeat thread pings every 1 s; firmware parks if it goes silent >3 s
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from rig import Reading, RigFault
from rules import RUNG_PARAMS

HEARTBEAT_S = 1.0
MOTION_TIMEOUT_S = 15.0   # longest rung is about 5.5 s; 15 s leaves margin without hiding a hang
REPLY_TIMEOUT_S = 2.0     # park, tare, weigh replies are immediate


class SerialRig:
    def __init__(self, link, camera_clean: Optional[Callable[[], bool]] = None,
                 max_lines: int = 50, heartbeat: bool = False,
                 motion_timeout_s: float = MOTION_TIMEOUT_S) -> None:
        self._link = link
        self._motion_timeout_s = motion_timeout_s
        self._camera = camera_clean
        self._max_lines = max_lines
        self._cancel = False
        self._next_id = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        if heartbeat:
            self._hb_thread = threading.Thread(target=self._beat, daemon=True)
            self._hb_thread.start()

    # --- transport ---------------------------------------------------------
    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self._write({"cmd": "ping"})
            except RigFault:
                return  # link is gone; the next command will raise

    def _write(self, obj: dict) -> None:
        with self._lock:
            try:
                # pyserial takes bytes only; json.loads accepts bytes too, so fakes still work
                self._link.write((json.dumps(obj) + "\n").encode("utf-8"))
            except Exception as exc:
                raise RigFault(f"link: {type(exc).__name__}") from exc

    def _read(self) -> str:
        try:
            return self._link.readline()
        except Exception as exc:
            raise RigFault(f"link: {type(exc).__name__}") from exc

    def _handle_async(self, msg: dict) -> bool:
        """Handle firmware-initiated events. True if the message was consumed."""
        evt = msg.get("evt")
        if evt == "fault":
            raise RigFault(msg.get("code", "fault"))
        if evt == "cancel":
            self._cancel = True
            return True
        return False

    def _wait_for(self, evt: str, timeout_s: Optional[float] = None, **match) -> dict:
        """Wait for one matching event. With timeout_s, keep reading until the deadline.

        pyserial returns '' after its own 2 s read timeout, so a long motion needs a
        deadline, not a single read. Without timeout_s an empty read is a timeout.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        lines = 0
        while lines < self._max_lines:
            line = self._read()
            if not line:
                if deadline is not None and time.monotonic() < deadline:
                    time.sleep(0.02)
                    continue
                raise RigFault("timeout")
            lines += 1
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RigFault("bad_json") from exc
            if self._handle_async(msg):
                continue
            if msg.get("evt") == evt and all(msg.get(k) == v for k, v in match.items()):
                return msg
        raise RigFault("timeout")

    def handshake(self, attempts: int = 8, retry_s: float = 0.5,
                  reply_s: float = REPLY_TIMEOUT_S) -> None:
        """Confirm the firmware is running before any command. Boot takes 1 to 2 s.

        Each try flushes stale bytes, sends park and waits for the reply. Silence in
        one try is normal while the board boots, so tries repeat before giving up.
        """
        last: Optional[RigFault] = None
        for _ in range(attempts):
            flush = getattr(self._link, "reset_input_buffer", None)
            if flush is not None:
                flush()
            try:
                self._write({"cmd": "park"})
                self._wait_for("parked", timeout_s=reply_s)
                return
            except RigFault as exc:
                last = exc
                time.sleep(retry_s)
        raise RigFault(f"no answer from firmware after {attempts} tries ({last})")

    # --- rig API (same shape as FakeRig) -----------------------------------
    def park(self) -> None:
        self._write({"cmd": "park"})
        self._wait_for("parked")

    def tare(self) -> None:
        self._write({"cmd": "tare"})
        self._wait_for("tared")

    def weigh(self) -> Reading:
        self._write({"cmd": "weigh"})
        m = self._wait_for("mass")
        return Reading(grams=float(m["g"]), settled=bool(m.get("settled", False)))

    def sweep(self, rung: str) -> None:
        if self._cancel:
            raise RigFault("cancelled_before_motion")
        p = RUNG_PARAMS[rung]
        mid = self._next_id
        self._next_id += 1
        self._write({"cmd": "sweep", "id": mid, "passes": p["passes"],
                     "speed": p["speed"], "amp": p["area"]})
        self._wait_for("motion_done", timeout_s=self._motion_timeout_s, id=mid)

    def camera_clean(self) -> bool:
        if self._camera is None:
            return False
        try:
            return bool(self._camera())
        except Exception:
            return False  # camera error is never a clean verdict

    def cancel_requested(self) -> bool:
        """Drain any pending firmware lines without blocking, then report cancel."""
        with self._lock:
            pending = getattr(self._link, "in_waiting", 0)
        while pending:
            line = self._read()
            if not line:
                break
            try:
                self._handle_async(json.loads(line))
            except json.JSONDecodeError:
                pass
            with self._lock:
                pending = getattr(self._link, "in_waiting", 0)
        return self._cancel

    def close(self) -> None:
        self._stop.set()
        close = getattr(self._link, "close", None)
        if close:
            close()


def open_serial_rig(port: str, baud: int = 115200, camera_clean=None,
                    handshake_attempts: int = 8) -> SerialRig:
    """Hardware factory. Needs pyserial and the ESP32 flashed with firmware/.

    DTR and RTS are held low before the port opens: on most ESP32 dev boards, asserting
    them resets the chip, which would drop the first commands. The handshake then waits
    for the firmware to answer, so the first run never starts on a board still booting.
    """
    import serial  # imported here so tests run without pyserial hardware

    link = serial.Serial()
    link.port = port
    link.baudrate = baud
    link.timeout = 2.0
    link.dtr = False
    link.rts = False
    link.open()
    rig = SerialRig(link, camera_clean=camera_clean, heartbeat=True)
    try:
        rig.handshake(attempts=handshake_attempts)
    except RigFault:
        rig.close()
        raise
    return rig
