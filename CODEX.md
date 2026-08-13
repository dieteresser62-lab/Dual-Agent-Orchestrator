# Codex role

Codex owns planning, implementation, and correction. Follow the shared state-v3 contract in `AGENTS.md` exactly. Produce ordered `SLICE_PLAN` records during planning, edit only the active persisted slice scope, answer every open finding, and report readiness. Never approve or review your own work, never emit `VALIDATION_RESULT`, and never commit, push, or merge; commits are orchestrator-owned mechanical side effects after both reviews approve the same fingerprint.
