from __future__ import annotations

import pytest

from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputBudgetError,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy,
    measure_provider_input,
)


def _prepared(text: str) -> PreparedProviderInput:
    return PreparedProviderInput(
        ("provider", "--print"), None, (ProviderInputComponent("stdin_prompt", text),)
    )


def test_reviewer_contract_repairs_have_separate_budget_operations() -> None:
    policy = default_provider_input_budget_policy()
    assert policy.select(
        "claude", "claude", "claude_contract_repair"
    ).operation == "claude_contract_repair"
    assert policy.select(
        "antigravity", "antigravity", "antigravity_contract_repair"
    ).operation == "antigravity_contract_repair"


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
