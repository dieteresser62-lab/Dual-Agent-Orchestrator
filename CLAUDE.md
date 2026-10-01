# Role-neutral CLI entry

Follow `AGENTS.md`. Claude can be implementer or reviewer; the run-bound profile from `orchestrator.toml` assigns the role. As implementer, follow the implementer contract and return request-bound native implementer JSON. As reviewer or final reviewer, follow the reviewer contract, work read-only, and return request-bound native review JSON. The canonical assignment or review request, provider policy and manifest are authoritative; this file assigns no role and supplies no validation evidence.

Per-slot certification, manufacturer separation and candidate start prohibition follow `AGENTS.md`. The default is Codex / Claude / Claude.

Claude implementer and both Codex review slots are `experimental` (1 October 2026); bound evidence and accepted weaknesses follow `AGENTS.md`.

The Claude implementer uses `--safe-mode` and does not automatically load `CLAUDE.md`; this file serves manual and other CLI use.

Certification is per slot; see `AGENTS.md`.
