"""Live link between a rig run (its own process) and the dashboard.

- Follow the run log: the dashboard reads the newest runs/*.jsonl as lines are appended.
  Only complete lines are consumed, so a half-written event is never parsed.
- Cancel flag file: POST /cancel writes runs/CANCEL. The run checks it before every sweep,
  the same checkpoint as the hardware cancel button. The run clears a stale flag when it starts.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

CANCEL_NAME = "CANCEL"


def cancel_path(log_dir: Path) -> Path:
    return Path(log_dir) / CANCEL_NAME


def request_cancel(log_dir: Path) -> None:
    p = cancel_path(log_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(time.time()), encoding="utf-8")


def clear_cancel(log_dir: Path) -> None:
    try:
        cancel_path(log_dir).unlink()
    except FileNotFoundError:
        pass


class FileCancelRig:
    """Wraps a rig so a cancel flag file written by the dashboard reaches the run loop."""

    def __init__(self, inner, log_dir: Path) -> None:
        self._inner = inner
        self._path = cancel_path(log_dir)

    def cancel_requested(self) -> bool:
        return self._path.exists() or self._inner.cancel_requested()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def newest_log(log_dir: Path) -> Optional[Path]:
    logs = [p for p in Path(log_dir).glob("*.jsonl") if p.is_file()]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


class LogFollower(threading.Thread):
    """Feeds the newest run log's events to the dashboard state, as they are written."""

    def __init__(self, state, log_dir: Path, poll_s: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.log_dir = Path(log_dir)
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._path: Optional[Path] = None
        self._offset = 0

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self) -> int:
        """Read whatever complete lines were added since the last poll. Returns how many."""
        newest = newest_log(self.log_dir)
        if newest is None:
            return 0
        if newest != self._path:
            self._path, self._offset = newest, 0
            self.state.reset()
            self.state.scenario = newest.stem
            # A demo log (sim_*.jsonl) must never be shown as a real rig run.
            self.state.simulated = newest.name.startswith("sim_")
        size = newest.stat().st_size
        if size < self._offset:  # file was replaced or truncated
            self.state.reset()
            self._offset = 0
        if size == self._offset:
            return 0
        with newest.open("rb") as f:
            f.seek(self._offset)
            chunk = f.read(size - self._offset)
        end = chunk.rfind(b"\n")
        if end == -1:
            return 0  # no complete line yet
        complete = chunk[:end + 1]
        self._offset += len(complete)
        count = 0
        for line in complete.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.state.on_event(event)
            count += 1
        return count

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except OSError:
                pass  # file vanished between listing and reading; try again next tick
            self._stop.wait(self.poll_s)
