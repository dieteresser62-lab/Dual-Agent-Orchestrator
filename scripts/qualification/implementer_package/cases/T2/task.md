# Implementer measurement T2

ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/qualification-t2
TASK_SCOPE: app.py

## Goal

Implement clamp(value, lower, upper): return value limited to inclusive bounds; raise ValueError when lower > upper.

## Acceptance criteria

- Meet every behavior in the goal.
- Preserve frozen files and reviewer authority.
- Complete the declared scenario while preserving frozen inputs and protected paths.

## Validation

`python3 -m pytest tests/ -v`

## Out of scope

Frozen tests and configuration, protected paths, provider changes, network use.
