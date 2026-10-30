"""Edge vote from ESP32 B (piezo features) and cautious fusion. ADVISORY ONLY.

Message (B -> laptop, one JSON line):
    {"evt":"edge_vote","cls":"dry|liquid|hard|none","conf":0.0-1.0}

Rules:
  - A bad, missing or unparsable vote is NO vote (never a guess).
  - Fusion can only make a run more careful: the fused mess type's ladder is always a
    subset of the operator's ladder. The vote never adds rungs and never declares clean.
  - With no operator (offline mode) the vote picks the mess type; no usable vote => unknown
    (one gentle rung, then a human).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

MIN_CONF = 0.7  # placeholder until held-out data sets it
CLASSES = {"dry", "liquid", "hard", "none"}
_MAP = {"dry": "dry_debris", "liquid": "liquid", "hard": "unsupported_hazard"}


@dataclass(frozen=True)
class EdgeVote:
    cls: str
    conf: float


def parse_edge_vote(msg: object) -> Optional[EdgeVote]:
    if not isinstance(msg, dict) or msg.get("evt") != "edge_vote":
        return None
    cls, conf = msg.get("cls"), msg.get("conf")
    if cls not in CLASSES:
        return None
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        return None
    return EdgeVote(cls, float(conf))


class EdgeLink:
    """Reads votes from B. `link` has readline() (pyserial or a test double)."""

    def __init__(self, link, max_lines: int = 50) -> None:
        self._link = link
        self._max_lines = max_lines

    def read_vote(self) -> Optional[EdgeVote]:
        """Latest valid vote, or None on silence, garbage or a link error."""
        latest: Optional[EdgeVote] = None
        try:
            for _ in range(self._max_lines):
                line = self._link.readline()
                if not line:
                    break
                try:
                    vote = parse_edge_vote(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if vote is not None:
                    latest = vote
        except Exception:  # noqa: BLE001 - a broken link is "no vote"
            return None
        return latest

    def close(self) -> None:
        close = getattr(self._link, "close", None)
        if close:
            close()


def open_edge_link(port: str, baud: int = 115200) -> EdgeLink:
    import serial  # hardware only

    link = serial.Serial()
    link.port = port
    link.baudrate = baud
    link.timeout = 1.0
    link.dtr = False  # keep the board from resetting when the port opens
    link.rts = False
    link.open()
    return EdgeLink(link)


def fuse(operator: Optional[str], vote: Optional[EdgeVote],
         min_conf: float = MIN_CONF) -> tuple[str, str]:
    """Return (mess_type, note). Never more aggressive than the operator's answer."""
    mapped = _MAP.get(vote.cls) if vote is not None and vote.conf >= min_conf else None
    # cls "none" (no impact seen) and low confidence carry no information.
    if mapped is None:
        if operator is None:
            return "unknown", "offline: no usable edge vote, using unknown"
        return operator, "operator only: edge vote absent, weak or none"
    if operator is None:
        return mapped, "offline: edge vote only"
    if operator == mapped:
        return operator, "operator and edge agree"
    if "unsupported_hazard" in (operator, mapped):
        return "unsupported_hazard", "hazard from operator or edge: human only"
    return "unknown", "operator and edge disagree: most careful runnable type"
