from __future__ import annotations

import argparse
import copy
import dataclasses
import inspect
import json
import hashlib
from pathlib import Path

import pytest

import native_provider_schema
from agent_config import MODEL_FAMILIES, add_agent_arguments, resolve_agent_settings
from contracts import AgentRole
from native_provider_schema import (
    AGY_PROVIDER,
    NativeProviderSchemaError,
    OPENAI_PROVIDER,
    OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS,
    assert_projected_provider_schema,
    assert_provider_capabilities,
    capability_profile_for_digest,
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
from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER


def _before_experimental_promotion(raw: str) -> str:
    """Keep the original byte guards, excluding only the authorized promotion.

    Restore status and evidence references, not capabilities, policies, rights,
    model/version restrictions or any already-certified row.
    """
    document = json.loads(raw)
    root = Path(__file__).resolve().parents[1]
    for row in document['certifications']:
        if row['capability_profile'] not in {BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER}:
            continue
        assert row['status'] == 'experimental'
        assert row['evidence']['path'] == f"docs/evidence/{row['provider']}/role-certification-v1.json"
        assert row['canary_evidence']['path'] == f"docs/evidence/{row['provider']}/role-canary-v1.json"
        original = (f"docs/evidence/{row['provider']}/reviewer-candidate-v1.json"
                    if row['capability_profile'] == BOUNDARY_REVIEWER
                    else 'docs/evidence/role-certification-candidates-v1.json')
        row['status'] = 'candidate'
        row['evidence'] = {'path': original,
                          'sha256': hashlib.sha256((root / original).read_bytes()).hexdigest()}
        row.pop('canary_evidence')
    return json.dumps(document, indent=2) + '\n'


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
        "--restricted",
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

    assert capabilities["schema_version"] == "native-provider-schema-capabilities-v2"
    assert [item["profile_id"] for item in capabilities["providers"]] == [
        "antigravity",
        "claude",
        "claude-implementer",  # allowlist:provider -- profile configuration: implementer transport
        "codex",
        "codex-reviewer",  # allowlist:provider -- profile configuration: reviewer transport
    ]
    assert {item["profile_id"]: item["version_policy"] for item in capabilities["providers"]} == {
        "antigravity": "forward", "claude": "forward", "codex": "forward",
        "claude-implementer": "forward",  # allowlist:provider -- profile configuration: implementer version policy
        "codex-reviewer": "forward",  # allowlist:provider -- profile configuration: reviewer version policy
    }
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
                "unique_items", "max_length", "min_length", "portable_pattern",
                "optional_properties", "const_value", "enum_values", "null_type",
                "refs", "defs", "all_of", "not_schema", "strict_writer",
                "numeric_constraints", "format_keyword",
            }
        )
    }
    assert exceptions["schema_version"] == "native-provider-schema-exceptions-v2"
    assert len(registered_exceptions("codex")) == 6
    assert len(registered_exceptions("claude")) == 6
    assert len(registered_exceptions("antigravity")) == 6


def test_agy_measured_capability_and_transport() -> None:
    capability = provider_capability(AGY_PROVIDER)
    assert capability["profile_id"] == "antigravity"
    digest = hashlib.sha256(json.dumps(capability, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert capability_profile_for_digest("antigravity", digest) == "antigravity"
    with pytest.raises(NativeProviderSchemaError, match="no unique bound capability profile"):
        capability_profile_for_digest("claude", digest)
    assert capability["cli_version"] == "1.2.12"
    assert compatible_cli_version(AGY_PROVIDER, "1.2.12")
    for version in ("1.2.11",):
        assert not compatible_cli_version(AGY_PROVIDER, version)
    for version in ("1.2.13", "2.0.0"):
        assert compatible_cli_version(AGY_PROVIDER, version)
    with pytest.raises(NativeProviderSchemaError, match="unsupported format"):
        compatible_cli_version(AGY_PROVIDER, "agy 1.2.12")
    assert capability["features"]["unique_items"]
    assert capability["features"]["nested_one_of"]
    assert not capability["features"]["positional_tuple"]
    command = [
        "/opt/bin/agy", "-p", "prompt", "--output-format", "json",
        "--json-schema", "{}", "--print-timeout", "590s",
        "--disable-slash-commands", "--sandbox", "--model", "gemini-3.1-pro-high",
        "--effort", "high", "--log-file", "/tmp/run/agy.log",
        "--agent", "dao-reviewer",
    ]
    profile = normalize_transport_profile(AGY_PROVIDER, command)
    assert_provider_capabilities(AGY_PROVIDER, (), profile=profile)
    other_model = command.copy()
    other_model[other_model.index("--model") + 1] = "gpt-oss-120b-medium"
    assert_provider_capabilities(AGY_PROVIDER, (), profile=normalize_transport_profile(AGY_PROVIDER, other_model))
    with pytest.raises(NativeProviderSchemaError, match="unclassified"):
        normalize_transport_profile(AGY_PROVIDER, [*command, "--future"])
    nul_command = command.copy()
    nul_command[nul_command.index("--model") + 1] = "gemini-\x00bad"
    with pytest.raises(NativeProviderSchemaError, match="invalid text"):
        normalize_transport_profile(AGY_PROVIDER, nul_command)
    with pytest.raises(NativeProviderSchemaError, match="not positively probed"):
        assert_provider_capabilities(AGY_PROVIDER, ("positional_tuple",))
    schema = {
        "type": "object", "properties": {
            "value": {"oneOf": [{"type": "string", "const": "x"}, {"type": "null"}]},
            "payload": {"type": "array", "enum": [["uniqueItems", "oneOf"]],
                        "uniqueItems": True, "items": {"type": "string"}},
        }, "required": ["value"], "additionalProperties": False,
    }
    projected = defensive_provider_projection(schema, provider=AGY_PROVIDER, required_features=())
    assert projected == schema  # const/enum payload data and supported keywords survive.
    assert_projected_provider_schema(projected, provider=AGY_PROVIDER)
    rejected = copy.deepcopy(schema)
    rejected["properties"]["payload"]["prefixItems"] = [{"type": "string"}]
    with pytest.raises(NativeProviderSchemaError, match="positional_tuple"):
        assert_projected_provider_schema(rejected, provider=AGY_PROVIDER)
    rejected = copy.deepcopy(schema)
    rejected["properties"]["value"]["not"] = {"type": "integer"}
    with pytest.raises(NativeProviderSchemaError, match="not_schema"):
        assert_projected_provider_schema(rejected, provider=AGY_PROVIDER)
    rejected = copy.deepcopy(schema)
    rejected["properties"]["value"]["minimum"] = 1
    with pytest.raises(NativeProviderSchemaError, match="numeric_constraints"):
        assert_projected_provider_schema(rejected, provider=AGY_PROVIDER)


def test_capability_profiles_are_identified_independently_of_provider(monkeypatch, tmp_path) -> None:
    table = load_capability_table()
    duplicate = copy.deepcopy(provider_capability("claude"))
    duplicate["profile_id"] = "claude-later"
    table["providers"].append(duplicate)
    table["providers"].sort(key=lambda item: item["profile_id"])
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(native_provider_schema, "CAPABILITY_PATH", path)
    loaded = load_capability_table()
    assert [row["provider"] for row in loaded["providers"]].count("claude") == 3
    digest = hashlib.sha256(json.dumps(duplicate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert capability_profile_for_digest("claude", digest) == "claude-later"


def test_agy_capability_evidence_matches_frozen_s6_bytes() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/antigravity/capability-v1.json").read_text())
    measured = evidence["measurements"]
    assert measured["version_stdout"] == "1.2.12\n"
    phase_path = root / measured["phase_0_path"]
    fixture_path = root / measured["format_fixture_path"]
    assert hashlib.sha256(phase_path.read_bytes()).hexdigest() == measured["phase_0_sha256"]
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == measured["format_fixture_sha256"]
    phase = json.loads(phase_path.read_text())
    fixture = json.loads(fixture_path.read_text())
    s6 = {item["call"][-2:]: item for item in phase["calls"] if item["series"] == "s6"}
    assert set(s6) == set(measured["writer_sha256"]) == set(fixture["cases"])
    for case, item in s6.items():
        checks = {check["name"]: check["status"] for check in item["checks"]}
        assert checks["writer_schema"] == checks["schema_echo"] == "pass"
        writer = fixture["cases"][case]["envelope"]["json_schema"]
        digest = hashlib.sha256(json.dumps(writer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert measured["writer_sha256"][case] == digest


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
    setup_defaults = inspect.signature(_fresh_state).parameters
    expected = {"codex": ("sol", "high"), "claude": ("opus", "high")}
    for role, provider in (("implementer", "codex"), ("reviewer", "claude")):
        role_field = AgentRole(role).name.lower() + "_profile"
        assert expected[provider][0] in MODEL_FAMILIES[provider]
        assert (settings[role].model, settings[role].effort) == expected[provider]
        binding_field = next(field for field in dataclasses.fields(ProtocolBinding) if field.name == role_field)
        default = binding_field.default_factory()
        assert (default.model, default.effort) == expected[provider]
        assert setup_defaults[role_field].default is None


def test_model_and_effort_are_recorded_but_do_not_bind_the_transport() -> None:
    for model in MODEL_FAMILIES["codex"]:
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
    assert compatible_cli_version("claude", "3.0.0 (Claude Code)") is True
    assert compatible_cli_version("codex", "codex-cli 1.0.0") is True
    for provider, version in (
        ("claude", "2.1.279 (Claude Code)"),
        ("claude", "2.1.282 (Claude Code)"),
        ("codex", "codex-cli 0.156.0"),
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


@pytest.mark.parametrize("restricted_count", (0, 2))
def test_claude_transport_requires_exactly_one_restricted_flag(
    restricted_count: int,
) -> None:
    command = _claude_command()
    command.remove("--restricted")
    command[command.index("--safe-mode"):command.index("--safe-mode")] = (
        ["--restricted"] * restricted_count
    )
    with pytest.raises(NativeProviderSchemaError, match="unclassified Claude command arguments"):
        normalize_transport_profile("claude", command)


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

    with pytest.raises(NativeProviderSchemaError, match="non-portable"):
        defensive_provider_projection(
            base, provider=OPENAI_PROVIDER, required_features=("closed_object",)
        )
    assert base == before
    base["properties"]["path"]["pattern"] = r"^[^/\x00]+$"
    with pytest.raises(NativeProviderSchemaError, match="no local compensation"):
        defensive_provider_projection(
            base, provider=OPENAI_PROVIDER, required_features=("closed_object",)
        )
    projected = defensive_provider_projection(
        base, provider="codex", required_features=("closed_object",),
        compensated_features=("uniqueItems",),
        compensated_unique_item_paths=("/properties/items/uniqueItems",),
    )
    assert projected["properties"]["path"]["pattern"] == r"^[^/\x00]+$"
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


@pytest.mark.parametrize("profile", (OPENAI_PROVIDER, "codex-reviewer"))  # allowlist:provider -- profile configuration: strict reviewer profiles
@pytest.mark.parametrize(("change", "message"), (
    ({}, "additionalProperties"),
    ({"additionalProperties": False}, "required"),
))
def test_strict_guard_treats_untyped_properties_as_an_object(profile, change, message) -> None:
    schema = {
        "type": "object", "properties": {"value": {"anyOf": [
            {"properties": {"name": {"type": "string"}}, **change},
            {"type": "null"},
        ]}}, "required": ["value"], "additionalProperties": False,
    }
    with pytest.raises(NativeProviderSchemaError, match=message):
        assert_projected_provider_schema(schema, provider=profile)


def test_reviewer_overlay_rejects_widened_or_unproved_property() -> None:
    parent = {"type": "string", "enum": ["a", "b"]}
    with pytest.raises(NativeProviderSchemaError, match="widens"):
        native_provider_schema._intersect_overlay_property(parent, {"enum": ["a", "c"]})
    with pytest.raises(NativeProviderSchemaError, match="not provably narrowing"):
        native_provider_schema._intersect_overlay_property(parent, {"pattern": "^a$"})
    assert native_provider_schema._intersect_overlay_property(
        {"enum": [1]}, {"const": True}
    ) is None


def test_openai_structured_output_limits_reject_each_published_bound() -> None:
    from native_provider_schema import assert_openai_structured_output_limits

    root = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    cases = [
        {**root, "properties": {str(i): {"type": "string"} for i in range(5001)}},
        {**root, "properties": {"v": {"enum": [str(i) for i in range(1001)]}}},
        {**root, "properties": {"v": {"const": "x" * 120001}}},
        {**root, "properties": {"v": {"enum": ["x" * 61] * 251}}},
    ]
    depth = {"type": "string"}
    for _ in range(11):
        depth = {"type": "object", "properties": {"v": depth},
                 "required": ["v"], "additionalProperties": False}
    cases.append(depth)
    for schema in cases:
        with pytest.raises(NativeProviderSchemaError, match="Structured Outputs limits"):
            assert_openai_structured_output_limits(schema)


def test_capability_and_certification_tables_keep_frozen_bytes() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = {
        "schemas/native-provider-schema-capabilities-v2.json": "48c610dfa893c25ae49b70e0ea21acaf22523de46fa518652c8c1ec9217bc5a9",
        "schemas/role-provider-certifications-v1.json": "68a099dfce9894b2ab21d5926fb7f0e266067e48fdc0a3953a62bd27165b0be9",
    }
    for path, digest in expected.items():
        content = (root / path).read_bytes()
        if path.endswith('role-provider-certifications-v1.json'):
            content = _before_experimental_promotion(content.decode()).encode()
        assert hashlib.sha256(content).hexdigest() == digest


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


@pytest.mark.parametrize("path,expected", [
    ("schemas/native-provider-schema-capabilities-v2.json", "75054abeb3361454d2b66c0fe9d6958673fd607bf448292549ba30eb456a4689"),
    ("schemas/role-provider-certifications-v1.json", "8e11e53f0ca2faae2d3c742796bb4e3af8bb72b955b1d4df8b795a71d085fc8b"),
])
def test_other_registry_rows_keep_exact_pre_5b1_bytes(path, expected):
    raw = (Path(__file__).resolve().parents[1] / path).read_text()
    if path.endswith('role-provider-certifications-v1.json'):
        raw = _before_experimental_promotion(raw)
    decoder = json.JSONDecoder()
    index = raw.index("[") + 1
    while True:
        while raw[index].isspace() or raw[index] == ",":
            index += 1
        row, end = decoder.raw_decode(raw, index)
        if row.get("profile_id", row.get("capability_profile")) == "claude-implementer":  # allowlist:provider -- certification data: sole changed candidate row
            masked = raw[:index] + "{}" + raw[end:]
            break
        index = end
    # Frozen from ce006cb with only the one changed row masked.
    assert hashlib.sha256(masked.encode()).hexdigest() == expected
