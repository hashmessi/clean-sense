"""ESP32 B rule baseline end to end: the host-compiled REAL sketch on a pseudo-terminal,
read by the laptop's real edge code (edge.py). Skipped without g++. No hardware, no key."""
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import edge

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "host_arduino"

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")

# one sharp strike, then quiet; the cycle repeats every 1300 samples
STRIKE = [4048, 3548, 2248] + [2048] * (1300 - 3)
# three separate bursts of 800 counts (not hard: below HARD_PEAK), then quiet
DRY_BURSTS = []
for _ in range(3):
    DRY_BURSTS += [2848, 2448, 2148] + [2048] * 90
DRY_BURSTS += [2048] * (1300 - len(DRY_BURSTS))
QUIET = [2048] * 1300


@pytest.fixture(scope="session")
def fw_b_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("fwb") / "fw_b_host"
    subprocess.run(
        ["g++", "-std=gnu++17", "-Wall", "-Wextra", "-Werror", "-Wno-error=unused-parameter",
         "-I", str(FW / "shim"), "-I", str(ROOT / "firmware" / "cleansense_b"),
         str(FW / "host_main_b.cpp"), "-o", str(out), "-lpthread"],
        check=True,
    )
    return out


class _Laptop:
    """The laptop side of the pty: opens the port like pyserial does and reads JSON lines."""

    def __init__(self, bridge):
        self.fd = os.open(bridge.port, os.O_RDWR | os.O_NOCTTY)
        self._buf = b""

    def send(self, line: str) -> None:
        os.write(self.fd, (line + "\n").encode())

    def lines_for(self, seconds: float) -> list[dict]:
        out = []
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if not r:
                continue
            self._buf += os.read(self.fd, 4096)
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                try:
                    out.append(json.loads(raw))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    out.append({"_raw": raw.decode(errors="replace")})
        return out

    def close(self):
        os.close(self.fd)


@pytest.fixture
def b_link(fw_b_binary):
    from firmware_bridge import FirmwareBridge

    bridge = FirmwareBridge(fw_b_binary)
    time.sleep(0.2)
    laptop = _Laptop(bridge)
    yield bridge, laptop
    laptop.close()
    bridge.close()


def _votes(msgs):
    return [m for m in msgs if isinstance(m, dict) and m.get("evt") == "edge_vote"]


def test_boot_reports_mid_rail(b_link):
    _, laptop = b_link
    boot = [m for m in laptop.lines_for(0.6) if m.get("evt") == "boot"]
    assert boot == [{"evt": "boot", "mid": 2048}]


def test_strike_is_hard_and_fuses_to_human_only(b_link):
    bridge, laptop = b_link
    laptop.lines_for(0.3)  # drain boot
    bridge.control("#adcq " + " ".join(map(str, STRIKE)))
    votes = _votes(laptop.lines_for(1.0))
    assert votes, "a strike must produce an edge_vote"
    assert votes[0]["cls"] == "hard"
    assert votes[0]["conf"] == pytest.approx(0.80)
    vote = edge.parse_edge_vote(votes[0])
    assert vote is not None
    # a confident hard vote never lets a run be more aggressive than the operator
    mess, note = edge.fuse("dry_debris", vote)
    assert mess == "unsupported_hazard"
    assert "human" in note


def test_dry_bursts_never_change_the_plan(b_link):
    bridge, laptop = b_link
    laptop.lines_for(0.3)
    bridge.control("#adcq " + " ".join(map(str, DRY_BURSTS)))
    votes = _votes(laptop.lines_for(1.2))
    assert votes, "bursts must produce an edge_vote"
    assert votes[0]["cls"] == "dry"
    assert votes[0]["conf"] == pytest.approx(0.50)
    vote = edge.parse_edge_vote(votes[0])
    # below MIN_CONF the vote is ignored: the operator's answer stands
    assert edge.fuse("liquid", vote) == ("liquid", "operator only: edge vote absent, weak or none")


def test_silence_gives_none_and_no_change(b_link):
    bridge, laptop = b_link
    laptop.lines_for(0.3)
    bridge.control("#adcq " + " ".join(map(str, QUIET)))
    laptop.lines_for(0.5)
    laptop.send("vote")
    votes = _votes(laptop.lines_for(0.5))
    assert votes and votes[-1]["cls"] == "none" and votes[-1]["conf"] == pytest.approx(0.0)
    assert edge.fuse("dry_debris", edge.parse_edge_vote(votes[-1])) == (
        "dry_debris", "operator only: edge vote absent, weak or none")


def test_vote_command_returns_latest_result(b_link):
    bridge, laptop = b_link
    laptop.lines_for(0.3)
    bridge.control("#adcq " + " ".join(map(str, STRIKE)))
    laptop.lines_for(0.8)
    laptop.send("vote")
    votes = _votes(laptop.lines_for(0.4))
    assert votes and votes[-1]["cls"] == "hard"


def test_only_rule_classes_are_ever_emitted(b_link):
    bridge, laptop = b_link
    laptop.lines_for(0.3)
    bridge.control("#adcq " + " ".join(map(str, STRIKE + DRY_BURSTS + QUIET)))
    votes = _votes(laptop.lines_for(2.0))
    assert votes
    assert {v["cls"] for v in votes} <= {"hard", "dry", "none"}
    assert "liquid" not in {v["cls"] for v in votes}
