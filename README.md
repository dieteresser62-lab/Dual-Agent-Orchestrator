# Dual-Agent Task Orchestrator

A CLI tool that automates complex coding work with a two-phase agent workflow: planning and implementation.

## Overview

The orchestrator reads a Markdown task description, creates/revises an implementation plan in Phase 1, and executes/fixes in Phase 2. State and artifacts are stored in `.orchestrator/`.

## Key Features

- Two-phase workflow: planning and implementation are separated.
- Stateful resume: continue from `.orchestrator/state.json`.
- Live streaming: follow agent output in compact or full mode.
- Test integration: run a configurable test command in Phase 2.
- Claude to Gemini fallback: optional quota/rate-limit fallback support.
- Fail-fast quota stop: freezes the run when an agent hits its API quota.

## Requirements

Platform support: Linux and macOS only. Windows is not supported because the orchestrator depends on `claude`, `codex`, and `gemini` CLI tools invoked via bash.

Install at least two (ideally all three) CLIs and make sure they are in `$PATH`:

- `codex`
- `claude`
- `gemini`

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
python3 src/orchestrator.py --task-file my-task.md --test-command "pytest -x"
python3 src/orchestrator.py --task-file my-task.md --test-command "npm test"
python3 src/orchestrator.py --task-file my-task.md --test-command ""
```

Dry run (simulates agent responses to validate workflow wiring):

```bash
python3 src/orchestrator.py --dry-run --task-file example-task.md --test-command ""
```

Help:

```bash
./run_task --help
python3 src/orchestrator.py --help
```

## CLI Reference

### Core Options

| Flag | Default | Description |
|---|---|---|
| `--task-file <path>` | `task.md` | Path to the Markdown task file. |
| `--resume` | off | Resume from existing `.orchestrator/state.json`. |
| `--force-overwrite-state` | off | Overwrite existing state without confirmation prompt. |
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
| `--test-command <cmd>` | _(empty = skip)_ | Shell command to run tests in Phase 2 (e.g. `pytest -x`, `npm test`). |

### Agent Output

| Flag | Default | Description |
|---|---|---|
| `--agent-output <none\|summary\|full>` | `summary` | How much of agent replies to show during execution. |
| `--agent-output-max-chars` | `1800` | Max characters shown per reply in `summary` mode. |
| `--agent-live-stream` | off | Stream agent CLI stdout/stderr live while running. |
| `--agent-live-stream-mode <compact\|full>` | `compact` | Verbosity for live stream output. |
| `--agent-live-stream-channels <both\|stdout\|stderr>` | `both` | Which output channels to print in live stream. |

### Context Limits

| Flag | Default | Description |
|---|---|---|
| `--max-shared-chars` | `30000` | Max characters from shared history included in prompts. |
| `--file-snapshot-max-lines` | `500` | Max lines per changed file snapshot for Claude review. |
| `--file-snapshot-max-files` | `10` | Max number of changed files included in snapshot. |

### Recovery & Fallback

| Flag | Default | Description |
|---|---|---|
| `--no-recover` | off | Disable automatic rollback to last cycle checkpoint after crashes. |
| `--allow-fallback-to-gemini` | off | If Claude hits quota/rate limits, retry that step with Gemini. |
| `--strict-preflight` | off | Fail preflight if DNS resolution fails for provider hosts. |

### Log Level

| Flag | Description |
|---|---|
| `--verbose` | Enable debug logging. |
| `--quiet` | Show warnings and errors only. |

`--verbose` and `--quiet` are mutually exclusive.

## Auto-Detection of Test Commands (`run_task`)

The `run_task` wrapper script automatically detects the test command before invoking the orchestrator. Detection priority:

1. If `pyproject.toml` exists and contains `[tool.pytest]` → `python3 -m pytest tests/ -v`
2. If `package.json` exists and contains a `"test"` script → `npm test`
3. If `Makefile` exists and contains a `test:` target → `make test`
4. Otherwise → empty (tests skipped)

Override auto-detection with the `RUN_TASK_TEST_CMD` environment variable:

```bash
RUN_TASK_TEST_CMD="py -m pytest" ./run_task        # custom command
RUN_TASK_TEST_CMD="" ./run_task                     # explicitly skip tests
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Pipeline completed successfully. |
| `1` | Pipeline failed (preflight, max cycles, or phase not completed). |
| `2` | Run frozen due to API quota/rate limit. Resume later with `--resume`. |
