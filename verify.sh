#!/usr/bin/env bash
# Runs every check that works without hardware or an API key. Exit non-zero on any failure.
set -euo pipefail
cd "$(dirname "$0")"
OUT="$(mktemp -d)"
echo "== python tests (rules, runs, serial, LLM wiring, camera, phase 4, firmware loopback) =="
python3 -m pytest -q
echo "== firmware A compiles on the host (real sketch, Arduino core stubbed) =="
g++ -std=gnu++17 -Wall -Wextra -Werror -Wno-error=unused-parameter -I firmware/host_arduino/shim -I firmware/host_arduino/third_party -I firmware/cleansense_a \
    firmware/host_arduino/host_main.cpp -o "$OUT/fw_host" -lpthread 2>/dev/null \
  || { echo "host compile of cleansense_a.ino failed"; exit 1; }
echo "  ok"
echo "== firmware safety limits (host build) =="
OUT="$(mktemp -d)"
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/cleansense_a firmware/tests/test_limits.cpp -o "$OUT/test_limits"
"$OUT/test_limits"
echo "== piezo impact features for ESP32 B (host build) =="
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/cleansense_b firmware/tests/test_piezo_features.cpp -o "$OUT/test_piezo"
"$OUT/test_piezo"
echo "== ESP32 B rule baseline: real sketch compiles on the host, rule boundaries (uncalibrated) =="
g++ -std=gnu++17 -Wall -Wextra -Werror -Wno-error=unused-parameter -I firmware/host_arduino/shim -I firmware/cleansense_b \
    firmware/host_arduino/host_main_b.cpp -o "$OUT/fw_b_host" -lpthread
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/cleansense_b firmware/tests/test_piezo_rules.cpp -o "$OUT/test_piezo_rules"
"$OUT/test_piezo_rules"
echo "== gate-1 simulation (SIMULATED) =="
python3 demo.py
echo "== rules-only ablation on identical seeds (SIMULATED) =="
python3 ablation.py
echo "== red-team replay (exit 1 if any hostile plan is accepted) =="
python3 red_team_replay.py
echo "== photo eval without key (expected: BLOCKED, exit 2) =="
set +e
CLEANSENSE_NO_DOTENV=1 GEMINI_API_KEY= OPENROUTER_API_KEY= ANTHROPIC_API_KEY= python3 eval_photos.py --labels /dev/null --out "$OUT/photos.json" >"$OUT/photo.txt" 2>&1
rc=$?
set -e
grep -q "BLOCKED" "$OUT/photo.txt" && [ "$rc" -eq 2 ] && echo "  blocked as expected" || { echo "  unexpected: rc=$rc"; cat "$OUT/photo.txt"; exit 1; }
echo "== verify: ALL CHECKS PASSED (hardware and API gates not included) =="
