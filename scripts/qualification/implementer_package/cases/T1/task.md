# Implementer measurement T1

ORCHESTRATOR_MODE: PLAN_ONLY
TARGET_BRANCH: feature/qualification-t1
WORK_PLAN_PATH: docs/work-plan.md
TASK_SCOPE: docs/work-plan.md

## Goal

Create docs/work-plan.md with exactly one future Slice for app.py. Plan clamp(value, lower, upper), inclusive limits and ValueError for lower > upper. Use canonical Slice/path/acceptance headings and python3 -m pytest tests/ -v. Only write the plan.

## Acceptance criteria

- Meet every behavior in the goal.
- Preserve frozen files and reviewer authority.
- Complete the declared scenario while preserving frozen inputs and protected paths.

## Validation

`python3 -m pytest tests/ -v`

## Out of scope

Frozen tests and configuration, protected paths, provider changes, network use.
