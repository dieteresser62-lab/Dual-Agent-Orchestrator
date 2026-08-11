# Dual-Agent Task Orchestrator

A CLI tool that automates complex coding work with a two-phase agent workflow: planning and implementation.

## Overview

The orchestrator reads a Markdown task description, creates/revises an implementation plan in Phase 1, and executes/fixes in Phase 2. State and artifacts are stored in `.orchestrator/`.

![Workflow](https://www.plantuml.com/plantuml/proxy?cache=no&src=https://raw.githubusercontent.com/dieteresser62-lab/Dual-Agent-Orchestrator/master/workflow.puml)

## Key Features

- Two-phase workflow: planning and implementation are separated.
- Stateful resume: continue from `.orchestrator/state.json`.
- Live streaming: follow agent output in compact or full mode.
- Test integration: run a configurable test command in Phase 2.
- Fail-fast agent handling: freezes the run when an agent hits its API quota; another agent is never substituted.
- Git safety preflight: blocks execution on dirty repositories by default.

## Requirements

Supported platforms are Linux, macOS, and WSL2. Native Windows is not yet supported because the complete CLI pipeline has not been verified there. Python 3.11 or newer is required; TOML parsing uses the standard library and needs no third-party package.

Install both CLIs and make sure they are in `$PATH`:

- `codex`
- `claude`

## Optional Global Command

Create a symlink to run `run_task` from any project:

```bash
mkdir -p ~/.local/bin
ln -s /absolute/path/to/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
chmod +x /absolute/path/to/Dual-Agent-Orchestrator/run_task
```

## Quick Start

Create `task.md` and run:

```bash
./run_task
```

If `.orchestrator/state.json` exists and is not `done`, execution resumes automatically.

`task.md` is gitignored by default, so each user creates it locally per task.

## Watch Mode (Inbox/Outbox)

You can run the orchestrator as a queue worker that watches an inbox directory for new Markdown tasks:

```bash
./run_task --watch
```

Default behavior in watch mode:

- Monitor `inbox/` for `*.md` files.
- Process files in FIFO order (oldest modified first).
- Skip very new files until they are stable (minimum age: 1 second).
- Enable `--skip-git-check` automatically (so local WIP changes do not block queue processing).
- Stream only `stdout` live by default (reduces noisy internal CLI traces from `stderr`).
- Move every processed task file to `outbox/` with a timestamp prefix, even if the run fails.
- Keep waiting for the next task until you stop with `Ctrl+C`.

Custom directories and poll interval:

```bash
./run_task --watch --inbox-dir /path/to/inbox --outbox-dir /path/to/outbox --poll-interval 2
```

Single-file mode is unchanged and still works:

```bash
./run_task my-task.md
```

## Artifact Layout

Run artifacts are written to `.orchestrator/runs/<run_id>/`:

- `00_task.md`: snapshot of the input task
- `10_phase1_plan.md`: Phase 1 planning and review history
- `20_phase2_implementation.md`: Phase 2 implementation and review history

`.orchestrator/LATEST_RUN.txt` stores the latest run directory path.

## Usage

Use a custom task file:

```bash
./run_task my-task.md
```

Run with tests:

```bash
python3 src/cli.py --task-file my-task.md --test-command "pytest -x"
python3 src/cli.py --task-file my-task.md --test-command "npm test"
python3 src/cli.py --task-file my-task.md --test-command ""
```

Dry run (simulates agent responses to validate workflow wiring):

```bash
python3 src/cli.py --dry-run --task-file example-task.md --test-command ""
```

Help:

```bash
./run_task --help
python3 src/cli.py --help
```

## CLI Reference

### Core Options

| Flag | Default | Description |
|---|---|---|
| `[task-file]` | `task.md` | Positional compatibility shorthand for the task file. |
| `--task-file <path>` | `task.md` | Explicit task-file path; cannot be combined with the positional shorthand. |
| `--config <path>` | `RUN_TASK_CONFIG` or `./orchestrator.toml` | Optional repository configuration. |
| `--agents-file <path>` | `Dual-Agent-Orchestrator/AGENTS.md` | AGENTS instructions prepended to every agent prompt. |
| `--resume` / `--no-resume` | auto | Unfinished state resumes automatically; either flag overrides that decision. |
| `--force-overwrite-state` | auto for completed state | Overwrite existing state without confirmation; completed state enables it automatically. |
| `--from-phase <phase1\|phase2>` | auto | Force the starting phase (overrides state). |
| `--dry-run` | off | Simulate agent responses and tests to validate wiring. |
| `--manual-gate` | off | Require manual confirmation before starting Phase 2. |
| `--watch` | off | Watch inbox directory for `.md` tasks and process continuously. |
| `--inbox-dir <path>` | `inbox` | Inbox directory used by watch mode. |
| `--outbox-dir <path>` | `outbox` | Outbox directory used by watch mode. |
| `--poll-interval <seconds>` | `5.0` | Poll interval for watch mode. |

### Cycle Limits

| Flag | Default | Description |
|---|---|---|
| `--phase1-max-cycles` | `4` | Maximum planning cycles in Phase 1. |
| `--phase2-max-cycles` | `6` | Maximum implementation/review cycles in Phase 2. |
| `--max-agent-retries` | `1` | Retries per agent call after first failure. |

### Test Integration

| Flag | Default | Description |
|---|---|---|
| `--test-command <cmd>` | env → repo config → detection | Shell command for Phase 2; an explicitly empty value skips tests. |

### Agent Output

| Flag | Default | Description |
|---|---|---|
| `--agent-output <none\|summary\|full>` | `none` | How much of completed agent replies to show. |
| `--agent-output-max-chars` | `1800` | Max characters shown per reply in `summary` mode. |
| `--agent-live-stream` / `--no-agent-live-stream` | on | Enable or disable live agent stdout/stderr. |
| `--agent-live-stream-mode <compact\|full>` | `compact` | Verbosity for live stream output. |
| `--agent-live-stream-channels <both\|stdout\|stderr>` | env or `stdout` | Which output channels to print in live stream. |

### Context Limits

| Flag | Default | Description |
|---|---|---|
| `--max-shared-chars` | `30000` | Max characters from shared history included in prompts. |
| `--file-snapshot-max-lines` | `500` | Max lines per changed file snapshot for Claude review. |
| `--file-snapshot-max-files` | `10` | Max number of changed files included in snapshot. |

The orchestrator truncates shared history (`--max-shared-chars`) and changed-file snapshots to prevent prompt/context blowups. Keep each `task.md` narrowly scoped (explicitly name allowed files) so agents do not drift into unrelated areas.

### Recovery & Preflight

| Flag | Default | Description |
|---|---|---|
| `--no-recover` | off | Disable automatic rollback to last cycle checkpoint after crashes. |
| `--strict-preflight` | off | Fail preflight if DNS resolution fails for provider hosts. |
| `--skip-git-check` / `--no-skip-git-check` | off; on in watch mode | Override the environment and the watch-mode default. |

Quota and rate-limit errors freeze the current run and identify the agent that failed. Resume later with `--resume`; the orchestrator never invokes another agent as a substitute.

The former `--allow-fallback-to-gemini` option has been removed. Existing aliases, scheduled jobs, and wrapper scripts must drop this flag; agent failures now stop the run instead of selecting a substitute.

### Log Level

| Flag | Description |
|---|---|
| `--verbose` | Enable debug logging. |
| `--quiet` | Show warnings and errors only. |

`--verbose` and `--quiet` are mutually exclusive.

## Python Entrypoint and Configuration

`src/cli.py` is the single source of truth for argument parsing, environment overrides, test detection, automatic resume, and watch defaults. `run_task` is only a compatibility launcher that locates this Python file and forwards every argument unchanged.

Configuration values use this precedence:

1. Explicit CLI option
2. `RUN_TASK_*` environment variable
3. Repository `orchestrator.toml`
4. Built-in default or test-command auto-detection

An explicitly empty test command is meaningful and disables tests; it is never replaced by auto-detection:

```bash
python3 src/cli.py --test-command ""
RUN_TASK_TEST_CMD="" ./run_task
```

When no test command is configured, Python detects the first matching project layout:

1. If `pyproject.toml` contains pytest tool configuration → `python3 -m pytest tests/ -v`
2. If `package.json` exists and contains a `"test"` script → `npm test`
3. If `Makefile` exists and contains a `test:` target → `make test`
4. Otherwise → empty (tests skipped)

Override auto-detection with the `RUN_TASK_TEST_CMD` environment variable:

```bash
RUN_TASK_TEST_CMD="py -m pytest" ./run_task        # custom command
RUN_TASK_TEST_CMD="" ./run_task                     # explicitly skip tests
```

If you want to control this behavior manually, set:

```bash
RUN_TASK_SKIP_GIT_CHECK=0 ./run_task --watch     # enforce clean-tree check even in watch mode
RUN_TASK_SKIP_GIT_CHECK=1 ./run_task my-task.md  # skip check in single-file mode
RUN_TASK_WATCH_STREAM_CHANNELS=stdout ./run_task --watch  # override watch live stream channels
RUN_TASK_WATCH_STREAM_CHANNELS=both ./run_task --watch    # stream both channels in watch mode
```

An explicit `--skip-git-check` or `--no-skip-git-check` overrides the environment and watch default.
`RUN_TASK_WATCH_STREAM_CHANNELS` accepts `stdout` (default), `stderr`, or `both`; invalid values fall back to `stdout`.

### Repository TOML Schema

The optional `orchestrator.toml` contains only portable repository policy: path classes, named stop rules, validation commands, and the future manual slice-gate default. Unknown keys and invalid types or path patterns stop before workflow state is written.

```toml
[paths]
productive = ["src/**/*.py", "run_task", "*.toml"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "**/__pycache__/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop when the domain invariant changes."

[validation]
default_command = "python3 -m pytest tests/ -v"

[[validation.rules]]
patterns = ["engine/**"]
command = "npm run build:engine"

[workflow]
manual_slice_gate = false
```

Patterns use `/`, are relative to the repository root, and may not contain `..`. The productive pattern list may not be empty; later scope consumers conservatively treat paths that match no configured class as productive. Agent binary paths, models, and timeouts deliberately do not belong in this versioned file; their role-specific configuration is introduced with the agent adapters.

## Agent Instruction Files

The dual-agent orchestration contract is defined through repository-local instruction files:

| File | Location | Required | Primary role |
|---|---|---|---|
| `AGENTS.md` | Orchestrator repo root | Yes | Global runtime policy and machine-parseable marker contract consumed by `src/orchestrator.py`. |
| `CLAUDE.md` | Project root | Yes | Claude role profile (Phase 1 final confirmation, Phase 2 review). |
| `CODEX.md` | Project root | Yes | Codex role profile (Phase 1 plan review, Phase 2 implementation). |
| `GEMINI.md` | Project root | Transitional | Legacy backend profile retained until the planned Antigravity adapter migration; the current runtime does not invoke it as a substitute. |

`AGENTS.md` is intentionally the single source of truth for shared execution policy, safety, validation, and output markers.  
`CLAUDE.md`, `CODEX.md`, and `GEMINI.md` should stay lean and role-specific, and should not duplicate global policy text.

### Contract Markers (must stay synchronized)

The parser in `src/orchestrator.py` and prompts in `src/prompts.py` expect stable markers:

- Final line in orchestrated outputs: `STATUS: DONE`
- Approval markers by step:
  - `PHASE1_APPROVAL: YES|NO` (Phase 1 review + confirm)
  - `PHASE2_APPROVAL: YES|NO` (Phase 2 review)
  - `IMPLEMENTATION_READY: YES|NO` (Phase 2 implementation report)
- Legacy compatibility approvals accepted by parser:
  - `CODEX_APPROVAL: YES|NO`
  - `CLAUDE_APPROVAL: YES|NO`
- Findings lifecycle markers for review steps:
  - `OPEN_FINDINGS: NONE` or `OPEN_FINDINGS: F-001,F-002,...`
  - `FINDING_STATUS: <ID> | OPEN|CLOSED | <rationale>`
  - `NEW_FINDING: <ID> | <description> | <acceptance test>`
- Finding IDs must use `F-001` format.

Decision consistency rule:
- `*_APPROVAL: YES` only with `OPEN_FINDINGS: NONE`
- `*_APPROVAL: NO` only when findings remain open

Maintenance flow:
1. Update `AGENTS.md` when runtime/output-contract policy changes.
2. Keep `CLAUDE.md`, `CODEX.md`, and `GEMINI.md` aligned, role-specific, and non-contradictory.
3. If marker semantics change, update `src/prompts.py` and `src/orchestrator.py` in the same change.

Target-repository guidance:
- In external target repositories, keep `AGENTS.md` focused on orchestration/runtime contract.
- Put project/domain constraints (architecture, stack rules, coding conventions, folder ownership) in that target repo's `CLAUDE.md` / `CODEX.md` / `GEMINI.md`.

Verification:
- `./run_task --help` shows the default `--agents-file` path.
- Run `python3 -m pytest tests/ -v` after contract/parser/orchestration changes.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Pipeline completed successfully. |
| `1` | Pipeline failed (preflight, max cycles, or phase not completed). |
| `2` | Run frozen due to API quota/rate limit. Resume later with `--resume`. |
