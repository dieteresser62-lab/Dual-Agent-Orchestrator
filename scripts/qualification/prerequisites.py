"""Record completed Phase-0 probes without changing the frozen manifest.

Example: python3 scripts/qualification/prerequisites.py --protocol PROTOCOL
  --protection P1/result.json ... --format F1/report.json ... --out RESULTS
Supply each --protection and --format argument once per case.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe


def _manifest(protocol: dict) -> dict:
    probe.validate_qualification(protocol)
    path = probe.qualification_pair(protocol).evidence_directory / "phase-0-v1.json"
    content = path.read_bytes()
    if probe.sha(content) != protocol["phase0_sha256"]:
        raise ValueError("Phase-0 prerequisite manifest digest differs from protocol")
    return probe.strict_json(content)


def _reports(paths: list[Path], manifest: dict, kind: str) -> list[dict]:
    expected = set(manifest["cases" if kind == "protection" else "format_cases"])
    entries = []
    for path in paths:
        content = path.read_bytes()
        report = probe.strict_json(content)
        if (report.get("case") not in expected or report.get("passed") is not True
                or not isinstance(report.get("checks"), dict) or not report["checks"]
                or any(value is not True for value in report["checks"].values())):
            raise ValueError(f"Phase-0 {kind} report is incomplete or failed: {path}")
        profile = report.get("profile") if kind == "protection" else report.get("capability")
        if profile != manifest["profile"]:
            raise ValueError(f"Phase-0 report profile differs from manifest: {path}")
        entries.append({"case": report["case"], "path": str(path.resolve()), "sha256": probe.sha(content)})
    if len(entries) != len(expected) or {entry["case"] for entry in entries} != expected:
        raise ValueError(f"Phase-0 requires every {kind} case exactly once")
    return sorted(entries, key=lambda entry: entry["case"])


def record(protocol_file: Path, protection: list[Path], formats: list[Path], output: Path) -> dict:
    protocol = probe.read_evidence(protocol_file)
    manifest = _manifest(protocol)
    result = {"schema_version": "reviewer-phase0-completion-v1",
              "manifest_sha256": protocol["phase0_sha256"],
              "protocol_sha256": probe.sha(protocol_file.read_bytes()),
              "profile": manifest["profile"], "counts_as_sample": False,
              "protection": _reports(protection, manifest, "protection"),
              "format": _reports(formats, manifest, "format")}
    if output.exists():
        raise FileExistsError("Phase-0 completion output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    probe._write_evidence_file(output, result)
    return result


def verify(protocol_file: Path, completion: Path | None) -> None:
    protocol = probe.read_evidence(protocol_file)
    manifest = _manifest(protocol)
    if manifest.get("status") != "pending_operator_run":
        return
    if completion is None:
        raise PermissionError("pending Phase-0 manifest requires --phase0-results with completed P/F probes")
    result = probe.read_evidence(completion)
    if (result.get("schema_version") != "reviewer-phase0-completion-v1"
            or result.get("manifest_sha256") != protocol["phase0_sha256"]
            or result.get("protocol_sha256") != probe.sha(protocol_file.read_bytes())
            or result.get("profile") != manifest["profile"] or result.get("counts_as_sample") is not False):
        raise ValueError("Phase-0 completion binding differs from protocol/manifest")
    for kind in ("protection", "format"):
        entries = result.get(kind)
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Phase-0 completion has no complete report list")
        measured = _reports([Path(entry["path"]) for entry in entries], manifest, kind)
        if measured != entries:
            raise ValueError("Phase-0 report bytes or case binding changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protection", type=Path, action="append", required=True)
    parser.add_argument("--format", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    record(args.protocol, args.protection, args.format, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
