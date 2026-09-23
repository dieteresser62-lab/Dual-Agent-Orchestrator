from __future__ import annotations

import argparse
import copy
import dataclasses
import inspect
import json

import pytest

import native_provider_schema
from agent_config import MODEL_FAMILIES, add_agent_arguments, resolve_agent_settings
from native_provider_schema import (
    NativeProviderSchemaError,
    OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS,
    assert_projected_provider_schema,
    assert_provider_capabilities,
    bind_required_empty_array,
    compatible_cli_version,
    defensive_provider_projection,
    load_capability_table,
    load_exception_table,
    normalize_transport_profile,
    provider_capability,
    registered_exceptions,
)
from workflow_run_setup import _fresh_state
from workflow_state import ProtocolBinding


def _codex_command(*, model: str = "gpt-6-sol", effort: str = "medium") -> list[str]:
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
        "opus",
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
    assert {
        item["version_policy"] for item in capabilities["providers"]
    } == {"same-major-forward"}
    assert {
        frozenset(item["features"]) for item in capabilities["providers"]
    } == {
        frozenset(
            {
                "closed_object",
                "const_list",
                "min_max_items",
                "nested_any_of",
                "nested_one_of",
                "positional_tuple",
            }
        )
    }
    assert exceptions["schema_version"] == "native-provider-schema-exceptions-v1"
    assert len(registered_exceptions("codex")) == 7
    assert len(registered_exceptions("claude")) == 6


def test_every_provider_must_use_the_shared_forward_version_policy(
    monkeypatch,
    tmp_path,
) -> None:
    capabilities = load_capability_table()
    capabilities["providers"][0]["version_policy"] = "same-minor-forward"
    capability_path = tmp_path / "capabilities.json"
    capability_path.write_text(json.dumps(capabilities), encoding="utf-8")
    monkeypatch.setattr(native_provider_schema, "CAPABILITY_PATH", capability_path)

    with pytest.raises(NativeProviderSchemaError, match="provider-wide version policy"):
        native_provider_schema.load_capability_table()


def test_every_default_site_selects_sol_and_opus_at_high_effort() -> None:
    parser = argparse.ArgumentParser()
    add_agent_arguments(parser)
    settings = resolve_agent_settings(parser.parse_args([]), {})
    binding_defaults = {
        field.name: field.default for field in dataclasses.fields(ProtocolBinding)
    }
    setup_defaults = inspect.signature(_fresh_state).parameters
    expected = {"codex": ("gpt-6-sol", "high"), "claude": ("opus", "high")}
    for role in ("codex", "claude"):
        assert expected[role][0] in MODEL_FAMILIES[role].values()
        assert (settings[role].model, settings[role].effort) == expected[role]
        for default in (
            binding_defaults[f"{role}_profile"],
            setup_defaults[f"{role}_profile"].default,
        ):
            assert (default.model, default.effort) == expected[role]


def test_model_and_effort_are_recorded_but_do_not_bind_the_transport() -> None:
    for model in MODEL_FAMILIES["codex"].values():
        for effort in ("low", "xhigh"):
            profile = normalize_transport_profile(
                "codex", _codex_command(model=model, effort=effort)
            )
            assert (profile.model, profile.reasoning_or_effort) == (model, effort)
            assert_provider_capabilities("codex", (), profile=profile)
    for model in MODEL_FAMILIES["claude"].values():
        command = _claude_command()
        command[command.index("--model") + 1] = model
        command[command.index("--effort") + 1] = "max"
        assert_provider_capabilities(
            "claude", (), profile=normalize_transport_profile("claude", command)
        )

    probed = normalize_transport_profile("codex", _codex_command())
    drifted = dataclasses.replace(
        probed, semantic_flags=(*probed.semantic_flags, "--future-switch")
    )
    with pytest.raises(NativeProviderSchemaError, match="transport profile differs"):
        assert_provider_capabilities("codex", (), profile=drifted)


def test_projection_feature_vocabulary_cannot_drift_from_capability_table(
    monkeypatch,
    tmp_path,
) -> None:
    capabilities = load_capability_table()
    del capabilities["providers"][1]["features"]["const_list"]
    capability_path = tmp_path / "capabilities.json"
    capability_path.write_text(json.dumps(capabilities), encoding="utf-8")
    monkeypatch.setattr(native_provider_schema, "CAPABILITY_PATH", capability_path)

    with pytest.raises(NativeProviderSchemaError, match="projection guard contract"):
        native_provider_schema.load_capability_table()


def test_unprobed_feature_and_out_of_policy_version_fail_closed() -> None:
    with pytest.raises(NativeProviderSchemaError, match="not positively probed"):
        assert_provider_capabilities("codex", ("positional_tuple",))
    assert compatible_cli_version("claude", "2.1.286 (Claude Code)") is True
    assert compatible_cli_version("claude", "2.9.0 (Claude Code)") is True
    assert compatible_cli_version("claude", "2.999.0 (Claude Code)") is True
    assert compatible_cli_version("codex", "codex-cli 0.156.2") is True
    assert compatible_cli_version("codex", "codex-cli 0.157.0") is True
    assert compatible_cli_version("codex", "codex-cli 0.160.1") is True
    assert compatible_cli_version("codex", "codex-cli 0.999.0") is True
    for provider, version in (
        ("claude", "2.1.279 (Claude Code)"),
        ("claude", "3.0.0 (Claude Code)"),
        ("codex", "codex-cli 0.156.0"),
        ("codex", "codex-cli 1.0.0"),
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


def test_exact_empty_array_binding_is_selected_from_probed_capabilities() -> None:
    codex_schema = {"type": "array", "minItems": 1, "maxItems": 2}
    claude_schema = {"type": "array", "minItems": 1, "maxItems": 2}

    bind_required_empty_array(codex_schema, provider="codex")
    bind_required_empty_array(claude_schema, provider="claude")

    assert codex_schema == {"type": "array", "minItems": 0, "maxItems": 0}
    assert claude_schema == {"type": "array", "const": []}


@pytest.mark.parametrize(
    ("violation", "message"),
    (
        ("root_any_of", "/anyOf: root must not use anyOf"),
        ("optional_property", "/required: every object property must be required"),
        (
            "open_object",
            "/additionalProperties: object must set additionalProperties false",
        ),
        (
            "nested_one_of",
            "/properties/value/oneOf: keyword 'oneOf' is not permitted",
        ),
        (
            "unsupported_keyword",
            "/properties/value/default: keyword 'default' is not a supported "
            "schema keyword",
        ),
        (
            "documented_unsupported_keyword",
            "/properties/value/allOf: keyword 'allOf' is not supported",
        ),
    ),
)
def test_projected_schema_guard_enforces_other_codex_provider_rules(
    violation: str, message: str
) -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    if violation == "root_any_of":
        schema["anyOf"] = [{"type": "object"}]
    elif violation == "optional_property":
        schema["required"] = []
    elif violation == "open_object":
        schema["additionalProperties"] = True
    elif violation == "nested_one_of":
        schema["properties"]["value"]["oneOf"] = [
            {"type": "string"},
            {"type": "null"},
        ]
    elif violation == "documented_unsupported_keyword":
        schema["properties"]["value"]["allOf"] = [{"type": "string"}]
    else:
        schema["properties"]["value"]["default"] = ""

    with pytest.raises(NativeProviderSchemaError, match=message):
        assert_projected_provider_schema(schema, provider="codex")


@pytest.mark.parametrize("provider", ("claude", "codex"))
@pytest.mark.parametrize(
    ("property_schema", "message"),
    (
        ({"type": "string", "enum": []}, "/properties/value/enum: must not be empty"),
        ({"anyOf": []}, "/properties/value/anyOf: must not be empty"),
        ({"oneOf": []}, "/properties/value/oneOf: must not be empty"),
    ),
)
def test_projected_schema_guard_rejects_degenerate_bindings_for_both_providers(
    provider: str,
    property_schema: dict[str, object],
    message: str,
) -> None:
    schema = {
        "type": "object",
        "properties": {"value": property_schema},
        "required": ["value"],
        "additionalProperties": False,
    }

    with pytest.raises(NativeProviderSchemaError) as raised:
        assert_projected_provider_schema(schema, provider=provider)

    assert message in str(raised.value)
    assert provider not in str(raised.value)


def test_projection_guard_reconciles_scalar_const_allowance_with_const_list_probe(
) -> None:
    assert "const" in OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS
    scalar_const = {
        "type": "object",
        "properties": {"value": {"type": "string", "const": "fixed"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    list_const = copy.deepcopy(scalar_const)
    list_const["properties"]["value"] = {"type": "array", "const": []}

    assert_projected_provider_schema(scalar_const, provider="codex")
    with pytest.raises(NativeProviderSchemaError, match="const_list feature"):
        assert_projected_provider_schema(list_const, provider="codex")
    assert_projected_provider_schema(list_const, provider="claude")


def test_required_empty_array_degeneracy_is_provider_dependent() -> None:
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "type": "array",
                "minItems": 0,
                "maxItems": 0,
                "items": {"type": "string"},
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }

    assert_projected_provider_schema(schema, provider="codex")
    with pytest.raises(
        NativeProviderSchemaError,
        match="required array property must not force an empty collection",
    ):
        assert_projected_provider_schema(schema, provider="claude")


@pytest.mark.parametrize("keyword", ("allOf", "type"))
@pytest.mark.parametrize("provider", ("claude", "codex"))
def test_projected_schema_guard_rejects_other_invalid_empty_schema_arrays(
    keyword: str, provider: str
) -> None:
    property_schema: dict[str, object] = {keyword: []}
    schema = {
        "type": "object",
        "properties": {"value": property_schema},
        "required": ["value"],
        "additionalProperties": False,
    }

    with pytest.raises(NativeProviderSchemaError) as raised:
        assert_projected_provider_schema(schema, provider=provider)

    assert f"/properties/value/{keyword}: must not be empty" in str(raised.value)
    assert provider not in str(raised.value)
