# Reviewer CLI entry

Follow `AGENTS.md`. The reviewer uses the run-bound reviewer or final reviewer profile from `orchestrator.toml`, works read-only, and returns only the request-bound native review JSON. The provider-specific system policy, request files, and review manifest are authoritative; this entry file is not a source of role assignment or validation evidence.

Experimental Antigravity reviewer slots follow `AGENTS.md`: both canaries must pass before explicit TOML selection; Claude stays default, Codex stays implementer, and Google receives the full review snapshot.
