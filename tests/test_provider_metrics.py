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
        adapter.extract_output(json.dumps(envelope), "", {})
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
    assert stored == adapter.metadata["permission_denials"]


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
        adapter.extract_output(json.dumps({"is_error": False, "permission_denials": [
            {"tool_name": "Bash", "tool_use_id": "toolu_x", "tool_input": {"command": command}}]}), "", {})
    assert classify_agent_failure(adapter.name, caught.value, invocation_id="unchanged-kind").kind.value == "permission"


@pytest.mark.parametrize("denials", ["invalid-denials", {"command": "npm test"}, [None]])
def test_implementer_keeps_original_fail_closed_check_for_malformed_denials(denials):
    adapter = NativeClaudeImplementerAdapter(  # allowlist:provider -- transport: preserve denial failure
        AgentSettings("claude", "claude", "opus", None, "high"))  # allowlist:provider -- profile configuration: malformed denial
    with pytest.raises(AgentPermissionError):
        adapter.extract_output(json.dumps({"is_error": False, "permission_denials": denials}), "", {})


@pytest.mark.parametrize("command", ['npm test --token secret-value', 'curl --password "secret-value"',
                                    'curl -H "Authorization: Basic secret-value"', 'TOKEN=secret-value npm test'])
def test_denial_command_scrubs_literal_credentials_before_truncation(command):
    from provider_metrics import permission_denial_summaries
    result = permission_denial_summaries([{"tool_name": "Bash", "tool_use_id": "toolu_x",
                                         "tool_input": {"command": command}}])
    assert "secret-value" not in json.dumps(result)
    assert "[redacted]" in result[0]["input_excerpt"]
