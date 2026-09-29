"""Render the reusable provider-neutral Phase-0 P1–P6 catalog and named profiles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.qualification.phase0_catalog import CASES
from scripts.qualification.profiles import PROTECTION_PROFILES


def matrix() -> dict:
    return {"schema_version": "reviewer-phase0-matrix-v1",
            "cases": {name: {"title": case.title, "attempts": list(case.attempts),
                             "forbidden_paths": list(case.forbidden_paths),
                             "forbidden_words": list(case.forbidden_words),
                             "soft_denial": case.soft_denial}
                      for name, case in CASES.items()},
            "profiles": {name: {"capability": profile.capability,
                                "cli_flags": list(profile.cli_flags),
                                "deny_rules": list(profile.deny_rules),
                                "settings_mode": profile.settings_mode,
                                "needs_isolated_home": profile.needs_isolated_home,
                                "soft_denial_without_result": profile.soft_denial_without_result,
                                "environment": dict(profile.environment)}
                         for name, profile in PROTECTION_PROFILES.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(matrix(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
