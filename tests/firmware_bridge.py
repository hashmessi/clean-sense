"""Runs the host-compiled sketch as a process and connects it to a pseudo-terminal.

The laptop's real pyserial code opens the pseudo-terminal's slave side. Bytes written by
the laptop go to the sketch's stdin, and the sketch's stdout goes back to the laptop.
Lines starting with '#' are harness controls (pins and HX711 counts), see
firmware/host_arduino/shim/Arduino.h.
"""
from __future__ import annotations

import os
import subprocess
import threading
import tty
from pathlib import Path


class FirmwareBridge:
    def __init__(self, binary: Path) -> None:
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)  # no echo: anything the laptop writes must not loop back
        self.port = os.ttyname(self.slave)
        self._lock = threading.Lock()
        self._closed = False
        self.proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._fw_to_laptop, daemon=True).start()
        threading.Thread(target=self._laptop_to_fw, daemon=True).start()

    def _fw_to_laptop(self) -> None:
        fd = self.proc.stdout.fileno()
        while not self._closed:
            data = os.read(fd, 4096)
            if not data:
                break
            os.write(self.master, data)

    def _laptop_to_fw(self) -> None:
        while not self._closed:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()

    def control(self, line: str) -> None:
        """Harness control, e.g. control('#pin 21 0') presses the e-stop."""
        with self._lock:
            self.proc.stdin.write((line.strip() + "\n").encode())
            self.proc.stdin.flush()

    def close(self) -> None:
        self._closed = True
        try:
            self.proc.kill()
        finally:
            self.proc.wait(timeout=5)
            for fd in (self.master, self.slave):
                try:
                    os.close(fd)
                except OSError:
                    pass
