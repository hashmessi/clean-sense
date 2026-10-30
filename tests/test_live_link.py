"""The live link: a rig run in its own process reaches the dashboard through its log, and Cancel
reaches it through a flag file. Token-checked. No hardware, no network."""
import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import ui
from app import AuditLog, Scenario, run
from rig import FakeRig
from runctl import FileCancelRig, LogFollower, cancel_path, clear_cancel, request_cancel
from rules import Context  # noqa: F401  (keeps import order stable for the rig)

ROOT = Path(__file__).resolve().parent.parent
DRY = Scenario("dry_debris_on_glass", "dry_debris", sensitive=False)


def _client(tmp_path, token="T0KEN"):
    st = ui.DashboardState()
    client = TestClient(ui.make_app(st, tmp_path / "runs", tmp_path / "calibration", token_arg=token))
    return st, client


def test_cancel_without_or_with_wrong_token_is_refused_and_writes_nothing(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/cancel").status_code == 403
    assert client.post("/cancel", headers={"x-cancel-token": "nope"}).status_code == 403
    assert not cancel_path(tmp_path / "runs").exists()


def test_cancel_with_token_writes_the_flag_file(tmp_path):
    _, client = _client(tmp_path)
    r = client.post("/cancel", headers={"x-cancel-token": "T0KEN"})
    assert r.status_code == 200
    assert cancel_path(tmp_path / "runs").exists()


def test_token_is_only_on_the_live_page(tmp_path):
    _, client = _client(tmp_path)
    assert 'CANCEL_TOKEN="T0KEN"' in client.get("/").text
    assert "T0KEN" not in client.get("/state").json().__repr__()
    assert "T0KEN" not in client.get("/runs").text


def test_cancel_flag_stops_a_run_before_motion(tmp_path):
    logs = tmp_path / "runs"
    request_cancel(logs)
    log = AuditLog(logs / "x.jsonl")
    res = run(DRY, FileCancelRig(FakeRig(residue_g=10.0, seed=1), logs), log=log)
    assert res.outcome == "cancelled"
    assert not any(e.get("voice") == "allowed" and "rung" in e for e in log.events)


def test_stale_flag_is_cleared_when_a_run_starts(tmp_path):
    logs = tmp_path / "runs"
    request_cancel(logs)
    clear_cancel(logs)
    assert not cancel_path(logs).exists()
    res = run(DRY, FileCancelRig(FakeRig(residue_g=10.0, seed=2), logs), log=AuditLog(logs / "y.jsonl"))
    assert res.outcome == "done"


def test_follower_replays_then_follows_appends(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    path = logs / "live.jsonl"
    log = AuditLog(path)
    run(DRY, FakeRig(residue_g=10.0, seed=3), log=log)
    st = ui.DashboardState()
    f = LogFollower(st, logs)
    f.poll_once()
    assert st.snapshot()["verdict"] == "done"
    assert st.scenario == "live"
    log.write("sensor", start_g=42.0)  # a new event after the first poll
    f.poll_once()
    assert st.snapshot()["mass_start_g"] == 42.0


def test_follower_never_parses_a_half_written_line(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    path = logs / "half.jsonl"
    path.write_text(json.dumps({"voice": "sensor", "start_g": 7.0, "prev": "x", "h": "y"}), encoding="utf-8")
    st = ui.DashboardState()
    f = LogFollower(st, logs)
    assert f.poll_once() == 0  # no newline yet: not consumed
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    assert f.poll_once() == 1
    assert st.snapshot()["mass_start_g"] == 7.0


def test_run_in_another_process_reaches_the_dashboard(tmp_path):
    """The journey that was broken: a CLI-style run in its own process, then the live view."""
    logs = tmp_path / "runs"
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "from app import AuditLog, Scenario, run\n"
        "from rig import FakeRig\n"
        "run(Scenario('dry_debris_on_glass','dry_debris',False), FakeRig(residue_g=10.0, seed=5),"
        " log=AuditLog(Path(%r)/'dry_debris_on_glass.jsonl'))\n" % (str(ROOT), str(logs))
    )
    logs.mkdir()
    subprocess.run([sys.executable, "-c", script], check=True, timeout=120)
    st = ui.DashboardState(simulated=False)  # the live server's mode, not the demo's
    LogFollower(st, logs).poll_once()
    snap = st.snapshot()
    assert snap["verdict"] == "done"
    assert snap["scenario"] == "dry_debris_on_glass"
    assert snap["SIMULATED"] is False  # real-run log, not the demo
