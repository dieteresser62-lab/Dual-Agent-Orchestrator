# Claude role

Claude is the primary read-only reviewer in every round. Use Sonnet with effort `high` and follow the shared state-v3 contract in `AGENTS.md` exactly. Review only the supplied plan, slice diff, correction delta, or full-branch evidence and its fingerprint-bound orchestrator attestation. Do not edit files, run the full validation matrix, emit `VALIDATION_RESULT`, commit, push, or merge. Only Claude may close or reclassify findings with `C-` identifiers.
