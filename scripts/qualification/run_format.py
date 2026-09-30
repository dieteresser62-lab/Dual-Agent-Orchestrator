"""Run one F1–F6 writer probe through the registered production reviewer adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe


def run_format(*, case: str, provider: str, protocol_file: Path, profile_file: Path,
               source: Path, output: Path, live: bool = False,
               invoke: Callable | None = None) -> dict:
    if not live and invoke is None:
        raise PermissionError("live writer probe requires --live")
    if case not in probe.EXPECTED_FORMAT:
        raise ValueError("unknown writer case")
    protocol = probe.strict_json(protocol_file.read_bytes())
    probe.validate_qualification(protocol)
    pair = probe.qualification_pair(protocol)
    capability = pair.capability(provider)
    profile = probe._profile_document(profile_file)
    if live and profile.get("live") is not True:
        raise PermissionError("writer probe profile must enable live execution")
    if output.exists():
        raise FileExistsError("writer probe output already exists")
    if not source.is_dir():
        raise ValueError("writer probe source is missing")
    probe.verify_qualification_source("transport", f"{case}:1", source)
    output.mkdir(parents=True)
    from agent_runtime import OrchestratorConfig, run_native_review_agent
    from native_review_request import build_native_review_request

    binary = Path(profile["binary"])
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("writer probe binary must be an absolute regular file")
    adapter = probe._qualification_adapter(protocol, provider, profile, binary,
        profile.get("timeout_seconds", protocol["limits"]["probe_seconds"]))
    probe._bind_qualification_catalog(adapter, capability)
    spec = probe.build_qualification_spec("transport", f"{case}:1",
        run_id=f"phase0-format-{provider}-{case}")
    bundle = build_native_review_request(spec, profile=capability)
    raw = {}
    extract = adapter.extract_output
    def capture(stdout, stderr, extra):
        probe.capture_review_output(adapter, raw, stdout, stderr, extra, capability)
        return extract(stdout, stderr, extra)
    adapter.extract_output = capture
    runner = invoke or run_native_review_agent
    ledger = probe._QualificationLedger(output / "ledger.jsonl", spec.context.run_id)
    try:
        output_result = runner(adapter, bundle,
            config=OrchestratorConfig(repo_root=source, provider_input_budget=probe._review_budget(capability)),
            shorten=lambda value, maximum: (value or "")[:maximum],
            operation=spec.context.operation, binding_fingerprint=spec.context.diff_fingerprint,
            attempt_invocation=ledger)
    except Exception as exc:
        ledger._record("result", status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    envelope = probe.strict_json(raw.get("stdout", "{}"))
    verdict = probe.validate_format_response(case, envelope, bundle=bundle,
        exit_code=int(raw.get("exit_code") or 0), stderr=raw.get("stderr", ""),
        transport_profile=capability)
    report = {"schema_version": "reviewer-format-probe-v1", "case": case,
              "provider": provider, "capability": capability,
              "passed": bool(verdict["pass"] and output_result.result is not None),
              "checks": verdict["checks"], "request_sha256": probe.sha(bundle.canonical_json.encode()),
              "writer_sha256": probe.sha(bundle.provider_response_schema_json.encode())}
    probe._write_evidence_file(output / "report.json", report)
    ledger._record("result", status="passed" if report["passed"] else "failed")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=probe.EXPECTED_FORMAT)
    parser.add_argument("provider")
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = run_format(case=args.case, provider=args.provider, protocol_file=args.protocol,
        profile_file=args.profile, source=args.source, output=args.output, live=args.live)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
