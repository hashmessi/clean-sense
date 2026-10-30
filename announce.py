"""Spoken announcements for a run. Fixed sentences only: model output never reaches the speaker.

Announce-only. Speech cannot change the plan, the rules, motion, the e-stop or cancel. Errors are
recorded and logged by the caller; they never raise into the run loop.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Mapping, Optional, Tuple

# The run waits this long for speech to finish before any motion, so people hear it first.
ANNOUNCE_WAIT_S = 10.0

# Every sentence says what the robot will try, which is true for each runnable class.
# "human" keys say a person will do the work. No placeholders: the text is fixed.
TEMPLATES: dict[str, str] = {
    "liquid_try": "Liquid spill detected. Keep people back. The robot will try a gentle clean.",
    "liquid_human": "Liquid spill detected. Keep people back. A person will clean it.",
    "dry_debris_try": "Dry debris detected. Keep people back. The robot will try a gentle clean.",
    "mixed_try": "Mixed mess detected. Keep people back. The robot will try a gentle clean.",
    "unknown_try": "Unknown mess detected. Keep people back. The robot will try a gentle clean.",
    "hazard_human": "Hazard detected. Keep people back. A person will handle it.",
}


class NullSpeaker:
    """Default: nothing is spoken."""

    def speak(self, text: str) -> None:
        return None


class PiperSpeaker:
    """Piper TTS through its command-line binary: text on stdin, a wav file out, then played."""

    def __init__(self, cmd: str, voice: str) -> None:
        self._cmd = cmd
        self._voice = voice

    def speak(self, text: str) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="cs_speech_"))
        wav = tmp / "say.wav"
        try:
            subprocess.run([self._cmd, "--model", self._voice, "--output_file", str(wav)],
                           input=text.encode("utf-8"), check=True, capture_output=True, timeout=30)
            _play(wav)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _play(wav: Path) -> None:
    if sys.platform.startswith("win"):
        import winsound  # stdlib on Windows; blocks until the sound has played

        winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        return
    player = shutil.which("aplay")
    if player is None:
        raise RuntimeError("no audio player found (aplay)")
    subprocess.run([player, "-q", str(wav)], check=True, timeout=30)


def speaker_from_env(env: Mapping[str, str]) -> Tuple[object, str]:
    """Pick the speaker from PIPER_VOICE / PIPER_CMD. Returns (speaker, note). Falls back to silence."""
    voice = (env.get("PIPER_VOICE") or "").strip()
    cmd = (env.get("PIPER_CMD") or "piper").strip() or "piper"
    if not voice:
        return NullSpeaker(), "no voice: set PIPER_VOICE in .env to speak"
    if not Path(voice).is_file():
        return NullSpeaker(), "voice file not found: check PIPER_VOICE"
    if shutil.which(cmd) is None:
        return NullSpeaker(), f"{cmd} not found on PATH: check PIPER_CMD"
    return PiperSpeaker(cmd, voice), "piper"


class Announcer:
    """Speaks one fixed template on a background thread. Records, never raises, a speaker error."""

    def __init__(self, speaker) -> None:
        self._speaker = speaker
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None

    def announce(self, key: str) -> None:
        if key not in TEMPLATES:
            raise KeyError(key)  # a programming error: fail fast, not at speech time
        text = TEMPLATES[key]

        def _speak() -> None:
            try:
                self._speaker.speak(text)
            except Exception as exc:  # noqa: BLE001  speech failure must not stop the run
                self.error = type(exc).__name__

        self._thread = threading.Thread(target=_speak, name="announce", daemon=True)
        self._thread.start()

    def wait(self, timeout: float = ANNOUNCE_WAIT_S) -> bool:
        """True when speech has finished (or nothing was started). False on timeout."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()


def announce_for_run(key: Optional[str], speaker, log, wait_s: float = ANNOUNCE_WAIT_S) -> None:
    """One announcement per run. Logs the template KEY only, never the sentence."""
    if key is None:
        return
    log.write("announced", key=key, spoken=not isinstance(speaker, NullSpeaker))
    announcer = Announcer(speaker)
    announcer.announce(key)
    if not announcer.wait(wait_s):
        log.write("announce_failed", reason="timeout")
    elif announcer.error is not None:
        log.write("announce_failed", reason=announcer.error)
