"""Sequential qualification campaign runner with production-equivalent retries.

The default command renders its calls. Provider starts require --live and a
live profile accepted by probe_reviewer.py. Evidence is append-only per call ID.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe


class CampaignStopped(RuntimeError):
    pass


def call_id(series_id: str, case_id: str, retry: int = 0) -> str:
    base = f"{series_id}-{case_id.replace(':', '-')}"
    return f"{base}-r{retry}" if retry else base


def run_block(*, kind: str, provider: str, series_id: str, profile: Path,
              protocol: Path, evidence_dir: Path, source_dir: Path,
              cases: tuple[str, ...], quota: Path | None = None,
              restart_diagnosis: str | None = None, restart_change: str | None = None,
              live: bool = False,
              invoke: Callable[[list[str]], dict] | None = None) -> list[dict]:
    """Run ordered cases; a fake invoke callback is only for provider-free tests."""
    if not live and invoke is None:
        raise PermissionError("campaign provider calls require --live")
    document = probe.strict_json(protocol.read_bytes())
    probe.validate_qualification(document)
    pair = probe.qualification_pair(document)
    expected = probe.qualification_cases(kind, provider, pair)
    if cases != expected and cases != expected[:len(cases)]:
        raise ValueError("block cases differ from the protocol order")
    if live and cases != expected:
        raise ValueError("live campaign must run the complete ordered series")
    if bool(restart_diagnosis) != bool(restart_change):
        raise ValueError("restart diagnosis and concrete change must be supplied together")
    if not cases:
        raise ValueError("empty block")
    results: list[dict] = []
    for case in cases:
        previous: dict | None = None
        transient_retries = contract_retries = 0
        retry = 0
        while True:
            identifier = call_id(series_id, case, retry)
            source = source_dir / identifier
            if source.exists():
                raise FileExistsError(f"review source already exists: {source}")
            source_kind, source_case = ("transport", "F2:1") if kind == "print_timeout" else (kind, case)
            prepare = [sys.executable, str(probe.ROOT / "scripts/probe_reviewer.py"),
                       "prepare-case", source_kind, source_case, str(source)]
            if invoke is None:
                prepared = subprocess.run(prepare, cwd=probe.ROOT, capture_output=True, text=True, check=False)
                if prepared.returncode:
                    raise CampaignStopped(f"source preparation failed for {identifier}: {prepared.stderr}")
            else:
                invoke(prepare)
            command = [sys.executable, str(probe.ROOT / "scripts/probe_reviewer.py"),
                       "qualification-call", kind, case, provider,
                       "--series-id", series_id, "--call-id", identifier,
                       "--profile", str(profile), "--protocol", str(protocol),
                       "--source-repo", str(source), "--output-dir", str(evidence_dir),
                       "--tag", series_id]
            if quota is not None:
                command.extend(("--quota-observation", str(quota)))
            if not results and restart_diagnosis:
                command.extend(("--restart-diagnosis", restart_diagnosis,
                                "--restart-change", restart_change or ""))
            if previous is not None:
                command.extend(("--production-retry-of", previous["call_id"]))
            if live:
                command.append("--live")
            if invoke is None:
                completed = subprocess.run(command, cwd=probe.ROOT, capture_output=True, text=True, check=False)
                try:
                    row = probe.strict_json(completed.stdout)
                except ValueError as exc:
                    raise CampaignStopped(f"unparseable call result for {identifier}: {completed.stderr}") from exc
            else:
                row = invoke(command)
            if not isinstance(row, dict) or row.get("call_id") != identifier:
                raise CampaignStopped(f"call result is unbound for {identifier}")
            results.append(row)
            safe_timeout = (kind == "print_timeout" and row.get("status") == "technical_rejection"
                            and row.get("checks", {}).get("print_timeout") is True
                            and row.get("checks", {}).get("no_valid_stop") is True)
            if row.get("status") == "success" or safe_timeout:
                break
            if kind == "print_timeout" or row.get("production_retryable") is not True:
                raise CampaignStopped(f"non-retryable technical failure at {identifier}")
            reason = probe._retry_reason(row)
            if reason == "transient":
                transient_retries += 1
                if transient_retries > document["production_retry"]["max_retries_per_case"]:
                    raise CampaignStopped(f"transient retry limit at {identifier}")
            elif reason == "contract":
                contract_retries += 1
                if contract_retries > document["production_retry"]["contract_max_retries_per_case"]:
                    raise CampaignStopped(f"contract retry limit at {identifier}")
            else:
                raise CampaignStopped(f"retry flag lacks a production retry reason at {identifier}")
            previous = row
            retry += 1
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("transport", "print_timeout", "large_output", "quality"))
    parser.add_argument("provider")
    parser.add_argument("series_id")
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--quota", type=Path)
    parser.add_argument("--restart-diagnosis")
    parser.add_argument("--restart-change")
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    protocol = probe.strict_json(arguments.protocol.read_bytes())
    cases = probe.qualification_cases(arguments.kind, arguments.provider, probe.qualification_pair(protocol))
    if not arguments.live:
        print(json.dumps({"series_id": arguments.series_id, "kind": arguments.kind,
                          "provider": arguments.provider, "cases": cases,
                          "live": False}, ensure_ascii=False))
        return 0
    rows = run_block(kind=arguments.kind, provider=arguments.provider,
                     series_id=arguments.series_id, profile=arguments.profile,
                     protocol=arguments.protocol, evidence_dir=arguments.evidence_dir,
                     source_dir=arguments.source_dir, cases=cases, quota=arguments.quota,
                     restart_diagnosis=arguments.restart_diagnosis,
                     restart_change=arguments.restart_change, live=True)
    print(json.dumps({"series_id": arguments.series_id, "calls": len(rows),
                      "status": "complete"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
