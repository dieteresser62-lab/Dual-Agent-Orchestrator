"""Shared, side-effect-free scope coverage for Finding routes."""

from __future__ import annotations

from collections.abc import Sequence


def uncovered_route_paths(
    scope_paths: Sequence[str], affected_paths: Sequence[str]
) -> tuple[str, ...]:
    """Return affected paths that the exact approved scope does not cover."""

    return tuple(
        path
        for path in affected_paths
        if not any(
            path == scope or path.startswith(scope.rstrip("/") + "/")
            for scope in scope_paths
        )
    )


__all__ = ["uncovered_route_paths"]
