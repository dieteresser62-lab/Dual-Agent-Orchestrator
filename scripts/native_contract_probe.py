#!/usr/bin/env python3
"""Probe provider Structured-Output schema features outside the repository.

This is deliberately a transport probe, not a workflow driver.  Every provider
process runs in a fresh temporary directory with a read-only/no-write sandbox,
and the repository status is compared byte-for-byte before and after the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from native_provider_schema import normalize_transport_profile  # noqa: E402
from agent_adapters import (  # noqa: E402
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
    NativeCodexExecutionBoundary,
)
from agent_config import default_agent_settings  # noqa: E402
from agent_runtime import (  # noqa: E402
    OrchestratorConfig,
    run_native_codex_agent,
    run_native_review_agent,
)
from contracts import (  # noqa: E402
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind  # noqa: E402
from native_codex_request import (  # noqa: E402
    NativeCodexEvidenceInput,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from native_review_contract import NativeReviewContext  # noqa: E402
from native_review_request import (  # noqa: E402
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestSpec,
    build_native_review_request,
)


FEATURE_SCHEMAS: dict[str, dict[str, Any]] = {
    "closed_object": {
        "type": "object",
        "properties": {"value": {"type": "string", "const": "ok"}},
        "required": ["value"],
        "additionalProperties": False,
    },
    "min_max_items": {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"type": "string"},
            }
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "const_list": {
        "type": "object",
        "properties": {
            "values": {"type": "array", "const": ["C-01", "C-02"]}
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "positional_tuple": {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "prefixItems": [
                    {"type": "string", "const": "C-01"},
                    {"type": "integer", "const": 2},
                ],
                "items": False,
                "minItems": 2,
                "maxItems": 2,
            }
        },
        "required": ["values"],
        "additionalProperties": False,
    },
    "nested_one_of": {
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "yes"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "no"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["result"],
        "additionalProperties": False,
    },
    "nested_any_of": {
        "type": "object",
        "properties": {
            "result": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "yes"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "const": "no"}
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["result"],
        "additionalProperties": False,
    },
}

CANARY_FORMS = {
    "codex": ("plan", "implementation", "correction", "final_report"),
    "claude": ("plan", "initial_slice", "convergence", "final"),
}
PROTECTED_REPOSITORY_PATHS = (".orchestrator", "inbox", "outbox")


def _run(command: list[str], *, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )


def _repository_status(repo_root: Path) -> str:
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git status failed")
    return completed.stdout


def _protected_repository_fingerprint(repo_root: Path) -> str:
    """Fingerprint ignored workflow stores that Git status cannot observe."""

    entries: list[dict[str, object]] = []
    for relative_root in PROTECTED_REPOSITORY_PATHS:
        root = repo_root / relative_root
        if not root.exists() and not root.is_symlink():
            entries.append({"path": relative_root, "kind": "missing"})
            continue
        paths = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
        for path in paths:
            stat = path.lstat()
            relative = path.relative_to(repo_root).as_posix()
            entry: dict[str, object] = {
                "path": relative,
                "mode": stat.st_mode,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if path.is_symlink():
                entry["kind"] = "symlink"
                entry["target"] = os.readlink(path)
            elif path.is_file():
                entry["kind"] = "file"
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                entry["kind"] = "directory"
            else:
                entry["kind"] = "other"
            entries.append(entry)
    raw = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _version(binary: str, *, cwd: Path) -> str:
    completed = _run([binary, "--version"], cwd=cwd)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{binary} --version failed")
    return (completed.stdout or completed.stderr).strip()


def _codex_probe(binary: str, schema: dict[str, Any], workdir: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    schema_path = workdir / "response-schema.json"
    output_path = workdir / "last-message.json"
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    command = [
        binary,
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--config",
        'model_reasoning_effort="medium"',
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    return command, _run(
        command,
        cwd=workdir,
        stdin="Return the smallest JSON value accepted by the supplied schema. Use no tools.",
    )


def _claude_probe(binary: str, schema: dict[str, Any], workdir: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    schema_json = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    command = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--tools",
        "Read",
        "--allowedTools",
        "Read",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "user",
        "--safe-mode",
        "--strict-mcp-config",
        "--prompt-suggestions",
        "false",
        "--add-dir",
        str(workdir),
        "--json-schema",
        schema_json,
        "--no-session-persistence",
        "--disable-slash-commands",
        "Return the smallest JSON value accepted by the supplied schema. Use no tools.",
    ]
    return command, _run(command, cwd=workdir)


def _head(repo_root: Path) -> str:
    completed = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git rev-parse HEAD failed")
    return completed.stdout.strip()


def _canary_finding() -> FindingRecord:
    return FindingRecord(
        "C-01",
        FindingClass.BLOCKER,
        FindingStatus.OPEN,
        "Close the isolated canary finding.",
        "The schema-bound canary returns one accepted disposition.",
        FindingOrigin("03", 1, AgentRole.CLAUDE),
    )


def _codex_canary_bundle(form: str, *, repo_root: Path):
    kind = NativeCodexRequestKind(form)
    work_kind = kind in {
        NativeCodexRequestKind.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION,
    }
    readiness = {
        NativeCodexRequestKind.PLAN: ReadinessMarker.PLAN,
        NativeCodexRequestKind.IMPLEMENTATION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.CORRECTION: ReadinessMarker.IMPLEMENTATION,
        NativeCodexRequestKind.FINAL_REPORT: ReadinessMarker.FINAL_REPORT,
    }[kind]
    context = NativeCodexContext(
        run_id="native-contract-canary",
        work_unit_id=f"codex-{form}",
        operation=(
            "codex_final_review"
            if kind is NativeCodexRequestKind.FINAL_REPORT
            else f"codex_{form}"
        ),
        current_fingerprint="a" * 64,
        request_kind=kind,
        contract=CodexStepContract(
            name=f"canary-{form}",
            readiness_marker=readiness,
            slice_id="03",
            round_number=1,
            require_test_files_record=work_kind,
            expected_test_files=(),
            test_changes_approved=True,
            enforce_expected_test_files=False,
            require_slice_plan=kind is NativeCodexRequestKind.PLAN,
            plan_artifact_path=(
                "docs/internal/native-agent-contract-closure-arbeitsplan.md"
                if kind is NativeCodexRequestKind.PLAN
                else None
            ),
        ),
        previous_findings=(
            (_canary_finding(),)
            if kind is NativeCodexRequestKind.CORRECTION
            else ()
        ),
    )
    assignment = (
        "Return the smallest ready result allowed by the bound contract. "
        "For a plan, return exactly one Slice whose numeric slice_id is 1 and "
        "whose only scope path is the authorized plan artifact. For correction, "
        "accept C-01 with a concise rationale. Do not use tools and do not modify "
        "files."
    )
    return build_native_codex_request(
        NativeCodexRequestSpec(
            context=context,
            target_branch="canary/native-contract-closure",
            base_commit=_head(repo_root),
            authorized_paths=(
                "docs/internal/native-agent-contract-closure-arbeitsplan.md",
            ),
            assignment=assignment,
            work_context="Isolated transport canary; no implementation is required.",
            evidence=(
                NativeCodexEvidenceInput(
                    "canary_evidence",
                    "canary",
                    "Return only the schema-bound result and use no tools.",
                ),
            ),
        ),
        inline_evidence_chars=1,
    )


def _canary_attestation() -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        "native-contract-canary-validation",
        "a" * 64,
        (command,),
        (ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        hashlib.sha256(b"passed").hexdigest(),
        "passed",
        (ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),),
    )


def _claude_canary_bundle(form: str, *, repo_root: Path):
    marker = {
        "plan": ApprovalMarker.PLAN,
        "initial_slice": ApprovalMarker.SLICE,
        "convergence": ApprovalMarker.SLICE,
        "final": ApprovalMarker.FINAL,
    }[form]
    convergence = form == "convergence"
    context = NativeReviewContext(
        run_id="native-contract-canary",
        work_unit_id=f"claude-{form}",
        operation={
            ApprovalMarker.PLAN: "claude_plan_review",
            ApprovalMarker.SLICE: "claude_slice_review",
            ApprovalMarker.FINAL: "claude_final_review",
        }[marker],
        diff_fingerprint="a" * 64,
        reviewer=AgentRole.CLAUDE,
        approval_marker=marker,
        slice_id="03" if marker is not ApprovalMarker.FINAL else "final",
        round_number=2 if convergence else 1,
        previous_findings=(_canary_finding(),) if convergence else (),
        validation_attestation=_canary_attestation(),
        test_files=(),
        test_changes_approved=False,
        allow_new_observations=not convergence,
        anchor_origin=None,
        validation_command_prefixes=(("python3", "-m", "pytest"),),
    )
    kind = {
        ApprovalMarker.PLAN: NativeReviewKind.PLAN,
        ApprovalMarker.SLICE: NativeReviewKind.SLICE,
        ApprovalMarker.FINAL: NativeReviewKind.FINAL,
    }[marker]
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=kind,
            target_branch="canary/native-contract-closure",
            base_commit=_head(repo_root),
            authorized_paths=(
                "docs/internal/native-agent-contract-closure-arbeitsplan.md",
            ),
            acceptance_criteria=(
                "Return an approval with review evidence and a realistic pre-mortem.",
                *(
                    ("Close C-01 with a concise rationale.",)
                    if convergence
                    else ()
                ),
            ),
            evidence=(
                NativeReviewEvidenceInput(
                    "canary_evidence",
                    "canary",
                    "This is an isolated contract transport canary. No defect is present.",
                ),
            ),
        )
    )


def _live_canary(provider: str, form: str, *, repo_root: Path) -> dict[str, Any]:
    if form not in CANARY_FORMS[provider]:
        raise ValueError(f"unsupported {provider} canary form: {form}")
    settings = default_agent_settings()
    config = OrchestratorConfig(
        repo_root=repo_root,
        strict_preflight=True,
        agent_output_mode="none",
    )
    with tempfile.TemporaryDirectory(prefix=f"dao-{provider}-{form}-canary-") as raw:
        canary_root = Path(raw)
        work_root = canary_root / "work"
        work_root.mkdir()
        if provider == "codex":
            bundle = _codex_canary_bundle(form, repo_root=repo_root)
            adapter = NativeCodexAdapter(settings["codex"])
            boundary = NativeCodexExecutionBoundary.canary(
                repo_root,
                execution_root=work_root,
                evidence_asset_root=work_root,
            )
            try:
                output = run_native_codex_agent(
                    adapter,
                    bundle,
                    config=config,
                    shorten=lambda value, maximum: (value or "")[:maximum],
                    operation=(
                        "codex_final_review"
                        if form == "final_report"
                        else f"codex_{form}"
                    ),
                    binding_fingerprint="a" * 64,
                    execution_boundary=boundary,
                )
            finally:
                adapter.cleanup()
            response_sha256 = output.response_sha256
            decision = "accepted"
        else:
            bundle = _claude_canary_bundle(form, repo_root=repo_root)
            adapter = NativeClaudeReviewAdapter(settings["claude"])
            try:
                output = run_native_review_agent(
                    adapter,
                    bundle,
                    config=config,
                    shorten=lambda value, maximum: (value or "")[:maximum],
                    reviewer_manifest_paths=(
                        "docs/internal/native-agent-contract-closure-arbeitsplan.md",
                    ),
                    operation=bundle.bound_context.context.operation,
                    binding_fingerprint="a" * 64,
                )
            finally:
                adapter.cleanup()
            response_sha256 = hashlib.sha256(
                output.canonical_json.encode("utf-8")
            ).hexdigest()
            decision = "approved" if output.result.approval else "denied"
        schema_sha256 = hashlib.sha256(
            bundle.provider_response_schema_json.encode("utf-8")
        ).hexdigest()
        return {
            "provider": provider,
            "writer_form": form,
            "request_id": bundle.bound_context.request_id,
            "writer_schema_sha256": schema_sha256,
            "response_sha256": response_sha256,
            "local_decision": decision,
            "adapter_runtime_path": "production",
            "repository_paths_writable": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--feature", choices=tuple(FEATURE_SCHEMAS))
    mode.add_argument("--canary-form")
    parser.add_argument("--binary")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    before = _repository_status(repo_root)
    protected_before = _protected_repository_fingerprint(repo_root)
    if args.canary_form is not None:
        report = _live_canary(
            args.provider, args.canary_form, repo_root=repo_root
        )
        after = _repository_status(repo_root)
        if after != before:
            raise RuntimeError("provider canary changed the repository worktree")
        if _protected_repository_fingerprint(repo_root) != protected_before:
            raise RuntimeError("provider canary changed a protected workflow store")
        report["repository_status_unchanged"] = True
        report["protected_workflow_stores_unchanged"] = True
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    binary = args.binary or args.provider
    with tempfile.TemporaryDirectory(prefix=f"dao-{args.provider}-schema-probe-") as raw:
        workdir = Path(raw)
        version = _version(binary, cwd=workdir)
        if args.provider == "codex":
            command, completed = _codex_probe(
                binary, FEATURE_SCHEMAS[args.feature], workdir
            )
        else:
            command, completed = _claude_probe(
                binary, FEATURE_SCHEMAS[args.feature], workdir
            )
    after = _repository_status(repo_root)
    if after != before:
        raise RuntimeError("provider probe changed the repository worktree")
    if _protected_repository_fingerprint(repo_root) != protected_before:
        raise RuntimeError("provider probe changed a protected workflow store")

    report = {
        "provider": args.provider,
        "binary_name": Path(binary).name,
        "cli_version": version,
        "feature": args.feature,
        "accepted": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "normalized_transport_profile": normalize_transport_profile(
            args.provider, command
        ).document,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "repository_status_unchanged": True,
        "protected_workflow_stores_unchanged": True,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
