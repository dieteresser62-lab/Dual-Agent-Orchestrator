from __future__ import annotations

import pytest

from provider_input_budget import (
    PROVIDER_OPERATIONS,
    PreparedProviderInput,
    ProviderInputBudgetError,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy,
    measure_provider_input,
)
from orchestrator_diagnostics import OrchestratorDiagnostic
from workflow_state import WorkflowStep


def _prepared(text: str) -> PreparedProviderInput:
    return PreparedProviderInput(
        ("provider", "--print"), None, (ProviderInputComponent("stdin_prompt", text),)
    )


def _policy(chars: int, bytes_: int) -> ProviderInputBudgetPolicy:
    defaults = default_provider_input_budget_policy()
    return ProviderInputBudgetPolicy(
        tuple(
            ProviderInputBudgetRule(
                rule.provider,
                rule.role,
                rule.operation,
                chars if rule.key == ("codex", "codex", "codex_implementation") else rule.max_chars,
                bytes_ if rule.key == ("codex", "codex", "codex_implementation") else rule.max_bytes,
            )
            for rule in defaults.rules
        )
    )


def test_budget_table_covers_every_reachable_provider_operation() -> None:
    reachable = {
        provider: {
            step.value
            for step in WorkflowStep
            if step.value.startswith(f"{provider}_")
        }
        for provider in ("codex", "claude")
    }

    assert reachable["codex"] <= PROVIDER_OPERATIONS["codex"]
    assert reachable["claude"] <= PROVIDER_OPERATIONS["claude"]
    assert PROVIDER_OPERATIONS["codex"] - reachable["codex"] == {
        "codex_final_correction",
        "codex_final_review",
    }
    assert PROVIDER_OPERATIONS["claude"] - reachable["claude"] == {
        "claude_final_review"
    }


def test_branch_discovery_uses_the_slice_review_input_ceiling() -> None:
    rules = {
        rule.operation: rule
        for rule in default_provider_input_budget_policy().rules
        if rule.provider == "claude"
    }

    discovery = rules["claude_branch_discovery"]
    slice_review = rules["claude_slice_review"]
    assert (discovery.max_chars, discovery.max_bytes) == (4_000_000, 16_000_000)
    assert (discovery.max_chars, discovery.max_bytes) == (
        slice_review.max_chars,
        slice_review.max_bytes,
    )


def test_budget_configuration_error_has_value_free_readable_diagnostic() -> None:
    provider_value = "provider-secret-value"
    error = ProviderInputBudgetError(f"unknown operation: {provider_value}")

    assert error.orchestrator_diagnostic is (
        OrchestratorDiagnostic.PROVIDER_BUDGET_CONFIG_RULE
    )
    assert provider_value not in error.orchestrator_diagnostic.text


@pytest.mark.parametrize(
    ("text", "chars", "bytes_", "allowed", "violations"),
    (
        ("abc", 4, 4, True, ()),
        ("abcd", 4, 4, True, ()),
        ("abcde", 4, 10, False, ("chars",)),
        ("ä", 1, 1, False, ("bytes",)),
    ),
)
def test_measurement_enforces_independent_inclusive_char_and_byte_limits(
    text: str, chars: int, bytes_: int, allowed: bool, violations: tuple[str, ...]
) -> None:
    result = measure_provider_input(
        _prepared(text),
        provider="codex",
        role="codex",
        operation="codex_implementation",
        binding_fingerprint="f" * 64,
        policy=_policy(chars, bytes_),
    )

    assert result.allowed is allowed
    assert result.violated_dimensions == violations
    assert result.total_chars == len(text)
    assert result.total_bytes == len(text.encode("utf-8"))
    assert result.technical_limit_chars is None
    assert result.technical_limit_bytes is None
    assert result.technical_limit_source is None


def test_measurement_sums_named_components_and_technical_limit_only_tightens() -> None:
    prepared = PreparedProviderInput(
        ("provider",),
        None,
        (
            ProviderInputComponent("system_policy", "ab"),
            ProviderInputComponent("response_schema", "€"),
        ),
    )
    result = measure_provider_input(
        prepared,
        provider="codex",
        role="codex",
        operation="codex_implementation",
        binding_fingerprint="binding",
        policy=_policy(20, 20),
        technical_limit_chars=3,
        technical_limit_bytes=5,
        technical_limit_source="catalog-v1:test",
    )

    assert [(item.name, item.chars, item.bytes) for item in result.components] == [
        ("system_policy", 2, 2),
        ("response_schema", 1, 3),
    ]
    assert result.effective_limit_chars == 3
    assert result.effective_limit_bytes == 5
    assert result.allowed


def test_policy_rejects_duplicates_and_incomplete_tables() -> None:
    rule = ProviderInputBudgetRule(
        "codex", "codex", "codex_implementation", 10, 10
    )
    with pytest.raises(ProviderInputBudgetError, match="unique"):
        ProviderInputBudgetPolicy((rule, rule))
    with pytest.raises(ProviderInputBudgetError, match="complete"):
        ProviderInputBudgetPolicy((rule,))


@pytest.mark.parametrize(
    "name",
    ("packet_chunk_001", "request_chunk_001", "evidence_asset_001"),
)
def test_provider_input_component_accepts_closed_indexed_names(name: str) -> None:
    assert ProviderInputComponent(name, "payload").name == name


def test_provider_input_component_rejects_unknown_indexed_name() -> None:
    with pytest.raises(ValueError, match="unknown provider input component"):
        ProviderInputComponent("arbitrary_chunk_001", "payload")


def test_prepared_provider_input_rejects_content_equal_components() -> None:
    with pytest.raises(ValueError, match="unique content"):
        PreparedProviderInput(
            ("provider",),
            None,
            (
                ProviderInputComponent("stdin_prompt", "same bytes"),
                ProviderInputComponent("response_schema", "same bytes"),
            ),
        )
