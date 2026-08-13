# Quickstart

This guide takes you from a prepared repository to one completed State-v3 run. See [README.md](README.md) for the complete workflow, configuration, and CLI reference. For design context, read the [Architecture and Domain Concept](docs/reference/architecture-and-domain-concept.md); for product positioning, read the [Market Comparison](docs/reference/market-comparison.md).

## 1. Check the prerequisites

You need Python 3.11 or newer, Git, and authenticated installations of all three role CLIs:

```bash
python3 --version
git --version
codex --version
claude --version
agy --version
```

Run the orchestrator on Linux, macOS, or WSL2. Native Windows is not currently supported. Under WSL2, configure `agy.exe` explicitly if the native `agy` command is unavailable.

## 2. Prepare the target repository

Check out the branch on which the orchestrator may create local slice commits. Start with a clean worktree unless you intentionally configured the run to accept existing changes:

```bash
cd /path/to/target-repository
git branch --show-current
git status --short
```

The orchestrator creates local commits only. It never pushes, merges, force-pushes, or rewrites history.

## 3. Write a bounded task

Copy [example-task.md](example-task.md) to the target repository as `task.md`, then replace its example content. At minimum, define:

- the intended outcome;
- the exact allowed paths;
- acceptance criteria and validation commands;
- explicit non-scope;
- conditions that require a user decision.

Keep the work plan and prepared slice audit documents inside the declared path scope. Do not place secrets or credentials in the task file.

## 4. Run a smoke test

From this orchestrator repository, verify the installation without calling agents or changing a repository:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

A successful smoke test exits with code 0.

## 5. Start the real run

From the target repository, invoke the launcher by its absolute path or through a configured global symlink:

```bash
/absolute/path/to/Dual-Agent-Orchestrator/run_task --task-file task.md
```

If `run_task` is on `PATH`, the shorter form is:

```bash
run_task --task-file task.md
```

The orchestrator asks Codex to plan and implement bounded slices. Claude Sonnet with effort `high` reviews the plan and targeted slice changes. Antigravity reviews each complete Claude-approved slice. Passing slices are committed locally, followed by a branch-wide final review.

## 6. Resume a stopped run

An unfinished single-task run resumes automatically when you repeat the command. After resolving a recorded gate or restarting the process, resume explicitly:

```bash
run_task --resume --task-file task.md
```

If exit code 4 requires an explicit gate decision, inspect the persisted reason before approving the exact fingerprint:

```bash
run_task --resume --task-file task.md \
  --approve-gate \
  --gate-actor "Your Name" \
  --gate-rationale "Reviewed the persisted gate and approved continuation"
```

Exit codes 2 and 3 indicate a resumable quota or agent failure. Repair the reported condition and resume the same run. Never edit `.orchestrator/state.json` or checkpoint files manually.

## 7. Verify completion

A completed run exits with code 0. Confirm the resulting local commits and clean status:

```bash
git log --oneline --decorate -n 10
git status --short
```

Runtime state, checkpoints, logs, and persisted agent outputs are stored below `.orchestrator/`. Human-readable work plans and slice audit documents remain in the paths declared by the task.
