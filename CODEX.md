# Role-neutral CLI entry

Follow `AGENTS.md`. Codex can be implementer or reviewer; the run-bound profile from `orchestrator.toml` assigns the role. As implementer, follow the implementer contract and return request-bound native implementer JSON. As reviewer or final reviewer, follow the reviewer contract, work read-only, and return request-bound native review JSON. The canonical assignment or review request, provider policy and manifest are authoritative; this entry file supplies neither role assignment nor validation evidence.

Role occupancy and certification follow `AGENTS.md`, including manufacturer separation and the candidate start prohibition. The shipped default is Codex / Claude / Claude.
