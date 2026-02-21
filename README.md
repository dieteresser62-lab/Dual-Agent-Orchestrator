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

## Requirements

Designed for Linux/Unix-like shells (including macOS and WSL).

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

Dry run:

```bash
python3 src/orchestrator.py --dry-run --auto --task-file example-task.md --test-command ""
```

Help:

```bash
./run_task --help
python3 src/orchestrator.py --help
```
