# Add JSON health output to the status command

## Context

The project already has a text-only `status` command. Automation needs a stable JSON representation without changing the existing text output.

## Goal

Add `--json` to the status command. With this option, print one JSON object containing `status`, `version`, and `checked_at_utc`. Without it, preserve the current text output exactly.

## Allowed Scope

- `src/status_cli.py`
- `tests/test_status_cli.py`
- `README.md`
- `docs/internal/orchestrator-work-plan.md`
- `docs/internal/slice-status-json.md`

Do not edit files outside this list. If another path is required, stop and request a scope decision.

## Requirements

1. Parse `--json` with the command's existing argument parser.
2. Serialize valid UTF-8 JSON with deterministic field names.
3. Format `checked_at_utc` as an ISO 8601 UTC timestamp ending in `Z`.
4. Keep the existing text-mode exit code and output byte-for-byte compatible.
5. Add focused tests for JSON mode, text-mode compatibility, and an invalid option.
6. Update the README command example and the prepared Slice audit document.

## Acceptance Criteria

- `status --json` exits with code 0 and emits exactly one JSON object.
- The object has exactly the keys `checked_at_utc`, `status`, and `version`.
- The existing command without `--json` passes its current snapshot test unchanged.
- Invalid options still return the parser's non-zero usage error.
- The configured validation matrix passes for the reviewed diff fingerprint.
- No file outside the allowed scope is changed or committed.

## Validation

- `python3 -m pytest tests/test_status_cli.py -v`
- `python3 -m pytest tests/ -v`

## Non-Scope

- No network health probe.
- No new dependency.
- No change to version discovery.
- No push, merge, release, or deployment.

## Stop Conditions

- Stop if the JSON schema requires a field not listed above.
- Stop if preserving text output requires an architecture change outside the allowed scope.
- Stop if tests reveal a platform-specific timestamp contract that is not documented.
