import json

import pytest

from agent_adapters import AgentOutputError, AgentPermissionError, NativeCodexAdapter, NativeClaudeReviewAdapter  # allowlist:provider -- transport: metrics coverage
from agent_config import AgentSettings
from claude_implementer_adapter import NativeClaudeImplementerAdapter  # allowlist:provider -- transport: diagnostic coverage
from codex_review_adapter import NativeCodexReviewAdapter  # allowlist:provider -- transport: usage coverage
from agent_runtime import classify_agent_failure, normalize_provider_usage
from provider_metrics import event_usage


@pytest.mark.parametrize("adapter_type", [NativeCodexAdapter, NativeCodexReviewAdapter])  # allowlist:provider -- transport: shared usage behavior
def test_both_transports_collect_terminal_event_usage_even_on_invalid_output(adapter_type):
    adapter = adapter_type(AgentSettings("codex", "codex", "gpt-6-sol", None, "high"))  # allowlist:provider -- profile configuration: usage fixture
    stream = '\n'.join(["not JSON", json.dumps({"type": "item.completed", "usage": {"input_tokens": 999}}),
                         json.dumps({"type": "turn.completed", "usage": {"input_tokens": 42, "cached_input_tokens": 30, "output_tokens": 7}})])
    with pytest.raises(AgentOutputError):
        adapter.extract_output(stream, "", {})
    usage = normalize_provider_usage(adapter.metadata)
    assert (usage.input_tokens, usage.cache_read_input_tokens, usage.output_tokens) == (42, 30, 7)
    adapter.cleanup()
    assert normalize_provider_usage(adapter.metadata) == usage


def test_usage_unknown_is_not_zero_and_turns_are_summed():
    assert event_usage('{"type":"turn.completed","usage":{"input_tokens":true,"output_tokens":-1}}') == {}
    stream = '\n'.join(json.dumps({"type": "turn.completed", "usage": {"input_tokens": i}}) for i in (0, 2))
    assert event_usage(stream) == {"usage": {"input_tokens": 2}}


@pytest.mark.parametrize("adapter_type", [NativeClaudeImplementerAdapter, NativeClaudeReviewAdapter])  # allowlist:provider -- transport: shared denial behavior
def test_denials_are_bounded_redacted_and_preserved_in_failure_data(adapter_type, monkeypatch, caplog):
    monkeypatch.setenv("DAO_SECRET_TOKEN", "private-value")
    adapter = adapter_type(AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: diagnostic fixture
    envelope = {"is_error": False, "permission_denials": [{
        "tool_name": "Bash", "tool_use_id": "toolu_123",
        "tool_input": {"command": 'TOKEN=private-value API_KEY="unknown-sensitive-value" npm test ' + "x" * 300,
                       "irrelevant_prompt": "never-store-me"},
    }, {"tool_name": "Edit", "tool_use_id": "toolu_456", "tool_input": {"file_path": "/protected/file"}}]}
    with pytest.raises(AgentPermissionError) as caught:
        adapter.extract_output(json.dumps({"type": "result", "subtype": "success", **envelope}) if not adapter.reviewer else json.dumps(envelope), "", {})
    assert "[PERMISSION_DENIAL]" in caplog.text
    assert "toolu_123" in caplog.text and "npm test" in caplog.text
    assert "private-value" not in caplog.text
    assert "npm test" in str(caught.value)
    assert "private-value" not in str(caught.value)
    assert "unknown-sensitive-value" not in str(caught.value)
    failure = classify_agent_failure(adapter.name, caught.value, invocation_id="denial-test")
    assert failure.kind.value == "permission"
    stored = failure.provider_data["permission_denials"]
    assert stored[0]["tool_name"] == "Bash" and stored[0]["tool_use_id"] == "toolu_123"
    assert len(stored[0]["input_excerpt"]) <= 201
    assert stored[1]["input_excerpt"] == "/protected/file"
    assert "never-store-me" not in json.dumps(failure.provider_data)
    assert stored == [{key: value for key, value in item.items() if key != "disposition"}
                      for item in adapter.metadata["permission_denials"]]


def test_denial_summary_survives_authoritative_record_roundtrip_without_retry():
    from types import SimpleNamespace
    from agent_runtime import QuotaWaitPolicy, TransientRetryPolicy
    from contracts import AgentRole
    from error_classification import classify_exception
    from workflow_state import init_workflow_state
    from workflow_failure_recording import _invocation_retry_decision, _invocation_failure_documents
    from artifact_models import ArtifactRecord, PermissionDenialSummary
    from test_artifact_models import _record

    state = init_workflow_state(run_id="denials", task_file="/tmp/task.md", branch="feature/denials",
        branch_base="a" * 40, first_slice_start_commit="a" * 40, slice_count=1)
    summaries = [{"tool_name": "Bash", "tool_use_id": "toolu_123", "input_excerpt": "npm test"}]
    error = classify_agent_failure("provider", AgentPermissionError("denied", provider_data={"permission_denials": summaries}),
                                   invocation_id="denial-roundtrip")
    classified = classify_exception(error)
    context = SimpleNamespace(quota_wait_policy=QuotaWaitPolicy(), transient_retry_policy=TransientRetryPolicy(),
                              max_transport_failures=3, max_contract_rejections=3)
    decision = _invocation_retry_decision(state=state, context=context, role=AgentRole.IMPLEMENTER,
        error=error, disposition_limit_failure=False, native_response_retry_allowed=True,
        fingerprint="a" * 64, now_utc=error.received_at, diagnostic_code=classified.diagnostic_code)
    assert decision.automatic is False
    _, payload, _ = _invocation_failure_documents(state=state, role=AgentRole.IMPLEMENTER, error=error,
        classified=classified, fingerprint="a" * 64, decision=decision)
    record = _record(payload)
    assert record.to_dict()["payload"]["permission_denials"] == summaries
    assert ArtifactRecord.from_dict(record.to_dict()).payload.permission_denials == (PermissionDenialSummary(**summaries[0]),)
    from dataclasses import replace
    assert "permission_denials" not in _record(replace(payload, permission_denials=())).to_dict()["payload"]


@pytest.mark.parametrize("field,value", [("tool_name", "x" * 66), ("tool_use_id", "x" * 102),
                                         ("input_excerpt", "x" * 202), ("input_excerpt", "bad\ninput")])
def test_denial_record_rejects_unbounded_or_control_input(field, value):
    from artifact_models import PermissionDenialSummary, ArtifactValidationError
    data = {"tool_name": "Bash", "tool_use_id": "toolu_id", "input_excerpt": "npm test"}
    data[field] = value
    with pytest.raises(ArtifactValidationError):
        PermissionDenialSummary(**data)


@pytest.mark.parametrize("command", ["npm test", "echo quota limit reached", "echo timeout 401"])
def test_diagnostic_command_does_not_reclassify_permission_failure(command):
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: classification preservation
        AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: denied command
    with pytest.raises(AgentPermissionError) as caught:
        adapter.extract_output(json.dumps({"type": "result", "subtype": "success", "is_error": False, "permission_denials": [
            {"tool_name": "Bash", "tool_use_id": "toolu_x", "tool_input": {"command": command}}]}), "", {})
    assert classify_agent_failure(adapter.name, caught.value, invocation_id="unchanged-kind").kind.value == "permission"


@pytest.mark.parametrize("denials", ["invalid-denials", {"command": "npm test"}, [None]])
def test_implementer_keeps_original_fail_closed_check_for_malformed_denials(denials):
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: preserve denial failure
        AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: malformed denial
    with pytest.raises(AgentPermissionError):
        adapter.extract_output(json.dumps({"type": "result", "subtype": "success", "is_error": False, "permission_denials": denials}), "", {})


@pytest.mark.parametrize("command", ['npm test --token secret-value', 'curl --password "secret-value"',
                                    'curl -H "Authorization: Basic secret-value"', 'TOKEN=secret-value npm test'])
def test_denial_command_scrubs_literal_credentials_before_truncation(command):
    from provider_metrics import permission_denial_summaries
    result = permission_denial_summaries([{"tool_name": "Bash", "tool_use_id": "toolu_x",
                                         "tool_input": {"command": command}}])
    assert "secret-value" not in json.dumps(result)
    assert "[redacted]" in result[0]["input_excerpt"]


def test_live_stream_real_fields_are_compact_redacted_and_hide_results(monkeypatch):
    from agent_runtime import _compact_stream_text
    monkeypatch.setenv("DAO_SECRET_TOKEN", "private-value")
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: stream formatting coverage
        AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: stream fixture
    state = {}
    def render(event):
        return _compact_stream_text(adapter, "stdout", json.dumps(event), state)
    init = {"type": "system", "subtype": "init", "model": "claude-opus-5-5",  # allowlist:provider -- transport: actual model fixture
            "claude_code_version": "2.1.285", "tools": ["Write", "Bash"], "permissionMode": "acceptEdits",  # allowlist:provider -- profile configuration: real event or smoke path fixture
            "plugins": [], "cwd": "/tmp/fake-repo"}
    assert render(init) == "claude model=claude-opus-5-5 version=2.1.285"  # allowlist:provider -- transport: compact init expectation
    assert render(init) is None
    tool = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash",
            "input": {"command": "TOKEN=private-value npm test " + "x" * 300}}]}}
    text = render(tool)
    assert "tool=Bash" in text and "npm test" in text and "private-value" not in text and len(text) < 250
    live = render({"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_x"})
    assert "[PERMISSION_DENIAL]" in live and "npm test" in live and "private-value" not in live
    for content in ("error private-value", [{"type": "text", "text": "error private-value"}]):
        error = render({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_x", "is_error": True, "content": content}]}})
        assert "tool_error=" in error and "private-value" not in error
    assert render({"type": "user", "message": {"content": [{"type": "tool_result", "content": "normal", "is_error": False}]}}) is None
    assert "text=Working [redacted]" == render({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working private-value"}]}})
    assert render({"type": "result", "subtype": "success", "structured_output": {"secret": "private-value"}}) is None
    assert _compact_stream_text(adapter, "stdout", 'broken', state) is None
    assert _compact_stream_text(adapter, "stdout", '[]', state) is None


def test_success_attempt_denials_reach_terminal_callback_and_roundtrip():
    from dataclasses import replace
    from agent_runtime import ProviderAttemptLifecycle, _ProviderAttemptInvocation
    from artifact_models import ArtifactRecord, AttemptPermissionDenial, Role
    from test_artifact_models import _attempt_for_pair, _record, CREATED_AT
    captured = []
    invocation = _ProviderAttemptInvocation(ProviderAttemptLifecycle(
        start=lambda *args: "handle", terminal=lambda *args, **kwargs: captured.append((args, kwargs)), monotonic_fn=lambda: 1.0))
    invocation.begin(None, None)
    summary = {"tool_name": "Bash", "tool_use_id": "toolu_x", "input_excerpt": "npm test", "disposition": "tolerated"}
    invocation.finish(None, {"permission_denials": [summary]})
    invocation.finish(None, {"permission_denials": [summary]})
    assert len(captured) == 1
    denials = captured[0][1]["permission_denials"]
    payload = replace(_attempt_for_pair("codex", Role.IMPLEMENTER), phase="succeeded",  # allowlist:provider -- certification data: generic attempt fixture
                      ended_at=CREATED_AT, duration_seconds=1.0, permission_denials=denials)
    record = _record(payload)
    assert record.to_dict()["payload"]["permission_denials"] == [summary]
    assert ArtifactRecord.from_dict(record.to_dict()).payload.permission_denials == (AttemptPermissionDenial(**summary),)
    assert "permission_denials" not in _record(replace(payload, permission_denials=())).to_dict()["payload"]
    from artifact_models import ArtifactValidationError
    with pytest.raises(ArtifactValidationError):
        replace(payload, phase="started")
    with pytest.raises(ArtifactValidationError):
        replace(payload, permission_denials=(AttemptPermissionDenial(**{**summary, "disposition": "violation"}),))


@pytest.mark.parametrize("mode", ["compact", "full"])
def test_fake_stream_logs_denial_immediately_as_warning_without_secrets(tmp_path, monkeypatch, caplog, mode):
    import logging
    import os
    import sys
    import agent_runtime
    from agent_runtime import OrchestratorConfig
    monkeypatch.setenv("DAO_SECRET_TOKEN", "private-value")
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: subprocess stream fake
        AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: stream fake
    events = [{"type": "system", "subtype": "init", "model": "claude-opus-5-5",  # allowlist:provider -- transport: fake actual model
               "claude_code_version": "2.1.285"},  # allowlist:provider -- profile configuration: real event or smoke path fixture
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "TOKEN=private-value npm test"}}]}},
              {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_x"},
              {"type": "result", "subtype": "success", "structured_output": {"hidden": "do-not-log"}}]
    stream = '\n'.join(json.dumps(event) for event in events) + '\n'
    caplog.set_level(logging.INFO)
    # Python is the fake producer; no provider executable is started.
    code = "import sys; sys.stdout.write(sys.argv[1]); sys.stdout.flush()"
    result = agent_runtime._run_agent_process(adapter, [sys.executable, "-c", code, stream], None,
        config=OrchestratorConfig(repo_root=tmp_path, agent_live_stream=True, agent_live_stream_mode=mode),
        env=os.environ.copy(), execution_root=tmp_path, timeout_seconds=5, agent_key="implementer")
    assert result.stdout == stream and result.returncode == 0
    warnings = [record for record in caplog.records if "[PERMISSION_DENIAL]" in record.message]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert "npm test" in warnings[0].message
    assert "private-value" not in caplog.text and "do-not-log" not in caplog.text
    assert "model=claude-opus-5-5 version=2.1.285" in caplog.text  # allowlist:provider -- transport: fake init assertion


def test_live_input_redacts_literal_json_credential_fields():
    from provider_metrics import compact_stream_event
    text = compact_stream_event({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Example",
        "input": {"api_key": "unknown-secret-value", "password": "unknown-password-value", "ordinary": "visible"}}]}}, {})
    assert "unknown-secret-value" not in text and "unknown-password-value" not in text
    assert "visible" in text and "[redacted]" in text


@pytest.mark.parametrize("actual,changed", [("initial-model", False), ("replacement-model", True)])
def test_observed_models_warn_only_on_drift(actual, changed, caplog):
    from provider_metrics import actual_model_metrics
    events = [{"type": "system", "subtype": "init", "model": "initial-model"},
              {"type": "assistant", "message": {"model": actual}},
              {"type": "result", "modelUsage": {actual: {}}}]
    assert actual_model_metrics(events) == {"init_model": "initial-model", "actual_models": [actual]}
    assert ("[MODEL_CHANGE]" in caplog.text) == changed
    assert actual_model_metrics([{"type": "turn.completed", "usage": {}}]) == {}
    assert actual_model_metrics([{"type": "turn.completed", "model": actual}]) == {"actual_models": [actual]}


def test_real_model_switch_reaches_adapter_metadata_and_warns(tmp_path, caplog):
    from pathlib import Path
    from test_claude_implementer_adapter import _prepared, _valid_plan  # allowlist:provider -- transport: reduced live stream fixture
    recording = json.loads((Path(__file__).parent / "fixtures/phase0-traces/s1/claude-W4.json").read_text())  # allowlist:provider -- transport: reduced live stream fixture
    events = [json.loads(line) for line in recording["run"]["stdout"].splitlines()]
    _, adapter, bundle, _ = _prepared(tmp_path)
    events[-1]["structured_output"] = {"result": _valid_plan(bundle)}
    try:
        result = json.loads(adapter.extract_output("\n".join(json.dumps(e) for e in events), "", {}))
        assert result["request_id"] == bundle.bound_context.request_id
        assert len(adapter.metadata["actual_models"]) == 2
        assert adapter.metadata["init_model"] in adapter.metadata["actual_models"]
        assert "[MODEL_CHANGE]" in caplog.text
    finally:
        adapter.cleanup()


def test_model_metadata_reaches_attempt_callback_and_record_roundtrip():
    from dataclasses import replace
    from agent_runtime import ProviderAttemptLifecycle, _ProviderAttemptInvocation
    from artifact_models import ArtifactRecord, ArtifactValidationError, Role
    from test_artifact_models import _attempt_for_pair, _record, CREATED_AT
    captured = []
    invocation = _ProviderAttemptInvocation(ProviderAttemptLifecycle(
        start=lambda *args: "handle", terminal=lambda *args, **kw: captured.append(kw), monotonic_fn=lambda: 1.0))
    invocation.begin(None, None)
    invocation.finish(None, {"init_model": "model-a", "actual_models": ["model-a", "model-b"]})
    telemetry = captured[0]
    assert telemetry == {"init_model": "model-a", "actual_models": ("model-a", "model-b")}
    started = _attempt_for_pair("codex", Role.IMPLEMENTER)  # allowlist:provider -- certification data: generic attempt fixture
    terminal = replace(started, phase="succeeded", ended_at=CREATED_AT, duration_seconds=1, **telemetry)
    document = _record(terminal).to_dict()
    assert document["payload"]["actual_models"] == ["model-a", "model-b"]
    assert ArtifactRecord.from_dict(document).to_dict() == document
    historical = _record(started).to_dict()
    assert "actual_models" not in historical["payload"] and "init_model" not in historical["payload"]
    assert ArtifactRecord.from_dict(historical).to_dict() == historical
    with pytest.raises(ArtifactValidationError):
        replace(started, **telemetry)
    with pytest.raises(ArtifactValidationError):
        replace(terminal, actual_models=("model-b", "model-a"))
