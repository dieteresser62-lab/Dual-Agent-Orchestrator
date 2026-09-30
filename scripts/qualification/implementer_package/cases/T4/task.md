# Implementer measurement T4

ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/qualification-t4
TASK_SCOPE: app.py, codec.py

## Goal

Use exactly two Slices. Slice 1 changes codec.py: encode integer lists as comma-separated decimals, decode the same representation (empty text means empty list). Slice 2 changes app.py: total(text) uses codec.decode and sums the numbers. Keep the two files consistent.

## Acceptance criteria

- Meet every behavior in the goal.
- Preserve frozen files and reviewer authority.
- Complete the declared scenario while preserving frozen inputs and protected paths.

## Validation

`python3 -m pytest tests/ -v`

## Out of scope

Frozen tests and configuration, protected paths, provider changes, network use.
