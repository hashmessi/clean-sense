"""T4 hash chain, T14 frame to planner, T19 call log, T21 judge comparison. No hardware, no key."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ablation import detector_table  # noqa: E402
from app import AuditLog, Scenario, run, verify_log  # noqa: E402
from planner_llm import LLMPlanner, latency_summary  # noqa: E402
from rig import FakeRig  # noqa: E402
from rules import Context  # noqa: E402

DRY = Scenario("dry", "dry_debris", sensitive=False)


# ---------- T4: hash chain -----------------------------------------------------
def _log(tmp_path, seed=1):
    p = tmp_path / "log.jsonl"
    run(DRY, FakeRig(residue_g=10.0, seed=seed), log=AuditLog(p))
    return p


def test_fresh_log_verifies(tmp_path):
    assert verify_log(_log(tmp_path)) == (True, None)


def test_editing_one_line_is_detected(tmp_path):
    p = _log(tmp_path)
    lines = p.read_text().splitlines()
    ev = json.loads(lines[2])
    ev["voice"] = "allowed" if ev["voice"] != "allowed" else "sensor"
    lines[2] = json.dumps(ev)
    p.write_text("\n".join(lines) + "\n")
    ok, bad = verify_log(p)
    assert ok is False and bad == 3


def test_deleting_a_line_is_detected(tmp_path):
    p = _log(tmp_path)
    lines = p.read_text().splitlines()
    del lines[1]
    p.write_text("\n".join(lines) + "\n")
    assert verify_log(p)[0] is False


def test_reordering_lines_is_detected(tmp_path):
    p = _log(tmp_path)
    lines = p.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    p.write_text("\n".join(lines) + "\n")
    assert verify_log(p)[0] is False


def test_appending_runs_to_one_file_keeps_one_chain(tmp_path):
    p = tmp_path / "multi.jsonl"
    for seed in range(3):
        run(DRY, FakeRig(residue_g=10.0, seed=seed), log=AuditLog(p))  # new AuditLog each run
    assert verify_log(p) == (True, None)


def test_tampered_tail_is_detected_even_with_valid_prefix(tmp_path):
    p = _log(tmp_path)
    lines = p.read_text().splitlines()
    ev = json.loads(lines[-1])
    ev["end_g"] = 0.0
    lines[-1] = json.dumps(ev)
    p.write_text("\n".join(lines) + "\n")
    assert verify_log(p)[0] is False


# ---------- T14: camera frame reaches the planner ----------------------------
def _client(text='{"mess_type":"dry_debris","confidence":0.9}'):
    seen = {}
    block = types.SimpleNamespace(type="text", text=text)
    msg = types.SimpleNamespace(content=[block])

    def create(**kw):
        seen["kw"] = kw
        return msg

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create)), seen


def test_frame_is_sent_as_image_block_when_available():
    client, seen = _client('{"action":"human","rung":null,"mess_type":"dry_debris","sensitive_surface":false,"reason":"x"}')
    planner = LLMPlanner(client=client, frame_png=lambda: b"\x89PNG-fake")
    planner("dry_debris", False, Context())
    content = seen["kw"]["messages"][0]["content"]
    assert isinstance(content, list) and content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["type"] == "text"


def test_no_frame_means_text_only():
    client, seen = _client('{"action":"human","rung":null,"mess_type":"dry_debris","sensitive_surface":false,"reason":"x"}')
    planner = LLMPlanner(client=client, frame_png=lambda: None)
    planner("dry_debris", False, Context())
    assert isinstance(seen["kw"]["messages"][0]["content"], str)


# ---------- T19: latency and token log ---------------------------------------
def test_call_log_records_latency_tokens_and_errors():
    usage = types.SimpleNamespace(input_tokens=120, output_tokens=40)
    ok_msg = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text='{"action":"human","rung":null,"mess_type":"liquid","sensitive_surface":false,"reason":"x"}')],
        usage=usage)
    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: ok_msg))
    planner = LLMPlanner(client=client)
    planner("liquid", False, Context())
    entry = planner.calls_log[-1]
    assert entry["ok"] and entry["in_tok"] == 120 and entry["out_tok"] == 40 and entry["ms"] >= 0

    def boom(**kw):
        raise TimeoutError("slow")

    bad = LLMPlanner(client=types.SimpleNamespace(messages=types.SimpleNamespace(create=boom)))
    with pytest.raises(Exception):
        bad("liquid", False, Context())
    assert bad.calls_log[-1]["ok"] is False


def test_latency_summary_nearest_rank():
    entries = [{"ms": float(m), "ok": True, "in_tok": 1, "out_tok": 2} for m in range(1, 21)]
    entries.append({"ms": 0.0, "ok": False, "in_tok": 0, "out_tok": 0})
    s = latency_summary(entries)
    assert s["p50_ms"] == 10.0 and s["p95_ms"] == 19.0
    assert s["calls"] == 21 and s["errors"] == 1 and s["in_tokens"] == 20 and s["out_tokens"] == 40


def test_latency_summary_empty():
    s = latency_summary([])
    assert s["p50_ms"] is None and s["p95_ms"] is None and s["calls"] == 0


# ---------- T21: judges compared on the same simulated runs -------------------
HUMAN = lambda m, sens, ctx: {"mess_type": m, "action": "human", "sensitive_surface": sens, "reason": "x"}


def test_camera_only_false_cleans_while_both_agree_never_does():
    # run stops at once (human), residue 5 g stays on the tile, camera often lies
    out = detector_table(DRY, residue=5.0, seeds=range(60), planner=HUMAN, camera_error=0.6)
    fc = out["false_clean"]
    assert fc["both_agree"] == 0
    assert fc["camera_only"] > 0
    assert fc["scale_only"] == 0  # the scale reads 5 g, so it never says clean here
