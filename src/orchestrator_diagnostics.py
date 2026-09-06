"""Closed, provider-free diagnostics that may be shown to an operator."""

from __future__ import annotations

from enum import StrEnum


class OrchestratorDiagnostic(StrEnum):
    """Diagnostics whose complete rendered text is owned by this repository."""

    SLICE_PLAN_PATHS_INVALID = (
        "slice-plan-invalid: "
        "planned slice paths must be sorted, unique, and non-empty"
    )

    @property
    def detail(self) -> str:
        if self is OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID:
            return "planned slice paths must be sorted, unique, and non-empty"
        raise AssertionError("unhandled orchestrator diagnostic")

    @property
    def text(self) -> str:
        return self.value


ORCHESTRATOR_DIAGNOSTIC_TEXTS = frozenset(
    item.value for item in OrchestratorDiagnostic
)
