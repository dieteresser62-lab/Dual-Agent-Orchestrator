"""Build and validate F1–F6 native writer cases using the shared probe contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe


def build(output: Path, capability: str) -> dict:
    if output.exists():
        raise FileExistsError("format case destination already exists")
    output.mkdir(parents=True)
    from native_review_request import build_native_review_request

    cases = {}
    for case in probe.EXPECTED_FORMAT:
        folder = output / case
        source = folder / "repo"
        folder.mkdir()
        probe.materialize_format_repo(case, source)
        bundle = build_native_review_request(probe.spec_for(case), profile=capability)
        (folder / "request.json").write_text(bundle.canonical_json, encoding="utf-8")
        (folder / "writer-schema.json").write_text(bundle.provider_response_schema_json, encoding="utf-8")
        cases[case] = {"request_id": bundle.bound_context.request_id,
                       "request_sha256": probe.sha(bundle.canonical_json.encode()),
                       "writer_sha256": probe.sha(bundle.provider_response_schema_json.encode()),
                       "source_sha256": probe._source_digest(source)}
    probe._write_evidence_file(output / "manifest.json", {"schema_version": "reviewer-format-cases-v1",
                                                     "capability": capability, "cases": cases})
    return cases


def validate(case: str, envelope: Path, *, exit_code: int, stderr: Path | None = None) -> dict:
    if case not in probe.EXPECTED_FORMAT:
        raise ValueError("unknown format case")
    return probe.validate_format_response(case, probe.strict_json(envelope.read_bytes()),
        exit_code=exit_code, stderr=stderr.read_text(errors="replace") if stderr else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    maker = commands.add_parser("build")
    maker.add_argument("--output", type=Path, required=True)
    maker.add_argument("--capability", required=True)
    check = commands.add_parser("validate")
    check.add_argument("case", choices=probe.EXPECTED_FORMAT)
    check.add_argument("--envelope", type=Path, required=True)
    check.add_argument("--exit-code", type=int, required=True)
    check.add_argument("--stderr", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.output, args.capability), sort_keys=True))
        return 0
    result = validate(args.case, args.envelope, exit_code=args.exit_code, stderr=args.stderr)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
