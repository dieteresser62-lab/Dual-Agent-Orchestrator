from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from agent_adapters import (
    AgentBudgetError,
    AgentOutputError,
    AgentPermissionError,
    AntigravityAdapter,
    ANTIGRAVITY_REVIEW_RESPONSE_MAX_CHARS,
    CLAUDE_REVIEW_PACKET_CHUNK_CHARS,
    ClaudeAdapter,
    CodexAdapter,
    NativeCodexAdapter,
    NativeClaudeReviewAdapter,
    build_agent_registry,
)
from agent_config import AgentSettings
from contracts import (
    AgentRole,
    ApprovalMarker,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from native_review_contract import NativeReviewContext
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestSpec,
    build_native_review_request,
)
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind
from native_codex_request import (
    NativeCodexEvidenceInput,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from contracts import CodexStepContract, ReadinessMarker
from provider_input_budget import default_provider_input_budget_policy, measure_provider_input


def _settings(
    role: str,
    *,
    binary: str | None = None,
    model: str = "model-id",
    effort: str = "medium",
    timeout: int = 123,
    budget: float | None = None,
) -> AgentSettings:
    return AgentSettings(
        name=role,
        binary=binary or role,
        model=model,
        timeout_seconds=timeout,
        effort=effort,
        max_budget_usd=budget,
    )


def _native_bundle(
    *, large: bool = False, anchor_origin: str | None = "plan"
):  # type: ignore[no-untyped-def]
    fingerprint = "a" * 64
    command = "python3 -m pytest tests/ -v"
    attestation = ValidationAttestation(
        attestation_id="validation-native-adapter",
        diff_fingerprint=fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0, "passed"),),
        output_digest=hashlib.sha256(b"passed").hexdigest(),
        summary="1 passed",
        command_specs=(
            ValidationCommandSpec(argv=("python3", "-m", "pytest", "tests/", "-v")),
        ),
    )
    context = NativeReviewContext(
        run_id="run-native-adapter",
        work_unit_id="work-unit-1",
        operation="claude_slice_review",
        diff_fingerprint=fingerprint,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=attestation,
        anchor_origin=anchor_origin,
    )
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/native_review_request.py",),
            acceptance_criteria=("Native output has no marker text.",),
            evidence=(
                NativeReviewEvidenceInput(
                    "current_diff", "diff", "x" * (25_000 if large else 20)
                ),
            ),
        )
    )


def _native_codex_bundle():  # type: ignore[no-untyped-def]
    contract = CodexStepContract(
        name="native-codex-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    context = NativeCodexContext(
        run_id="run-native-codex",
        work_unit_id="work-unit-1",
        operation="codex_plan",
        current_fingerprint="c" * 64,
        request_kind=NativeCodexRequestKind.PLAN,
        contract=contract,
    )
    return build_native_codex_request(
        NativeCodexRequestSpec(
            context=context,
            target_branch="feature/native-codex",
            base_commit="d" * 40,
            authorized_paths=("docs/internal/plan.md",),
            assignment="Create the approved work plan.",
            work_context="Repository-grounded context.",
            evidence=(NativeCodexEvidenceInput("e01_plan", "plan", "plan body"),),
        )
    )


def test_registry_contains_exact_role_identities() -> None:
    registry = build_agent_registry(
        {
            "codex": _settings("codex"),
            "claude": _settings("claude"),
            "antigravity": _settings("antigravity", binary="agy"),
        }
    )

    assert set(registry) == {"codex", "claude", "antigravity"}
    assert "gemini" not in registry


def test_codex_command_is_configured_workspace_write_jsonl_and_stdin() -> None:
    adapter = CodexAdapter(
        _settings("codex", binary="/opt/codex", model="gpt-model", effort="high")
    )
    command, use_stdin = adapter.build_command("secret long prompt")
    message_path = Path(command[command.index("--output-last-message") + 1])

    try:
        assert command[0] == "/opt/codex"
        assert command[1] == "exec"
        assert command[command.index("--model") + 1] == "gpt-model"
        assert 'model_reasoning_effort="high"' in command
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        assert "--ephemeral" in command
        assert "--json" in command
        assert command[-1] == "-"
        assert "secret long prompt" not in command
        assert use_stdin is True
        assert message_path.parent.exists()
    finally:
        adapter.cleanup()

    assert not message_path.parent.exists()


def test_prepared_codex_input_uses_the_exact_stdin_prompt() -> None:
    adapter = CodexAdapter(_settings("codex"))
    prepared = adapter.prepare_provider_input("full secret prompt €")
    try:
        assert prepared.stdin_text == "full secret prompt €"
        assert [(item.name, item.content) for item in prepared.components] == [
            ("stdin_prompt", "full secret prompt €")
        ]
    finally:
        adapter.cleanup()


def test_native_codex_adapter_uses_exact_request_and_output_schema() -> None:
    bundle = _native_codex_bundle()
    adapter = NativeCodexAdapter(
        _settings("codex", binary="/opt/codex", model="gpt-model", effort="high")
    )
    prepared = adapter.prepare_native_provider_input(bundle)
    schema_path = Path(
        prepared.command[prepared.command.index("--output-schema") + 1]
    )
    message_path = Path(
        prepared.command[prepared.command.index("--output-last-message") + 1]
    )
    try:
        assert prepared.stdin_text == bundle.canonical_json
        assert prepared.command[0] == "/opt/codex"
        assert prepared.command[-1] == "-"
        assert prepared.command[prepared.command.index("--sandbox") + 1] == (
            "workspace-write"
        )
        assert json.loads(schema_path.read_text(encoding="utf-8"))["type"] == "object"
        by_name = {item.name: item.content for item in prepared.components}
        assert by_name["stdin_prompt"] == bundle.canonical_json
        assert json.loads(by_name["response_schema"])["required"] == ["result"]
        response = {
            "schema_version": "native-agent-codex-result-v1",
            "result_type": "plan_result",
            "request_id": bundle.bound_context.request_id,
            "ready": True,
            "slice_plan": [
                {
                    "slice_id": 1,
                    "summary": "Implement it.",
                    "scope_paths": ["src/native_codex_contract.py"],
                }
            ],
        }
        message_path.write_text(
            json.dumps({"result": response}, indent=2), encoding="utf-8"
        )
        output = adapter.extract_output("ignored jsonl", "", {})
        assert json.loads(output) == response
        assert output == json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    finally:
        runtime_dir = schema_path.parent
        adapter.cleanup()
    assert not runtime_dir.exists()


def test_native_codex_adapter_rejects_wrappers_and_wrong_request() -> None:
    bundle = _native_codex_bundle()
    adapter = NativeCodexAdapter(_settings("codex"))
    prepared = adapter.prepare_native_provider_input(bundle)
    message_path = Path(
        prepared.command[prepared.command.index("--output-last-message") + 1]
    )
    try:
        message_path.write_text("```json\n{}\n```", encoding="utf-8")
        with pytest.raises(AgentOutputError, match="not valid JSON"):
            adapter.extract_output("", "", {})
        message_path.write_text(
            json.dumps(
                {"result": {
                    "schema_version": "native-agent-codex-result-v1",
                    "result_type": "plan_result",
                    "request_id": "native-codex-request-" + "0" * 64,
                    "ready": True,
                    "slice_plan": [],
                }}
            ),
            encoding="utf-8",
        )
        with pytest.raises(AgentOutputError, match="request_id differs"):
            adapter.extract_output("", "", {})
    finally:
        adapter.cleanup()


def test_codex_prefers_final_message_file_over_jsonl() -> None:
    adapter = CodexAdapter(_settings("codex"))
    command, _ = adapter.build_command("prompt")
    message_path = Path(command[command.index("--output-last-message") + 1])
    message_path.write_text("IMPLEMENTATION_READY: YES\nSTATUS: DONE\n", encoding="utf-8")

    try:
        output = adapter.extract_output('{"message":"noise"}\n', "", {})
    finally:
        adapter.cleanup()

    assert output.endswith("STATUS: DONE")
    assert "noise" not in output


def test_claude_defaults_are_quota_conscious_and_permissions_are_separate() -> None:
    adapter = ClaudeAdapter(
        _settings(
            "claude",
            binary="claude",
            model="sonnet",
            effort="medium",
            budget=1.25,
        )
    )
    command, use_stdin = adapter.build_command("secret long prompt")

    try:
        assert command[command.index("--model") + 1] == "sonnet"
        assert command[command.index("--effort") + 1] == "medium"
        assert command[command.index("--output-format") + 1] == "json"
        assert command[command.index("--tools") + 1] == "Read"
        allowed = command[command.index("--allowedTools") + 1]
        assert allowed == "Read"
        assert command[command.index("--permission-mode") + 1] == "dontAsk"
        assert command[command.index("--setting-sources") + 1] == "user"
        assert command[command.index("--disallowedTools") + 1] == (
            "Bash,Edit,Write,NotebookEdit,Grep,Glob"
        )
        assert "--safe-mode" in command
        assert "--strict-mcp-config" in command
        assert command[command.index("--prompt-suggestions") + 1] == "false"
        assert "--system-prompt" in command
        assert "--append-system-prompt" not in command
        policy = command[command.index("--system-prompt") + 1]
        assert "exactly 2 Read calls" in policy
        assert "do not explore the repository" in policy
        assert "do not run validation commands" in policy
        assert "legacy test snapshot" in policy
        assert "failure paths" in policy
        schema = json.loads(command[command.index("--json-schema") + 1])
        assert schema["properties"]["response"]["maxLength"] == 12_000
        assert command[command.index("--max-budget-usd") + 1] == "1.25"
        assert "--no-session-persistence" in command
        assert "plan" not in command
        assert "opus" not in command
        assert "secret long prompt" not in command
        packet_dir = Path(command[command.index("--add-dir") + 1])
        manifest_path = packet_dir / "review-manifest.md"
        packet_path = packet_dir / "review-packet-001.md"
        assert packet_path.read_text(encoding="utf-8") == "secret long prompt"
        assert str(packet_path) in manifest_path.read_text(encoding="utf-8")
        assert str(manifest_path) in command[-1]
        assert "2 Read calls total" in command[-1]
        assert "Do not run tests or the review harness" in command[-1]
        assert use_stdin is False
    finally:
        adapter.cleanup()

    assert not packet_dir.exists()


def test_prepared_claude_input_is_lossless_and_includes_every_model_channel() -> None:
    adapter = ClaudeAdapter(_settings("claude"))
    prompt = "a" * (CLAUDE_REVIEW_PACKET_CHUNK_CHARS + 17)
    prepared = adapter.prepare_provider_input(prompt)
    try:
        by_name = {item.name: item.content for item in prepared.components}
        chunks = [
            item.content for item in prepared.components if item.name.startswith("packet_chunk_")
        ]
        assert "".join(chunks) == prompt
        assert "packet_manifest" in by_name
        assert "system_policy" in by_name
        assert "response_schema" in by_name
        assert "start_directive" in by_name
        assert prepared.stdin_text is None
    finally:
        adapter.cleanup()


def test_prepared_claude_input_digest_ignores_random_runtime_transport_paths() -> None:
    adapter = ClaudeAdapter(_settings("claude"))
    policy = default_provider_input_budget_policy()
    prompt = "stable review packet\n" * 3

    first = adapter.prepare_provider_input(prompt)
    first_runtime_path = first.command[first.command.index("--add-dir") + 1]
    first_manifest = Path(first_runtime_path, "review-manifest.md").read_text(
        encoding="utf-8"
    )
    first_measurement = measure_provider_input(
        first,
        provider="claude",
        role="claude",
        operation="claude_final_review",
        binding_fingerprint="f" * 64,
        policy=policy,
    )
    second = adapter.prepare_provider_input(prompt)
    second_runtime_path = second.command[second.command.index("--add-dir") + 1]
    second_measurement = measure_provider_input(
        second,
        provider="claude",
        role="claude",
        operation="claude_final_review",
        binding_fingerprint="f" * 64,
        policy=policy,
    )
    try:
        assert first_runtime_path != second_runtime_path
        assert first.components == second.components
        assert first_measurement.input_digest == second_measurement.input_digest
        first_by_name = {
            component.name: component.content for component in first.components
        }
        assert len(first_by_name["packet_manifest"]) == len(first_manifest)
        assert len(first_by_name["start_directive"]) == len(first.command[-1])
        assert all(
            first_runtime_path not in component.content
            for component in first.components
        )
        assert all(
            second_runtime_path not in component.content
            for component in second.components
        )
    finally:
        adapter.cleanup()


def test_native_claude_adapter_is_separate_and_measures_all_request_channels() -> None:
    legacy = ClaudeAdapter(_settings("claude"))
    adapter = legacy.native_review_adapter()
    assert isinstance(adapter, NativeClaudeReviewAdapter)
    assert adapter is not legacy
    bundle = _native_bundle(large=True)
    prepared = adapter.prepare_native_provider_input(bundle)
    try:
        by_name = {item.name: item.content for item in prepared.components}
        assert prepared.components[0].content == bundle.canonical_json
        assert prepared.components[1].content == bundle.evidence_assets[0].content
        assert "packet_manifest" in by_name
        assert "system_policy" in by_name
        assert "response_schema" in by_name
        assert "start_directive" in by_name
        schema = json.loads(by_name["response_schema"])
        assert schema["type"] == "object"
        assert "oneOf" not in schema
        assert "allOf" not in schema
        assert "anyOf" not in schema
        assert "response" not in schema.get("properties", {})
        assert schema["required"] == ["result"]
        assert schema["properties"]["result"]["oneOf"] == [
            {"$ref": "#/$defs/review_result"},
            {"$ref": "#/$defs/stop_request"},
        ]
        assert schema["$defs"]["common"]["properties"]["reviewer"] == {
            "const": "claude"
        }
        assert schema["$defs"]["finding"]["properties"]["finding_id"][
            "pattern"
        ].startswith("^C-")
        assert schema["$defs"]["review_result"]["allOf"][1]["properties"][
            "anchors"
        ]["maxItems"] == 64
        assert "$schema" not in schema
        assert "$id" not in schema
        assert hashlib.sha256(
            by_name["response_schema"].encode("utf-8")
        ).hexdigest() == bundle.document["response_contract"]["schema_sha256"]
        assert "STATUS: DONE" not in by_name["start_directive"]
        assert prepared.stdin_text is None
    finally:
        adapter.cleanup()


def test_native_claude_adapter_forbids_anchors_without_bound_origin() -> None:
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    bundle = _native_bundle(anchor_origin=None)
    prepared = adapter.prepare_native_provider_input(bundle)
    try:
        by_name = {item.name: item.content for item in prepared.components}
        schema = json.loads(by_name["response_schema"])
        assert schema["$defs"]["review_result"]["allOf"][1]["properties"][
            "anchors"
        ]["maxItems"] == 0
        assert hashlib.sha256(
            by_name["response_schema"].encode("utf-8")
        ).hexdigest() == bundle.document["response_contract"]["schema_sha256"]
    finally:
        adapter.cleanup()


def test_native_claude_measurement_names_evidence_assets_separately() -> None:
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    prepared = adapter.prepare_native_provider_input(_native_bundle(large=True))
    try:
        component_names = {item.name for item in prepared.components}
        assert "request_chunk_001" in component_names
        assert "evidence_asset_001" in component_names
        assert not any(name.startswith("packet_chunk_") for name in component_names)
    finally:
        adapter.cleanup()


def test_native_claude_input_digest_ignores_random_runtime_paths() -> None:
    bundle = _native_bundle(large=True)
    first_adapter = NativeClaudeReviewAdapter(_settings("claude"))
    second_adapter = NativeClaudeReviewAdapter(_settings("claude"))
    first = first_adapter.prepare_native_provider_input(bundle)
    second = second_adapter.prepare_native_provider_input(bundle)
    policy = default_provider_input_budget_policy()
    try:
        assert first.components == second.components
        first_measurement = measure_provider_input(
            first,
            provider="claude",
            role="claude",
            operation="claude_slice_review",
            binding_fingerprint="a" * 64,
            policy=policy,
        )
        second_measurement = measure_provider_input(
            second,
            provider="claude",
            role="claude",
            operation="claude_slice_review",
            binding_fingerprint="a" * 64,
            policy=policy,
        )
        assert first_measurement.input_digest == second_measurement.input_digest
    finally:
        first_adapter.cleanup()
        second_adapter.cleanup()


def test_native_claude_adapter_requires_explicit_settings() -> None:
    with pytest.raises(TypeError, match="explicit Claude AgentSettings"):
        NativeClaudeReviewAdapter()
    with pytest.raises(TypeError, match="explicit Claude AgentSettings"):
        NativeClaudeReviewAdapter(None)


def test_native_claude_extracts_only_complete_structured_result() -> None:
    adapter = NativeClaudeReviewAdapter(_settings("claude"))
    bundle = _native_bundle()
    adapter.prepare_native_provider_input(bundle)
    response = {
        "schema_version": "native-agent-review-result-v1",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness, failure paths, resume",
            "largest_residual_risk": "pilot integration",
            "break_condition": "text marker fallback is used",
        },
        "pre_mortem": "Recovery could rebuild another request.",
    }
    envelope = json.dumps(
        {
            "is_error": False,
            "structured_output": {"result": response},
            "permission_denials": [],
        }
    )
    try:
        assert json.loads(adapter.extract_output(envelope, "", {})) == response
        with pytest.raises(AgentOutputError):
            adapter.extract_output(
                json.dumps(
                    {
                        "is_error": False,
                        "structured_output": {"response": json.dumps(response)},
                        "permission_denials": [],
                    }
                ),
                "",
                {},
            )
    finally:
        adapter.cleanup()


def test_prepared_antigravity_input_includes_file_schema_and_directive() -> None:
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))
    prepared = adapter.prepare_provider_input("complete review request")
    try:
        by_name = {item.name: item.content for item in prepared.components}
        assert by_name["prompt_file"] == "complete review request"
        assert json.loads(by_name["response_schema"])["required"] == ["response"]
        assert "Read the complete request" in by_name["start_directive"]
        assert prepared.stdin_text is None
    finally:
        adapter.cleanup()


def test_claude_json_envelope_tracks_usage_and_rejects_permission_denials() -> None:
    adapter = ClaudeAdapter(_settings("claude"))
    success = json.dumps(
        {
            "is_error": False,
            "result": "OPEN_FINDINGS: NONE\nSTATUS: DONE\ntrailing chatter",
            "num_turns": 2,
            "usage": {"output_tokens": 10},
            "permission_denials": [],
        }
    )

    assert adapter.extract_output(success, "", {}).endswith("STATUS: DONE")
    assert adapter.metadata["num_turns"] == 2

    marker_discussion = json.dumps(
        {
            "is_error": False,
            "result": (
                "The parser must preserve the literal `STATUS: DONE` when discussed.\n"
                "PHASE2_APPROVAL: YES\n"
                "OPEN_FINDINGS: NONE\n"
                "STATUS: DONE\n"
                "trailing chatter"
            ),
            "permission_denials": [],
        }
    )
    marker_output = adapter.extract_output(marker_discussion, "", {})
    assert "PHASE2_APPROVAL: YES" in marker_output
    assert marker_output.endswith("STATUS: DONE")
    assert "trailing chatter" not in marker_output

    structured = json.dumps(
        {
            "is_error": False,
            "result": "",
            "structured_output": {
                "response": "OPEN_FINDINGS: NONE\nSTATUS: DONE\ntrailing chatter"
            },
            "permission_denials": [],
        }
    )
    assert adapter.extract_output(structured, "", {}).endswith("STATUS: DONE")

    claude_2_1_227_wrapped = json.dumps(
        {
            "is_error": False,
            "result": "",
            "structured_output": {
                "response": (
                    "REVIEWER: claude\n"
                    "REVIEW_EVIDENCE: scope | risk | break\n"
                    "PRE_MORTEM: drift\n"
                    "PLAN_APPROVAL: YES\n"
                    "STATUS: DONE</response>\n</invoke>\n"
                )
            },
            "permission_denials": [],
        }
    )
    normalized = adapter.extract_output(claude_2_1_227_wrapped, "", {})
    assert normalized.endswith("STATUS: DONE")
    assert "</response>" not in normalized
    assert "</invoke>" not in normalized

    encoded_result = json.dumps(
        {
            "is_error": False,
            "result": json.dumps(
                {"response": "OPEN_FINDINGS: NONE\nSTATUS: DONE"}
            ),
            "permission_denials": [],
        }
    )
    assert adapter.extract_output(encoded_result, "", {}).endswith("STATUS: DONE")

    object_result = json.dumps(
        {
            "is_error": False,
            "result": {"response": "OPEN_FINDINGS: NONE\nSTATUS: DONE"},
            "permission_denials": [],
        }
    )
    assert adapter.extract_output(object_result, "", {}).endswith("STATUS: DONE")

    denied = json.dumps(
        {
            "is_error": False,
            "result": "STATUS: DONE",
            "permission_denials": [{"tool_name": "Bash"}],
        }
    )
    with pytest.raises(AgentPermissionError, match="non-allowlisted tool"):
        adapter.extract_output(denied, "", {})

    failed = json.dumps({"is_error": True, "result": "Execution error"})
    with pytest.raises(AgentOutputError, match="is_error=true"):
        adapter.extract_output(failed, "", {})

    budget_stopped = json.dumps(
        {"is_error": True, "subtype": "error_max_budget_usd", "result": ""}
    )
    with pytest.raises(AgentBudgetError, match="budget guard"):
        adapter.extract_output(budget_stopped, "", {})
    assert adapter.metadata["subtype"] == "error_max_budget_usd"

    with pytest.raises(AgentOutputError, match="invalid JSON"):
        adapter.extract_output('{"type":"event"}\n{"type":"result"}', "", {})


def test_normal_claude_review_does_not_expose_bound_harness() -> None:
    adapter = ClaudeAdapter(_settings("claude"))
    command, _ = adapter.build_command("prompt")

    try:
        allowed = command[command.index("--allowedTools") + 1]
        assert allowed == "Read"
        assert all("review_harness.py" not in part for part in command)
    finally:
        adapter.cleanup()


def test_capability_smoke_is_explicit_and_binds_harness_into_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    harness = source / "src" / "review_harness.py"
    harness.parent.mkdir(parents=True)
    snapshot.mkdir()

    claude = ClaudeAdapter(_settings("claude"), review_harness=harness)
    claude.bind_reviewer_workspace(source, snapshot)
    claude_command, _ = claude.build_capability_smoke_command(
        "diagnose", test_command="python3 -m pytest tests/ -v"
    )
    try:
        bound_harness = str(snapshot / "src" / "review_harness.py")
        assert claude_command[claude_command.index("--tools") + 1] == "Bash,Read"
        allowed = claude_command[claude_command.index("--allowedTools") + 1]
        assert bound_harness in allowed
        assert "python3 -m pytest tests/ -v" in allowed
        assert "capability diagnostic" in claude_command[-1]
    finally:
        claude.cleanup()

    antigravity = AntigravityAdapter(
        _settings("antigravity", binary="agy"), review_harness=harness
    )
    antigravity.bind_reviewer_workspace(source, snapshot)
    antigravity_command, _ = antigravity.build_capability_smoke_command(
        "diagnose", test_command="python3 -m pytest tests/ -v"
    )
    try:
        assert str(snapshot / "src" / "review_harness.py") in antigravity_command[-1]
        assert "python3 -m pytest tests/ -v" in antigravity_command[-1]
        assert "capability diagnostic" in antigravity_command[-1]
    finally:
        antigravity.cleanup()


def test_claude_review_packet_is_losslessly_chunked_with_dynamic_read_budget() -> None:
    prompt = ("line\n" * 9_000) + ("x" * 30_000)
    adapter = ClaudeAdapter(_settings("claude"))
    command, _ = adapter.build_command(prompt)

    try:
        packet_dir = Path(command[command.index("--add-dir") + 1])
        chunks = sorted(packet_dir.glob("review-packet-*.md"))
        assert len(chunks) >= 4
        assert "".join(path.read_text(encoding="utf-8") for path in chunks) == prompt
        assert all(len(path.read_text(encoding="utf-8")) <= 24_000 for path in chunks)
        manifest = (packet_dir / "review-manifest.md").read_text(encoding="utf-8")
        assert all(str(path) in manifest for path in chunks)
        expected_calls = len(chunks) + 1
        policy = command[command.index("--system-prompt") + 1]
        assert f"exactly {expected_calls} Read calls" in policy
        assert f"{expected_calls} Read calls total" in command[-1]
        assert str(packet_dir / "review-manifest.md") in command[-1]
    finally:
        adapter.cleanup()


def test_claude_review_chunk_bound_is_exclusive_at_newline_edge() -> None:
    prompt = ("x" * CLAUDE_REVIEW_PACKET_CHUNK_CHARS) + "\nremainder"
    adapter = ClaudeAdapter(_settings("claude"))
    command, _ = adapter.build_command(prompt)

    try:
        packet_dir = Path(command[command.index("--add-dir") + 1])
        chunks = sorted(packet_dir.glob("review-packet-*.md"))
        contents = [path.read_text(encoding="utf-8") for path in chunks]
        assert "".join(contents) == prompt
        assert all(
            len(chunk) <= CLAUDE_REVIEW_PACKET_CHUNK_CHARS for chunk in contents
        )
    finally:
        adapter.cleanup()


def test_antigravity_print_is_last_option_and_long_prompt_is_file_backed() -> None:
    long_prompt = "PRIVATE-PROMPT-" + ("x" * 100_000)
    adapter = AntigravityAdapter(
        _settings(
            "antigravity",
            binary="agy.exe",
            model="gemini-model",
            effort="high",
            timeout=900,
        )
    )
    command, use_stdin = adapter.build_command(long_prompt)
    print_index = command.index("--print")
    add_dir = Path(command[command.index("--add-dir") + 1])
    prompt_path = add_dir / "review-prompt.md"

    try:
        assert command[0] == "agy.exe"
        assert command[command.index("--model") + 1] == "gemini-model"
        assert command[command.index("--effort") + 1] == "high"
        assert command[command.index("--output-format") + 1] == "json"
        schema = json.loads(command[command.index("--json-schema") + 1])
        assert schema["properties"]["response"]["maxLength"] == (
            ANTIGRAVITY_REVIEW_RESPONSE_MAX_CHARS
        )
        assert command[command.index("--print-timeout") + 1] == "900s"
        assert "--sandbox" in command
        assert "--dangerously-skip-permissions" in command
        assert "--mode" not in command
        assert print_index == len(command) - 2
        assert long_prompt not in command
        assert prompt_path.name in command[-1]
        assert str(prompt_path.parent) not in command[-1]
        assert prompt_path.read_text(encoding="utf-8") == long_prompt
        assert use_stdin is False
    finally:
        adapter.cleanup()

    assert not add_dir.exists()


def test_antigravity_exposes_bound_snapshot_as_repository_search_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot" / "repo"
    source.mkdir()
    snapshot.mkdir(parents=True)
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))
    adapter.bind_reviewer_workspace(source, snapshot)
    command, use_stdin = adapter.build_command("review request")
    add_dirs = [
        Path(command[index + 1])
        for index, value in enumerate(command)
        if value == "--add-dir"
    ]
    runtime_dir = add_dirs[0]

    try:
        assert add_dirs == [runtime_dir, snapshot.resolve()]
        assert runtime_dir != snapshot.resolve()
        assert (runtime_dir / "review-prompt.md").is_file()
        assert (
            "Use the supplied read-only repository working directory for every "
            "repository-relative search or read."
        ) in command[-1]
        assert str(snapshot.resolve()) not in command[-1]
        assert use_stdin is False
    finally:
        adapter.cleanup()

    assert not runtime_dir.exists()


def test_antigravity_json_envelope_requires_success_and_trims_chatter() -> None:
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))
    output = adapter.extract_output(
        json.dumps(
            {
                "status": "SUCCESS",
                "response": "SLICE_APPROVAL: 03 | YES\nSTATUS: DONE\nextra",
                "num_turns": 1,
            }
        ),
        "",
        {},
    )

    assert output.endswith("STATUS: DONE")
    assert "extra" not in output
    assert adapter.metadata["num_turns"] == 1

    for opening_fence in ("```text", "```"):
        fenced = adapter.extract_output(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "response": (
                        f"{opening_fence}\n"
                        "REVIEWER: antigravity\n"
                        "TEST_FILES_TOUCHED: NONE\n"
                        "REVIEW_EVIDENCE: scope | risk | break\n"
                        "PRE_MORTEM: drift\n"
                        "SLICE_APPROVAL: 04 | YES\n"
                        "STATUS: DONE\n"
                        "```"
                    ),
                }
            ),
            "",
            {},
        )
        assert fenced.startswith("REVIEWER: antigravity")
        assert fenced.endswith("STATUS: DONE")
        assert "```" not in fenced

    prefixed_fence = adapter.extract_output(
        json.dumps(
            {
                "status": "SUCCESS",
                "response": (
                    "Unexpected preamble\n"
                    "```text\n"
                    "REVIEWER: antigravity\n"
                    "STATUS: DONE\n"
                    "```"
                ),
            }
        ),
        "",
        {},
    )
    assert prefixed_fence.startswith("Unexpected preamble\n```text")

    with pytest.raises(AgentOutputError, match="non-success") as exc_info:
        adapter.extract_output(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": "temporary network failure",
                    "response": "REVIEWER: antigravity\nnetwork is a residual risk",
                }
            ),
            "",
            {},
        )
    assert exc_info.value.provider_text == "temporary network failure"
    assert "response" not in (exc_info.value.provider_data or {})

    with pytest.raises(AgentOutputError) as schema_exc:
        adapter.extract_output(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": "additional properties 'LineNumber' not allowed",
                    "duration_seconds": 1.25,
                    "num_turns": 2,
                    "usage": {"input_tokens": 11, "output_tokens": 3},
                    "response": "SLICE_APPROVAL: 01 | YES\nSTATUS: DONE",
                    "structured_output": {"response": "NEW_FINDING: A-01"},
                }
            ),
            "",
            {},
        )
    assert schema_exc.value.provider_data == {
        "status": "ERROR",
        "error": "additional properties 'LineNumber' not allowed",
    }
    assert adapter.metadata["duration_seconds"] == 1.25
    assert adapter.metadata["num_turns"] == 2
    assert adapter.metadata["usage"] == {"input_tokens": 11, "output_tokens": 3}

    structured = adapter.extract_output(
        json.dumps(
            {
                "status": "SUCCESS",
                "response": json.dumps(
                    {"response": "REVIEWER: antigravity\nSTATUS: DONE"}
                ),
            }
        ),
        "",
        {},
    )
    assert structured.startswith("REVIEWER: antigravity")


@pytest.mark.parametrize(
    ("response", "preserved"),
    (
        (
            "Here is the corrected output complying with the STATE-V3 CONTRACT:\n"
            "```text\nREVIEWER: antigravity\nSTATUS: DONE\n```",
            "REVIEWER: antigravity",
        ),
        (
            "```text\r\n"
            "REVIEWER: antigravity\r\n"
            "REVIEW_EVIDENCE: scope | risk | break\r\n"
            "PRE_MORTEM: drift\r\n"
            "SLICE_APPROVAL: 04 | YES\r\n"
            "STATUS: DONE\r\n"
            "```",
            "REVIEW_EVIDENCE: scope | risk | break",
        ),
        (
            "```text\nREVIEWER: antigravity\nSTATUS: DONE   \n```",
            "REVIEWER: antigravity",
        ),
        (
            "```text\nREVIEWER: antigravity\nSTATUS: DONE\n\n```",
            "REVIEWER: antigravity",
        ),
        (
            "```TEXT\nreviewer: antigravity\nstatus: done\n```",
            "reviewer: antigravity",
        ),
        (
            "```text\n"
            "REVIEWER: antigravity\n"
            "REVIEW_EVIDENCE: discussed a literal marker\n"
            "STATUS: DONE\n"
            "PRE_MORTEM: the decoy marker must not truncate this line\n"
            "SLICE_APPROVAL: 04 | YES\n"
            "STATUS: DONE\n"
            "```",
            "PRE_MORTEM: the decoy marker must not truncate this line",
        ),
    ),
)
def test_antigravity_unwraps_only_complete_contract_fences(
    response: str,
    preserved: str,
) -> None:
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))

    output = adapter.extract_output(
        json.dumps({"status": "SUCCESS", "response": response}),
        "",
        {},
    )

    assert output.upper().startswith("REVIEWER: ANTIGRAVITY")
    assert output.upper().endswith("STATUS: DONE")
    assert preserved in output
    assert "```" not in output
    assert "\r" not in output


def test_antigravity_does_not_unwrap_malformed_closing_fence() -> None:
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))
    response = (
        "```text\n"
        "REVIEWER: antigravity\n"
        "STATUS: DONE\n"
        "```text"
    )

    output = adapter.extract_output(
        json.dumps({"status": "SUCCESS", "response": response}),
        "",
        {},
    )

    assert output.startswith("```text\nREVIEWER: antigravity")
    assert "```text" in output


@pytest.mark.parametrize(
    "warning",
    [
        "Warning: --mode plan has no effect when --disable-slash-commands is set",
        "Permission request rejected by policy",
    ],
)
def test_incompatible_cli_warnings_are_failures(warning: str) -> None:
    adapter = AntigravityAdapter(_settings("antigravity", binary="agy"))

    with pytest.raises(AgentOutputError):
        adapter.validate_process_output(warning)
