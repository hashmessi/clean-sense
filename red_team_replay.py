"""Replay the red-team planner outputs through the validator and print the table.

    python3 red_team_replay.py      # exit 1 if any hostile plan is accepted

Cases live in tests/test_rules.py (RED_TEAM). This script replays them outside
pytest so the result can be shown on the bench or in the README.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

from rules import validate  # noqa: E402
from test_rules import RED_TEAM  # noqa: E402


def main() -> int:
    accepted = []
    print(f"{'case':36} {'expected':9} result")
    for name, raw, ctx, code in RED_TEAM:
        d = validate(raw, ctx)
        blocked_by = next((r.split(":")[0] for r in d.reasons), "-")
        ok = (not d.ok) and any(r.startswith(code) for r in d.reasons)
        if not ok:
            accepted.append(name)
        print(f"{name:36} {code:9} {'BLOCKED by ' + blocked_by if not d.ok else 'ACCEPTED'}")
    print(f"\n{len(RED_TEAM) - len(accepted)}/{len(RED_TEAM)} hostile plans blocked with the expected rule")
    if accepted:
        print("VIOLATION: accepted:", accepted)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
