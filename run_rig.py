"""Run one cleaning session on the real rig.

    python3 run_rig.py --port /dev/ttyUSB0 --camera 0 --reference calibration/reference.npy \
        --mess dry_debris --surface glass
    add --llm to use the AI planner (needs ANTHROPIC_API_KEY); default is rules-only.
    add --edge-port <port of ESP32 B> to fuse the piezo edge vote (can only make the run more careful);
    add --offline (needs --edge-port) for no network and no LLM: the edge vote picks the mess type.

Camera start frame = the first frame grabbed at the start of the run.
Without --camera, the camera check always says NOT CLEAN, so no run can say DONE.
"""
from __future__ import annotations

import argparse
import os
from typing import Optional
import json
from pathlib import Path

import numpy as np

from app import AuditLog, Scenario, run
from planner import rules_planner
from planner_llm import CircuitBreaker, LLMPlanner, perceive_via_transports, TIMEOUT_S
from perception import forced_human, template_key, guarded_planner
from cases import CASES, PERCEPTION_FRAME, resolve
from announce import ANNOUNCE_WAIT_S, NullSpeaker, announce_for_run, speaker_from_env
from llm_transport import load_env_file
from edge import fuse, open_edge_link
from serial_rig import open_serial_rig


def _save_frame(png: bytes, path: Optional[Path]) -> bool:
    """Keep the frame the look was made from, so the dashboard can show it. Never raises."""
    if path is None:
        return False
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _perceive(camera, llm, log, frame_path: Optional[Path] = None) -> Optional[str]:
    """One colour frame, one provider call. A failed look is logged and means no extra caution."""
    from camera import encode_png

    saved = False
    try:
        png = encode_png(camera.grab_bgr())
        saved = _save_frame(png, frame_path)
        reply = perceive_via_transports(llm.transports, png, timeout_s=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001  any failed look must not stop the run before motion
        log.write("perceived", error=type(exc).__name__, reason=getattr(exc, "reason", "error"), frame=saved)
        return None
    provider = getattr(llm.transports[0], "name", "unknown")
    log.write("perceived", provider=provider, mess_type=reply["mess_type"],
              confidence=reply["confidence"], frame=saved)
    return reply["mess_type"]


class CameraCheck:
    """Camera verdict relative to the first frame of this run (the 'before' image)."""

    def __init__(self, camera, reference: np.ndarray) -> None:
        self._camera = camera
        self._reference = reference
        self._start = None
        self.last = None

    def __call__(self) -> bool:
        from camera import camera_clean

        now = self._camera.grab_gray()
        self.last = now
        if self._start is None:
            self._start = now
        return camera_clean(self._reference, self._start, now)

    def png(self) -> Optional[bytes]:
        """Latest frame as PNG for the planner. None when no frame has been grabbed."""
        import cv2

        last = getattr(self, "last", None)
        if last is None:
            return None
        ok, buf = cv2.imencode(".png", last)
        return buf.tobytes() if ok else None


def main() -> None:
    from camera import Camera

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--reference", default="calibration/reference.npy")
    ap.add_argument("--mess", default=None, choices=["dry_debris", "liquid", "mixed", "unknown"],
                    help="operator's mess type (default dry_debris; none in --offline)")
    ap.add_argument("--edge-port", default=None, help="ESP32 B serial port (piezo edge vote)")
    ap.add_argument("--offline", action="store_true",
                    help="no network, no LLM: mess type comes from the edge vote (plus --mess if given)")
    ap.add_argument("--surface", default=None, choices=["glass", "wood"],
                    help="default glass, or the case's surface")
    ap.add_argument("--case", default=None, choices=sorted(CASES),
                    help="tabletop industry preset: sets the mess type and surface (explicit flags win)")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--log", default="runs")
    args = ap.parse_args()

    if args.offline and args.llm:
        raise SystemExit("--offline and --llm cannot be combined")
    if args.offline and not args.edge_port:
        raise SystemExit("--offline needs --edge-port (the edge vote is the only perception)")

    mess_arg, surface = resolve(args.case, args.mess, args.surface, offline=args.offline)
    operator = mess_arg if mess_arg else (None if args.offline else "dry_debris")
    edge = open_edge_link(args.edge_port) if args.edge_port else None
    vote = edge.read_vote() if edge is not None else None
    if edge is not None:
        edge.close()
    mess, note = fuse(operator, vote)

    sensitive = surface == "wood"
    scenario = Scenario(f"{mess}_on_{surface}", mess, sensitive)

    camera = Camera(args.camera) if args.camera is not None else None
    check = None
    if camera is not None:
        check = CameraCheck(camera, np.load(args.reference))

    from runctl import FileCancelRig, clear_cancel
    clear_cancel(Path(args.log))  # a stale CANCEL from an old run must not stop this one
    rig = FileCancelRig(open_serial_rig(args.port, camera_clean=check), args.log)
    planner = rules_planner
    breaker = None
    if args.llm:
        llm = LLMPlanner.from_env(frame_png=check.png if check else None)
        breaker = CircuitBreaker(llm)
        planner = breaker

    Path(args.log).mkdir(exist_ok=True)
    log = AuditLog(Path(args.log) / f"{scenario.name}.jsonl")
    # First event of each run: what is connected. Provider NAMES only; keys never enter the log.
    log.write("setup", scale=f"ESP32 A on {args.port}",
              camera=None if camera is None else f"camera index {args.camera}",
              edge=None if not args.edge_port else f"ESP32 B on {args.edge_port}",
              case=args.case,
              planner="ai" if args.llm else "rules",
              providers=[t.name for t in llm.transports] if args.llm else [])
    if args.offline or edge is not None:
        log.write("edge", offline=args.offline, operator=operator,
                  vote=None if vote is None else {"cls": vote.cls, "conf": vote.conf},
                  fused=mess, note=note)

    # Camera perception (needs --llm: the provider classifies the frame). The camera can only add caution:
    # a liquid or hazard look that differs from the run's mess type sends this run to a person.
    perceived = _perceive(camera, llm, log, Path(args.log) / PERCEPTION_FRAME) if (camera is not None and args.llm) else None
    forced = forced_human(mess, perceived)
    load_env_file()  # .env for PIPER_VOICE / PIPER_CMD; never overrides real env vars
    speaker, speaker_note = speaker_from_env(os.environ)
    log.write("speaker", spoken=not isinstance(speaker, NullSpeaker), note=speaker_note)
    announce_for_run(template_key(perceived, forced), speaker, log, wait_s=ANNOUNCE_WAIT_S)
    planner = guarded_planner(planner, forced, perceived, mess)
    try:
        result = run(scenario, rig, planner=planner, log=log)
    finally:
        rig.close()
        if camera is not None:
            camera.close()

    if breaker is not None:
        from planner_llm import latency_summary

        (Path(args.log) / "llm_calls.json").write_text(json.dumps(
            {"summary": latency_summary(llm.calls_log), "calls": llm.calls_log}, indent=2))

    banner = "  [AI OFFLINE: rules planner used]" if breaker and breaker.ai_offline else ""
    print(json.dumps({"scenario": result.scenario, "outcome": result.outcome,
                      "attempts": result.attempts, "start_g": result.start_g,
                      "end_g": result.end_g}, indent=2) + banner)


if __name__ == "__main__":
    main()
