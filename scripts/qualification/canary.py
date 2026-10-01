"""Direct role canaries. Default: print the operator command; starts require --live."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts import probe_reviewer as probe
from scripts.qualification import offline_boundary as boundary
from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER

DEFAULT_PROTOCOL = ROOT / "docs/evidence/codex/qualification-protocol-v6.json"  # allowlist:provider -- certification data: candidate protocol


def public_profile(profile, provider):
    """Bind effective settings and binary bytes without the operator's home path."""
    return {"provider": provider, "model": profile["model"], "effort": profile["effort"],
            "timeout_seconds": profile.get("timeout_seconds", 600),
            "binary_name": Path(profile["binary"]).name,
            "binary_sha256": probe.sha(Path(profile["binary"]).read_bytes()),
            "commit_sha": profile["commit_sha"]}


def role_document(slot, provider, case, profile, raw, checks, transport_series):
    from agent_roles import AgentSlot, role_for_slot
    role = role_for_slot(AgentSlot(slot)).value
    fields = {"provider": provider, "role": role, "slot": slot,
              "case": case, "transport_series": transport_series}
    evidence = raw["evidence"]
    proof = {**fields, "request_id": raw["request_id"],
             "request_sha256": probe.digest(evidence["request_document"]),
             "writer_sha256": probe.digest(evidence["writer_schema"]),
             "raw_sha256": probe.digest(raw), "profile_sha256": probe.digest(profile),
             "profile": profile, "model": profile["model"], "checks": checks, "raw": raw}
    passed = raw["status"] == "success" and all(value is True for value in checks.values())
    return {**fields, "status": "passed" if passed else "failed", "proof": proof}


def assert_public(document):
    # Newly collected public proof is sealed only after this check. Private
    # diagnostic streams and absolute binary paths are not part of this format.
    text = probe.canonical(document)
    names = {Path.home().name, os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}
    personal_name = any(re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text)
                        for name in names - {"", "root"})
    if personal_name or re.search(r"/home/|/Users/|\\Users\\", text):
        raise ValueError("canary public proof contains a personal path; keep the private evidence")


def implementer_call(profile, work, toolchain_roots):
    from agent_adapters import _implementer_transports
    from agent_config import AgentSettings
    from agent_runtime import OrchestratorConfig, run_native_implementer_agent
    from native_provider_schema import provider_capability
    from toolchain_paths import validate_toolchain_read_roots
    from gates import render_implementer_stop_instructions
    from provider_input_budget import default_provider_input_budget_policy
    from scripts.qualification.run_probe import _semantic_capture
    provider = provider_capability(BOUNDARY_IMPLEMENTER)["provider"]
    reviewer = provider_capability(BOUNDARY_REVIEWER)["provider"]
    if profile["model"] != "opus" or profile["effort"] != "high":
        raise ValueError("implementer canary requires the bound opus/high profile")
    paths = boundary.fixture(work)
    repo = paths["repo"]
    prompt = ("Read README.md and verify the bound branch and base commit. "
              "Create positive-write.txt containing exactly CANARY_OK and a newline, "
              "using a repository-relative path. Do not change anything else. "
              "Return exactly the bound implementation result with ready=true and no test files.")
    bundle = boundary.implementer_bundle(prompt, repo)
    # Each task must have its own request identity, including repeat campaigns.
    from native_implementer_request import NativeImplementerEvidenceInput, NativeImplementerRequestSpec, build_native_implementer_request
    spec = NativeImplementerRequestSpec(bundle.bound_context.context,
        boundary.fixture_git(repo, "branch", "--show-current"), boundary.fixture_git(repo, "rev-parse", "HEAD"),
        ("positive-write.txt",), prompt, "Direct operator-owned disposable implementer canary.\n" + render_implementer_stop_instructions(),
        (NativeImplementerEvidenceInput("positive-control", "canary", "README.md contains PHASE0_POSITIVE."),))
    spec = replace(spec, context=replace(spec.context, run_id="canary-" + work.name))
    bundle = build_native_implementer_request(spec, profile=BOUNDARY_IMPLEMENTER)
    settings = AgentSettings(provider, profile["binary"], profile["model"],
                             profile.get("timeout_seconds", 600) or None, profile["effort"])
    adapter = _implementer_transports()[provider](settings)
    adapter.bind_implementer_boundary(repo, repo / "inbox", repo / "outbox", spec.context.run_id)
    adapter.settings = replace(settings, toolchain_read_roots=validate_toolchain_read_roots(
        tuple(str(path) for path in toolchain_roots), repo, adapter._protected_paths))
    before = {}
    observation_roots = {name: path for name, path in paths.items()}
    def prepared(_measurement):
        before.update(_semantic_capture(observation_roots)["entries"])
    extract = adapter.extract_output
    def capture(stdout, stderr, extra_files):
        probe._write_evidence_file(work / "provider-output.json",
            {"stdout": stdout, "stderr": stderr, "extra_files": extra_files})
        return extract(stdout, stderr, extra_files)
    adapter.extract_output = capture
    try:
        output = run_native_implementer_agent(adapter, bundle,
            config=OrchestratorConfig(repo_root=repo, inbox_dir=repo / "inbox", outbox_dir=repo / "outbox",
                provider_input_budget=default_provider_input_budget_policy((
                    ("implementer", "implementer", provider), ("reviewer", "reviewer", reviewer),
                    ("final_reviewer", "reviewer", reviewer)))),
            shorten=lambda value, limit: (value or "")[:limit], operation=spec.context.operation,
            binding_fingerprint=spec.context.current_fingerprint, pre_start_callback=prepared)
        adapter.remove_sandbox_placeholders()
        result = probe.strict_json(output.canonical_json)
        positive = (repo / "positive-write.txt").is_file() and (repo / "positive-write.txt").read_text() == "CANARY_OK\n"
        after = _semantic_capture(observation_roots)["entries"]
        # Only the authorized file may differ; the root's link count is not a
        # file-content change. Retain modes and all protected Git metadata.
        def comparable(entries):
            return {name: {key: value for key, value in row.items()
                           if key != "links" or row.get("type") != "directory"}
                    for name, row in entries.items() if name != "repo/positive-write.txt"}
        protected = bool(before) and comparable(before) == comparable(after)
        probe._write_evidence_file(work / "snapshots.json", {"before": before, "after": after})
        checks = {"writer": True, "domain": output.result.stop_request is None and output.result.ready is True,
                  "effective_rights": positive, "isolation_postcheck": protected,
                  "no_denials": not adapter.metadata.get("permission_denials")}
        raw = {"request_id": bundle.bound_context.request_id,
               "status": "success" if all(checks.values()) else "failed",
               "evidence": {"request_document": bundle.document, "writer_schema": bundle.provider_response_schema,
                            "result_document": result, "actual_models": adapter.metadata.get("actual_models", [])}}
        return role_document("implementer", provider, "I1", public_profile(profile, provider),
                             raw, checks, "direct-implementer-canary-v1")
    except Exception as exc:
        probe._write_evidence_file(work / "failure.json", {"error": f"{type(exc).__name__}: {exc}",
                                                         "metadata": adapter.metadata})
        raw = {"request_id": bundle.bound_context.request_id, "status": "failed",
               "evidence": {"request_document": bundle.document,
                            "writer_schema": bundle.provider_response_schema,
                            "error_type": type(exc).__name__}}
        checks = dict.fromkeys(("writer", "domain", "effective_rights", "isolation_postcheck", "no_denials"), False)
        return role_document("implementer", provider, "I1", public_profile(profile, provider), raw,
                             checks, "direct-implementer-canary-v1")
    finally:
        adapter.cleanup()


def run(slot, *, profile_file, output, protocol_file=DEFAULT_PROTOCOL,
        evidence_dir=None, operator_decisions=None, phase0_results=None,
        private_dir=None, toolchain_roots=(), live=False):
    if not live:
        raise PermissionError("role canary provider starts require --live")
    profile = probe._profile_document(profile_file)
    probe._assert_committed_qualification_code()
    if profile.get("live") is not True or profile.get("commit_sha") != probe._current_commit():
        raise ValueError("canary profile must enable live and bind the committed HEAD")
    from native_provider_schema import provider_capability
    capability = BOUNDARY_IMPLEMENTER if slot == "implementer" else BOUNDARY_REVIEWER
    provider = provider_capability(capability)["provider"]
    binary = Path(profile["binary"])
    if not binary.is_absolute() or binary.name != provider or not binary.is_file():
        raise ValueError(f"canary binary must be an absolute executable named {provider}")
    if binary.stat().st_mode & 0o111 == 0:
        raise ValueError("canary binary is not executable")
    timeout = profile.get("timeout_seconds", 600)
    if type(timeout) is not int or timeout < 0:
        raise ValueError("canary timeout must be a non-negative integer")
    existing = probe.read_evidence(output) if output.exists() else {"schema_version": "role-canary-v1", "status": "passed", "slots": {}}
    if slot in existing["slots"] or output.is_symlink():
        raise FileExistsError("canary slot already has evidence or output is a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    if private_dir is not None:
        private_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="dao-role-canary-", dir=private_dir))
    print(f"Private canary evidence: {work}", file=sys.stderr)
    # Keep the original streams/ledger even if extraction or publication fails.
    try:
        if slot == "implementer":
            item = implementer_call(profile, work, toolchain_roots)
        else:
            if evidence_dir is None:
                raise ValueError("reviewer canaries require --evidence-dir with measured qualification")
            from scripts.qualification.prerequisites import verify
            verify(protocol_file, phase0_results)
            protocol = probe.read_evidence(protocol_file)
            pair = probe.qualification_pair(protocol)
            if protocol.get("schema_version") != "qualification-protocol-v6" or pair.capability(pair.candidate) != BOUNDARY_REVIEWER:
                raise ValueError("role reviewer canary requires the bound candidate v6 protocol")
            decisions = probe.read_evidence(operator_decisions or evidence_dir / "operator-decisions-v1.json")
            verdict = probe.qualification_summary(
                probe.read_evidence(evidence_dir / "qualification-series-v1.json"),
                probe.read_evidence(evidence_dir / "qualification-envelopes-v1.json.gz" if (evidence_dir / "qualification-envelopes-v1.json.gz").exists() else evidence_dir / "qualification-envelopes-v1.json"),
                protocol, probe.read_evidence(evidence_dir / "quality-results-v1.json"), decisions)
            if not verdict["qualified_for_canary"]:
                raise PermissionError("measured qualification does not permit a canary")
            direct = probe.run_canary_call(slot, profile_file=profile_file, output_dir=work,
                live=True, protocol_file=protocol_file, reference_evidence_dir=evidence_dir)
            raw = direct["proof"]["raw"]
            evidence = raw["evidence"]
            # Public role proof binds the validated native result, not private
            # process diagnostics. The private direct report keeps the stream.
            public_raw = {"request_id": raw["request_id"], "status": raw["status"],
                "evidence": {"request_document": evidence["request_document"],
                             "writer_schema": evidence["writer_schema"],
                             "result_document": probe.measured_review_result(evidence["envelope"])}}
            item = role_document(slot, provider, direct["case"], public_profile(profile, provider),
                                 public_raw, direct["proof"]["checks"], "qualification-protocol-v6")
        document = {**existing, "slots": {**existing["slots"], slot: item}}
        document["status"] = "passed" if all(row["status"] == "passed" for row in document["slots"].values()) else "failed"
        assert_public(document)
        if document["status"] == "passed":
            from role_certification import _validate_role_canaries
            from agent_roles import AgentRoleName, AgentSlot
            _validate_role_canaries(document, provider=provider,
                role=AgentRoleName.IMPLEMENTER if slot == "implementer" else AgentRoleName.REVIEWER,
                slot=AgentSlot(slot), model_family_pattern=None)
        probe._write_evidence_file(output, document)
        return document
    finally:
        probe._write_evidence_file(work / "profile.json", profile)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", choices=("reviewer", "final_reviewer", "implementer"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--operator-decisions", type=Path)
    parser.add_argument("--phase0-results", type=Path)
    parser.add_argument("--private-dir", type=Path)
    parser.add_argument("--toolchain-root", type=Path, action="append", default=[])
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live:
        command = ["python3", "scripts/qualification/canary.py", *(argv if argv is not None else sys.argv[1:]), "--live"]
        print(shlex.join(command))
        return 0
    report = run(args.slot, profile_file=args.profile, output=args.out,
                 protocol_file=args.protocol, evidence_dir=args.evidence_dir,
                 operator_decisions=args.operator_decisions, phase0_results=args.phase0_results,
                 private_dir=args.private_dir, toolchain_roots=args.toolchain_root, live=True)
    print(json.dumps({"status": report["status"], "output": str(args.out)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
