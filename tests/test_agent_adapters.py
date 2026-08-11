from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_adapters import (
    AgentBudgetError,
    AgentOutputError,
    AgentPermissionError,
    AntigravityAdapter,
    ClaudeAdapter,
    CodexAdapter,
    build_agent_registry,
)
from agent_config import AgentSettings


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
        assert command[command.index("--tools") + 1] == "Bash,Read"
        allowed = command[command.index("--allowedTools") + 1]
        assert allowed.startswith("Read,Bash(")
        assert "review_harness.py" in allowed
        assert command[command.index("--permission-mode") + 1] == "dontAsk"
        assert command[command.index("--disallowedTools") + 1] == (
            "Edit,Write,NotebookEdit,Grep,Glob"
        )
        assert "--safe-mode" in command
        assert "--strict-mcp-config" in command
        assert command[command.index("--prompt-suggestions") + 1] == "false"
        assert "--system-prompt" in command
        assert "--append-system-prompt" not in command
        policy = command[command.index("--system-prompt") + 1]
        assert "at most 6 tool calls" in policy
        assert "do not explore the repository" in policy
        schema = json.loads(command[command.index("--json-schema") + 1])
        assert schema["properties"]["response"]["maxLength"] == 12_000
        assert command[command.index("--max-budget-usd") + 1] == "1.25"
        assert "--no-session-persistence" in command
        assert "plan" not in command
        assert "opus" not in command
        assert "secret long prompt" not in command
        packet_dir = Path(command[command.index("--add-dir") + 1])
        packet_path = packet_dir / "review-packet.md"
        assert packet_path.read_text(encoding="utf-8") == "secret long prompt"
        assert str(packet_path) in command[-1]
        assert "at most 6 tool calls" in command[-1]
        assert use_stdin is False
    finally:
        adapter.cleanup()

    assert not packet_dir.exists()


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


def test_reviewer_harness_is_rebound_into_disposable_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    harness = source / "src" / "review_harness.py"
    harness.parent.mkdir(parents=True)
    snapshot.mkdir()
    adapter = ClaudeAdapter(_settings("claude"), review_harness=harness)
    adapter.bind_reviewer_workspace(source, snapshot)
    command, _ = adapter.build_command("prompt")

    try:
        allowed = command[command.index("--allowedTools") + 1]
        assert str(snapshot / "src" / "review_harness.py") in allowed
        assert str(source / "src" / "review_harness.py") not in allowed
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
        assert command[command.index("--print-timeout") + 1] == "900s"
        assert "--sandbox" in command
        assert "--dangerously-skip-permissions" in command
        assert "--mode" not in command
        assert print_index == len(command) - 2
        assert long_prompt not in command
        assert str(prompt_path) in command[-1]
        assert prompt_path.read_text(encoding="utf-8") == long_prompt
        assert use_stdin is False
    finally:
        adapter.cleanup()

    assert not add_dir.exists()


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

    with pytest.raises(AgentOutputError, match="non-success"):
        adapter.extract_output(
            json.dumps({"status": "ERROR", "response": "permission denied"}),
            "",
            {},
        )


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
