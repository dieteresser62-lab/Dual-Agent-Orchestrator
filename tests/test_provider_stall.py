"""Provider-free checks for model silence and adapter-owned tool lifetimes."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import agent_runtime
from agent_adapters import CodexToolActivity, ClaudeToolActivity  # allowlist:provider -- transport: event protocol tests
from agent_config import AgentConfigError, default_agent_settings, parse_profile_tables
from orchestrator_diagnostics import OrchestratorDiagnostic
from workflow_state import AgentFailureKind


def _adapter(protocol, limit=0.3):
    return SimpleNamespace(
        supports_stall_detection=protocol.supports_stall_detection,
        tool_activity=protocol.tool_activity, stall_timeout_seconds=limit,
        bound_slot="implementer", reviewer=False, suppress_live_stream=True,
    )


def _run(tmp_path, adapter, code, live=False, timeout=None):
    return agent_runtime._run_agent_process(
        adapter, [sys.executable, "-u", "-c", code], None,
        config=agent_runtime.OrchestratorConfig(repo_root=tmp_path, agent_live_stream=live),
        env=os.environ.copy(), execution_root=tmp_path, timeout_seconds=timeout,
        agent_key="test-provider", operation="implementer_slice",
    )


@pytest.mark.parametrize("live", [False, True])
def test_model_silence_stalls_without_stdout(tmp_path, caplog, live):
    with pytest.raises(agent_runtime.AgentProcessError) as error:
        _run(tmp_path, _adapter(CodexToolActivity), "import time; time.sleep(20)", live)  # allowlist:provider -- transport: JSON protocol fixture
    failure = agent_runtime.classify_agent_failure("test-provider", error.value, invocation_id="stall-1")
    assert failure.kind is AgentFailureKind.NETWORK
    assert failure.orchestrator_diagnostic is OrchestratorDiagnostic.PROVIDER_STALLED
    assert "minutes of model silence" in failure.technical_text
    assert "last activity" in failure.technical_text
    assert "provider=test-provider role=implementer operation=implementer_slice" in caplog.text
    assert "provider stalled" in caplog.text


@pytest.mark.parametrize("protocol,started,completed", [
    (CodexToolActivity, {"type": "item.started", "item": {"id": "t1", "type": "command_execution"}},  # allowlist:provider -- transport: JSON protocol fixture
     {"type": "item.completed", "item": {"id": "t1", "type": "command_execution"}}),
    (ClaudeToolActivity, {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1"}]}},  # allowlist:provider -- transport: stream-json protocol fixture
     {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}}),
])
@pytest.mark.parametrize("live", [False, True])
def test_long_tool_execution_pauses_model_silence(tmp_path, protocol, started, completed, live):
    code = (f"import time; print({json.dumps(started)!r}, flush=True); time.sleep(0.8); "
            f"print({json.dumps(completed)!r}, flush=True); print('completed', flush=True)")
    result = _run(tmp_path, _adapter(protocol), code, live)
    assert result.returncode == 0
    assert result.stdout.endswith("completed\n")


def test_stdout_resets_clock_and_internal_stream_remains_hidden(tmp_path, caplog):
    caplog.set_level("INFO", logger="agent_runtime")
    result = _run(tmp_path, _adapter(CodexToolActivity, 0.5),  # allowlist:provider -- transport: internal event stream test
                  "import time\nfor i in range(5):\n print('activity', flush=True); time.sleep(0.2)")
    assert result.stdout == "activity\n" * 5
    assert "activity" not in caplog.text


def test_stderr_does_not_reset_model_clock(tmp_path):
    with pytest.raises(agent_runtime.AgentProcessError, match="provider stalled"):
        _run(tmp_path, _adapter(CodexToolActivity),  # allowlist:provider -- transport: stdout-only clock test
             "import sys,time\nfor i in range(100):\n print('noise', file=sys.stderr, flush=True); time.sleep(0.02)")


def test_continuously_queued_stderr_cannot_hide_a_stall(tmp_path):
    with pytest.raises(agent_runtime.AgentProcessError, match="provider stalled"):
        _run(tmp_path, _adapter(CodexToolActivity),  # allowlist:provider -- transport: stderr backlog must not defer model clock
             "import os\nwhile True:\n os.write(2, b'noise\\n' * 10000)", timeout=3)


def test_zero_disables_stall_detection(tmp_path):
    assert _run(tmp_path, _adapter(CodexToolActivity, 0),  # allowlist:provider -- transport: disable event detection
                "import time; time.sleep(0.6); print('completed')").returncode == 0


def test_print_transports_keep_only_total_timeout(tmp_path):
    from agent_adapters import _review_transports
    print_transports = [transport for transport in _review_transports().values()
                        if not transport.supports_stall_detection]
    assert len(print_transports) == 2
    adapter = SimpleNamespace(supports_stall_detection=False, stall_timeout_seconds=0.01)
    assert _run(tmp_path, adapter, "import time; time.sleep(0.6); print('completed')").returncode == 0
    with pytest.raises(subprocess.TimeoutExpired):
        _run(tmp_path, adapter, "import time; time.sleep(20)", timeout=0.3)


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call", "dynamic_tool_call"])
def test_matching_tool_ids_and_overlapping_tools(item_type):
    clock = agent_runtime._ModelSilence(0)
    protocol = CodexToolActivity  # allowlist:provider -- transport: tool item coverage
    def observe(kind, identity, now):
        clock.observe(protocol, json.dumps({"type": kind, "item": {"id": identity, "type": item_type}}), now)
    observe("item.started", "a", 10)
    observe("item.started", "b", 20)
    observe("item.completed", "unknown", 30)
    observe("item.completed", "a", 40)
    assert clock.duration(10000) == 0
    observe("item.completed", "b", 10000)
    assert clock.duration(10003) == 3


def test_model_items_and_malformed_lines_do_not_pause_clock():
    clock = agent_runtime._ModelSilence(0)
    for line in ('not json', '[]', '{"type":"item.started","item":{"type":"reasoning","id":"m"}}'):
        clock.observe(CodexToolActivity, line, 10)  # allowlist:provider -- transport: model work stays timed
        assert clock.duration(20) == 10


def test_stream_adapter_registration_and_default():
    from agent_adapters import NativeCodexAdapter  # allowlist:provider -- transport: production event observer
    from codex_review_adapter import NativeCodexReviewAdapter  # allowlist:provider -- transport: production event observer
    from claude_implementer_adapter import NativeClaudeImplementerAdapter  # allowlist:provider -- transport: production event observer
    for transport in (NativeCodexAdapter, NativeCodexReviewAdapter, NativeClaudeImplementerAdapter):  # allowlist:provider -- transport: all supported streams
        assert transport.supports_stall_detection is True
    assert {settings.stall_timeout_seconds for settings in default_agent_settings().values()} == {900}


@pytest.mark.parametrize("value", [0, 17, 900])
def test_profile_stall_timeout(value):
    _, profiles = parse_profile_tables(None, {"implementation": {"stall_timeout_seconds": value}})
    assert profiles["implementation"].stall_timeout_seconds == value


@pytest.mark.parametrize("value", [-1, True, 0.5, "900", None])
def test_invalid_profile_stall_timeout(value):
    with pytest.raises(AgentConfigError, match="stall_timeout_seconds"):
        parse_profile_tables(None, {"implementation": {"stall_timeout_seconds": value}})


def test_tool_completion_restarts_silence_clock(tmp_path):
    started = json.dumps({"type": "item.started", "item": {"id": "t1", "type": "command_execution"}})
    completed = json.dumps({"type": "item.completed", "item": {"id": "t1", "type": "command_execution"}})
    with pytest.raises(agent_runtime.AgentProcessError, match="provider stalled.*last activity at elapsed 0.[89]s"):
        _run(tmp_path, _adapter(CodexToolActivity),  # allowlist:provider -- transport: clock resumes after tool completion
             f"import time; print({started!r},flush=True); time.sleep(0.8); print({completed!r},flush=True); time.sleep(20)")


def test_total_timeout_remains_active_during_tools(tmp_path):
    started = json.dumps({"type": "item.started", "item": {"id": "t1", "type": "command_execution"}})
    with pytest.raises(subprocess.TimeoutExpired):
        _run(tmp_path, _adapter(CodexToolActivity, 0.1),  # allowlist:provider -- transport: independent hard deadline
             f"import time; print({started!r},flush=True); time.sleep(20)", timeout=0.4)


def test_parallel_tool_blocks_only_resume_after_matching_results():
    clock = agent_runtime._ModelSilence(0)
    protocol = ClaudeToolActivity  # allowlist:provider -- transport: matching stream-json tool IDs
    clock.observe(protocol, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a"}, {"type": "tool_use", "id": "b"},
    ]}}), 10)
    for identity in ("unknown", "a"):
        clock.observe(protocol, json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": identity, "is_error": True},
        ]}}), 20)
        assert clock.duration(10000) == 0
    clock.observe(protocol, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "b"},
    ]}}), 10000)
    assert clock.duration(10003) == 3


def test_heartbeat_reports_model_silence_without_live_display(tmp_path, monkeypatch, caplog):
    import time
    real_monotonic = time.monotonic
    start = real_monotonic()
    monkeypatch.setattr(agent_runtime, "time", SimpleNamespace(
        monotonic=lambda: (real_monotonic() - start) * 100,
        sleep=time.sleep,
    ))
    caplog.set_level("INFO", logger="agent_runtime")
    _run(tmp_path, _adapter(CodexToolActivity, 900),  # allowlist:provider -- transport: accelerated heartbeat test
         "import time; time.sleep(0.6); print('completed')")
    assert "still running (elapsed:" in caplog.text
    assert "model silence:" in caplog.text


def test_stall_terminates_descendants_with_the_process_group(tmp_path):
    from provider_process import _proc_stat
    pid_file = tmp_path / "child.pid"
    code = ("import subprocess,sys,time\n"
            "child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])\n"
            f"open({str(pid_file)!r},'w').write(str(child.pid))\n"
            "time.sleep(20)\n")
    child_states = []
    original_stop = agent_runtime._stop_provider_group
    # Capture the child before cleanup; a PID alone would be vulnerable to reuse.
    from unittest.mock import patch
    def stop(*args, **kwargs):
        if pid_file.exists():
            child_states.append(_proc_stat(int(pid_file.read_text())))
        return original_stop(*args, **kwargs)
    with patch.object(agent_runtime, "_stop_provider_group", stop):
        with pytest.raises(agent_runtime.AgentProcessError, match="provider stalled"):
            _run(tmp_path, _adapter(CodexToolActivity), code)  # allowlist:provider -- transport: group cleanup on stall
    assert child_states and child_states[0] is not None
    current = _proc_stat(int(pid_file.read_text()))
    assert current is None or current.start_ticks != child_states[0].start_ticks or current.state == "Z"
