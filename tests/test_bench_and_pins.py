"""Bench checks run against the simulator, and the pin map must agree across firmware, docs and tools."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bench_check  # noqa: E402
from rig import FakeRig, RigFault  # noqa: E402


# ---------- bench checks (simulator) -----------------------------------------
def test_link_and_scale_pass_on_clean_empty_tile():
    res = bench_check.run_steps(FakeRig(residue_g=0.0, seed=1), ["link", "scale"])
    assert [r["pass"] for r in res] == [True, True]


def test_scale_fails_when_tile_is_not_empty():
    res = bench_check.run_steps(FakeRig(residue_g=8.0, seed=1), ["scale"])
    assert res[0]["pass"] is False and "should be empty" in res[0]["detail"]


def test_scale_can_be_run_with_something_on_the_tile():
    res = bench_check.run_steps(FakeRig(residue_g=8.0, seed=1), ["scale"], expect_empty=False)
    assert res[0]["pass"] is True


def test_sweep_refused_without_confirmation_and_rig_untouched():
    rig = FakeRig(residue_g=5.0, seed=1)
    res = bench_check.run_steps(rig, ["sweep"], confirmed=False)
    assert res[0]["pass"] is False and rig.sweeps == []


def test_sweep_with_confirmation_runs_one_gentle_pass():
    rig = FakeRig(residue_g=5.0, seed=1)
    res = bench_check.run_steps(rig, ["sweep"], confirmed=True)
    assert res[0]["pass"] is True and rig.sweeps == ["L1"]


class Broken(FakeRig):
    def park(self):
        raise RigFault("timeout")


def test_link_fault_stops_and_reports():
    res = bench_check.run_steps(Broken(), ["link", "scale"])
    assert len(res) == 1 and res[0]["pass"] is False and "timeout" in res[0]["detail"]


def test_steps_stop_at_first_failure():
    res = bench_check.run_steps(FakeRig(residue_g=8.0, seed=1), ["link", "scale", "sweep"], confirmed=True)
    assert [r["step"] for r in res] == ["link", "scale"]


# ---------- pin map: firmware, docs and tools must agree ----------------------
FIRMWARE_A = ROOT / "firmware" / "cleansense_a" / "cleansense_a.ino"
INTEGRATION = ROOT / "INTEGRATION.md"

# ESP32 WROOM-class rules for this build. Flash pins 6-11 must never be used.
# 0, 2, 12, 15 are strapping pins. 1 and 3 are the USB serial. 34-39 are input-only.
FORBIDDEN_OUT = set(range(6, 12)) | {0, 1, 2, 3, 12, 15}
ADC1_PINS = set(range(32, 40))


def _firmware_pins() -> dict[str, int]:
    src = FIRMWARE_A.read_text(encoding="utf-8")
    pins = {}
    for line in src.splitlines():
        if line.startswith("constexpr int PIN_"):
            for name, num in re.findall(r"(PIN_[A-Z_]+)\s*=\s*(\d+)", line):
                pins[name] = int(num)
    return pins


EXPECTED_A = {"PIN_HX_DT": 16, "PIN_HX_SCK": 4, "PIN_SERVO": 13,
              "PIN_ESTOP": 21, "PIN_CANCEL": 22, "PIN_SENSITIVE": 23}


def test_firmware_pins_match_the_agreed_map():
    assert _firmware_pins() == EXPECTED_A


def test_no_pin_is_used_twice_and_none_is_forbidden():
    pins = _firmware_pins()
    assert len(set(pins.values())) == len(pins)
    assert not (set(pins.values()) & FORBIDDEN_OUT)


def test_integration_doc_lists_every_firmware_pin():
    text = INTEGRATION.read_text(encoding="utf-8")
    for name, num in EXPECTED_A.items():
        assert re.search(rf"`{name}`\s*\|\s*[AB]\s*\|\s*{num}\s*\|", text), f"{name}={num} missing in INTEGRATION.md"


def test_piezo_pin_for_esp32_b_is_an_adc1_pin():
    text = INTEGRATION.read_text(encoding="utf-8")
    m = re.search(r"`PIEZO_ADC`\s*\|\s*B\s*\|\s*(\d+)\s*\|", text)
    assert m, "PIEZO_ADC row missing in INTEGRATION.md"
    pin = int(m.group(1))
    assert pin in ADC1_PINS and pin not in FORBIDDEN_OUT


def test_platformio_build_pins_match_the_known_good_bench_toolchain():
    """firmware/cleansense_a/platformio.ini mirrors CleanSense_Servo_Handoff (the sketch known to build)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    ini = (root / "firmware" / "cleansense_a" / "platformio.ini").read_text(encoding="utf-8")
    assert "platform = espressif32 @ 8.0.0" in ini and "ESP32Servo @ 3.2.1" in ini and "ArduinoJson" in ini
    assert "src_dir = ." in ini and "board = esp32dev" in ini
    handoff = root / "CleanSense_Servo_Handoff" / "platformio.ini"
    if handoff.exists():  # the bench folder travels with the repo; same pins, same toolchain
        text = handoff.read_text(encoding="utf-8")
        assert "espressif32 @ 8.0.0" in text and "ESP32Servo @ 3.2.1" in text


def test_run_firmware_uses_the_bench_validated_servo_pin_and_the_bench_sketch_is_not_the_run_firmware():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    ino = (root / "firmware" / "cleansense_a" / "cleansense_a.ino").read_text(encoding="utf-8")
    assert "PIN_SERVO = 13" in ino  # same GPIO as CleanSense_Servo_Handoff, so no wire moves between bench and run
    bench = root / "CleanSense_Servo_Handoff" / "test_servo_wired.ino"
    if bench.exists():
        text = bench.read_text(encoding="utf-8")
        assert "#define PIN_SERVO_SIG             13" in text  # the validated wiring
        assert '"cmd"' not in text  # no JSON protocol: run_rig.py cannot talk to it
