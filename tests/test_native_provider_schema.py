from __future__ import annotations

import copy

import pytest

from native_provider_schema import (
    NativeProviderSchemaError,
    assert_provider_capabilities,
    compatible_cli_version,
    defensive_provider_projection,
    load_capability_table,
    load_exception_table,
    normalize_transport_profile,
    provider_capability,
    registered_exceptions,
)


def _codex_command(*, model: str = "gpt-5.6-sol", effort: str = "medium") -> list[str]:
    return [
        "/opt/bin/codex",
        "exec",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--json",
        "--output-schema",
        "/tmp/runtime-a/schema.json",
        "--output-last-message",
        "/tmp/runtime-a/last.json",
        "-",
    ]


def _claude_command(*, budget: str | None = None) -> list[str]:
    command = [
        "/opt/bin/claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--tools",
        "Read",
        "--allowedTools",
        "Read",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "user",
        "--safe-mode",
        "--strict-mcp-config",
        "--prompt-suggestions",
        "false",
        "--add-dir",
        "/tmp/runtime-a",
        "--system-prompt",
        "policy",
        "--json-schema",
        "{}",
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
    if budget is not None:
        command.extend(("--max-budget-usd", budget))
    command.append("directive")
    return command


def test_capability_and_exception_tables_are_typed_and_versioned() -> None:
    capabilities = load_capability_table()
    exceptions = load_exception_table()

    assert capabilities["schema_version"] == "native-provider-schema-capabilities-v1"
    assert [item["provider"] for item in capabilities["providers"]] == [
        "claude",
        "codex",
    ]
    assert exceptions["schema_version"] == "native-provider-schema-exceptions-v1"
    assert len(registered_exceptions("codex")) == 7
    assert len(registered_exceptions("claude")) == 6


def test_unprobed_feature_and_stale_version_fail_closed() -> None:
    with pytest.raises(NativeProviderSchemaError, match="not positively probed"):
        assert_provider_capabilities("codex", ("positional_tuple",))
    assert compatible_cli_version("claude", "2.1.246 (Claude Code)") is True
    assert compatible_cli_version("claude", "2.9.0 (Claude Code)") is True
    assert compatible_cli_version("codex", "codex-cli 0.147.1") is True
    for provider, version in (
        ("claude", "2.1.240 (Claude Code)"),
        ("claude", "3.0.0 (Claude Code)"),
        ("codex", "codex-cli 0.146.9"),
        ("codex", "codex-cli 0.148.0"),
    ):
        with pytest.raises(NativeProviderSchemaError, match="CLI version differs"):
            assert_provider_capabilities(provider, (), cli_version=version)


def test_unknown_cli_version_format_fails_closed() -> None:
    with pytest.raises(NativeProviderSchemaError, match="unsupported format"):
        compatible_cli_version("claude", "Claude Code development build")


def test_codex_transport_profile_ignores_only_isolation_and_runtime_paths() -> None:
    production = _codex_command()
    probe = _codex_command()
    probe[probe.index("--sandbox") + 1] = "read-only"
    probe[probe.index("--output-schema") + 1] = "/tmp/other/schema.json"
    probe[probe.index("--output-last-message") + 1] = "/tmp/other/result.json"

    expected = normalize_transport_profile("codex", production)
    assert normalize_transport_profile("codex", probe) == expected
    assert expected.document == provider_capability("codex")["transport_profile"]
    assert normalize_transport_profile(
        "codex", _codex_command(model="different")
    ) != expected
    assert normalize_transport_profile(
        "codex", _codex_command(effort="high")
    ) != expected


def test_claude_budget_is_deliberately_profile_neutral_for_c25() -> None:
    without_budget = normalize_transport_profile("claude", _claude_command())
    with_budget = normalize_transport_profile(
        "claude", _claude_command(budget="1.25")
    )

    assert with_budget == without_budget
    assert with_budget.document == provider_capability("claude")["transport_profile"]


def test_unknown_command_argument_is_profile_drift() -> None:
    command = _codex_command()
    command.insert(-1, "--future-switch")
    with pytest.raises(NativeProviderSchemaError, match="unclassified"):
        normalize_transport_profile("codex", command)


def test_defensive_projection_does_not_mutate_reader_schema() -> None:
    base = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "pattern": "^(?!/).+$",
            },
            "items": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string"},
            },
        },
    }
    before = copy.deepcopy(base)

    projected = defensive_provider_projection(
        base,
        provider="codex",
        required_features=("closed_object",),
    )

    assert base == before
    assert "pattern" not in projected["properties"]["path"]
    assert "uniqueItems" not in projected["properties"]["items"]
