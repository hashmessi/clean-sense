"""Phase 4 checks: results table, ablation, dashboard, photo pipeline. No hardware, no key."""
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import AuditLog, Scenario, run  # noqa: E402
from ablation import compare, run_arm  # noqa: E402
from eval_table import table  # noqa: E402
from planner import rules_planner  # noqa: E402
from rig import FakeRig  # noqa: E402

DRY = Scenario("dry_debris_on_glass", "dry_debris", sensitive=False)


# ---------- results table from logs ------------------------------------------
def test_table_is_generated_from_real_logs(tmp_path):
    paths = []
    for seed in range(5):
        p = tmp_path / f"run{seed}.jsonl"
        run(DRY, FakeRig(residue_g=10.0, seed=seed, camera_error=0.0), log=AuditLog(p))
        paths.append(p)
    t = table(paths)
    assert t["runs"] == 5
    assert t["outcomes"] == {"done": 5}
    assert t["done_rate"] == 1.0
    assert t["max_end_residue_g_when_done"] <= 0.5


def test_already_clean_run_is_counted_done_not_human(tmp_path):
    p = tmp_path / "clean.jsonl"
    run(DRY, FakeRig(residue_g=0.0, seed=1), log=AuditLog(p))
    assert table([p])["outcomes"] == {"done": 1}


def test_cancelled_run_is_reported_as_cancelled(tmp_path):
    p = tmp_path / "cancel.jsonl"
    run(DRY, FakeRig(residue_g=10.0, seed=1, cancel_at_countdown=True), log=AuditLog(p))
    assert table([p])["outcomes"] == {"cancelled": 1}


# ---------- ablation -----------------------------------------------------------
def test_ablation_rules_arm_has_no_false_clean_on_sim():
    arm = run_arm(DRY, 10.0, range(8), rules_planner)
    assert arm["n"] == 8
    assert arm["false_clean"] == 0
    assert arm["done_rate"] == 1.0


def test_ablation_compares_identical_inputs_for_each_arm():
    seen = {}

    def recording(name):
        def planner(mess, sens, ctx):
            seen.setdefault(name, []).append((mess, sens))
            return rules_planner(mess, sens, ctx)
        return planner

    out = compare(DRY, 10.0, {"a": recording("a"), "b": recording("b")}, seeds=range(3))
    assert set(out) == {"a", "b"}
    assert seen["a"] == seen["b"]  # same non-LLM inputs to both arms


# ---------- dataset import -----------------------------------------------------
def _folder(tmp_path):
    root = tmp_path / "dataset"
    for cls in ["cardboard", "glass", "__MACOSX"]:
        (root / cls).mkdir(parents=True)
        (root / cls / "a.jpg").write_bytes(b"x")
    return root


def test_dataset_import_skips_hidden_folders_and_leaves_labels_empty(tmp_path):
    from dataset_import import build_manifest, write_manifest

    rows, skipped = build_manifest(_folder(tmp_path))
    assert {r["source_class"] for r in rows} == {"cardboard", "glass"}
    assert all(r["label"] == "" for r in rows)
    out = tmp_path / "m.csv"
    write_manifest(rows, out)
    assert out.read_text().splitlines()[0].split(",")[:2] == ["path", "source_class"]


def test_dataset_import_applies_mapping_only_when_given(tmp_path):
    from dataset_import import build_manifest

    rows, skipped = build_manifest(_folder(tmp_path), mapping={"glass": "liquid"})
    assert {r["source_class"] for r in rows} == {"glass"}
    assert all(r["label"] == "liquid" for r in rows)
    assert skipped == ["cardboard"]  # unmapped classes are reported, never guessed


# ---------- photo eval ----------------------------------------------------------
def _rows(tmp_path, n, label="dry_debris"):
    rows = []
    for i in range(n):
        p = tmp_path / f"p{i}.png"
        p.write_bytes(b"png")
        rows.append({"path": str(p), "label": label})
    return rows


def test_score_refuses_fewer_than_25_labels(tmp_path):
    from eval_photos import score

    with pytest.raises(ValueError):
        score(_rows(tmp_path, 24), lambda png: {"mess_type": "dry_debris"})


def test_score_counts_errors_as_wrong_and_gates(tmp_path):
    from eval_photos import score
    from planner_llm import PlannerError

    rows = _rows(tmp_path, 25)
    calls = {"n": 0}

    def flaky(png):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PlannerError("bad json")
        return {"mess_type": "dry_debris"}

    res = score(rows, flaky)
    assert res["n"] == 25 and res["errors"] == 3
    assert res["accuracy"] == round(22 / 25, 3)
    assert res["gate_passed"] is False  # 0.88 < 0.90


def test_eval_photos_without_key_is_blocked(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    labels = tmp_path / "labels.csv"
    labels.write_text("path,label\n")
    res = subprocess.run([sys.executable, str(ROOT / "eval_photos.py"),
                          "--labels", str(labels), "--out", str(tmp_path / "o.json")],
                         env=env, capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 2
    assert "BLOCKED: no LLM provider key is set" in res.stdout
    assert not (tmp_path / "o.json").exists()


def _fake_client(text):
    block = types.SimpleNamespace(type="text", text=text)
    msg = types.SimpleNamespace(content=[block])
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: msg))


def test_perceive_photo_accepts_valid_reply():
    from planner_llm import perceive_photo

    out = perceive_photo(_fake_client('{"mess_type":"liquid","confidence":0.8}'), b"png")
    assert out == {"mess_type": "liquid", "confidence": 0.8}


def test_perceive_photo_rejects_out_of_enum_mess_type():
    from planner_llm import PlannerError, perceive_photo

    with pytest.raises(PlannerError):
        perceive_photo(_fake_client('{"mess_type":"glass","confidence":0.9}'), b"png")


def test_perceive_photo_rejects_bad_confidence():
    from planner_llm import PlannerError, perceive_photo

    with pytest.raises(PlannerError):
        perceive_photo(_fake_client('{"mess_type":"liquid","confidence":1.5}'), b"png")


def test_perceive_photo_without_client_is_unavailable():
    from planner_llm import PlannerUnavailable, perceive_photo

    with pytest.raises(PlannerUnavailable):
        perceive_photo(None, b"png")


# ---------- dashboard -----------------------------------------------------------
def test_dashboard_reflects_audit_events_and_cancel(tmp_path):
    import re

    from fastapi.testclient import TestClient

    import ui

    st = ui.DashboardState()
    client = TestClient(ui.make_app(st, tmp_path / "runs", tmp_path / "calibration"))
    run(DRY, ui.CancelableRig(FakeRig(residue_g=10.0, seed=2), st.cancel_event),
        log=AuditLog(listener=st.on_event))
    s = client.get("/state").json()
    assert s["SIMULATED"] is True
    assert s["verdict"] == "done"
    assert s["mass_start_g"] > 9
    token = re.search(r'CANCEL_TOKEN=("[^"]+")', client.get("/").text).group(1).strip('"')
    assert client.post("/cancel").status_code == 403  # no token: refused
    assert client.post("/cancel", headers={"x-cancel-token": token}).json() == {"cancel_requested": True}
    assert client.get("/state").json()["cancel_requested"] is True
    assert client.get("/").status_code == 200


def test_dashboard_cancel_reaches_run_loop():
    import ui

    st = ui.DashboardState()
    st.cancel_event.set()
    res = run(DRY, ui.CancelableRig(FakeRig(residue_g=10.0, seed=3), st.cancel_event))
    assert res.outcome == "cancelled"
