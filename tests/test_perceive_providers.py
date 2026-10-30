"""Photo classification through the provider layer, and the eval's image handling. No network."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from eval_photos import as_png, score
from llm_transport import GeminiTransport, Timeout
from planner_llm import PlannerError, PlannerUnavailable, perceive_via_transports

PNG = b"\x89PNG\r\n\x1a\nfake"


class Script:
    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(json.loads(req.data))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def gem(text):
    return (200, {}, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def transport(*items):
    s = Script(*items)
    return GeminiTransport(api_key="k", model="m", opener=s), s


def test_valid_classification_returns_mess_and_confidence():
    t, s = transport(gem('{"mess_type":"liquid","confidence":0.8}'))
    out = perceive_via_transports([t], PNG)
    assert out == {"mess_type": "liquid", "confidence": 0.8}
    assert s.requests[0]["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/png"


def test_out_of_enum_is_an_error_not_coerced():
    t, _ = transport(gem('{"mess_type":"bogus","confidence":0.8}'))
    with pytest.raises(PlannerError):
        perceive_via_transports([t], PNG)


@pytest.mark.parametrize("conf", ["1.5", "-0.1", '"high"'])
def test_confidence_must_be_a_number_from_0_to_1(conf):
    t, _ = transport(gem('{"mess_type":"dry_debris","confidence":%s}' % conf))
    with pytest.raises(PlannerError):
        perceive_via_transports([t], PNG)


def test_extra_keys_are_refused():
    t, _ = transport(gem('{"mess_type":"dry_debris","confidence":0.5,"note":"x"}'))
    with pytest.raises(PlannerError):
        perceive_via_transports([t], PNG)


def test_no_provider_is_unavailable_not_a_guess():
    with pytest.raises(PlannerUnavailable) as info:
        perceive_via_transports([], PNG)
    assert info.value.reason == "not_configured"


def test_request_failure_is_unavailable_with_reason_and_no_fallback():
    primary, _ = transport(Timeout("slow"))
    backup, backup_script = transport(gem('{"mess_type":"dry_debris","confidence":0.9}'))
    with pytest.raises(PlannerUnavailable) as info:
        perceive_via_transports([primary, backup], PNG)
    assert info.value.reason == "timeout"
    assert backup_script.requests == []  # one provider per measurement: never mix


def test_as_png_converts_jpeg_and_keeps_png(tmp_path):
    img = np.full((48, 64, 3), 128, np.uint8)
    jpg = tmp_path / "a.jpg"
    cv2.imwrite(str(jpg), img)
    out = as_png(jpg)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    png = tmp_path / "b.png"
    png.write_bytes(PNG)
    assert as_png(png) == PNG


def test_as_png_rejects_unreadable_file(tmp_path):
    bad = tmp_path / "c.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        as_png(bad)


def test_score_resolves_relative_paths_under_root(tmp_path):
    seen = []
    rows = [{"path": f"img{i}.png", "label": "dry_debris"} for i in range(25)]

    def load(p):
        seen.append(p)
        return PNG

    res = score(rows, lambda png: {"mess_type": "dry_debris"}, load=load, root=tmp_path)
    assert all(p.parent == tmp_path for p in seen)
    assert res["accuracy"] == 1.0 and res["gate_passed"] is True


def test_unreadable_image_counts_as_wrong_and_does_not_abort(tmp_path):
    rows = [{"path": f"ok{i}.png", "label": "dry_debris"} for i in range(24)]
    rows.append({"path": "broken.png", "label": "liquid"})

    def load(p):
        if p.name == "broken.png":
            raise ValueError("unreadable image")
        return PNG

    res = score(rows, lambda png: {"mess_type": "dry_debris"}, load=load, root=tmp_path)
    assert res["n"] == 25 and res["errors"] == 1
    assert res["accuracy"] == round(24 / 25, 3)
