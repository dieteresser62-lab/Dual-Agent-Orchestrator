from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys

import agent_runtime

import pytest

from agent_adapters import AgentOutputError, create_agent_pair
from agent_config import AgentSettings
from agent_roles import AgentRoleName, AgentSlot
from antigravity_adapter import (
    AntigravityTransport, AntigravityWorkspace, NativeAntigravityReviewAdapter,
    classify_agy_stderr, strict_json_object,
)
from native_review_contract import NativeReviewContext
from native_review_request import (
    NativeReviewEvidenceInput, NativeReviewKind, NativeReviewRequestSpec,
    build_native_review_request,
)
from contracts import (
    AgentRole, ApprovalMarker, ValidationAttestation, ValidationRecord,
    ValidationStatus,
)
from role_binding import binding_for_role
from workflow_state import AgentFailureKind
from provider_input_budget import (
    PreparedProviderInput, ProviderInputBudgetPolicy, ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy, measure_provider_input,
)


FIXTURE = Path(__file__).parent / "fixtures/antigravity-envelopes-v1.json"


def _cases() -> dict[str, dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["schema_version"] == "antigravity-envelopes-v1"
    return {case["name"]: case for case in data["cases"]}


def _settings(timeout: int | None = 1800) -> AgentSettings:
    return AgentSettings("antigravity", "agy", "gemini-3.1-pro-high", timeout, "high")


def _bundle():
    attestation = ValidationAttestation(
        attestation_id="validation-agy-fake", diff_fingerprint="c" * 64,
        expected_commands=("pytest",),
        records=(ValidationRecord(ValidationStatus.PASS, "pytest", 0, "passed"),),
        output_digest=hashlib.sha256(b"passed").hexdigest(), summary="passed",
    )
    context = NativeReviewContext(
        run_id="run-agy-fake", work_unit_id="1", operation="reviewer_slice_review",
        diff_fingerprint="c" * 64, reviewer=AgentRole.REVIEWER,
        approval_marker=ApprovalMarker.SLICE, slice_id="01", round_number=1,
        validation_attestation=attestation,
    )
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=context, review_kind=NativeReviewKind.SLICE,
            target_branch="feature/fake", base_commit="b" * 40,
            authorized_paths=("src/example.py",),
            acceptance_criteria=("Review exactly one change.",),
            evidence=(NativeReviewEvidenceInput("diff", "diff", "diff --git"),),
        ), profile="antigravity",
    )


def _workspace(tmp_path: Path) -> AntigravityWorkspace:
    container = tmp_path / "sp ace-ü"
    input_dir = container / "input"
    input_dir.mkdir(parents=True)
    repo = container / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    agent = home / "agent.md"
    agent.write_text("tools: [view_file]\n" + binding_for_role(AgentRoleName.REVIEWER).policy, encoding="utf-8")
    settings = home / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    return AntigravityWorkspace(container, repo, input_dir, home, agent, settings, container / "agy.log")


def test_measured_envelopes_have_source_digests_and_distinct_rejections() -> None:
    cases = _cases()
    assert len(cases) == 5
    expected = {
        "soft_deny_s1": AgentFailureKind.PERMISSION,
        "soft_deny_s4": AgentFailureKind.PERMISSION,
        "error_with_output": AgentFailureKind.OUTPUT,
        "print_timeout": AgentFailureKind.TIMEOUT,
    }
    for name, kind in expected.items():
        case = cases[name]
        assert len(case["stdout_sha256"]) == 64 and len(case["stderr_sha256"]) == 64
        assert case["source"] and case["stderr_source"]
        with pytest.raises(AgentOutputError) as caught:
            AntigravityTransport.envelope(
                json.dumps(case["envelope"]), case["stderr"], case["exit_code"],
                json.dumps(case["envelope"]["json_schema"]),
            )
        assert caught.value.kind_hint is kind
    success = cases["valid_s6"]
    envelope, usage = AntigravityTransport.envelope(
        json.dumps(success["envelope"]), "", 0,
        json.dumps(success["envelope"]["json_schema"]),
    )
    assert isinstance(envelope["structured_output"], dict)
    assert usage["total_tokens"] == 82374
    assert usage["input_tokens"] + usage["output_tokens"] == usage["total_tokens"]
    assert usage["cache_read_tokens"] == 97175  # separate category, never summed again


@pytest.mark.parametrize("stdout", [
    '{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', 'prefix {"a":1}', '{"a":1} trailing',
    '[]', 'null', '',
])
def test_stdout_requires_one_strict_object(stdout: str) -> None:
    with pytest.raises(AgentOutputError):
        strict_json_object(stdout)


def test_schema_echo_is_type_exact_and_status_exit_denials_jointly_checked() -> None:
    base = {
        "status": "SUCCESS", "structured_output": {"result": {}},
        "json_schema": {"flag": True}, "usage": {},
    }
    for change, exit_code, expected in (
        ({"json_schema": {"flag": 1}}, 0, AgentFailureKind.OUTPUT),
        ({"denied_actions": [{"tool": "read_file"}]}, 0, AgentFailureKind.PERMISSION),
        ({"error": "bad"}, 0, AgentFailureKind.OUTPUT),
        ({"structured_output": None}, 0, AgentFailureKind.OUTPUT),
        ({}, 3, AgentFailureKind.PROCESS),
    ):
        with pytest.raises(AgentOutputError) as caught:
            AntigravityTransport.envelope(
                json.dumps({**base, **change}), "", exit_code, '{"flag":true}',
            )
        assert caught.value.kind_hint is expected
    assert AntigravityTransport.exit_diagnostic(1) != AntigravityTransport.exit_diagnostic(3)


@pytest.mark.parametrize("stderr, kind", [
    ("authentication required", AgentFailureKind.AUTH),
    ("quota exceeded", AgentFailureKind.QUOTA),
    ("network error", AgentFailureKind.NETWORK),
    ("invalid json schema", AgentFailureKind.OUTPUT),
    ("unknown model", AgentFailureKind.PROCESS),
    ("AGY_ERROR: backend failed", AgentFailureKind.PROCESS),
    ("unexpected user@example.com token=secret", AgentFailureKind.PROCESS),
])
def test_versioned_stderr_mapping_is_secret_free(stderr: str, kind: AgentFailureKind) -> None:
    classified, code = classify_agy_stderr(stderr)
    assert classified is kind
    assert "example.com" not in code and "secret" not in code


def test_native_result_uses_only_structured_output_and_bound_request() -> None:
    envelope = _cases()["valid_s6"]["envelope"]
    result = envelope["structured_output"]["result"]
    adapter = NativeAntigravityReviewAdapter(_settings())
    adapter._writer_json = json.dumps(envelope["json_schema"])
    adapter._request_id = result["request_id"]
    text = json.dumps(envelope)
    assert json.loads(adapter.extract_output(text, "", {"exit_code": "0"})) == result
    assert adapter.metadata["usage"]["total_tokens"] == 82374
    adapter._request_id = "native-review-request-" + "0" * 64
    with pytest.raises(AgentOutputError, match="request binding"):
        adapter.extract_output(text, "", {"exit_code": "0"})
    adapter._request_id = result["request_id"]
    changed = {**envelope, "json_schema": {"wrong": True}}
    with pytest.raises(AgentOutputError, match="schema-echo-mismatch"):
        adapter.extract_output(json.dumps(changed), "", {"exit_code": "0"})


@pytest.mark.parametrize("timeout,print_timeout", [(None, "0s"), (17, "17s")])
def test_prepared_command_is_measured_and_timeout_consistent(
    tmp_path: Path, timeout: int | None, print_timeout: str,
) -> None:
    bundle = _bundle()
    workspace = _workspace(tmp_path)
    adapter = NativeAntigravityReviewAdapter(_settings(timeout))
    with pytest.raises(AgentOutputError, match="isolation workspace"):
        adapter.prepare_native_provider_input(bundle)
    adapter.bind_prepared_workspace(workspace)
    prepared = adapter.prepare_native_provider_input(bundle)
    command = list(prepared.command)
    assert command[:3] == ["agy", "-p", command[2]]
    assert command[command.index("--print-timeout") + 1] == print_timeout
    assert command[command.index("--json-schema") + 1] == str(workspace.input_dir / "writer-schema.json")
    assert command[-2:] == ["--agent", "dao-reviewer"]
    assert prepared.stdin_text is None
    assert adapter.timeout == timeout
    assert adapter.env == {
        "HOME": str(workspace.home), "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8", "TERM": "dumb", "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
    }
    assert {item.name for item in prepared.components} >= {
        "system_policy", "response_schema", "packet_manifest", "start_directive",
        "request_chunk_001",
    }
    assert any("ü" in item.content for item in prepared.components if item.name == "start_directive")
    assert next(item.content for item in prepared.components if item.name == "packet_manifest") == (
        workspace.input_dir / "native-review-manifest.md"
    ).read_text(encoding="utf-8")
    defaults = default_provider_input_budget_policy()
    policy = ProviderInputBudgetPolicy(
        tuple(ProviderInputBudgetRule(
            "antigravity" if rule.provider == "claude" else rule.provider,  # allowlist:provider -- profile configuration: substitute reviewer budget
            rule.role, rule.operation, rule.max_chars, rule.max_bytes,
        ) for rule in defaults.rules),
        (("implementer", "implementer", "codex"),  # allowlist:provider -- profile configuration: measured budget occupancy
         ("reviewer", "reviewer", "antigravity"),
         ("final_reviewer", "reviewer", "antigravity")),
    )
    measurement = measure_provider_input(
        prepared, provider="antigravity", role="reviewer",
        operation="reviewer_slice_review", binding_fingerprint="f" * 64,
        policy=policy,
    )
    assert measurement.allowed
    assert measurement.total_bytes == sum(len(item.content.encode("utf-8")) for item in prepared.components)
    assert measurement.total_bytes > len(bundle.canonical_json.encode("utf-8"))
    adapter.cleanup()


def test_factory_uses_reviewer_role_and_candidate_stays_closed() -> None:
    class FakeAdmitted:
        def require(self, provider, role, slot, *, model):
            assert (provider, role, slot, model) == (
                "antigravity", AgentRoleName.REVIEWER, AgentSlot.REVIEWER,
                "gemini-3.1-pro-high",
            )

    adapter = create_agent_pair(
        "antigravity", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER,
        settings=_settings(), certifications=FakeAdmitted(),
    )
    assert isinstance(adapter, NativeAntigravityReviewAdapter)
    with pytest.raises(Exception, match="missing qualification evidence"):
        create_agent_pair(
            "antigravity", AgentRoleName.REVIEWER, slot=AgentSlot.REVIEWER,
            settings=_settings(),
        )
    with pytest.raises(Exception):
        create_agent_pair(
            "antigravity", AgentRoleName.IMPLEMENTER, slot=AgentSlot.IMPLEMENTER,
            settings=_settings(),
        )


def test_fake_process_receives_large_argv_null_stdin_and_no_deadline(tmp_path: Path) -> None:
    script = (
        "import json,os,sys; "
        "print(json.dumps({'size':len(sys.argv[1]), 'stdin':sys.stdin.read(), "
        "'home':os.environ.get('HOME'), 'leak':os.environ.get('AGY_SECRET')}))"
    )
    env = {"HOME": str(tmp_path / "isolated ü home"),
           "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"}
    class FakeAdapter:
        stdin_closed_when_unused = True
        suppress_live_stream = True

    result = agent_runtime._run_agent_process(
        FakeAdapter(), [sys.executable, "-c", script, "ü" * 50_000], None,
        config=agent_runtime.OrchestratorConfig(repo_root=tmp_path), env=env,
        execution_root=tmp_path, timeout_seconds=None, agent_key="fake-provider",
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload == {"size": 50_000, "stdin": "", "home": env["HOME"], "leak": None}


def test_preflight_discloses_bound_egress_data_without_start(caplog: pytest.LogCaptureFixture) -> None:
    adapter = NativeAntigravityReviewAdapter(_settings())
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        assert agent_runtime.preflight(
            ["reviewer"], False, {"reviewer": adapter}, skip_git_check=True,
        )
    assert "destination=Google" in caplog.text
    assert "repository snapshot" in caplog.text
    assert "writer schema" in caplog.text


def test_agy_quota_does_not_call_generic_reset_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("quota reset parser must not run")

    monkeypatch.setattr(agent_runtime, "parse_quota_reset", forbidden)
    kind, code = classify_agy_stderr("quota exceeded for this account")
    failure = agent_runtime.classify_agent_failure(
        "fake-provider", AgentOutputError(
            f"antigravity {code}", kind_hint=kind,
        ), invocation_id="fake-agy-quota",
    )
    assert failure.kind is AgentFailureKind.QUOTA
    assert failure.quota_reset is None


def test_runtime_rejects_unledgered_agy_start_before_capability_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NativeAntigravityReviewAdapter(_settings())

    def forbidden(*args, **kwargs):
        raise AssertionError("provider probe or start must not run")

    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", forbidden)
    with pytest.raises(ValueError, match="provider attempt ledger"):
        agent_runtime.run_agent(
            adapter, "request", config=agent_runtime.OrchestratorConfig(repo_root=tmp_path),
            shorten=lambda value, _maximum: value or "",
            operation="reviewer_slice_review",
        )


def test_generic_runtime_has_no_provider_literal_branch() -> None:
    runtime = (Path(__file__).resolve().parents[1] / "src/agent_runtime.py").read_text(encoding="utf-8")
    assert "antigravity" not in runtime.casefold()


def test_runtime_obeys_adapter_process_properties_without_provider_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "prepared ü workspace"
    root.mkdir()
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/inter op")
    monkeypatch.setenv("AGY_SECRET", "must-not-inherit")
    events: list[str] = []

    class FakeAdapter:
        name = "fake-provider"
        reviewer = True
        role_binding = binding_for_role(AgentRoleName.REVIEWER)
        timeout = None
        env = {"HOME": str(tmp_path / "isolated home"), "PATH": "/usr/bin:/bin"}
        metadata: dict[str, object] = {}
        inherit_process_environment = False
        environment_passthrough = ("WSL_INTEROP",)
        stdin_closed_when_unused = True
        suppress_live_stream = True
        requires_attempt_ledger = True
        sanitize_reviewer_environment = False
        set_pwd = False

        def prepared_execution_root(self) -> Path:
            return root

        def prepare_provider_input(self, prompt: str) -> PreparedProviderInput:
            return PreparedProviderInput(
                ("fake-provider", "-p", prompt), None,
                (ProviderInputComponent("start_directive", prompt),),
            )

        def validate_process_output(self, stderr: str) -> None:
            assert stderr == ""

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            assert extra_files["exit_code"] == "0"
            return stdout

        def cleanup(self) -> None:
            events.append("cleanup")

    defaults = default_provider_input_budget_policy()
    policy = ProviderInputBudgetPolicy(
        tuple(ProviderInputBudgetRule(
            "fake-provider" if rule.provider == "claude" else rule.provider,  # allowlist:provider -- profile configuration: fake reviewer occupancy
            rule.role, rule.operation, rule.max_chars, rule.max_bytes,
        ) for rule in defaults.rules),
        (("implementer", "implementer", "codex"),  # allowlist:provider -- profile configuration: fake implementer occupancy
         ("reviewer", "reviewer", "fake-provider"),
         ("final_reviewer", "reviewer", "fake-provider")),
    )
    assert policy.registered_operations("fake-provider") == {
        "reviewer_plan_review", "reviewer_slice_review", "reviewer_final_review",
    }

    class Ledger:
        def begin(self, measurement, bootstrap):
            assert measurement.provider == "fake-provider"
            assert measurement.role == "reviewer"
            assert bootstrap is None
            events.append("intent")

        def process_started(self, pid):
            assert pid == 321
            events.append("started")

    def fake_process(_adapter, command, stdin_text, *, env, execution_root, timeout_seconds, process_started, **kwargs):
        assert events == ["intent"]
        assert command == ["fake-provider", "-p", "request"]
        assert stdin_text is None and timeout_seconds is None
        assert execution_root == root
        assert env == {**FakeAdapter.env, "WSL_INTEROP": "/run/WSL/inter op"}
        process_started(321)
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")

    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda _adapter, command: list(command))
    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    adapter = FakeAdapter()
    config = agent_runtime.OrchestratorConfig(repo_root=tmp_path, provider_input_budget=policy)
    with pytest.raises(ValueError, match="provider attempt ledger"):
        agent_runtime.run_agent(
            adapter, "request", config=config, shorten=lambda value, _maximum: value or "",
            operation="reviewer_slice_review",
        )
    assert events == []
    output = agent_runtime.run_agent(
        adapter, "request", config=config, shorten=lambda value, _maximum: value or "",
        operation="reviewer_slice_review", attempt_invocation=Ledger(),
    )
    assert output == '{"ok":true}'
    assert events == ["intent", "started", "cleanup"]


def test_adapter_suppresses_live_stream_without_discarding_output(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeAdapter:
        stdin_closed_when_unused = True
        suppress_live_stream = True

    config = agent_runtime.OrchestratorConfig(
        repo_root=tmp_path, agent_live_stream=True, agent_live_stream_mode="full",
    )
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        result = agent_runtime._run_agent_process(
            FakeAdapter(),
            [sys.executable, "-c", "import sys; print('secret-output'); print('secret-error', file=sys.stderr)"],
            None, config=config, env={"PATH": "/usr/bin:/bin"},
            execution_root=tmp_path, timeout_seconds=5, agent_key="fake-provider",
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "secret-output"
    assert result.stderr.strip() == "secret-error"
    assert "secret-output" not in caplog.text
    assert "secret-error" not in caplog.text


def test_one_fake_agy_review_passes_envelope_writer_and_full_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    adapter = NativeAntigravityReviewAdapter(_settings(17))
    adapter.bind_prepared_workspace(_workspace(tmp_path))
    defaults = default_provider_input_budget_policy()
    policy = ProviderInputBudgetPolicy(
        tuple(ProviderInputBudgetRule(
            "antigravity" if rule.provider == "claude" else rule.provider,  # allowlist:provider -- profile configuration: fake reviewer occupancy
            rule.role, rule.operation, rule.max_chars, rule.max_bytes,
        ) for rule in defaults.rules),
        (("implementer", "implementer", "codex"),  # allowlist:provider -- profile configuration: fake implementer occupancy
         ("reviewer", "reviewer", "antigravity"),
         ("final_reviewer", "reviewer", "antigravity")),
    )
    result = {
        "schema_version": "native-agent-review-result-v3",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "reviewer",
        "decision": "approved",
        "new_findings": [], "status_changes": [], "anchors": [],
        "review_evidence": {
            "dimensions": "Correctness, contracts, failure paths and resume state checked.",
            "largest_residual_risk": "Provider output can be incomplete.",
            "break_condition": "The provider omits one bound finding.",
        },
        "pre_mortem": "A future schema change could make the response invalid.",
    }
    envelope = {
        "status": "SUCCESS", "structured_output": {"result": result},
        "json_schema": json.loads(bundle.provider_response_schema_json),
        "usage": {"input_tokens": 4, "output_tokens": 3, "thinking_tokens": 1,
                  "cache_read_tokens": 2, "total_tokens": 7},
    }
    events: list[str] = []

    class Ledger:
        def begin(self, measurement, bootstrap):
            assert measurement.provider == "antigravity"
            events.append("intent")

        def process_started(self, pid):
            assert pid == 321
            events.append("started")

    def fake_process(_adapter, command, stdin_text, *, env, execution_root, timeout_seconds, process_started, **kwargs):
        assert events == ["intent"]
        assert stdin_text is None and timeout_seconds == 17
        assert execution_root == adapter.prepared_workspace.container
        assert env["HOME"] == str(adapter.prepared_workspace.home)
        assert "PWD" not in env
        process_started(321)
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda _adapter, command: list(command))
    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    output = agent_runtime.run_native_review_agent(
        adapter, bundle,
        config=agent_runtime.OrchestratorConfig(repo_root=tmp_path, provider_input_budget=policy),
        shorten=lambda value, _maximum: value or "",
        operation="reviewer_slice_review", binding_fingerprint="c" * 64,
        attempt_invocation=Ledger(),
    )
    assert output.result.approval is True
    assert output.request_id == bundle.bound_context.request_id
    assert agent_runtime.normalize_provider_usage(adapter.metadata).total_tokens == 7
    assert events == ["intent", "started"]
