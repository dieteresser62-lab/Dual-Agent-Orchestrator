from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping


PROVIDER_OPERATIONS: Mapping[str, frozenset[str]] = {
    "codex": frozenset(
        {
            "codex_plan",
            "codex_plan_revision",
            "codex_implementation",
            "codex_correction",
            "codex_final_review",
            "codex_final_correction",
        }
    ),
    "claude": frozenset(
        {
            "claude_plan_review",
            "claude_slice_review",
            "claude_final_review",
        }
    ),
}
PROVIDER_INPUT_COMPONENT_NAMES = frozenset(
    {
        "stdin_prompt",
        "packet_manifest",
        "system_policy",
        "response_schema",
        "start_directive",
    }
)
INDEXED_COMPONENT_NAME = re.compile(
    r"(?:packet_chunk|request_chunk|evidence_asset)_[0-9]{3}"
)


class ProviderInputBudgetError(ValueError):
    """Raised for an invalid or incomplete provider-input budget policy."""


class ProviderInputBudgetExceeded(RuntimeError):
    """A deterministic local denial raised before capability or provider processes."""

    def __init__(self, measurement: "ProviderInputMeasurement") -> None:
        self.measurement = measurement
        dimensions = ",".join(measurement.violated_dimensions)
        super().__init__(
            "provider input budget exceeded: "
            f"provider={measurement.provider} role={measurement.role} "
            f"operation={measurement.operation} dimensions={dimensions} "
            f"chars={measurement.total_chars}/{measurement.effective_limit_chars} "
            f"bytes={measurement.total_bytes}/{measurement.effective_limit_bytes}"
        )


@dataclass(frozen=True)
class ProviderInputComponent:
    name: str
    content: str

    def __post_init__(self) -> None:
        if (
            self.name not in PROVIDER_INPUT_COMPONENT_NAMES
            and INDEXED_COMPONENT_NAME.fullmatch(self.name) is None
        ):
            raise ValueError(f"unknown provider input component name: {self.name}")
        if not isinstance(self.content, str):
            raise TypeError("provider input component content must be text")


@dataclass(frozen=True)
class PreparedProviderInput:
    command: tuple[str, ...]
    stdin_text: str | None
    components: tuple[ProviderInputComponent, ...]

    def __post_init__(self) -> None:
        if not self.command or any(not isinstance(item, str) for item in self.command):
            raise ValueError("prepared provider command must contain strings")
        names = tuple(item.name for item in self.components)
        if not names or len(names) != len(set(names)):
            raise ValueError("prepared provider components must have unique names")
        content_digests = tuple(
            hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            for item in self.components
        )
        if len(content_digests) != len(set(content_digests)):
            raise ValueError(
                "prepared provider components must have unique content"
            )


@dataclass(frozen=True)
class ProviderInputBudgetRule:
    provider: str
    role: str
    operation: str
    max_chars: int
    max_bytes: int

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_OPERATIONS:
            raise ProviderInputBudgetError(f"unknown provider: {self.provider}")
        if self.role != self.provider:
            raise ProviderInputBudgetError(
                f"unsupported provider/role combination: {self.provider}/{self.role}"
            )
        if self.operation not in PROVIDER_OPERATIONS[self.provider]:
            raise ProviderInputBudgetError(
                f"unknown operation for {self.provider}: {self.operation}"
            )
        for value, label in ((self.max_chars, "max_chars"), (self.max_bytes, "max_bytes")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ProviderInputBudgetError(f"{label} must be a positive integer")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.provider, self.role, self.operation


@dataclass(frozen=True)
class ProviderInputBudgetPolicy:
    rules: tuple[ProviderInputBudgetRule, ...]

    def __post_init__(self) -> None:
        keys = tuple(rule.key for rule in self.rules)
        if len(keys) != len(set(keys)):
            raise ProviderInputBudgetError("provider input budget rules must be unique")
        expected = {
            (provider, provider, operation)
            for provider, operations in PROVIDER_OPERATIONS.items()
            for operation in operations
        }
        actual = set(keys)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ProviderInputBudgetError(
                f"provider input budget table must be complete; missing={missing}, extra={extra}"
            )

    def select(self, provider: str, role: str, operation: str) -> ProviderInputBudgetRule:
        key = provider, role, operation
        for rule in self.rules:
            if rule.key == key:
                return rule
        raise ProviderInputBudgetError(f"no provider input budget rule for {key}")

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            [
                {
                    "provider": rule.provider,
                    "role": rule.role,
                    "operation": rule.operation,
                    "max_chars": rule.max_chars,
                    "max_bytes": rule.max_bytes,
                }
                for rule in sorted(self.rules, key=lambda item: item.key)
            ],
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def default_provider_input_budget_policy() -> ProviderInputBudgetPolicy:
    return ProviderInputBudgetPolicy(
        tuple(
            ProviderInputBudgetRule(provider, provider, operation, 4_000_000, 16_000_000)
            for provider, operations in sorted(PROVIDER_OPERATIONS.items())
            for operation in sorted(operations)
        )
    )


@dataclass(frozen=True)
class ProviderInputComponentSize:
    name: str
    chars: int
    bytes: int


@dataclass(frozen=True)
class ProviderInputMeasurement:
    provider: str
    role: str
    operation: str
    binding_fingerprint: str
    input_digest: str
    policy_digest: str
    components: tuple[ProviderInputComponentSize, ...]
    total_chars: int
    total_bytes: int
    safety_limit_chars: int
    safety_limit_bytes: int
    technical_limit_chars: int | None
    technical_limit_bytes: int | None
    technical_limit_source: str | None
    effective_limit_chars: int
    effective_limit_bytes: int
    allowed: bool
    violated_dimensions: tuple[str, ...]
    char_overage: int
    byte_overage: int
    largest_component: str


def measure_provider_input(
    prepared: PreparedProviderInput,
    *,
    provider: str,
    role: str,
    operation: str,
    binding_fingerprint: str,
    policy: ProviderInputBudgetPolicy,
    technical_limit_chars: int | None = None,
    technical_limit_bytes: int | None = None,
    technical_limit_source: str | None = None,
) -> ProviderInputMeasurement:
    rule = policy.select(provider, role, operation)
    technical_values = (technical_limit_chars, technical_limit_bytes)
    if any(value is not None for value in technical_values):
        if (
            not isinstance(technical_limit_source, str)
            or not technical_limit_source.strip()
            or any(value is None for value in technical_values)
        ):
            raise ProviderInputBudgetError(
                "technical limits require chars, bytes, and a source together"
            )
        for value in technical_values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ProviderInputBudgetError("technical limits must be positive integers")
    elif technical_limit_source is not None:
        raise ProviderInputBudgetError("technical limit source requires technical limits")

    component_sizes = tuple(
        ProviderInputComponentSize(
            item.name, len(item.content), len(item.content.encode("utf-8"))
        )
        for item in prepared.components
    )
    total_chars = sum(item.chars for item in component_sizes)
    total_bytes = sum(item.bytes for item in component_sizes)
    effective_chars = min(
        rule.max_chars,
        technical_limit_chars if technical_limit_chars is not None else rule.max_chars,
    )
    effective_bytes = min(
        rule.max_bytes,
        technical_limit_bytes if technical_limit_bytes is not None else rule.max_bytes,
    )
    violated = tuple(
        name
        for name, actual, limit in (
            ("chars", total_chars, effective_chars),
            ("bytes", total_bytes, effective_bytes),
        )
        if actual > limit
    )
    digest_payload = json.dumps(
        [(item.name, item.content) for item in prepared.components],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    largest = max(component_sizes, key=lambda item: (item.bytes, item.chars, item.name))
    return ProviderInputMeasurement(
        provider=provider,
        role=role,
        operation=operation,
        binding_fingerprint=binding_fingerprint,
        input_digest=hashlib.sha256(digest_payload).hexdigest(),
        policy_digest=policy.digest,
        components=component_sizes,
        total_chars=total_chars,
        total_bytes=total_bytes,
        safety_limit_chars=rule.max_chars,
        safety_limit_bytes=rule.max_bytes,
        technical_limit_chars=technical_limit_chars,
        technical_limit_bytes=technical_limit_bytes,
        technical_limit_source=technical_limit_source,
        effective_limit_chars=effective_chars,
        effective_limit_bytes=effective_bytes,
        allowed=not violated,
        violated_dimensions=violated,
        char_overage=max(0, total_chars - effective_chars),
        byte_overage=max(0, total_bytes - effective_bytes),
        largest_component=largest.name,
    )
