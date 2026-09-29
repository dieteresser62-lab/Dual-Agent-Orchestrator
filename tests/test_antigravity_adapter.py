from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import agent_runtime
import antigravity_adapter

import pytest

from agent_adapters import AgentOutputError, create_agent_pair
from agent_config import AgentSettings
from agent_roles import AgentRoleName, AgentSlot
from antigravity_adapter import (
    AGY_DENY, AGY_TOOLS, AntigravityTransport, AntigravityWorkspace, NativeAntigravityReviewAdapter,
    _expected_settings, _agent_markdown,
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
from provider_identity import ProviderIdentity


FIXTURE = Path(__file__).parent / "fixtures/antigravity-envelopes-v1.json"


@pytest.fixture(autouse=True)
def _writable_test_run_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    # The agent sandbox need not permit writes to production's /var/tmp.
    monkeypatch.setattr(antigravity_adapter, "AGY_RUN_PARENT", Path("/tmp"))


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


def _isolated_adapter(tmp_path: Path, run_root: Path) -> NativeAntigravityReviewAdapter:
    home = tmp_path / "isolated-home"
    token = home / ".gemini/antigravity-cli/antigravity-oauth-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_bytes(b"opaque-test-token")
    token.chmod(0o600)
    return NativeAntigravityReviewAdapter(_settings(17), isolated_home=home, run_root=run_root)


def _effective_log(base: Path) -> str:
    return (
        f"I0000] CLI settings initialized: permissions=&{{Allow:[read_file({base})] "
        f"Deny:[{' '.join(AGY_DENY)}] Ask:[]}}, toolPermission=request-review\n"
    )


def test_measured_envelopes_have_source_digests_and_distinct_rejections() -> None:
    cases = _cases()
    assert len(cases) == 5
    expected = {
        "soft_deny_s1": AgentFailureKind.PERMISSION,
        "soft_deny_s4": AgentFailureKind.PERMISSION,
        # Operator decision 29 Sep 2026: the measured malformed-call ERROR is transient.
        "error_with_output": AgentFailureKind.NETWORK,
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


def test_measured_stream_interruption_is_transient_and_never_accepted() -> None:
    # Measured live on 29 Sep 2026 (AGY 1.2.12, envelope sha256 b63c5b4e…): an ERROR
    # status with a complete structured result and exit code 0.
    interrupted = "The stream was interrupted. Please continue the task you were working on."
    base = {
        "status": "ERROR", "error": interrupted,
        "structured_output": {"result": {"request_id": "x"}},
        "json_schema": {"flag": True}, "usage": {},
    }
    with pytest.raises(AgentOutputError) as caught:
        AntigravityTransport.envelope(json.dumps(base), "", 0, '{"flag":true}')
    assert caught.value.kind_hint is AgentFailureKind.NETWORK
    assert "stream-interrupted (agy-stderr-v4)" in str(caught.value)
    assert interrupted not in str(caught.value)
    for change in ({"error": interrupted + " Retry."}, {"error": "bad"},
                   {"status": "SUCCESS"}, {"status": "FAILED"}):
        with pytest.raises(AgentOutputError) as caught:
            AntigravityTransport.envelope(json.dumps({**base, **change}), "", 0, '{"flag":true}')
        assert caught.value.kind_hint is AgentFailureKind.OUTPUT


def test_measured_malformed_function_call_is_transient_and_never_accepted() -> None:
    # Measured live on 28 Sep (Phase-0 s4 F1) and 29 Sep 2026 (agy-quality-s2 Q1), AGY 1.2.12.
    malformed = ("Your previous response contained an improperly formatted function call: "
                 "Malformed function call: Failed to parse function call: Function call is empty - "
                 "no input to parse.\nPlease retry with a properly formatted function call\n"
                 "Retries remaining: 3")
    base = {"status": "ERROR", "error": malformed, "structured_output": {"result": {"request_id": "x"}},
            "json_schema": {"flag": True}, "usage": {}}
    with pytest.raises(AgentOutputError) as caught:
        AntigravityTransport.envelope(json.dumps(base), "", 0, '{"flag":true}')
    assert caught.value.kind_hint is AgentFailureKind.NETWORK
    assert "malformed-function-call (agy-stderr-v4)" in str(caught.value)
    for change in ({"error": malformed.replace("Retries remaining: 3", "Retries remaining: none")},
                   {"error": malformed + "\nextra"}, {"status": "SUCCESS"}):
        with pytest.raises(AgentOutputError) as caught:
            AntigravityTransport.envelope(json.dumps({**base, **change}), "", 0, '{"flag":true}')
        assert caught.value.kind_hint is AgentFailureKind.OUTPUT


@pytest.mark.parametrize("stderr, kind", [
    ("authentication required", AgentFailureKind.AUTH),
    ("quota exceeded", AgentFailureKind.QUOTA),
    ("network error", AgentFailureKind.NETWORK),
    ("invalid json schema", AgentFailureKind.OUTPUT),
    ("unknown model", AgentFailureKind.PROCESS),
    ("AGY_ERROR: backend failed", AgentFailureKind.PROCESS),
    ('error: Eligibility check failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist": '
     "dial tcp: lookup daily-cloudcode-pa.googleapis.com on 10.255.255.254:53: server misbehaving",
     AgentFailureKind.NETWORK),
    ('error: Post "https://example.invalid/v1": dial tcp: lookup example.invalid: no such host',
     AgentFailureKind.PROCESS),
    ('error: Eligibility check failed: Post "https://daily-cloudcode-pa.googleapis.com/v1": '
     "dial tcp: lookup daily-cloudcode-pa.googleapis.com: permission denied by policy",
     AgentFailureKind.PROCESS),
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
    adapter = NativeAntigravityReviewAdapter(_settings(timeout), isolated_home=workspace.home)
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


def test_factory_uses_reviewer_role_and_promoted_slots() -> None:
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
    for slot in (AgentSlot.REVIEWER, AgentSlot.FINAL_REVIEWER):
        admitted = create_agent_pair(
            "antigravity", AgentRoleName.REVIEWER, slot=slot,
            settings=_settings(),
        )
        assert isinstance(admitted, NativeAntigravityReviewAdapter)
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
    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.txt").write_text("review evidence", encoding="utf-8")
    records = source / ".orchestrator" / "artifacts" / "run" / "records"
    records.mkdir(parents=True)
    (records / "0001.json").write_text("authoritative record", encoding="utf-8")
    personal = tmp_path / "personal-config.json"
    personal.write_text("personal configuration", encoding="utf-8")
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
        assert env["WSL_INTEROP"] == "/run/WSL/test"
        assert "PWD" not in env
        assert adapter.prepared_workspace.input_dir.stat().st_mode & 0o222 == 0
        assert adapter.prepared_workspace.container.stat().st_mode & 0o222 == 0
        base = adapter.prepared_workspace.container.parent
        adapter.prepared_workspace.log_file.write_text(_effective_log(base) + "account=user@example.com\n", encoding="utf-8")
        process_started(321)
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(agent_runtime, "verify_agent_capabilities", lambda *args, **kwargs: None)
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/test")
    monkeypatch.setattr(agent_runtime, "_bound_launch_command", lambda _adapter, command: list(command))
    monkeypatch.setattr(agent_runtime, "_run_agent_process", fake_process)
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        output = agent_runtime.run_native_review_agent(
            adapter, bundle,
            config=agent_runtime.OrchestratorConfig(repo_root=source, provider_input_budget=policy),
            shorten=lambda value, _maximum: value or "",
            operation="reviewer_slice_review", binding_fingerprint="c" * 64,
            attempt_invocation=Ledger(),
        )
        assert not list((Path(location) / "runs").glob("*.log"))
        assert (source / "evidence.txt").read_text(encoding="utf-8") == "review evidence"
        assert (records / "0001.json").read_text(encoding="utf-8") == "authoritative record"
        assert personal.read_text(encoding="utf-8") == "personal configuration"
    assert output.result.approval is True
    assert output.request_id == bundle.bound_context.request_id
    assert agent_runtime.normalize_provider_usage(adapter.metadata).total_tokens == 7
    assert events == ["intent", "started"]


def test_native_boundary_exact_files_readability_and_write_detection(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof.txt").write_text("visible evidence", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        with pytest.raises(AgentOutputError, match="postcheck failed"):
            with adapter.review_execution_boundary(source, None):
                workspace = adapter.prepared_workspace
                assert workspace is not None
                base = workspace.container.parent
                assert json.loads(workspace.settings_file.read_text()) == _expected_settings(base)
                assert b"trustedWorkspaces" not in workspace.settings_file.read_bytes()
                assert workspace.agent_file.read_bytes() == _agent_markdown(adapter.role_binding.policy)
                assert [f"  - {tool}" for tool in AGY_TOOLS] == [
                    line for line in workspace.agent_file.read_text().splitlines() if line.startswith("  - ")
                ]
                assert (workspace.repo / "proof.txt").read_text() == "visible evidence"
                (workspace.input_dir / "request.json").write_text("{}")
                adapter.seal_provider_input()
                for target in (workspace.repo / "proof.txt", workspace.input_dir / "request.json", workspace.container / "new.txt"):
                    with pytest.raises(PermissionError):
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT)
                        os.close(fd)
                adapter.before_provider_process()
                workspace.log_file.write_text(_effective_log(base), encoding="utf-8")
                (workspace.input_dir / "request.json").chmod(0o600)
                (workspace.input_dir / "request.json").write_text("tampered")
        assert not list((Path(location) / "runs").glob("dao-agy-run-*"))


@pytest.mark.parametrize("mutation", ["settings", "numeric_boolean", "trust", "agent", "missing_log", "wrong_log"])
def test_native_boundary_postcheck_is_fail_closed_and_secret_free(tmp_path: Path, mutation: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        with pytest.raises(AgentOutputError, match="postcheck failed") as caught:
            with adapter.review_execution_boundary(source, None):
                workspace = adapter.prepared_workspace
                assert workspace is not None
                base = workspace.container.parent
                adapter.seal_provider_input()
                adapter.before_provider_process()
                if mutation == "settings":
                    workspace.settings_file.write_text('{"toolPermission":"strict"}')
                elif mutation == "numeric_boolean":
                    value = _expected_settings(base)
                    value["enableTerminalSandbox"] = 1
                    workspace.settings_file.write_text(json.dumps(value))
                elif mutation == "trust":
                    value = _expected_settings(base)
                    value["trustedWorkspaces"] = [str(base)]
                    workspace.settings_file.write_text(json.dumps(value))
                elif mutation == "agent":
                    workspace.agent_file.write_text("replacement")
                elif mutation == "wrong_log":
                    workspace.log_file.write_text("account=user@example.com\nCLI settings initialized: wrong\n")
        assert "user@example.com" not in str(caught.value)
        assert not list((Path(location) / "runs").glob("*.log"))


def test_native_boundary_preflight_and_resume_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = NativeAntigravityReviewAdapter(
            _settings(), isolated_home=tmp_path / "missing", run_root=Path(location) / "runs",
        )
        with pytest.raises(AgentOutputError, match="env -i HOME=") as caught:
            with adapter.review_execution_boundary(source, None):
                pass
        assert caught.value.kind_hint is AgentFailureKind.AUTH
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        token = adapter.isolated_home / ".gemini/antigravity-cli/antigravity-oauth-token"
        token.chmod(0o644)
        with pytest.raises(AgentOutputError, match="0600"):
            with adapter.review_execution_boundary(source, None):
                pass
        token.chmod(0o600)
        token.unlink()
        token.symlink_to(tmp_path / "decoy-token")
        with pytest.raises(AgentOutputError, match="0600"):
            with adapter.review_execution_boundary(source, None):
                pass
        token.unlink()
        token.write_bytes(b"opaque-test-token")
        token.chmod(0o600)
        seen = []
        for index in range(2):
            with adapter.review_execution_boundary(source, None):
                workspace = adapter.prepared_workspace
                assert workspace is not None
                seen.append(workspace.container.parent)
                assert json.loads(workspace.settings_file.read_text()) == _expected_settings(seen[-1])
                assert workspace.agent_file.read_bytes() == _agent_markdown(adapter.role_binding.policy)
                if index == 0:
                    workspace.settings_file.write_text("stale")
                    workspace.agent_file.write_text("stale")
        assert seen[0] != seen[1]
        personal = NativeAntigravityReviewAdapter(
            _settings(), isolated_home=Path.home(), run_root=Path(location) / "runs",
        )
        with pytest.raises(AgentOutputError, match="HOME must be isolated"):
            with personal.review_execution_boundary(source, None):
                pass


def test_native_boundary_serializes_one_home(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        first = _isolated_adapter(tmp_path, Path(location) / "runs")
        second = NativeAntigravityReviewAdapter(
            _settings(), isolated_home=first.isolated_home, run_root=first.run_root,
        )
        entered = threading.Event()
        exited = threading.Event()
        second_entered = threading.Event()

        def concurrent() -> None:
            entered.set()
            with second.review_execution_boundary(source, None):
                second_entered.set()
            exited.set()

        with first.review_execution_boundary(source, None):
            worker = threading.Thread(target=concurrent)
            worker.start()
            assert entered.wait(2)
            assert not second_entered.wait(0.25)
        assert exited.wait(3)
        worker.join(timeout=3)


def test_native_boundary_accepts_measured_default_normalization(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        with adapter.review_execution_boundary(source, None):
            workspace = adapter.prepared_workspace
            assert workspace is not None
            base = workspace.container.parent
            adapter.seal_provider_input()
            adapter.before_provider_process()
            normalized = _expected_settings(base)
            normalized.pop("toolPermission")
            workspace.settings_file.write_text(json.dumps(normalized), encoding="utf-8")
            workspace.log_file.write_text(_effective_log(base), encoding="utf-8")


def test_native_boundary_cleanup_does_not_follow_snapshot_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    (source / "link.txt").symlink_to(outside)
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        with pytest.raises(AgentOutputError, match="postcheck failed"):
            with adapter.review_execution_boundary(source, None):
                workspace = adapter.prepared_workspace
                assert workspace is not None
                assert (workspace.repo / "link.txt").is_symlink()
                adapter.seal_provider_input()
                adapter.before_provider_process()
                workspace.log_file.write_text(_effective_log(workspace.container.parent), encoding="utf-8")
                workspace.repo.chmod(0o700)
                (workspace.repo / "link.txt").unlink()
        assert outside.read_text(encoding="utf-8") == "untouched"


def test_native_boundary_detects_write_into_run_base(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        with pytest.raises(AgentOutputError, match="postcheck failed"):
            with adapter.review_execution_boundary(source, None):
                workspace = adapter.prepared_workspace
                assert workspace is not None
                base = workspace.container.parent
                adapter.seal_provider_input()
                adapter.before_provider_process()
                workspace.log_file.write_text(_effective_log(base), encoding="utf-8")
                (base / "forbidden-write.txt").write_text("tampered", encoding="utf-8")
        assert not list((Path(location) / "runs").glob("dao-agy-run-*"))


def test_fresh_config_replaces_existing_hardlinks_without_touching_targets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with tempfile.TemporaryDirectory(prefix="dao-agy-test-", dir="/tmp") as location:
        adapter = _isolated_adapter(tmp_path, Path(location) / "runs")
        original_settings = tmp_path / "personal-settings.json"
        original_settings.write_text("personal settings", encoding="utf-8")
        settings = adapter.isolated_home / ".gemini/antigravity-cli/settings.json"
        os.link(original_settings, settings)
        original_agent = tmp_path / "personal-agent.md"
        original_agent.write_text("personal agent", encoding="utf-8")
        agent = adapter.isolated_home / ".gemini/config/agents/dao-reviewer/agent.md"
        agent.parent.mkdir(parents=True)
        os.link(original_agent, agent)
        with adapter.review_execution_boundary(source, None):
            assert settings.stat().st_ino != original_settings.stat().st_ino
            assert agent.stat().st_ino != original_agent.stat().st_ino
        assert original_settings.read_text(encoding="utf-8") == "personal settings"
        assert original_agent.read_text(encoding="utf-8") == "personal agent"


def test_native_profile_matches_all_phase0_protection_classes() -> None:
    base = Path("/var/tmp/dao-agy-run-example")
    settings = _expected_settings(base)
    assert settings["permissions"] == {
        "allow": [f"read_file({base})"],
        "deny": [
            "command(*)", "write_file(*)", "mcp(*)", "read_url(*)", "execute_url(*)",
            "read_file(/tmp)", "read_file(/home)", "read_file(/root)",
            "read_file(/mnt)", "read_file(/proc)", "read_file(/run)",
        ],
    }
    assert settings["toolPermission"] == "request-review"
    assert "trustedWorkspaces" not in settings
    agent = _agent_markdown(binding_for_role(AgentRoleName.REVIEWER).policy).decode()
    for rule in (
        "inheritCustomizations: false", "inheritMcp: false", "subagent: false",
        'commandExecutionPolicy: "off"',
    ):
        assert rule in agent
    assert "run_command" not in agent and "mcp" not in AGY_TOOLS
    # P1 customization, P2 file writes, P3 shell/WSL/links, P4 MCP/web/delegation,
    # P5 explicit external reads and P6 unlisted sibling reads each depend on this
    # exact measured surface; the live assertions remain in phase-0-v1.json.


def test_capability_probe_uses_isolated_home_and_neutral_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = NativeAntigravityReviewAdapter(_settings(), isolated_home=tmp_path / "isolated")
    monkeypatch.setenv("AGY_HOST_SECRET", "do-not-inherit")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/test")
    observed = []

    def fake_run(command, **kwargs):
        observed.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "1.2.12\n", "")

    monkeypatch.setattr(antigravity_adapter.subprocess, "run", fake_run)
    assert agent_runtime._capability_runner(adapter)(["agy", "--version"]) == (0, "1.2.12\n", "")
    command, options = observed[0]
    assert command == ["agy", "--version"] and options["cwd"] == "/"
    assert options["env"]["HOME"] == str(adapter.isolated_home)
    assert options["env"]["WSL_INTEROP"] == "/run/WSL/test"
    assert "AGY_HOST_SECRET" not in options["env"]


def test_resume_identity_recheck_uses_isolated_capability_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "agy"
    binary.write_bytes(b"fake binary")
    binary.chmod(0o755)
    adapter = NativeAntigravityReviewAdapter(
        AgentSettings("antigravity", str(binary), "gemini-3.1-pro-high", 17, "high"),
        isolated_home=tmp_path / "isolated",
    )
    identity = ProviderIdentity(
        str(binary), str(binary), "1.2.12", "a" * 64,
        None, None, None,
    )
    adapter.provider_identity = identity
    adapter.capability_verified = True
    seen = []

    def capture(entry, args, runner, *, path, expected):
        assert entry == str(binary) and expected is identity
        seen.append(runner([entry, *args]))
        return identity

    def fake_run(command, **kwargs):
        assert kwargs["env"]["HOME"] == str(adapter.isolated_home)
        assert kwargs["cwd"] == "/"
        return subprocess.CompletedProcess(command, 0, "1.2.12", "")

    monkeypatch.setattr(agent_runtime, "capture_provider_identity", capture)
    monkeypatch.setattr(antigravity_adapter.subprocess, "run", fake_run)
    agent_runtime.verify_agent_capabilities(adapter)
    assert seen == [(0, "1.2.12", "")]


def test_orchestrator_review_path_accepts_the_registered_agy_transport(tmp_path: Path) -> None:
    import inspect

    import orchestrator
    from agent_adapters import is_native_review_adapter

    adapter = NativeAntigravityReviewAdapter(_settings(17), isolated_home=tmp_path / "home",
                                             run_root=Path("/var/tmp/dao-agy-review-test"))
    assert is_native_review_adapter(adapter)
    assert not is_native_review_adapter(object())
    source = inspect.getsource(orchestrator.ProductionWorkflowDriver.invoke_reviewer)
    assert "is_native_review_adapter(native_adapter)" in source
    assert orchestrator.is_native_review_adapter is is_native_review_adapter



def test_every_certification_lookup_binds_the_slot_model() -> None:
    # Found by the first real AGY run (29 Sep 2026): record creation called require()
    # without the model, so the Gemini family binding rejected an experimental AGY slot.
    import ast

    missing = []
    for path in sorted((Path(__file__).resolve().parents[1] / "src").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "require" and len(node.args) >= 3
                    and not any(keyword.arg == "model" for keyword in node.keywords)):
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


def test_run_records_accept_registered_reviewer_providers() -> None:
    # Found by the second real AGY run (29 Sep 2026): the provider-input measurement
    # record accepted only the two baseline providers, so the first AGY review halted.
    from artifact_models import Role, _agent_provider_role_matches

    assert _agent_provider_role_matches("antigravity", Role.REVIEWER)
    assert not _agent_provider_role_matches("unregistered", Role.REVIEWER)


def test_workflow_bootstrap_facts_accept_the_registered_agy_reviewer() -> None:
    # Found by the third real AGY run (29 Sep 2026): bootstrap facts accepted only the
    # providers of the shipped TOML, so the first AGY review halted before its start.
    from role_occupancy import registered_providers

    assert "antigravity" in registered_providers()


def test_prestart_identity_check_uses_bound_entry_under_minimal_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Found by the fourth real AGY run (29 Sep 2026): the profile names the binary
    # "agy" (installed in ~/.local/bin) and the isolated environment's PATH omits that
    # directory, so the pre-start re-verification failed with "Missing CLI binary".
    from types import SimpleNamespace
    import agent_runtime

    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    binary = tool_dir / "agy"
    binary.write_text("#!/bin/sh\necho 1.2.13\n", encoding="utf-8")
    binary.chmod(0o755)
    bound = SimpleNamespace(kind="verified", entry_path=str(binary))
    adapter = SimpleNamespace(name="antigravity", cli_binary="agy", provider_identity=bound,
                              bound_slot="reviewer", capability=SimpleNamespace(version_args=("--version",)))
    seen = {}

    def capture(entry, version_args, runner, *, path, expected):
        seen["entry"] = entry
        return expected

    monkeypatch.setattr(agent_runtime, "capture_provider_identity", capture)
    assert agent_runtime._check_bound_provider_identity(adapter, path="/usr/bin:/bin") is bound
    assert seen["entry"] == str(binary)
    adapter.provider_identity = SimpleNamespace(kind="verified", entry_path="agy")
    with pytest.raises(agent_runtime.AgentCompatibilityError, match="Missing CLI binary"):
        agent_runtime._check_bound_provider_identity(adapter, path="/usr/bin:/bin")


def test_denied_actions_stop_even_under_a_transient_error_status() -> None:
    # Branch review 29 Sep 2026: a denial hidden behind a stream interruption was
    # classified as transient network and would have been retried automatically.
    base = {"status": "ERROR",
            "error": "The stream was interrupted. Please continue the task you were working on.",
            "denied_actions": [{"tool": "read_file"}], "structured_output": {"result": {}},
            "json_schema": {"flag": True}, "usage": {}}
    with pytest.raises(AgentOutputError) as caught:
        AntigravityTransport.envelope(json.dumps(base), "", 0, '{"flag":true}')
    assert caught.value.kind_hint is AgentFailureKind.PERMISSION


def test_a_new_attempt_never_reports_the_previous_attempts_usage(tmp_path: Path) -> None:
    # Branch review 29 Sep 2026: failed attempts recorded the prior attempt's token usage.
    adapter = NativeAntigravityReviewAdapter(_settings(17), isolated_home=tmp_path / "home",
                                             run_root=Path("/var/tmp/dao-agy-usage-test"))
    adapter.metadata = {"usage": {"total_tokens": 12345}}
    with pytest.raises(AgentOutputError, match="not prepared"):
        adapter.prepare_native_provider_input(object())  # type: ignore[arg-type]
    assert adapter.metadata == {}
