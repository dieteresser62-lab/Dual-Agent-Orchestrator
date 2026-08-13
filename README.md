# Dual-Agent Task Orchestrator

A resumable CLI for bounded coding work with Codex as implementer, Claude as the primary reviewer, and Antigravity as the independent closing reviewer.

## Overview

The orchestrator turns one Markdown task into an ordered State-v3 slice plan. Every slice has an exact path allowlist, deterministic validation, asymmetric reviews, and a verified local Git commit. After the final slice, all three roles inspect the complete branch change before the run is complete.

![State-v3 workflow](https://www.plantuml.com/plantuml/proxy?cache=no&src=https://raw.githubusercontent.com/dieteresser62-lab/Dual-Agent-Orchestrator/master/workflow.puml)

The normal workflow is:

1. Inspect the repository, branch, task, configuration, and existing state.
2. Ask Codex for ordered `SLICE_PLAN` records and have Claude review the plan.
3. For each planned slice:
   - Codex edits only the persisted path scope.
   - The orchestrator collects the canonical diff and runs the configured validation matrix once for that fingerprint.
   - Claude reviews only the slice changes on the first round and only the correction delta on later rounds.
   - Antigravity reviews the complete approved slice diff once after Claude approves the same fingerprint.
   - The orchestrator stages only the reviewed paths, creates a local `Slice NN: ...` commit, and verifies it.
4. Run a branch-wide completeness report and final Claude/Antigravity review against the branch base.
5. If the final review finds a blocker, process it as another bounded correction slice, commit it, and repeat the complete final review.

No role substitutes for another. Codex never approves or commits its own work. Reviewers cannot edit the source worktree or claim validation results.

## Requirements and Supported Platforms

Python 3.11 or newer is required. TOML parsing uses the Python standard library; the project has no runtime Python package dependencies.

The supported execution environments are:

- Linux
- macOS
- WSL2

Native Windows is not currently supported because the complete workflow has not been verified there. Under WSL2, use the native `agy` command when available or explicitly configure `agy.exe`.

Install and authenticate all three role CLIs, then place them in `PATH` or configure explicit binary paths:

- `codex`
- `claude`
- `agy` or `agy.exe`

The runtime checks each binary and its required capabilities lazily immediately before that role's first invocation.

## Quick Start

Create a bounded task from [example-task.md](example-task.md), save it as `task.md` in the target repository, and run:

```bash
./run_task
```

The positional form is equivalent:

```bash
./run_task path/to/my-task.md
```

Use either the positional path or `--task-file`, not both:

```bash
./run_task --task-file path/to/my-task.md
```

An unfinished `.orchestrator/state.json` is resumed automatically in single-task mode. A completed State-v3 run starts a new run. Use explicit `--resume` when resolving a gate or resuming after a process restart.

## Task Boundaries

A good task names the intended outcome, allowed paths, non-scope, acceptance criteria, validation commands, and conditions that require a user decision. Codex converts that request into one or more persisted slices. Each `SLICE_PLAN` record contains:

```text
SLICE_PLAN: <1-based id> | <summary> | <comma-separated repository-relative paths>
```

The listed paths are exact commit allowlists. A slice may contain at most ten productive file groups according to the configured path classes. Tests and documentation can be classified separately; an unclassified path is treated conservatively as productive.

Unexpected paths, a changed branch, a changed slice-start commit, or a fingerprint that differs after review blocks the commit.

## State, Checkpoints, Logs, and Audit Documents

Runtime data is stored below `.orchestrator/`:

| Path | Purpose |
|---|---|
| `.orchestrator/state.json` | Atomic, machine-readable State-v3 source for the active run. |
| `.orchestrator/checkpoints/work-unit-####-slice-####-round-####.json` | Resume checkpoints with one-based work-unit, slice, and round identities. |
| `.orchestrator/logs/` | Raw ephemeral agent invocation and diagnostic logs. |
| `.orchestrator/runs/<run_id>/work-unit-####-codex.md` | Persisted Codex output used to resume plan or implementation context. |

Do not edit state or checkpoints manually.

Human-readable plan and slice audit Markdown files belong in the target repository, normally below `docs/internal/`, and are committed with their slice. They must exist before the run, be linked from the work plan, contain the required managed audit sections, and appear in the corresponding `SLICE_PLAN` scope. The orchestrator projects structured findings, reviews, validation attestations, and authorization status only into those managed sections. Git is the historical source of truth after each local slice commit.

Active or frozen version-2 state is rejected without mutation. A completed version-2 state remains recognizable as historical completion but is not resumed or silently migrated to State v3.

## Validation and Review Isolation

Only the orchestrator runs deterministic validation. The validation matrix is selected from the canonical changed paths and open finding acceptance commands, then cached by diff fingerprint. Both reviewers receive the same complete, fingerprint-bound attestation.

Codex runs with workspace-write access. Claude and Antigravity receive disposable read-only repository copies while their private runtime, prompt, cache, and log paths remain writable. Normal reviews do not expose the validation harness and cannot modify the target worktree.

Claude uses Sonnet with effort `high` by default. Its first slice review receives the slice's changed paths and hunks, acceptance criteria, structured findings, and bound attestation. A correction review receives only the delta since Claude's last reviewed fingerprint. A format-only contract repair receives the rejected response and marker contract, not the implementation evidence again.

Antigravity runs only after Claude approves the same fingerprint. It does not review plans and receives the complete current slice or branch diff for its closing review.

The explicit review-harness command builders are diagnostics for installation, CLI-version changes, or troubleshooting. They prove test execution and tracked-file write denial in the isolated copy; they are not part of a normal review.

## Gates, Findings, and Resume

The workflow persists before returning from a resumable halt. Resolve the underlying condition, then continue with `--resume`. A gate with a fingerprint requires an explicit recorded decision:

```bash
./run_task --resume --approve-gate \
  --gate-actor "Dieter" \
  --gate-rationale "Reviewed the exact persisted fingerprint and approved continuation"
```

Use `--reject-gate` with the same actor and rationale requirements to record a rejection.

The principal gates are:

- changed tests without prior authorization;
- an optional manual gate before each slice commit;
- more than ten productive change groups;
- a repository-defined stop rule or agent `STOP_REQUESTED` record;
- paths outside the persisted slice scope;
- branch, HEAD, diff-fingerprint, or validation-attestation drift;
- missing or unavailable validation;
- changed structured anchor values;
- four implementer returns in one work unit;
- missing implementation changes;
- malformed, missing, or inconsistent review verdicts;
- quota, authentication, binary, permission, network, process, or timeout failures.

An approving review requires a complete passing attestation for the same fingerprint, authorized test changes, no reviewer-owned open blocker, review evidence or concrete findings, and a pre-mortem. Only the reviewer that reported a finding may close or reclassify it.

Quota handling is role-local. With automatic quota resume enabled, an unambiguous reset within the configured wait limit is persisted, waited for with heartbeats, and resumed once at the exact failed step. Otherwise the process exits with code 2 and remains resumable. There is no fallback role.

## Local Commits and External Git Actions

After Claude and Antigravity approve the same slice fingerprint, the orchestrator:

1. re-collects repository status and the canonical diff;
2. verifies branch, slice boundary, allowed paths, reviews, findings, and validation attestation;
3. stages only the exact reviewed paths;
4. creates a local `Slice NN: <planned summary>` commit with hooks and signing disabled for the mechanical transaction;
5. verifies the commit path list and resulting commit hash.

The orchestrator never pushes, merges, force-pushes, or rewrites history. Those actions remain explicit user operations outside this workflow.

## Watch Mode

Run the orchestrator as a FIFO queue worker:

```bash
./run_task --watch
```

Watch mode:

- monitors stable `*.md` files in `inbox/`, oldest first;
- holds a single-process `inbox/.lock` where `fcntl` is available;
- assigns each task a persisted run ID and task-content digest;
- enables `--skip-git-check` by default because reviewed slice commits intentionally change the worktree;
- streams `stdout` by default;
- moves completed tasks to `outbox/done/` with a UTC timestamp;
- retries technical failures and moves exhausted tasks to `outbox/failed/` as poison tasks;
- stops the queue on exit 2, 3, or 4 so the first resumable task keeps FIFO ownership;
- does not re-execute a successfully completed task when only its move to the outbox needs retrying.

Override directories, polling, or technical retry count:

```bash
./run_task --watch \
  --inbox-dir /path/to/inbox \
  --outbox-dir /path/to/outbox \
  --poll-interval 2 \
  --watch-max-retries 3
```

After resolving a paused watch task, restart the watcher. Its task identity sidecar resumes the same run and work unit.

## Dry Runs

The built-in dry run exercises plan approval, two slice commits, and final review without agent/API calls or repository writes:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

For deterministic negative and resume scenarios, supply a State-v3 JSON scenario and optionally write its audit report:

```bash
./run_task \
  --dry-run-scenario path/to/scenario.json \
  --dry-run-report path/to/report.json \
  --task-file example-task.md
```

## CLI Reference

`src/cli.py` is the argument-parsing source of truth. `run_task` is a compatibility launcher that locates it and forwards all arguments.

### Core and State Options

| Flag | Default | Description |
|---|---|---|
| `[task-file]` | `task.md` | Positional compatibility shorthand for the task file. |
| `--task-file <path>` | `task.md` | Explicit task path; cannot be combined with the positional form. |
| `--config <path>` | `RUN_TASK_CONFIG` or `./orchestrator.toml` | Repository policy configuration. |
| `--agents-file <path>` | repository `AGENTS.md` | Shared agent instructions injected into prompts. |
| `--resume` / `--no-resume` | auto | Automatically resume unfinished single-task state; explicitly override when needed. |
| `--force-overwrite-state` | auto for completed state | Start a new run despite existing state; explicit use bypasses the normal state guard. |
| `--strict-preflight` | off | Treat provider DNS preflight failure as fatal. |
| `--skip-git-check` / `--no-skip-git-check` | off; on in watch mode | Override repository-cleanliness checking. |
| `--manual-slice-gate` / `--no-manual-slice-gate` | repository config or off | Require explicit approval before every slice commit. |
| `--approve-gate` / `--reject-gate` | unset | With explicit `--resume`, decide the exact persisted user gate. |
| `--gate-actor <name>` | unset | Required identity for an explicit gate decision. |
| `--gate-rationale <text>` | unset | Required rationale for an explicit gate decision. |

### Validation, Dry Run, and Quota

| Flag | Default | Description |
|---|---|---|
| `--test-command <cmd>` | environment, repository matrix, or detection | Compatibility validation command; an explicit empty string disables it. |
| `--retry-incomplete-validation` | off | Re-run a cached `INCOMPLETE` matrix for the same fingerprint after repairing its environment. |
| `--dry-run` | off | Run the built-in State-v3 success scenario without API calls or writes. |
| `--dry-run-scenario <path>` | unset | Run a deterministic JSON scenario. |
| `--dry-run-report <path>` | unset | Write the scripted scenario audit report. |
| `--quota-auto-resume` / `--no-quota-auto-resume` | on | Enable one automatic continuation for an unambiguous reset. |
| `--quota-safety-margin <seconds>` | `60` | Delay added after a recognized reset. |
| `--quota-max-wait <seconds>` | `86400` | Maximum automatic wait. |
| `--quota-max-auto-resumes <count>` | `1` | Automatic continuations per blocked role step. |
| `--quota-heartbeat-interval <seconds>` | `30` | Heartbeat interval during quota waiting. |

### Agent Output and Role Configuration

| Flag | Default | Description |
|---|---|---|
| `--agent-output <none\|summary\|full>` | `none` | Amount of each completed agent response to print. |
| `--agent-output-max-chars <count>` | `1800` | Maximum completed-response characters in summary mode. |
| `--agent-live-stream` / `--no-agent-live-stream` | on | Enable or disable live process output. |
| `--agent-live-stream-mode <compact\|full>` | `compact` | Live-stream verbosity. |
| `--agent-live-stream-channels <both\|stdout\|stderr>` | environment or `stdout` | Live channels to print. |

Role settings use CLI, then `RUN_TASK_<ROLE>_*`, then these persistent defaults:

| Role | CLI options | Defaults |
|---|---|---|
| Codex | `--codex-binary`, `--codex-model`, `--codex-timeout`, `--codex-effort` | `codex`, `gpt-5.6-sol`, 1800s, `medium` |
| Claude | `--claude-binary`, `--claude-model`, `--claude-timeout`, `--claude-effort` | `claude`, `sonnet`, 1800s, `high` |
| Antigravity | `--antigravity-binary`, `--antigravity-model`, `--antigravity-timeout`, `--antigravity-effort` | detected `agy`, `gemini-3.1-pro-high`, 1800s, `high` |

`--claude-max-budget-usd` or `RUN_TASK_CLAUDE_MAX_BUDGET_USD` adds an optional print-mode budget ceiling. Opus is not the default; use `--claude-model opus` only for an explicit escalation.

Examples:

```bash
./run_task --claude-model sonnet --claude-effort high
RUN_TASK_ANTIGRAVITY_BINARY=agy.exe ./run_task
./run_task --codex-binary /opt/codex/bin/codex --codex-timeout 2400
```

### Watch and Logging Options

| Flag | Default | Description |
|---|---|---|
| `--watch` | off | Continuously process Markdown tasks from the inbox. |
| `--inbox-dir <path>` | `inbox` | Watch input directory. |
| `--outbox-dir <path>` | `outbox` | Watch completion/failure root. |
| `--poll-interval <seconds>` | `5.0` | Inbox polling interval. |
| `--watch-max-retries <count>` | `3` | Technical failures before poison handling. |
| `--verbose` | off | Enable debug logging. |
| `--quiet` | off | Show warnings and errors only. |

`--verbose` and `--quiet` are mutually exclusive.

## Configuration

Configuration precedence is:

1. explicit CLI value;
2. matching `RUN_TASK_*` environment value;
3. repository `orchestrator.toml` value;
4. built-in default or test-command auto-detection.

Agent binary, model, effort, timeout, and Claude budget values deliberately bypass repository TOML and use only CLI, environment, and role defaults.

An explicitly empty test command disables validation-command detection:

```bash
./run_task --test-command ""
RUN_TASK_TEST_CMD="" ./run_task
```

Without a declared validation command, detection checks `pyproject.toml` with pytest configuration, a `package.json` test script, then a Makefile `test` target.

The repository TOML schema contains portable policy only:

```toml
[paths]
productive = ["src/**/*.py", "run_task", "*.toml"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "**/__pycache__/**", ".pytest_cache/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop when the named domain invariant changes."

[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-v"]
default_timeout_seconds = 1800

[[validation.rules]]
patterns = ["frontend/**"]
command = ["npm", "test"]
timeout_seconds = 1200

[workflow]
manual_slice_gate = false
```

Use `default_shell_command` or a rule-local `shell_command` only when shell semantics are required. A validation entry must not declare both an argv command and a shell command. Patterns are repository-relative, use `/`, and cannot escape with `..`.

Useful environment overrides include:

```bash
RUN_TASK_TEST_CMD="python3 -m pytest tests/ -v" ./run_task
RUN_TASK_SKIP_GIT_CHECK=0 ./run_task --watch
RUN_TASK_WATCH_STREAM_CHANNELS=both ./run_task --watch
RUN_TASK_QUOTA_AUTO_RESUME=0 ./run_task
```

## Agent Instruction and Output Contract

The active repository instruction files are:

| File | Responsibility |
|---|---|
| `AGENTS.md` | Shared execution, safety, review, and marker contract. |
| `CODEX.md` | Implementer role and readiness records. |
| `CLAUDE.md` | Primary targeted reviewer; persistent Sonnet/High profile. |
| `ANTIGRAVITY.md` | Independent closing reviewer. |

All agent responses end with `STATUS: DONE`. State-v3 records are:

| Producer or step | Required record |
|---|---|
| Codex plan | `SLICE_PLAN: <id> \| <summary> \| <paths>` and `PLAN_READY: YES\|NO` |
| Codex implementation | `TEST_FILES_TOUCHED: NONE\|<paths>` and `IMPLEMENTATION_READY: <slice-id> \| YES\|NO` |
| Codex final report | `FINAL_REPORT_READY: YES\|NO` |
| Any reviewer, first line | `REVIEWER: claude\|antigravity` |
| Claude plan review | `PLAN_APPROVAL: YES\|NO` |
| Slice review | `SLICE_APPROVAL: <slice-id> \| YES\|NO` |
| Branch-wide final review | `FINAL_APPROVAL: YES\|NO` |
| New finding | `NEW_FINDING: C-01\|A-01 \| BLOCKER\|OBSERVATION \| <description> \| <acceptance test>` |
| Finding owner update | `FINDING_STATUS: <id> \| OPEN\|CLOSED \| <rationale>` |
| Optional owner reclassification | `FINDING_RECLASSIFIED: <id> \| BLOCKER\|OBSERVATION \| <rationale>` |
| Codex finding response | `FINDING_RESPONSE: <id> \| ACCEPTED\|REJECTED \| <rationale>` |
| Review with no concrete weakness | `REVIEW_EVIDENCE: <dimensions> \| <largest residual risk> \| <break condition>` |
| Positive review prerequisite | `PRE_MORTEM: <most likely failure cause in three months>` |
| Any role stop | `STOP_REQUESTED: <rule-id> \| <rationale>` instead of readiness or approval |

The orchestrator owns validation attestations; agents must not emit `VALIDATION_RESULT`. State-v2 approval and aggregate-finding markers are invalid.

## Exit Codes

| Code | Meaning |
|---:|---|
| `0` | The complete workflow, including all slice commits and branch-wide final review, finished successfully. |
| `1` | Technical, configuration, state-schema, repository, or internal workflow failure. |
| `2` | Quota cannot be resumed automatically or the configured quota-wait policy is exhausted. |
| `3` | A required agent instance failed, timed out, or is unavailable. |
| `4` | A user decision or policy gate is required. |

Codes 2, 3, and 4 preserve resumable state. Inspect the logged gate reason, repair or decide it, and continue the same run with `--resume`.

## Optional Global Command

To call the launcher from other repositories:

```bash
mkdir -p ~/.local/bin
ln -s /absolute/path/to/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
chmod +x /absolute/path/to/Dual-Agent-Orchestrator/run_task
```

## Verification

```bash
./run_task --help
./run_task --dry-run --task-file example-task.md --quiet
python3 -m pytest tests/ -v
```
