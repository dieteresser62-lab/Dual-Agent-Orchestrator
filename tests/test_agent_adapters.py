from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_adapters import (
    AgentBudgetError,
    AgentOutputError,
    AgentPermissionError,
    AntigravityAdapter,
    CLAUDE_REVIEW_PACKET_CHUNK_CHARS,
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
        assert "review-packet-001.md" in manifest_path.read_text(encoding="utf-8")
        assert str(manifest_path) in command[-1]
        assert "2 Read calls total" in command[-1]
        assert "Do not run tests or the review harness" in command[-1]
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
        assert all(path.name in manifest for path in chunks)
        expected_calls = len(chunks) + 1
        policy = command[command.index("--system-prompt") + 1]
        assert f"exactly {expected_calls} Read calls" in policy
        assert f"{expected_calls} Read calls total" in command[-1]
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

    with pytest.raises(AgentOutputError, match="non-success"):
        adapter.extract_output(
            json.dumps({"status": "ERROR", "response": "permission denied"}),
            "",
            {},
        )


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
