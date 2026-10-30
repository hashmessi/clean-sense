"""Dashboard pages: run history, offline tier, results, calibration. Read-only over logs. No hardware."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import ui  # noqa: E402
from app import AuditLog, Scenario, run  # noqa: E402
from edge import EdgeVote, fuse  # noqa: E402
from rig import FakeRig  # noqa: E402

DRY = Scenario("dry_debris_on_glass", "dry_debris", sensitive=False)
PAGES = ["/", "/runs", "/offline", "/results", "/calibration"]


def _client(tmp_path, logs=None, calib=None):
    st = ui.DashboardState()
    return TestClient(ui.make_app(st, logs or tmp_path / "runs", calib or tmp_path / "calibration"))


def _write_run(path, seed=1, residue=10.0, prefix_edge=None):
    log = AuditLog(path)
    if prefix_edge is not None:
        mess, note = fuse(*prefix_edge)
        log.write("edge", offline=True, operator=prefix_edge[0],
                  vote=None if prefix_edge[1] is None else {"cls": prefix_edge[1].cls, "conf": prefix_edge[1].conf},
                  fused=mess, note=note)
    run(DRY, FakeRig(residue_g=residue, seed=seed, camera_error=0.0), log=log)


def test_every_page_renders_with_no_logs(tmp_path):
    c = _client(tmp_path)
    for p in PAGES:
        r = c.get(p)
        assert r.status_code == 200, p
        assert "CleanSense" in r.text
        assert 'aria-current="page"' in r.text  # nav marks the current page
    assert "No runs logged yet" in c.get("/runs").text
    assert "No runs to summarise" in c.get("/results").text
    assert "No edge votes logged yet" in c.get("/offline").text
    assert "Not calibrated yet" in c.get("/calibration").text


def test_runs_page_lists_each_run_with_source_and_chain_status(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    _write_run(logs / "sim_test.jsonl", seed=1)
    _write_run(logs / "sim_test.jsonl", seed=2)  # same file: two runs, one chain
    text = _client(tmp_path, logs=logs).get("/runs").text
    assert text.count("<tr><td class='num'>") == 2
    assert "SIMULATED" in text
    assert "hash chain intact" in text
    assert ">Clean<" in text


def test_broken_chain_is_reported(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    p = logs / "rig_session.jsonl"
    _write_run(p, seed=3)
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["t"] = rec["t"] + 1  # edit a record after the fact
    lines[1] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert "chain broken at line" in _client(tmp_path, logs=logs).get("/runs").text


def test_offline_page_shows_edge_votes_and_fused_type(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    _write_run(logs / "sim_offline.jsonl", seed=4, prefix_edge=(None, EdgeVote("dry", 0.80)))
    _write_run(logs / "sim_offline.jsonl", seed=5, prefix_edge=("dry_debris", EdgeVote("hard", 0.80)))
    text = _client(tmp_path, logs=logs).get("/offline").text
    assert "dry 0.80" in text and "hard 0.80" in text
    assert "dry_debris" in text
    assert "unsupported_hazard" in text  # hard vote fuses to no motion
    assert "No accuracy is claimed" in text


def test_results_page_is_generated_from_logs(tmp_path):
    from eval_table import table

    logs = tmp_path / "runs"
    logs.mkdir()
    for s in range(3):
        _write_run(logs / f"sim_{s}.jsonl", seed=s)
    text = _client(tmp_path, logs=logs).get("/results").text
    t = table(sorted(logs.glob("*.jsonl")))
    assert f"{t['runs']}" in text
    assert "Runs in the logs" in text
    assert "ablation.py" in text  # false-clean is not claimed on this page


def test_calibration_page_reads_calibration_file_and_flags_placeholder(tmp_path):
    calib = tmp_path / "calibration"
    calib.mkdir()
    (calib / "calibration.json").write_text(json.dumps({"n": 20, "mean_g": 0.12, "std_g": 0.09, "span_g": 0.3}))
    text = _client(tmp_path, calib=calib).get("/calibration").text
    assert "0.12" in text and "0.09" in text
    assert "Passes" in text
    assert "Not captured" in text  # no reference.npy yet
    assert "Placeholder" in text  # CAL_FACTOR is still 1.0 in firmware


def test_log_text_is_escaped(tmp_path):
    logs = tmp_path / "runs"
    logs.mkdir()
    p = logs / "rig_x.jsonl"
    log = AuditLog(p)
    log.write("edge", offline=False, operator="<script>alert(1)</script>", vote=None,
              fused="dry_debris", note="x")
    run(DRY, FakeRig(residue_g=0.0, seed=1), log=log)
    for page in ("/offline",):
        text = _client(tmp_path, logs=logs).get(page).text
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text


def test_demo_loop_writes_simulated_logs_with_all_four_offline_paths(tmp_path):
    import threading

    st = ui.DashboardState()
    stop = threading.Event()
    logs = tmp_path / "runs"
    th = threading.Thread(target=ui.demo_loop, args=(st, stop, logs, 0.0), daemon=True)
    th.start()
    while len(list(logs.glob("sim_*.jsonl"))) < 1 or sum(
            1 for p in logs.glob("sim_*.jsonl") for _ in p.read_text().splitlines()) < 40:
        th.join(0.05)
        if not th.is_alive():
            break
    stop.set()
    th.join(5)
    rows = ui.run_rows(logs)
    assert len(rows) >= 4
    assert all(r["simulated"] for r in rows)
    votes = [r["edge"]["vote"] for r in rows if r["edge"] and r["edge"]["vote"]]
    assert {v["cls"] for v in votes} >= {"dry", "hard"}


def test_piezo_constants_on_pages_match_firmware_header():
    header = (ROOT / "firmware" / "cleansense_b" / "piezo_rules.h").read_text(encoding="utf-8")
    peak = int(re.search(r"HARD_PEAK\s*=\s*(\d+)", header).group(1))
    decay = int(re.search(r"HARD_DECAY_MS\s*=\s*(\d+)", header).group(1))
    bursts = int(re.search(r"DRY_MIN_BURSTS\s*=\s*(\d+)", header).group(1))
    assert (ui.PIEZO_HARD_PEAK, ui.PIEZO_HARD_DECAY_MS, ui.PIEZO_DRY_BURSTS) == (peak, decay, bursts)


def test_state_reports_simulated_flag(tmp_path):
    c = _client(tmp_path)
    assert c.get("/state").json()["SIMULATED"] is True
