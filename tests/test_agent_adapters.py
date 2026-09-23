from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_adapters import (
    REVIEW_PACKET_CHUNK_CHARS,
    AgentOutputError,
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
    NativeCodexExecutionBoundary,
    _write_review_manifest,
    build_agent_registry,
)
from agent_config import AgentSettings
from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    ReadinessMarker,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind
from native_codex_request import NativeCodexEvidenceInput, NativeCodexRequestSpec, build_native_codex_request
from native_review_contract import NativeReviewContext
from native_review_request import NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec, PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND, build_native_review_request
from provider_input_budget import default_provider_input_budget_policy, measure_provider_input


def _settings(role: str) -> AgentSettings:
    return AgentSettings(
        role,
        role,
        "gpt-6-sol" if role == "codex" else "opus",
        1800,
        "medium" if role == "codex" else "high",
    )


def _codex_bundle(*, assignment: str = "Create the plan."):
    contract = CodexStepContract(
        name="plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    context = NativeCodexContext(
        run_id="run-native",
        work_unit_id="1",
        operation="codex_plan",
        current_fingerprint="a" * 64,
        request_kind=NativeCodexRequestKind.PLAN,
        contract=contract,
    )
    return build_native_codex_request(
        NativeCodexRequestSpec(
            context=context,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("docs/internal/plan.md",),
            assignment=assignment,
            work_context="Use the typed request.",
            evidence=(NativeCodexEvidenceInput("policy", "system_policy", "JSON only"),),
        )
    )


def _review_bundle():
    attestation = ValidationAttestation(
        attestation_id="validation-native",
        diff_fingerprint="c" * 64,
        expected_commands=("pytest",),
        records=(ValidationRecord(ValidationStatus.PASS, "pytest", 0, "passed"),),
        output_digest=hashlib.sha256(b"passed").hexdigest(),
        summary="passed",
    )
    context = NativeReviewContext(
        run_id="run-native",
        work_unit_id="1",
        operation="claude_slice_review",
        diff_fingerprint="c" * 64,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=attestation,
    )
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Native result validates.",),
            evidence=(NativeReviewEvidenceInput("diff", "diff", "diff --git"),),
        )
    )


def test_registry_constructs_only_native_adapters() -> None:
    registry = build_agent_registry({"codex": _settings("codex"), "claude": _settings("claude")})
    assert type(registry["codex"]) is NativeCodexAdapter
    assert type(registry["claude"]) is NativeClaudeReviewAdapter


def test_native_adapter_api_and_mro_are_closed(tmp_path: Path) -> None:
    codex = NativeCodexAdapter(_settings("codex"))
    claude = NativeClaudeReviewAdapter(_settings("claude"))
    assert NativeCodexAdapter.reviewer is False
    assert NativeClaudeReviewAdapter.reviewer is True
    assert not hasattr(NativeCodexAdapter, "build_capability_smoke_command")
    assert hasattr(NativeClaudeReviewAdapter, "build_capability_smoke_command")
    assert all("CodexAdapter" != cls.__name__ for cls in NativeCodexAdapter.__mro__[1:])
    assert all("ClaudeAdapter" != cls.__name__ for cls in NativeClaudeReviewAdapter.__mro__[1:])
    codex.bind_reviewer_workspace(tmp_path, tmp_path / "snapshot")


def test_native_codex_prepares_schema_request_and_assets(tmp_path: Path) -> None:
    adapter = NativeCodexAdapter(_settings("codex"))
    bundle = _codex_bundle()
    repository = tmp_path / "repository"
    execution = tmp_path / "execution"
    assets = tmp_path / "assets"
    repository.mkdir()
    execution.mkdir()
    assets.mkdir()
    boundary = NativeCodexExecutionBoundary.canary(
        repository,
        execution_root=execution,
        evidence_asset_root=assets,
    )
    prepared = adapter.prepare_native_provider_input(bundle, boundary)
    assert prepared.stdin_text == bundle.canonical_json
    assert "--output-schema" in prepared.command
    assert prepared.command[prepared.command.index("--sandbox") + 1] == "read-only"
    assert {item.name for item in prepared.components} >= {"stdin_prompt", "response_schema"}
    assert NativeCodexAdapter.required_hosts == ("chatgpt.com", "api.openai.com")
    adapter.cleanup()


def test_native_codex_transports_assignment_without_inlining_root_roles(
    tmp_path: Path,
) -> None:
    assignment = "S6-TRANSPORT-SENTINEL: create the declared work-plan artifact."
    adapter = NativeCodexAdapter(_settings("codex"))
    bundle = _codex_bundle(assignment=assignment)
    repository = tmp_path / "repository"
    execution = tmp_path / "execution"
    assets = tmp_path / "assets"
    repository.mkdir()
    execution.mkdir()
    assets.mkdir()

    prepared = adapter.prepare_native_provider_input(
        bundle,
        NativeCodexExecutionBoundary.canary(
            repository, execution_root=execution, evidence_asset_root=assets
        ),
    )

    assert prepared.stdin_text is not None
    canonical_request = json.loads(prepared.stdin_text)
    assert canonical_request["assignment"] == assignment
    assert prepared.stdin_text == bundle.canonical_json
    assert next(
        item.content for item in prepared.components if item.name == "stdin_prompt"
    ) == bundle.canonical_json
    assert all(
        role_file not in canonical_request["assignment"]
        for role_file in ("AGENTS.md", "CLAUDE.md", "CODEX.md")
    )
    assert "Structured artifact authority" not in canonical_request["assignment"]
    adapter.cleanup()


def test_native_codex_extracts_only_bound_result(tmp_path: Path) -> None:
    adapter = NativeCodexAdapter(_settings("codex"))
    bundle = _codex_bundle()
    repository = tmp_path / "repository"
    execution = tmp_path / "execution"
    assets = tmp_path / "assets"
    repository.mkdir()
    execution.mkdir()
    assets.mkdir()
    adapter.prepare_native_provider_input(
        bundle,
        NativeCodexExecutionBoundary.canary(
            repository, execution_root=execution, evidence_asset_root=assets
        ),
    )
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "request_id": bundle.bound_context.request_id,
        "result_type": "plan_result",
        "ready": True,
        "slice_plan": [{
            "slice_id": 1,
            "summary": "Plan",
            "scope_paths": ["docs/internal/plan.md"],
            "acceptance_criteria": [{
                "text": "The reviewed plan artifact is complete.",
                "measured_against": "SOURCE",
            }],
        }],
        "finding_dispositions": [],
    }
    assert adapter._last_message_file is not None
    adapter._last_message_file.write_text(json.dumps({"result": document}), encoding="utf-8")
    assert json.loads(adapter.extract_output("", "", {}))["request_id"] == bundle.bound_context.request_id
    document["request_id"] = "native-codex-request-" + "0" * 64
    adapter._last_message_file.write_text(json.dumps({"result": document}), encoding="utf-8")
    with pytest.raises(AgentOutputError, match="request_id"):
        adapter.extract_output("", "", {})
    adapter.cleanup()


def test_native_claude_workspace_binding_and_capability_smoke(tmp_path: Path) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    harness = source / "src/review_harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("# harness\n", encoding="utf-8")
    adapter = NativeClaudeReviewAdapter(_settings("claude"), review_harness=harness)
    adapter.bind_reviewer_workspace(source, snapshot)
    command, use_stdin = adapter.build_capability_smoke_command(
        "Probe.", test_command="python3 -m pytest tests/ -v"
    )
    assert use_stdin is False
    assert str(snapshot / "src/review_harness.py") in " ".join(command)
    assert "--json-schema" in command
    assert NativeClaudeReviewAdapter.required_hosts == ("api.anthropic.com",)
    adapter.cleanup()


def test_native_claude_prepares_request_components_and_bound_output() -> None:
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    bundle = _review_bundle()
    prepared = adapter.prepare_native_provider_input(bundle)
    names = {item.name for item in prepared.components}
    assert {"packet_manifest", "system_policy", "response_schema", "start_directive"} <= names
    assert "--json-schema" in prepared.command
    result = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness",
            "largest_residual_risk": "provider schema drift",
            "break_condition": "binding changes",
        },
        "pre_mortem": "A future schema drift bypasses the writer.",
    }
    envelope = {"is_error": False, "permission_denials": [], "structured_output": {"result": result}}
    assert json.loads(adapter.extract_output(json.dumps(envelope), "", {}))["decision"] == "approved"
    adapter.cleanup()


def test_native_claude_boundary_notice_allows_reading_repository_paths() -> None:
    original = _review_bundle()
    context = original.bound_context.context
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect current files.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "diff",
                    PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND,
                    '{"evidence_complete":false}',
                ),
            ),
        )
    )
    adapter = NativeClaudeReviewAdapter(_settings("claude"))

    prepared = adapter.prepare_native_provider_input(bundle)
    directive = next(
        item.content for item in prepared.components if item.name == "start_directive"
    )

    assert "additional Read calls for repository paths" in directive
    assert "current read-only repository snapshot" in directive
    assert "Read calls total" not in directive
    adapter.cleanup()


def test_reviewer_receives_large_single_line_evidence_in_bound_parts() -> None:
    content = "".join(str(index % 10) for index in range(104_551))
    original = _review_bundle()
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=original.bound_context.context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect all evidence.",),
            evidence=(NativeReviewEvidenceInput("large_diff", "diff", content),),
        )
    )
    binding = bundle.document["evidence_manifest"][0]
    assert binding["delivery"] == "content_ref"
    assert binding["byte_count"] == len(content.encode("utf-8"))
    assert binding["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()

    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    prepared = adapter.prepare_native_provider_input(bundle)
    manifest = adapter._review_manifest_file
    assert manifest is not None
    manifest_text = manifest.read_text(encoding="utf-8")
    assert "concatenate its numbered parts without separators" in manifest_text
    assert "full-content sha256 and byte_count" in manifest_text
    assert binding["content_ref"] in manifest_text

    rows = [line for line in manifest_text.splitlines() if line.startswith("- `")]
    request_rows = [line for line in rows if "component=request_chunk_" in line]
    evidence_rows = [line for line in rows if "component=evidence_asset_" in line]
    assert len(evidence_rows) == 5
    assert [int(row.split(" | part=")[1].split(" | ")[0]) for row in evidence_rows] == list(range(1, 6))
    assert all(f"content_ref={binding['content_ref']}" in row for row in evidence_rows)

    delivered_paths = [Path(row.split("`")[1]) for row in rows]
    assert delivered_paths[:len(request_rows)] == list(adapter._review_packet_files)
    assert delivered_paths[len(request_rows):] == list(adapter._native_evidence_files)
    assert all(
        len(path.read_text(encoding="utf-8")) <= REVIEW_PACKET_CHUNK_CHARS
        for path in (manifest, *delivered_paths)
    )
    for row, path in zip(rows, delivered_paths, strict=True):
        data = path.read_bytes()
        assert f"bytes={len(data)}" in row
        assert f"sha256={hashlib.sha256(data).hexdigest()}" in row
    reconstructed = b"".join(path.read_bytes() for path in adapter._native_evidence_files)
    assert reconstructed == content.encode("utf-8")
    assert len(reconstructed) == binding["byte_count"]
    assert hashlib.sha256(reconstructed).hexdigest() == binding["sha256"]
    directive = next(item.content for item in prepared.components if item.name == "start_directive")
    assert f"({1 + len(rows)} Read calls total)" in directive
    adapter.cleanup()


def test_reviewer_evidence_unicode_boundary_is_byte_exact() -> None:
    content = "a" * 23_999 + "ä" + "b" * 55
    original = _review_bundle()
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=original.bound_context.context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect all evidence.",),
            evidence=(NativeReviewEvidenceInput("utf8_diff", "diff", content),),
        )
    )
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    adapter.prepare_native_provider_input(bundle)
    assert len(adapter._native_evidence_files) == 2
    assert b"".join(path.read_bytes() for path in adapter._native_evidence_files) == content.encode("utf-8")
    adapter.cleanup()


def test_reviewer_keeps_evidence_at_inline_limit_inline() -> None:
    content = "i" * 24_000
    original = _review_bundle()
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=original.bound_context.context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect all evidence.",),
            evidence=(NativeReviewEvidenceInput("inline_diff", "diff", content),),
        )
    )
    assert bundle.document["evidence_manifest"][0]["delivery"] == "inline"
    assert bundle.document["evidence_manifest"][0]["content"] == content
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    adapter.prepare_native_provider_input(bundle)
    assert adapter._native_evidence_files == ()
    assert all(
        len(path.read_text(encoding="utf-8")) <= REVIEW_PACKET_CHUNK_CHARS
        for path in (adapter._review_manifest_file, *adapter._review_packet_files)
    )
    adapter.cleanup()


def test_review_manifest_pages_remain_bounded_and_ordered(tmp_path: Path) -> None:
    entries = [
        (
            f"evidence_asset_{index:03d}",
            tmp_path / f"evidence-{index:03d}.part",
            hashlib.sha256(str(index).encode()).hexdigest(),
            24_000,
            "evidence/large.txt",
            index,
        )
        for index in range(1, 201)
    ]
    manifest, pages = _write_review_manifest(tmp_path, entries)
    assert len(pages) > 1
    assert all(
        len(path.read_text(encoding="utf-8")) <= REVIEW_PACKET_CHUNK_CHARS
        for path in (manifest, *pages)
    )
    index_text = manifest.read_text(encoding="utf-8")
    assert "each manifest page once in order" in index_text
    for page in pages:
        data = page.read_bytes()
        assert f"- `{page}` | bytes={len(data)} | sha256={hashlib.sha256(data).hexdigest()}" in index_text
    rows = [
        line
        for page in pages
        for line in page.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `")
    ]
    assert len(rows) == len(entries)
    assert [int(row.split(" | part=")[1].split(" | ")[0]) for row in rows] == list(range(1, 201))


def test_reviewer_budget_includes_manifest_pages_and_all_evidence_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_adapters.REVIEW_PACKET_CHUNK_CHARS", 5_000)
    content = "z" * 240_001
    original = _review_bundle()
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=original.bound_context.context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect all evidence.",),
            evidence=(NativeReviewEvidenceInput("e" * 180, "diff", content),),
        )
    )
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    prepared = adapter.prepare_native_provider_input(bundle)
    manifest = adapter._review_manifest_file
    assert manifest is not None
    index_text = manifest.read_text(encoding="utf-8")
    pages = [
        Path(line.split("`")[1])
        for line in index_text.splitlines() if line.startswith("- `")
    ]
    assert len(pages) > 1
    listed = [
        Path(line.split("`")[1])
        for page in pages
        for line in page.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `")
    ]
    assert listed == [*adapter._review_packet_files, *adapter._native_evidence_files]
    assert all(len(path.read_text(encoding="utf-8")) <= 5_000 for path in (manifest, *pages, *listed))
    assert b"".join(path.read_bytes() for path in adapter._native_evidence_files) == content.encode("utf-8")
    directive = next(item.content for item in prepared.components if item.name == "start_directive")
    assert f"({1 + len(pages) + len(listed)} Read calls total)" in directive
    adapter.cleanup()


@pytest.mark.parametrize(
    ("chunk_size", "content_size", "has_manifest_pages"),
    [(24_000, 104_551, False), (5_000, 240_001, True)],
)
def test_reviewer_packet_components_stable_across_runtime_dirs(
    monkeypatch: pytest.MonkeyPatch,
    chunk_size: int,
    content_size: int,
    has_manifest_pages: bool,
) -> None:
    monkeypatch.setattr("agent_adapters.REVIEW_PACKET_CHUNK_CHARS", chunk_size)
    original = _review_bundle()
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=original.bound_context.context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/workflow.py",),
            acceptance_criteria=("Inspect all evidence.",),
            evidence=(NativeReviewEvidenceInput("e" * 180, "diff", "z" * content_size),),
        )
    )
    adapters = [NativeClaudeReviewAdapter(_settings("claude")) for _ in range(2)]
    try:
        prepared = [adapter.prepare_native_provider_input(bundle) for adapter in adapters]
        manifests = [adapter._review_manifest_file for adapter in adapters]
        assert all(manifest is not None for manifest in manifests)
        assert manifests[0].parent != manifests[1].parent

        for manifest in manifests:
            index_rows = [
                line for line in manifest.read_text(encoding="utf-8").splitlines()
                if line.startswith("- `")
            ]
            pages = [Path(row.split("`")[1]) for row in index_rows] if has_manifest_pages else []
            assert bool(pages) is has_manifest_pages
            rows = index_rows + [
                line for page in pages
                for line in page.read_text(encoding="utf-8").splitlines()
                if line.startswith("- `")
            ]
            for row in rows:
                path = Path(row.split("`")[1])
                data = path.read_bytes()
                assert f"bytes={len(data)}" in row
                assert f"sha256={hashlib.sha256(data).hexdigest()}" in row

        assert prepared[0].components == prepared[1].components
        measured = {item.name: item.content for item in prepared[0].components}
        measured_index = measured["packet_manifest"]
        for name, content in measured.items():
            if name.startswith("packet_chunk_"):
                assert hashlib.sha256(content.encode("utf-8")).hexdigest() in measured_index
        digests = [
            measure_provider_input(
                item,
                provider="claude",
                role="claude",
                operation="claude_slice_review",
                binding_fingerprint="c" * 64,
                policy=default_provider_input_budget_policy(),
            ).input_digest
            for item in prepared
        ]
        assert digests[0] == digests[1]
    finally:
        for adapter in adapters:
            adapter.cleanup()
