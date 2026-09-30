# Arbeitsauftrag aus dem Abnahmereview

Der vollständige Abnahmereview hat die folgenden Befunde ergeben. Plane ihre Behebung wie bei jedem anderen Arbeitsauftrag von Grund auf.

Zielbranch: `feature/qualification-t3`

ACCEPTANCE_REVIEW_NUMBER: 2

## Befund 1: discount accepts rates outside the closed interval [0,100], producing invalid negative prices or increases instead of rejecting the input.

Betroffene Pfade:
- `app.py`

Abnahmekriterium:
discount(price, rate) raises ValueError for rates below 0 or above 100 before calculating a price. Preserve the existing percentage calculation for valid rates, including 0 and 100, different prices and fractional percentages. Correct only app.py; frozen tests and configuration remain unchanged.

ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/qualification-t3
TASK_SCOPE: app.py

## Validation

`python3 -m pytest tests/ -v`

## Out of scope

Frozen tests and configuration, protected paths, provider changes, network use.
