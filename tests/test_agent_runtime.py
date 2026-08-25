from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path

import pytest

import agent_runtime
from agent_adapters import (
    AGENT_REGISTRY,
    AgentBudgetError,
    AgentOutputError,
    AgentPermissionError,
    AntigravityAdapter,
    CapabilitySpec,
    ClaudeAdapter,
    CodexAdapter,
    NativeCodexAdapter,
    NativeCodexExecutionBoundary,
)
from agent_config import AgentSettings
from agent_runtime import (
    AgentCompatibilityError,
    AgentInvocationError,
    OrchestratorConfig,
    ProviderAttemptLifecycle,
    QuotaReachedError,
    check_git_clean,
    collect_file_snapshots,
    classify_agent_failure,
    compute_retry_backoff_seconds,
    create_read_only_reviewer_workspace,
    preflight,
    repo_snapshot,
    run_agent,
    run_agent_checked,
    run_native_review_agent,
    run_native_review_agent_checked,
    run_native_codex_agent,
    run_native_codex_agent_checked,
    NativeAgentCodexOutput,
    run_tests_snapshot,
    run_validation_matrix,
    verify_agent_capabilities,
    _compact_result_lines,
    _compact_stream_text,
    _compact_usage_metadata,
    normalize_provider_usage,
)
from artifact_bridge import ArtifactBridge, provider_input_measurement_payload
from artifact_models import ProviderAttemptPayload
from artifact_store import ArtifactStore
from validation_matrix import ValidationCommand, ValidationRequest
from repo_changes import ChangedPath, RepositoryChanges
from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputBudgetError,
    ProviderInputBudgetExceeded,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy,
)
from workflow_state import AgentFailureKind
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


def _runtime_native_codex_bundle():  # type: ignore[no-untyped-def]
    contract = CodexStepContract(
        name="native-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/plan.md",
    )
    context = NativeCodexContext(
        run_id="run-native-codex-runtime",
        work_unit_id="work-unit-1",
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
            assignment="Create the plan.",
            work_context="Context.",
            evidence=(NativeCodexEvidenceInput("e01_plan", "plan", "body"),),
        )
    )


def test_compute_retry_backoff_seconds_exponential() -> None:
    assert compute_retry_backoff_seconds("generic error", 1) == 2
    assert compute_retry_backoff_seconds("generic error", 2) == 4
    assert compute_retry_backoff_seconds("generic error", 5) == 30


def test_compute_retry_backoff_seconds_rate_limit_floor() -> None:
    assert compute_retry_backoff_seconds("HTTP 429 too many requests", 1) == 10
    assert compute_retry_backoff_seconds("rate limit", 2) == 10
    assert compute_retry_backoff_seconds("rate limit", 5) == 30


def test_run_native_codex_agent_parses_bound_result_without_text_contract(
    monkeypatch,
) -> None:
    bundle = _runtime_native_codex_bundle()
    response = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [],
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Implement native Codex.",
                "scope_paths": ["src/native_codex_contract.py"],
            }
        ],
    }
    canonical = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    captured: dict[str, object] = {}

    class FakeNativeCodex:
        name = "codex"

        def prepare_native_provider_input(  # type: ignore[no-untyped-def]
            self, request_bundle, execution_boundary
        ):
            assert request_bundle is bundle
            assert execution_boundary.sandbox_mode == "workspace-write"
            return PreparedProviderInput(
                command=("codex",),
                stdin_text=bundle.canonical_json,
                components=(
                    ProviderInputComponent("stdin_prompt", bundle.canonical_json),
                ),
            )

    def fake_run_agent(_adapter, prompt, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        captured.update(kwargs)
        return canonical

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    output = run_native_codex_agent(
        FakeNativeCodex(),  # type: ignore[arg-type]
        bundle,
        config=OrchestratorConfig(),
        shorten=lambda value, _maximum: value or "",
        operation="codex_plan",
        binding_fingerprint="a" * 64,
    )
    assert output.result.ready is True
    assert output.result.slice_plan[0].slice_id == 1
    assert output.request_id == bundle.bound_context.request_id
    assert output.response_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert captured["prompt"] == bundle.canonical_json
    assert isinstance(captured["prepared_provider_input"], PreparedProviderInput)


def test_native_codex_exposes_schema_valid_bytes_before_domain_rejection(
    monkeypatch,
) -> None:
    bundle = _runtime_native_codex_bundle()
    response = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [],
        "slice_plan": [
            {
                "slice_id": 2,
                "summary": "Non-contiguous domain-invalid slice.",
                "scope_paths": ["src/native_codex_contract.py"],
            }
        ],
    }
    canonical = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    class FakeNativeCodex:
        name = "codex"

        def prepare_native_provider_input(  # type: ignore[no-untyped-def]
            self, request_bundle, execution_boundary
        ):
            assert execution_boundary.sandbox_mode == "workspace-write"
            return PreparedProviderInput(
                command=("codex",),
                stdin_text=request_bundle.canonical_json,
                components=(
                    ProviderInputComponent(
                        "stdin_prompt", request_bundle.canonical_json
                    ),
                ),
            )

    monkeypatch.setattr(agent_runtime, "run_agent", lambda *args, **kwargs: canonical)
    persisted: list[str] = []
    with pytest.raises(AgentOutputError) as raised:
        run_native_codex_agent(
            FakeNativeCodex(),  # type: ignore[arg-type]
            bundle,
            config=OrchestratorConfig(),
            shorten=lambda value, _maximum: value or "",
            operation="codex_plan",
            binding_fingerprint="a" * 64,
            validated_response_callback=persisted.append,
        )
    assert "slice-plan-invalid" in raised.value.technical_text
    assert persisted == [canonical]


def test_native_codex_writer_invalid_bytes_never_reach_validated_callback(
    monkeypatch,
) -> None:
    bundle = _runtime_native_codex_bundle()
    response = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Reader-valid but writer-incomplete result.",
                "scope_paths": ["src/native_codex_contract.py"],
            }
        ],
    }
    canonical = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    class FakeNativeCodex:
        name = "codex"

        def prepare_native_provider_input(  # type: ignore[no-untyped-def]
            self, request_bundle, execution_boundary
        ):
            return PreparedProviderInput(
                command=("codex",),
                stdin_text=request_bundle.canonical_json,
                components=(
                    ProviderInputComponent(
                        "stdin_prompt", request_bundle.canonical_json
                    ),
                ),
            )

    monkeypatch.setattr(agent_runtime, "run_agent", lambda *args, **kwargs: canonical)
    persisted: list[str] = []
    with pytest.raises(AgentOutputError) as raised:
        run_native_codex_agent(
            FakeNativeCodex(),  # type: ignore[arg-type]
            bundle,
            config=OrchestratorConfig(),
            shorten=lambda value, _maximum: value or "",
            operation="codex_plan",
            binding_fingerprint="a" * 64,
            validated_response_callback=persisted.append,
        )
    assert "schema-invalid" in raised.value.technical_text
    assert persisted == []


def test_native_codex_runtime_forwards_canary_execution_root(
    monkeypatch, tmp_path: Path
) -> None:
    bundle = _runtime_native_codex_bundle()
    repository_root = tmp_path / "repository"
    execution_root = tmp_path / "isolated" / "work"
    evidence_root = tmp_path / "isolated" / "evidence"
    repository_root.mkdir()
    execution_root.mkdir(parents=True)
    evidence_root.mkdir(parents=True)
    boundary = NativeCodexExecutionBoundary.canary(
        repository_root,
        execution_root=execution_root,
        evidence_asset_root=evidence_root,
    )
    response = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [],
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Exercise the isolated runtime.",
                "scope_paths": ["src/native_codex_contract.py"],
            }
        ],
    }
    captured: dict[str, object] = {}

    class FakeNativeCodex:
        name = "codex"

        def prepare_native_provider_input(  # type: ignore[no-untyped-def]
            self, request_bundle, execution_boundary
        ):
            captured["boundary"] = execution_boundary
            return PreparedProviderInput(
                command=("codex",),
                stdin_text=request_bundle.canonical_json,
                components=(
                    ProviderInputComponent(
                        "stdin_prompt", request_bundle.canonical_json
                    ),
                ),
            )

    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return json.dumps(response)

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    run_native_codex_agent(
        FakeNativeCodex(),  # type: ignore[arg-type]
        bundle,
        config=OrchestratorConfig(repo_root=repository_root),
        shorten=lambda value, _maximum: value or "",
        operation="codex_plan",
        binding_fingerprint="a" * 64,
        execution_boundary=boundary,
    )

    assert captured["boundary"] is boundary
    assert captured["execution_root_override"] == execution_root.resolve()


def test_native_codex_checked_writes_raw_before_accepted_callback(
    monkeypatch, tmp_path: Path
) -> None:
    bundle = _runtime_native_codex_bundle()
    result = parse_bound_native_codex_contract_result_for_test(bundle)
    canonical = json.dumps(
        {
            "schema_version": "native-agent-codex-result-v2",
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
            "finding_dispositions": [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    output = NativeAgentCodexOutput(
        result=result,
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    events: list[str] = []

    def fake_native_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["validated_response_callback"](canonical)
        return output

    monkeypatch.setattr(agent_runtime, "run_native_codex_agent", fake_native_run)
    monkeypatch.setattr(agent_runtime, "print_agent_output", lambda *args, **kwargs: None)

    class Adapter:
        name = "codex"
        metadata: dict[str, object] = {}

    raw_path = tmp_path / "raw.json"

    def write_raw(path: Path, content: str) -> None:
        events.append("write")
        path.write_text(content, encoding="utf-8")

    def accept(_output: NativeAgentCodexOutput) -> None:
        assert raw_path.read_text(encoding="utf-8") == canonical
        events.append("accept")

    returned = run_native_codex_agent_checked(
        adapter=Adapter(),  # type: ignore[arg-type]
        bundle=bundle,
        raw_response_path=raw_path,
        config=OrchestratorConfig(),
        write_file=write_raw,
        shorten=lambda value, _maximum: value or "",
        operation="codex_plan",
        binding_fingerprint="a" * 64,
        pre_start_callback=None,
        provider_attempt_lifecycle=None,
        accepted_output_callback=accept,
    )
    assert returned is output
    assert events == ["write", "accept"]


def test_native_codex_checked_write_failure_prevents_callback(
    monkeypatch, tmp_path: Path
) -> None:
    bundle = _runtime_native_codex_bundle()
    result = parse_bound_native_codex_contract_result_for_test(bundle)
    output = NativeAgentCodexOutput(
        result=result,
        canonical_json="{}",
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(b"{}").hexdigest(),
    )
    def fake_native_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["validated_response_callback"]("{}")
        return output

    monkeypatch.setattr(agent_runtime, "run_native_codex_agent", fake_native_run)
    accepted: list[bool] = []

    class Adapter:
        name = "codex"
        metadata: dict[str, object] = {}

    with pytest.raises(AgentInvocationError):
        run_native_codex_agent_checked(
            adapter=Adapter(),  # type: ignore[arg-type]
            bundle=bundle,
            raw_response_path=tmp_path / "raw.json",
            config=OrchestratorConfig(),
            write_file=lambda _path, _content: (_ for _ in ()).throw(OSError("disk")),
            shorten=lambda value, _maximum: value or "",
            operation="codex_plan",
            binding_fingerprint="a" * 64,
            pre_start_callback=None,
            provider_attempt_lifecycle=None,
            accepted_output_callback=lambda _output: accepted.append(True),
        )
    assert accepted == []


def parse_bound_native_codex_contract_result_for_test(bundle):  # type: ignore[no-untyped-def]
    document = {
        "schema_version": "native-agent-codex-result-v2",
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
        "finding_dispositions": [],
    }
    from native_codex_contract import parse_bound_native_codex_contract_result

    return parse_bound_native_codex_contract_result(document, bundle.bound_context)


def test_orchestrator_config_has_no_agent_substitution_state() -> None:
    config = OrchestratorConfig()

    assert not hasattr(config, "allow_fallback_to_gemini")
    assert not hasattr(config, "claude_quota_reached")


def test_native_review_runtime_returns_bound_contract_without_marker_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint = "a" * 64
    command = "python3 -m pytest tests/ -v"
    attestation = ValidationAttestation(
        attestation_id="validation-native-runtime",
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
        run_id="run-native-runtime",
        work_unit_id="work-unit-1",
        operation="claude_slice_review",
        diff_fingerprint=fingerprint,
        reviewer=AgentRole.CLAUDE,
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        validation_attestation=attestation,
        anchor_origin="plan",
    )
    bundle = build_native_review_request(
        NativeReviewRequestSpec(
            context=context,
            review_kind=NativeReviewKind.SLICE,
            target_branch="feature/native",
            base_commit="b" * 40,
            authorized_paths=("src/native_review_request.py",),
            acceptance_criteria=("No marker parser is invoked.",),
            evidence=(NativeReviewEvidenceInput("diff", "diff", "change"),),
        )
    )
    response = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "correctness and resume",
            "largest_residual_risk": "later workflow integration",
            "break_condition": "a text marker is required",
        },
        "pre_mortem": "A later caller could select the legacy path.",
    }
    canonical_response = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    captured: dict[str, object] = {}

    class FakeNativeAdapter:
        def prepare_native_provider_input(self, request_bundle):  # type: ignore[no-untyped-def]
            assert request_bundle is bundle
            return PreparedProviderInput(
                command=("claude",),
                stdin_text=None,
                components=(ProviderInputComponent("stdin_prompt", bundle.canonical_json),),
            )

    returned = {"canonical": canonical_response}

    def fake_run_agent(_adapter, prompt, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        captured.update(kwargs)
        return returned["canonical"]

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    output = run_native_review_agent(
        FakeNativeAdapter(),  # type: ignore[arg-type]
        bundle,
        config=OrchestratorConfig(),
        shorten=lambda value, _maximum: value or "",
        operation="claude_slice_review",
        binding_fingerprint=fingerprint,
    )

    assert output.result.approval is True
    assert output.request_id == bundle.bound_context.request_id
    assert output.canonical_json == canonical_response
    assert captured["prompt"] == bundle.canonical_json
    assert isinstance(captured["prepared_provider_input"], PreparedProviderInput)

    mismatched = {**response, "request_id": "native-review-request-" + "0" * 64}
    returned["canonical"] = json.dumps(
        mismatched, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with pytest.raises(AgentOutputError) as raised:
        run_native_review_agent(
            FakeNativeAdapter(),  # type: ignore[arg-type]
            bundle,
            config=OrchestratorConfig(),
            shorten=lambda value, _maximum: value or "",
            operation="claude_slice_review",
            binding_fingerprint=fingerprint,
        )
    assert "request-mismatch" in raised.value.technical_text
    assert raised.value.provider_data == mismatched

    writer_invalid = {
        **response,
        "new_findings": [
            {
                "finding_id": "C-01",
                "finding_class": "BLOCKER",
                "summary": "Approval still contains an open blocker.",
                "acceptance_test": {
                    "kind": "prose",
                    "text": "Close the blocker before approval.",
                },
            }
        ],
    }
    returned["canonical"] = json.dumps(
        writer_invalid, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    persisted: list[str] = []
    with pytest.raises(AgentOutputError) as raised:
        run_native_review_agent(
            FakeNativeAdapter(),  # type: ignore[arg-type]
            bundle,
            config=OrchestratorConfig(),
            shorten=lambda value, _maximum: value or "",
            operation="claude_slice_review",
            binding_fingerprint=fingerprint,
            validated_response_callback=persisted.append,
        )
    assert "schema-invalid" in raised.value.technical_text
    assert persisted == []


def test_native_review_checked_preserves_schema_valid_domain_rejection(
    monkeypatch, tmp_path: Path
) -> None:
    canonical = '{"schema_version":"native-agent-review-result-v2"}'

    def reject_after_persist(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["validated_response_callback"](canonical)
        raise AgentOutputError(
            "native review result violates its bound contract",
            technical_text="approval-invalid: approval contains an open blocker",
        )

    monkeypatch.setattr(agent_runtime, "run_native_review_agent", reject_after_persist)

    class Adapter:
        name = "claude"
        metadata: dict[str, object] = {}

    with pytest.raises(AgentInvocationError) as raised:
        run_native_review_agent_checked(
            adapter=Adapter(),  # type: ignore[arg-type]
            bundle=object(),  # type: ignore[arg-type]
            log_prefix="native-review",
            config=OrchestratorConfig(),
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda value, _maximum: value or "",
            reviewer_manifest_paths=None,
            operation="claude_plan_review",
            binding_fingerprint="a" * 64,
            pre_start_callback=None,
            provider_attempt_lifecycle=None,
        )

    assert raised.value.kind is AgentFailureKind.OUTPUT
    assert (tmp_path / "native-review.attempt-1.log").read_text(
        encoding="utf-8"
    ) == canonical
    failure = json.loads(
        (tmp_path / "native-review.attempt-1.failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["failure_kind"] == "output"
    assert failure["technical_text"] == (
        "approval-invalid: approval contains an open blocker"
    )


def test_budget_denial_happens_after_preparation_but_before_capability_or_process(
    monkeypatch,
) -> None:
    calls = {"prepare": 0, "capability": 0, "process": 0, "cleanup": 0}
    measurements = []

    class FakeCodex:
        name = "codex"
        cli_binary = "codex"
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = False
        metadata: dict[str, object] = {}

        def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
            calls["prepare"] += 1
            return PreparedProviderInput(
                ("codex", "exec", "-"),
                prompt,
                (ProviderInputComponent("stdin_prompt", prompt),),
            )

        def cleanup(self) -> None:
            calls["cleanup"] += 1

    defaults = default_provider_input_budget_policy()
    policy = ProviderInputBudgetPolicy(
        tuple(
            ProviderInputBudgetRule(
                rule.provider,
                rule.role,
                rule.operation,
                3 if rule.key == ("codex", "codex", "codex_implementation") else rule.max_chars,
                3 if rule.key == ("codex", "codex", "codex_implementation") else rule.max_bytes,
            )
            for rule in defaults.rules
        )
    )
    monkeypatch.setattr(
        agent_runtime,
        "verify_agent_capabilities",
        lambda *args, **kwargs: calls.__setitem__("capability", calls["capability"] + 1),
    )
    monkeypatch.setattr(
        agent_runtime.subprocess,
        "run",
        lambda *args, **kwargs: calls.__setitem__("process", calls["process"] + 1),
    )

    with pytest.raises(ProviderInputBudgetExceeded) as exc_info:
        run_agent(
            FakeCodex(),
            "four",
            operation="codex_implementation",
            binding_fingerprint="binding",
            pre_start_callback=measurements.append,
            config=OrchestratorConfig(provider_input_budget=policy),
            shorten=lambda text, limit: (text or "")[:limit],
        )

    assert exc_info.value.measurement.violated_dimensions == ("chars", "bytes")
    assert measurements == [exc_info.value.measurement]
    assert calls == {"prepare": 1, "capability": 0, "process": 0, "cleanup": 1}


def test_unregistered_adapter_is_denied_before_preparation_or_process() -> None:
    class UnregisteredAdapter:
        name = "fourth-provider"

    with pytest.raises(
        ProviderInputBudgetError,
        match="has no provider input budget registration",
    ):
        run_agent(
            UnregisteredAdapter(),  # type: ignore[arg-type]
            "prompt",
            config=OrchestratorConfig(dry_run=False),
            shorten=lambda text, limit: (text or "")[:limit],
        )


def test_compact_live_output_extracts_codex_text_and_hides_reviewer_envelopes() -> None:
    state: dict[str, str | bool] = {}
    codex_line = (
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"Slice geprüft und bereit."}}'
    )

    assert _compact_stream_text(AGENT_REGISTRY["codex"], "stdout", codex_line, state) == (
        "Slice geprüft und bereit."
    )
    assert _compact_stream_text(
        AGENT_REGISTRY["claude"],
        "stdout",
        '{"usage":{"output_tokens":9000},"result":"very large"}',
        {},
    ) is None
    assert _compact_stream_text(
        AGENT_REGISTRY["antigravity"],
        "stdout",
        '{"usage":{"total_tokens":48000},"response":"very large"}',
        {},
    ) is None

    warning = "same important warning"
    assert _compact_stream_text(AGENT_REGISTRY["codex"], "stderr", warning, state) == warning
    assert _compact_stream_text(AGENT_REGISTRY["codex"], "stdout", warning, state) == warning


def test_compact_result_and_usage_keep_decisions_without_nested_json() -> None:
    output = "\n".join(
        (
            "REVIEWER: claude",
            "Long evidence paragraph that stays in the log only.",
            "NEW_FINDING: C-01 | BLOCKER | gate is red | npm test",
            "REMEDIATION_PATHS: src/prior.py, tests/prior.py",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )

    assert _compact_result_lines(output) == (
        "REVIEWER: claude",
        "NEW_FINDING: C-01 | BLOCKER | gate is red | npm test",
        "REMEDIATION_PATHS: src/prior.py, tests/prior.py",
        "SLICE_APPROVAL: 01 | NO",
        "STATUS: DONE",
    )
    summary = _compact_usage_metadata(
        {
            "duration_api_ms": 64202,
            "num_turns": 5,
            "total_cost_usd": 0.211611,
            "usage": {"input_tokens": 6, "output_tokens": 5726},
            "modelUsage": {"large": {"nested": "payload"}},
        }
    )
    assert summary == "input_tokens=6 output_tokens=5726 turns=5 cost_usd=0.2116"
    assert "modelUsage" not in summary


def test_v3_agent_level_dry_run_never_invents_an_approval() -> None:
    with pytest.raises(agent_runtime.AgentProcessError, match="--dry-run-scenario"):
        agent_runtime.build_dry_run_agent_output(
            "claude",
            "STATE-V3 CONTRACT (mandatory for step slice-15):\n"
            "SLICE_APPROVAL: 15 | YES|NO",
        )


def test_v3_agent_level_dry_run_uses_last_real_contract() -> None:
    with pytest.raises(agent_runtime.AgentProcessError, match="--dry-run-scenario"):
        agent_runtime.build_dry_run_agent_output(
            "claude",
            "repository diff contains CONTRACT (mandatory):\n"
            "STATE-V3 CONTRACT (mandatory for step slice-15):\n"
            "SLICE_APPROVAL: 15 | YES|NO",
        )


def test_runtime_executes_structured_validation_request(tmp_path: Path) -> None:
    request = ValidationRequest(
        "a" * 64,
        (ValidationCommand(argv=("python3", "-c", "print('matrix-ok')")),),
    )

    attestation = run_validation_matrix(
        config=OrchestratorConfig(repo_root=tmp_path), request=request
    )

    assert attestation.passed
    assert attestation.records[0].output == "matrix-ok"


def test_run_agent_checked_does_not_retry_instance_failure(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    sleeps: list[int] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(adapter.name)
        if len(calls) == 1:
            raise RuntimeError("temporary network glitch")
        return "CODEX_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(AgentInvocationError) as exc_info:
        run_agent_checked(
            agent_key="codex",
            prompt="prompt",
            log_prefix="unit",
            max_retries=1,
            required_flags=["CODEX_APPROVAL"],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={"codex": AGENT_REGISTRY["codex"]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
            validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
        )

    assert exc_info.value.kind.value == "network"
    assert calls == ["codex"]
    assert sleeps == []
    failure_logs = tuple(tmp_path.glob("unit.attempt-1.failure.json"))
    assert len(failure_logs) == 1
    assert exc_info.value.invocation_id in failure_logs[0].read_text(encoding="utf-8")


def test_failure_classification_ignores_model_response_prose() -> None:
    failure = classify_agent_failure(
        "antigravity",
        AgentOutputError(
            "non-success envelope",
            provider_text="status=ERROR",
            technical_text="status=ERROR",
            provider_data={
                "status": "ERROR",
                "response": "network quota 429 authentication are review risks",
            },
            exit_code=7,
        ),
        invocation_id="invocation-1",
    )

    assert failure.kind.value == "process"
    assert failure.process_exit_code == 7
    assert failure.provider_data == {"status": "ERROR"}


def test_failure_classification_uses_technical_network_diagnostic() -> None:
    failure = classify_agent_failure(
        "antigravity",
        AgentOutputError(
            "non-success envelope",
            provider_text="connection reset by peer",
            technical_text="connection reset by peer",
            provider_data={"status": "ERROR", "response": "valid review contract"},
            exit_code=1,
        ),
        invocation_id="invocation-2",
    )

    assert failure.kind.value == "network"
    assert "response" not in (failure.provider_data or {})


def test_claude_structured_output_retry_exhaustion_is_bounded_transient() -> None:
    failure = classify_agent_failure(
        "claude",
        agent_runtime.AgentProcessError(
            "native Claude error",
            exit_code=1,
            provider_data={
                "type": "result",
                "subtype": "error_max_structured_output_retries",
            },
        ),
        invocation_id="claude-structured-output-1",
    )

    assert failure.kind is AgentFailureKind.NETWORK
    assert failure.provider_data == {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
    }


@pytest.mark.parametrize(
    ("agent_key", "provider_data"),
    (
        (
            "codex",
            {
                "type": "result",
                "subtype": "error_max_structured_output_retries",
            },
        ),
        ("claude", {"type": "result", "subtype": "different_error"}),
        ("claude", {"type": "different", "subtype": "error_max_structured_output_retries"}),
    ),
)
def test_structured_output_retry_classification_rejects_near_misses(
    agent_key: str, provider_data: dict[str, object]
) -> None:
    failure = classify_agent_failure(
        agent_key,
        agent_runtime.AgentProcessError(
            "native provider error",
            exit_code=1,
            provider_data=provider_data,
        ),
        invocation_id="structured-output-near-miss",
    )

    assert failure.kind is AgentFailureKind.PROCESS


@pytest.mark.parametrize(
    "error_value",
    [
        "additional properties 'LineNumber' not allowed",
        {"message": "additional properties 'LineNumber' not allowed"},
        {
            "message": "additional properties 'LineNumber' not allowed",
            "code": "INVALID_ARGUMENT",
        },
    ],
)
def test_antigravity_line_number_schema_failure_is_narrowly_classified(
    error_value: object,
) -> None:
    failure = classify_agent_failure(
        "antigravity",
        AgentOutputError(
            "failed envelope",
            provider_data={"status": "ERROR", "error": error_value},
            technical_text="unrelated runtime prose",
        ),
        invocation_id="schema-1",
    )

    assert failure.kind is AgentFailureKind.ANTIGRAVITY_TOOL_SCHEMA


@pytest.mark.parametrize(
    ("provider_data", "technical_text"),
    [
        ({"status": "ERROR", "error": "additional properties 'Other' not allowed"}, "runtime"),
        ({"status": "ERROR", "error": "invalid arguments"}, "runtime"),
        ({"status": "ERROR", "message": "additional properties 'LineNumber' not allowed"}, "runtime"),
        ({"status": "SUCCESS", "error": "additional properties 'LineNumber' not allowed"}, "runtime"),
        ({"status": "ERROR", "response": "additional properties 'LineNumber' not allowed"}, "runtime"),
        ({"status": "ERROR"}, "additional properties 'LineNumber' not allowed"),
    ],
)
def test_antigravity_schema_failure_rejects_prose_and_envelope_variants(
    provider_data: dict[str, object], technical_text: str,
) -> None:
    failure = classify_agent_failure(
        "antigravity",
        AgentOutputError(
            "failed envelope",
            provider_data=provider_data,
            technical_text=technical_text,
        ),
        invocation_id="schema-negative",
    )

    assert failure.kind is not AgentFailureKind.ANTIGRAVITY_TOOL_SCHEMA


def test_failed_provider_attempt_uses_injected_clock_and_allowlisted_usage() -> None:
    ticks = iter((10.0, 12.75))
    terminal: list[tuple[float, str | None, object]] = []
    invocation = agent_runtime._ProviderAttemptInvocation(
        ProviderAttemptLifecycle(
            start=lambda _measurement, _bootstrap: "attempt-1",
            terminal=lambda _handle, duration, failure, usage: terminal.append(
                (duration, failure, usage)
            ),
            monotonic_fn=lambda: next(ticks),
        )
    )
    measurement = object()

    invocation.begin(measurement, None)  # type: ignore[arg-type]
    invocation.finish(
        AgentFailureKind.ANTIGRAVITY_TOOL_SCHEMA,
        {
            "usage": {"input_tokens": 7, "output_tokens": 2, "raw": "secret"},
            "response": "private provider output",
        },
    )

    duration, failure_kind, usage = terminal[0]
    assert duration == 2.75
    assert failure_kind == "antigravity_tool_schema"
    assert usage is not None and usage.input_tokens == 7 and usage.output_tokens == 2
    assert not hasattr(usage, "raw")


def test_run_agent_checked_validation_error_backoff(monkeypatch, tmp_path: Path) -> None:
    sleeps: list[int] = []
    prompts: list[str] = []

    def fake_run_agent(_adapter, prompt, **kwargs):  # type: ignore[no-untyped-def]
        prompts.append(prompt)
        return "CODEX_APPROVAL: YES\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    output = run_agent_checked(
        agent_key="codex",
        prompt=(
            "SENSITIVE_FULL_REVIEW_EVIDENCE\n\n"
            "Output format (Markdown):\n"
            "- Marker line: CODEX_APPROVAL: YES|NO\n"
            "- Final line: STATUS: DONE"
        ),
        log_prefix="unit",
        max_retries=1,
        required_flags=[],
        output_validator=lambda _output: "not valid" if len(prompts) == 1 else None,
        config=OrchestratorConfig(dry_run=False),
        agents={"codex": AGENT_REGISTRY["codex"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES",
        validate_done_marker=lambda text: True,
    )

    assert "STATUS: DONE" in output
    assert len(prompts) == 2
    assert sleeps == [2]
    assert "SENSITIVE_FULL_REVIEW_EVIDENCE" in prompts[0]
    assert "SENSITIVE_FULL_REVIEW_EVIDENCE" not in prompts[1]
    assert "not valid" in prompts[1]
    assert "Rejected answer to repair" in prompts[1]
    assert "CODEX_APPROVAL: YES\nSTATUS: DONE" in prompts[1]
    assert "Output format (Markdown)" in prompts[1]


@pytest.mark.parametrize(
    "failure",
    [
        AgentPermissionError("denied Bash(find /)"),
        AgentBudgetError("budget guard stopped the call"),
    ],
)
def test_run_agent_checked_does_not_retry_policy_failure(
    monkeypatch, tmp_path: Path, failure: Exception
) -> None:
    calls: list[int] = []

    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise failure

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)

    with pytest.raises(AgentInvocationError) as exc_info:
        run_agent_checked(
            agent_key="claude",
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={"claude": AGENT_REGISTRY["claude"]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: None,
            validate_done_marker=lambda text: True,
        )

    assert calls == [1]
    assert exc_info.value.kind.value in {"permission", "runtime"}


def test_run_agent_checked_accepts_alternative_required_flags(monkeypatch, tmp_path: Path) -> None:
    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        _ = kwargs
        return "CODEX_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)

    output = run_agent_checked(
        agent_key="codex",
        prompt="prompt",
        log_prefix="unit",
        max_retries=0,
        required_flags=["PHASE1_APPROVAL|CODEX_APPROVAL"],
        output_validator=None,
        config=OrchestratorConfig(dry_run=False),
        agents={"codex": AGENT_REGISTRY["codex"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output


@pytest.mark.parametrize("agent_key", ["claude", "codex"])
def test_run_agent_checked_quota_errors_stop_requested_agent_without_substitution(
    monkeypatch, tmp_path: Path, agent_key: str
) -> None:
    calls: list[str] = []
    sleeps: list[int] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        _ = kwargs
        calls.append(adapter.name)
        raise RuntimeError("usage cap exceeded")

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(QuotaReachedError) as exc_info:
        run_agent_checked(
            agent_key=agent_key,
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={agent_key: AGENT_REGISTRY[agent_key]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: "YES",
            validate_done_marker=lambda text: True,
        )

    assert exc_info.value.agent_key == agent_key
    assert agent_key in str(exc_info.value)
    assert calls == [agent_key]
    assert sleeps == []


def test_check_git_clean_skips_when_git_missing(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: None)

    ok, message = check_git_clean()

    assert ok is True
    assert "skipping git cleanliness check" in message.lower()


def test_check_git_clean_skips_when_not_in_git_repo(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        if args == ["git", "rev-parse", "--is-inside-work-tree"]:
            return 128, "", "fatal: not a git repository"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is True
    assert "skipped" in message.lower()


def test_check_git_clean_fails_on_tracked_changes(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        table = {
            ("git", "rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
            ("git", "rev-parse", "--verify", "HEAD"): (0, "abc123\n", ""),
            ("git", "status", "--porcelain", "--untracked-files=normal"): (0, " M run_task\n", ""),
            ("git", "update-index", "-q", "--refresh"): (0, "", ""),
            ("git", "diff-index", "--quiet", "HEAD", "--"): (1, "", ""),
        }
        try:
            return table[tuple(args)]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"Unexpected command: {args}") from exc

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is False
    assert "tracked changes" in message.lower()
    assert "run_task" in message
    assert "git status --porcelain" in message


def test_check_git_clean_fails_on_untracked_files(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        table = {
            ("git", "rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
            ("git", "rev-parse", "--verify", "HEAD"): (0, "abc123\n", ""),
            ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "?? task.md\n", ""),
            ("git", "update-index", "-q", "--refresh"): (0, "", ""),
            ("git", "diff-index", "--quiet", "HEAD", "--"): (0, "", ""),
        }
        try:
            return table[tuple(args)]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"Unexpected command: {args}") from exc

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is False
    assert "not clean" in message.lower()
    assert "?? task.md" in message
    assert "git status --porcelain" in message


def test_preflight_skip_git_check_bypasses_dirty_repo(monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(agent_runtime, "can_resolve_host", lambda _host: True)

    def fake_check_git_clean() -> tuple[bool, str]:
        calls["count"] += 1
        return False, "dirty"

    monkeypatch.setattr(agent_runtime, "check_git_clean", fake_check_git_clean)

    ok = preflight(
        required_agents=["codex"],
        strict=False,
        agents={"codex": AGENT_REGISTRY["codex"]},
        skip_git_check=True,
    )

    assert ok is True
    assert calls["count"] == 0


def test_preflight_fails_when_git_not_clean(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(agent_runtime, "can_resolve_host", lambda _host: True)
    monkeypatch.setattr(
        agent_runtime,
        "check_git_clean",
        lambda: (False, "dirty"),
    )

    ok = preflight(
        required_agents=["codex"],
        strict=False,
        agents={"codex": AGENT_REGISTRY["codex"]},
    )

    assert ok is False


def test_agent_capability_check_is_lazy_and_cached(monkeypatch) -> None:
    adapter = CodexAdapter(
        AgentSettings("codex", "codex", "gpt-5.6-sol", 1800, "medium")
    )
    calls: list[list[str]] = []

    def fake_local(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append(args)
        if args[-1] == "--version":
            return 0, "codex-cli 0.147.0\n", ""
        return 0, " ".join(adapter.capability.required_help_flags), ""

    monkeypatch.setattr(agent_runtime, "_resolve_agent_binary", lambda _binary: "/bin/codex")
    monkeypatch.setattr(agent_runtime, "run_local_command", fake_local)

    verify_agent_capabilities(adapter)
    verify_agent_capabilities(adapter)

    assert adapter.capability_verified is True
    assert calls == [
        ["/bin/codex", "--version"],
        ["/bin/codex", "exec", "--help"],
    ]


@pytest.mark.parametrize(
    ("adapter", "version_text"),
    [
        (
            CodexAdapter(AgentSettings("codex", "codex", "model", 1800, "medium")),
            "codex-cli 0.147.99",
        ),
        (
            ClaudeAdapter(AgentSettings("claude", "claude", "model", 1800, "high")),
            "2.1.999 (Claude Code)",
        ),
        (
            AntigravityAdapter(
                AgentSettings("antigravity", "agy", "model", 1800, "high")
            ),
            "1.1.999",
        ),
    ],
)
def test_agent_capability_check_accepts_patch_updates(
    monkeypatch, adapter, version_text: str
) -> None:
    calls: list[list[str]] = []

    def fake_local(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append(args)
        if args[-1] == "--version":
            return 0, f"{version_text}\n", ""
        return 0, " ".join(adapter.capability.required_help_flags), ""

    monkeypatch.setattr(
        agent_runtime, "_resolve_agent_binary", lambda _binary: f"/bin/{adapter.name}"
    )
    monkeypatch.setattr(agent_runtime, "run_local_command", fake_local)

    verify_agent_capabilities(adapter)

    assert adapter.capability_verified is True
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("adapter", "version_text"),
    [
        (
            CodexAdapter(AgentSettings("codex", "codex", "model", 1800, "medium")),
            "codex-cli 0.148.0",
        ),
        (
            ClaudeAdapter(AgentSettings("claude", "claude", "model", 1800, "high")),
            "2.2.0 (Claude Code)",
        ),
        (
            AntigravityAdapter(
                AgentSettings("antigravity", "agy", "model", 1800, "high")
            ),
            "1.2.0",
        ),
    ],
)
def test_agent_capability_check_rejects_minor_updates(
    monkeypatch, adapter, version_text: str
) -> None:
    monkeypatch.setattr(
        agent_runtime, "_resolve_agent_binary", lambda _binary: f"/bin/{adapter.name}"
    )
    monkeypatch.setattr(
        agent_runtime,
        "run_local_command",
        lambda _args, timeout=20: (0, f"{version_text}\n", ""),
    )

    with pytest.raises(AgentCompatibilityError, match="Unsupported .* CLI version"):
        verify_agent_capabilities(adapter)


def test_collect_file_snapshots_truncates_limits_and_handles_missing(tmp_path: Path) -> None:
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("1\n2\n3\n4\n", encoding="utf-8")
    file_b.write_text("ok\n", encoding="utf-8")

    output = collect_file_snapshots(
        changed_files=["a.py", "# ignored heading", "missing.py", "b.py"],
        max_lines=2,
        max_files=2,
        repository_root=tmp_path,
    )

    assert "<<<FILES_BEGIN>>>" in output
    assert "### a.py" in output
    assert "1\n2" in output
    assert "...[truncated to 2 lines]" in output
    assert "### missing.py" in output or "### b.py" in output
    assert "<<<FILES_END>>>" in output


from conftest import can_symlink


@pytest.mark.skipif(not can_symlink(), reason="symlinks are unavailable")
def test_collect_file_snapshots_rejects_paths_outside_repository(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository with spaces"
    repository.mkdir()
    safe_file = repository / "safe.txt"
    safe_file.write_text("SAFE CONTENT\n", encoding="utf-8")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("FOREIGN SECRET CONTENT\n", encoding="utf-8")
    (repository / "escape.txt").symlink_to(secret_file)

    with caplog.at_level("WARNING", logger="agent_runtime"):
        output = collect_file_snapshots(
            changed_files=[
                "../secret.txt",
                r"..\secret.txt",
                str(secret_file),
                "escape.txt",
                str(safe_file),
            ],
            max_lines=20,
            max_files=1,
            repository_root=repository,
        )

    assert "SAFE CONTENT" in output
    assert "### safe.txt" in output
    assert "FOREIGN SECRET CONTENT" not in output
    assert "secret.txt" not in output
    assert "escape.txt" not in output
    assert caplog.text.count("Rejected file snapshot path") == 4


def test_collect_file_snapshots_normalizes_duplicates_and_skips_directory(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_file = source_dir / "module.py"
    source_file.write_text("pass\n", encoding="utf-8")

    output = collect_file_snapshots(
        changed_files=[r"source\module.py", "source/module.py", "source"],
        max_lines=20,
        max_files=3,
        repository_root=tmp_path,
    )

    assert output.count("### source/module.py") == 1
    assert "### source\n[skip] Path is a directory." in output


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation requires POSIX")
def test_collect_file_snapshots_skips_non_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fifo = tmp_path / "review-pipe"
    os.mkfifo(fifo)
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == fifo:
            raise AssertionError("snapshot collector attempted to read a FIFO")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    output = collect_file_snapshots(
        changed_files=[fifo.name],
        max_lines=20,
        max_files=1,
        repository_root=tmp_path,
    )

    assert "### review-pipe\n[skip] Path is not a regular file." in output


def test_repo_snapshot_renders_precollected_canonical_changes(tmp_path: Path) -> None:
    changes = RepositoryChanges(
        repository_root=tmp_path,
        merge_base="a" * 40,
        entries=(ChangedPath("src/new.py", "added", "A"),),
        diff_text="diff body that is deliberately longer than the limit",
        fingerprint="b" * 64,
    )

    snapshot = repo_snapshot(changes, max_diff_chars=12)

    assert f"since {'a' * 40}" in snapshot
    assert "A src/new.py" in snapshot
    assert "b" * 64 in snapshot
    assert "...[truncated]" in snapshot


def test_run_tests_snapshot_uses_shell_true_and_raw_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)

    rc, snapshot = run_tests_snapshot(
        config=OrchestratorConfig(dry_run=False),
        test_command="npm test",
        test_timeout_seconds=30,
        shorten=lambda text, _limit: text or "",
    )

    assert rc == 0
    assert "Exit code: 0" in snapshot
    assert captured["command"] == "npm test"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is True


def test_run_agent_calls_adapter_cleanup_on_timeout(monkeypatch) -> None:
    class TimeoutAdapter:
        name = "codex"
        cli_binary = "timeout"
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

        def __init__(self) -> None:
            self.cleaned = False

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            _ = prompt
            return ["timeout-cli"], True

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            _ = stdout
            _ = stderr
            _ = extra_files
            return ""

        def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
            _ = channel
            _ = line
            _ = state
            return False

        def validate_process_output(self, stderr: str) -> None:
            _ = stderr

        def cleanup(self) -> None:
            self.cleaned = True

    adapter = TimeoutAdapter()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        raise agent_runtime.subprocess.TimeoutExpired("timeout-cli", kwargs["timeout"])

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)

    try:
        run_agent(
            adapter,
            "prompt",
            config=OrchestratorConfig(dry_run=False, agent_live_stream=False),
            shorten=lambda text, _limit: text or "",
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - defensive
        assert False, "expected RuntimeError"

    assert adapter.cleaned is True


def test_run_agent_preserves_exit_code_when_adapter_rejects_envelope(monkeypatch) -> None:
    class RejectingAdapter:
        name = "codex"
        cli_binary = "rejecting"
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            return ["rejecting-cli"], True

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            raise AgentOutputError(
                "provider rejected envelope", technical_text="status=ERROR"
            )

        def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
            return False

        def validate_process_output(self, stderr: str) -> None:
            return None

        def cleanup(self) -> None:
            return None

    class Result:
        returncode = 9
        stdout = '{"status":"ERROR"}'
        stderr = ""

    monkeypatch.setattr(agent_runtime.subprocess, "run", lambda *args, **kwargs: Result())

    with pytest.raises(AgentOutputError) as exc_info:
        run_agent(
            RejectingAdapter(),
            "prompt",
            config=OrchestratorConfig(dry_run=False, agent_live_stream=False),
            shorten=lambda text, _limit: text or "",
        )

    assert exc_info.value.exit_code == 9


def test_read_only_reviewer_workspace_blocks_writes_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")

    workspace = create_read_only_reviewer_workspace(source)
    try:
        copied = workspace.root / "tracked.txt"
        with pytest.raises(PermissionError):
            copied.write_text("changed\n", encoding="utf-8")
        assert copied.read_text(encoding="utf-8") == "original\n"
        assert tracked.read_text(encoding="utf-8") == "original\n"
        if os.name != "nt":
            with pytest.raises(PermissionError):
                (workspace.container / "outside-repo.txt").write_text(
                    "unexpected\n", encoding="utf-8"
                )
    finally:
        workspace.cleanup()

    assert not workspace.container.exists()


def test_manifest_reviewer_workspace_contains_only_exact_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "included.py").write_text("included\n", encoding="utf-8")
    (source / "src" / "foreign.py").write_text("foreign\n", encoding="utf-8")
    (source / ".orchestrator").mkdir()
    (source / ".orchestrator" / "record.json").write_text("secret\n", encoding="utf-8")

    workspace = create_read_only_reviewer_workspace(
        source, ("src/included.py",)
    )
    try:
        assert (workspace.root / "src" / "included.py").read_text(encoding="utf-8") == "included\n"
        assert not (workspace.root / "src" / "foreign.py").exists()
        assert not (workspace.root / ".orchestrator").exists()
    finally:
        workspace.cleanup()


def test_manifest_reviewer_workspace_rejects_missing_and_symlink_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (source / "link.txt").symlink_to(outside)

    with pytest.raises(RuntimeError, match="missing or not a file"):
        create_read_only_reviewer_workspace(source, ("missing.txt",))
    with pytest.raises(RuntimeError, match="traverses a symlink"):
        create_read_only_reviewer_workspace(source, ("link.txt",))


def test_reviewer_process_pwd_matches_disposable_working_directory(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("original\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class ReviewerAdapter:
        name = "claude"
        cli_binary = "reviewer"
        model = "model"
        effort = "medium"
        timeout = 30
        reviewer = True
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {
            "duration_api_ms": 1250,
            "num_turns": 2,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
            captured["bound_source"] = source_root
            captured["bound_snapshot"] = snapshot_root

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            return ["reviewer"], True

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            return stdout

        def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
            return False

        def validate_process_output(self, stderr: str) -> None:
            return None

        def cleanup(self) -> None:
            return None

    class Result:
        returncode = 0
        stdout = "STATUS: DONE"
        stderr = ""

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)
    monkeypatch.setenv("RUN_TASK_REVIEW_TEST_COMMAND", "python3 -m pytest tests/ -v")
    monkeypatch.setenv("RUN_TASK_REVIEW_PROBE_PATH", "README.md")
    monkeypatch.setenv("RUN_TASK_REVIEW_TIMEOUT", "1800")
    caplog.set_level("INFO")
    output = run_agent(
        ReviewerAdapter(),
        "prompt",
        config=OrchestratorConfig(repo_root=source, agent_live_stream=False),
        shorten=lambda text, limit: (text or "")[:limit],
        operation="claude_contract_repair",
    )

    working_directory = captured["cwd"]
    process_environment = captured["env"]
    assert isinstance(working_directory, Path)
    assert isinstance(process_environment, dict)
    assert process_environment["PWD"] == str(working_directory)
    assert process_environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "RUN_TASK_REVIEW_TEST_COMMAND" not in process_environment
    assert "RUN_TASK_REVIEW_PROBE_PATH" not in process_environment
    assert "RUN_TASK_REVIEW_TIMEOUT" not in process_environment
    assert captured["bound_snapshot"] == working_directory
    assert not working_directory.exists()
    assert output == "STATUS: DONE"
    assert "operation=claude_contract_repair" in caplog.text
    assert "input_tokens=10 output_tokens=20 turns=2" in caplog.text


def test_provider_usage_normalization_is_closed_and_preserves_unknown() -> None:
    usage = normalize_provider_usage(
        {
            "conversation_id": "secret-conversation",
            "num_turns": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 7,
                "private_provider_key": 99,
            },
        }
    )

    assert usage is not None
    assert usage.input_tokens == 0
    assert usage.output_tokens == 7
    assert usage.turns == 0
    assert usage.total_tokens is None
    assert "conversation" not in _compact_usage_metadata(
        {"conversation_id": "secret-conversation", "usage": {"output_tokens": 7}}
    )
    assert normalize_provider_usage({"conversation_id": "secret"}) is None
    assert _compact_usage_metadata(None) == "unknown"


def test_provider_attempt_lifecycle_starts_after_preflight_and_terminalizes_success(
    monkeypatch, tmp_path: Path
) -> None:
    events: list[object] = []

    class Adapter:
        name = "codex"
        cli_binary = "codex"
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            return ["codex"], True

        def validate_process_output(self, stderr: str) -> None:
            return None

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            self.metadata = {
                "conversation_id": "excluded",
                "usage": {"input_tokens": 0, "output_tokens": 3},
            }
            return stdout

        def cleanup(self) -> None:
            events.append("cleanup")

    class Result:
        returncode = 0
        stdout = "STATUS: DONE"
        stderr = ""

    monkeypatch.setattr(
        agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: events.append("preflight")
    )
    monkeypatch.setattr(
        agent_runtime.subprocess, "run",
        lambda *args, **kwargs: (events.append("process") or Result()),
    )
    lifecycle = ProviderAttemptLifecycle(
        start=lambda measurement, bootstrap: (
            events.append(("start", bootstrap, measurement.input_digest)) or "attempt-1"
        ),
        terminal=lambda handle, duration, failure, usage: events.append(
            ("terminal", handle, failure, usage)
        ),
    )

    output = run_agent_checked(
        agent_key="codex", prompt="prompt", log_prefix="lifecycle", max_retries=0,
        required_flags=[], output_validator=None, config=OrchestratorConfig(),
        agents={"codex": Adapter()}, log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: None, validate_done_marker=lambda text: True,
        operation="codex_implementation", binding_fingerprint="a" * 64,
        pre_start_callback=lambda measurement: "measurement-1",
        provider_attempt_lifecycle=lifecycle,
    )

    assert output == "STATUS: DONE"
    assert events[0] == "preflight"
    assert events[1][0:2] == ("start", "measurement-1")  # type: ignore[index]
    assert events[2] == "process"
    terminal = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "terminal"
    )
    assert terminal[0:3] == ("terminal", "attempt-1", None)  # type: ignore[index]
    assert terminal[3].input_tokens == 0  # type: ignore[index,union-attr]


def test_provider_attempt_rejected_output_is_durably_failed_not_succeeded(
    monkeypatch, tmp_path: Path
) -> None:
    class Adapter:
        name = "codex"
        cli_binary = "codex"
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            return ["codex"], True

        def validate_process_output(self, stderr: str) -> None:
            return None

        def extract_output(
            self, stdout: str, stderr: str, extra_files: dict[str, str]
        ) -> str:
            self.metadata = {"usage": {"output_tokens": 5}}
            return stdout

        def cleanup(self) -> None:
            return None

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    monkeypatch.setattr(
        agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        agent_runtime.subprocess, "run", lambda *args, **kwargs: Result("invalid output")
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "rejected-output"))

    def persist_measurement(measurement):  # type: ignore[no-untyped-def]
        payload = provider_input_measurement_payload(
            measurement,
            work_unit_id=1,
            transition_fingerprint="b" * 64,
            relevant_record_head="0" * 64,
        )
        return bridge.append(
            payload,
            logical_id="measurement-1",
            idempotency_key="measurement:1",
            fingerprint_sha256="a" * 64,
        )

    with pytest.raises(AgentInvocationError) as exc_info:
        run_agent_checked(
            agent_key="codex", prompt="prompt", log_prefix="rejected", max_retries=0,
            required_flags=["READY"], output_validator=None, config=OrchestratorConfig(),
            agents={"codex": Adapter()}, log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: key if key in text else None,
            validate_done_marker=lambda text: text.endswith("STATUS: DONE"),
            operation="codex_implementation", binding_fingerprint="a" * 64,
            pre_start_callback=persist_measurement,
            provider_attempt_lifecycle=ProviderAttemptLifecycle(
                start=lambda _measurement, bootstrap: bridge.start_provider_attempt(
                    measurement_record=bootstrap,
                    binding_fingerprint="a" * 64,
                    work_unit_id=1,
                ),
                terminal=(
                    lambda started, duration, failure, usage:
                    bridge.finish_provider_attempt(
                        started,
                        duration_seconds=duration,
                        failure_kind=failure,
                        usage=usage,
                    )
                ),
            ),
        )

    assert exc_info.value.kind is AgentFailureKind.OUTPUT
    attempts = tuple(
        record.payload
        for record in bridge.store.load_chain()
        if isinstance(record.payload, ProviderAttemptPayload)
    )
    assert [attempt.phase for attempt in attempts] == ["started", "failed"]
    assert attempts[-1].failure_kind == AgentFailureKind.OUTPUT.value
    assert all(attempt.phase != "succeeded" for attempt in attempts)


def test_unknown_agent_version_is_a_non_retryable_gate(monkeypatch, tmp_path: Path) -> None:
    adapter = ClaudeAdapter(
        AgentSettings("claude", "claude", "sonnet", 1800, "medium")
    )
    calls: list[list[str]] = []

    def fake_local(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append(args)
        return 0, "9.9.9 (Claude Code)\n", ""

    monkeypatch.setattr(agent_runtime, "_resolve_agent_binary", lambda _binary: "/bin/claude")
    monkeypatch.setattr(agent_runtime, "run_local_command", fake_local)

    with pytest.raises(AgentInvocationError, match="Unsupported claude CLI version") as exc_info:
        run_agent_checked(
            agent_key="claude",
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False, repo_root=tmp_path),
            agents={"claude": adapter},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: None,
            validate_done_marker=lambda text: True,
        )

    assert calls == [["/bin/claude", "--version"]]
    assert exc_info.value.kind.value == "runtime"
