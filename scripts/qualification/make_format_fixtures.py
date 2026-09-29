"""Create six provider-neutral repository fixtures for Phase-0 writer checks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("format fixture destination already exists")
    args.output.mkdir(parents=True)
    paths = {}
    for case in probe.EXPECTED_FORMAT:
        destination = args.output / case
        probe.materialize_format_repo(case, destination)
        paths[case] = probe._source_digest(destination)
    print(json.dumps(paths, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
