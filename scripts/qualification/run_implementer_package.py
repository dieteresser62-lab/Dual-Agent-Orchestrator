"""Operator-only live measurement; default mode runs the offline self-test."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.qualification.implementer_package.evaluate import (
    PACKAGE, RULE_SHA256, TASK_IDS, digest, evaluate, load_rule, summarize,
)
from scripts.qualification.implementer_package.offline import build_fixture, selftest
from scripts.qualification.implementer_package.records import implementation_active, project, read_records

IMPLEMENTER_PROVIDER = "claude"  # allowlist:provider -- profile configuration: measurement candidate
REVIEWER_PROVIDER = "codex"  # allowlist:provider -- profile configuration: production fallback


def config(toolchain_read_roots: list[Path]) -> str:
    roots = [str(path.resolve(strict=True)) for path in toolchain_read_roots]
    if any(not Path(path).is_dir() for path in roots):
        raise ValueError("toolchain_read_roots must be existing directories")
    return ('''[repository]
base_branch = "main"
[paths]
productive = ["app.py", "codec.py"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md", "audit/**"]
generated = [".orchestrator/**", "__pycache__/**", ".pytest_cache/**"]
[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-v"]
default_timeout_seconds = 30
[workflow]
merge_completed_branch = false
plan_gate = false
test_change_gate = false
manual_slice_gate = false
scope_extension_gate = false
[roles]
implementer = "candidate"
reviewer = "reference"
final_reviewer = "reference"
[agent_profiles.candidate]
provider = "$IMPLEMENTER_PROVIDER"
model = "opus"
effort = "high"
timeout_seconds = 0
[agent_profiles.candidate.provider_options.$IMPLEMENTER_PROVIDER]
toolchain_read_roots = ''' + json.dumps(roots) + '''
[agent_profiles.reference]
provider = "$REVIEWER_PROVIDER"
model = "sol"
effort = "medium"
timeout_seconds = 0
''').replace("$IMPLEMENTER_PROVIDER", IMPLEMENTER_PROVIDER).replace("$REVIEWER_PROVIDER", REVIEWER_PROVIDER)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def protected_inventory(repo: Path) -> dict[str, str]:
    """Freeze static Git infrastructure, queue files and unmanaged control files.

    Mutable Git objects/refs/index are checked against the commit ledger. The
    orchestrator's records and evidence are checked by the store and replay.
    """
    files = {}
    for area in (".git", ".orchestrator", "inbox", "outbox"):
        root = repo / area
        if not root.exists():
            continue
        for path in root.rglob("*"):
            name = path.relative_to(repo).as_posix()
            if area == ".git" and (name.split("/")[1] in {"objects", "refs", "logs"}
                or path.name in {"HEAD", "index", "ORIG_HEAD", "COMMIT_EDITMSG"}):
                continue
            if area == ".orchestrator" and name.split("/")[1] in {
                "artifacts", "runs", "logs", "checkpoints", "state.json", "state.json.lock",
            }:
                continue
            if path.is_symlink() or path.is_file():
                files[name] = digest(path)
    return files


def unauthorized_protected(repo: Path, before: dict, evidence: dict) -> list[str]:
    after = protected_inventory(repo)
    authorized = {}
    for record in evidence.get("records", []):
        if record["record_type"] != "side_effect":
            continue
        item = record["payload"]
        if item["phase"] != "result":
            continue
        operation = item["operation"]
        if item["effect_class"] in {"file_write", "queue_move"}:
            path = Path(operation[0] if item["effect_class"] == "file_write" else operation[1])
            target = path if path.is_absolute() else repo / path
            if target.is_relative_to(repo):
                authorized[target.relative_to(repo).as_posix()] = item["result"]
    return [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)
            and (name in before or authorized.get(name) != after.get(name))]


def target_branch(case: str) -> str:
    """Read the frozen task's TARGET_BRANCH line; fail closed on anything else."""
    lines = [line for line in (PACKAGE / "cases" / case / "task.md").read_text(encoding="utf-8").splitlines()
             if line.startswith("TARGET_BRANCH:")]
    if len(lines) != 1:
        raise ValueError(f"{case} task must declare exactly one TARGET_BRANCH")
    branch = lines[0].split(":", 1)[1].strip()
    if not branch.startswith("feature/") or any(part in branch for part in ("..", " ", "~", "^", ":")):
        raise ValueError(f"{case} task declares an invalid TARGET_BRANCH")
    return branch


def make_clone(case: str, folder: Path, tool_roots: list[Path]) -> tuple[Path, str, dict]:
    seed = build_fixture(case, folder / "seed")
    (seed / "orchestrator.toml").write_text(config(tool_roots))
    git(seed, "init", "--initial-branch=main")
    git(seed, "config", "user.name", "Implementer qualification")
    git(seed, "config", "user.email", "qualification@example.invalid")
    git(seed, "add", ".")
    git(seed, "commit", "-m", f"Frozen {case} baseline")
    repo = folder / "repo"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(seed), str(repo)], check=True)
    git(repo, "remote", "remove", "origin")
    git(repo, "config", "user.name", "Implementer qualification")
    git(repo, "config", "user.email", "qualification@example.invalid")
    # The operator creates the task branch before a run; the orchestrator
    # refuses to start on any other branch.
    git(repo, "checkout", "--quiet", "-b", target_branch(case))
    # Freeze canaries in every protected root. Orchestrator-owned records and
    # Git transactions are audited separately using their authoritative ledger.
    frozen = {"orchestrator.toml": digest(repo / "orchestrator.toml")}
    for name in (".git/hooks/package-canary", ".orchestrator/package-canary",
                 "inbox/package-canary", "outbox/package-canary"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("frozen protected-path canary\n")
        frozen[name] = digest(path)
    frozen.update(protected_inventory(repo))
    return repo, git(repo, "rev-parse", "HEAD"), frozen


def stop_process(process) -> None:
    """Bound cleanup to the session created by this runner."""
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_process(command: list[str], repo: Path, log: Path, *, interrupt: bool = False,
                timeout: float = 3600, popen=subprocess.Popen, ready=implementation_active,
                clock=time.monotonic, sleep=time.sleep) -> dict:
    start = clock()
    interrupted = False
    timed_out = False
    with log.open("wb") as output:
        process = popen(command, cwd=repo, stdin=subprocess.DEVNULL, stdout=output,
                        stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if interrupt and not interrupted and ready(repo):
                    process.send_signal(signal.SIGINT)
                    interrupted = True
                    # Allow normal orchestrator/provider cleanup; do not signal twice.
                if clock() - start >= timeout:
                    timed_out = True
                    stop_process(process)
                    break
                sleep(0.05)
        finally:
            stop_process(process)
    return {"returncode": process.returncode, "interrupted": interrupted,
            "timed_out": timed_out, "log": str(log), "seconds": clock() - start}


def run_task(case: str, folder: Path, orchestrator: Path, tool_roots: list[Path], timeout: float) -> dict:
    folder.mkdir()
    repo, baseline, protected = make_clone(case, folder, tool_roots)
    infrastructure = protected_inventory(repo)
    task = folder / "task.md"
    shutil.copyfile(PACKAGE / "cases" / case / "task.md", task)
    command = [str(orchestrator / "run_task"), "--task-file", str(task), "--config", str(repo / "orchestrator.toml")]
    processes = [run_process([*command, "--no-resume"], repo, folder / "initial.log",
                             interrupt=case == "T5", timeout=timeout)]
    interrupted_head = None
    resume_prefix = []
    if case == "T5" and processes[0]["interrupted"] and not processes[0]["timed_out"]:
        interrupted_head = git(repo, "rev-parse", "HEAD")
        before, _ = read_records(repo)
        resume_prefix = [item["record_id"] for item in before]
        processes.append(run_process([*command, "--resume"], repo, folder / "resume.log", timeout=timeout))
    try:
        records, results = read_records(repo)
        evidence = project(records, results)
    except (ValueError, OSError, RuntimeError) as exc:
        evidence = {"valid_records": False, "record_error": str(exc)}
    tracked = git(repo, "diff", "--name-only", baseline).splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    protected_changes = [name for name, expected in protected.items() if digest(repo / name) != expected]
    protected_changes += unauthorized_protected(repo, infrastructure, evidence)
    if digest(task) != digest(PACKAGE / "cases" / case / "task.md"):
        protected_changes.append("inbox/modified-measurement-task")
    evidence.update({"changed_paths": sorted(set(tracked + untracked + protected_changes)),
                     "returncode": processes[-1]["returncode"], "processes": processes,
                     "baseline_head": baseline, "final_head": git(repo, "rev-parse", "HEAD"),
                     "task_sha256": digest(task), "config_sha256": digest(repo / "orchestrator.toml"),
                     "interrupted_head": interrupted_head})
    committed = [item["payload"]["result"] for item in evidence.get("records", [])
                 if item["record_type"] == "side_effect" and item["payload"]["phase"] == "result"
                 and item["payload"]["effect_class"] == "git_commit"]
    actual = git(repo, "rev-list", f"{baseline}..HEAD").splitlines()
    authorized_git = set(actual) == set(committed) and len(actual) == len(committed)
    if not authorized_git:
        evidence["changed_paths"].append(".git/unauthorized-commits")
    evidence["resume_verified"] = (case == "T5" and len(processes) == 2
                                   and processes[0]["returncode"] != 0 and processes[1]["returncode"] == 0
                                   and all(not item["timed_out"] for item in processes)
                                   and evidence.get("ledger_clean") and authorized_git
                                   and bool(resume_prefix)
                                   and [item["record_id"] for item in evidence.get("records", [])][:len(resume_prefix)] == resume_prefix
                                   and len(evidence.get("records", [])) > len(resume_prefix)
                                   and evidence.get("slice_commits") == 1)
    return evaluate(case, repo, evidence)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Operator only: permits real orchestrator/provider runs")
    parser.add_argument("--output", type=Path, required=True, help="New JSON report path")
    parser.add_argument("--workspace", type=Path, help="New isolated live campaign directory")
    parser.add_argument("--orchestrator-root", type=Path, default=ROOT,
                        help="Operator-prepared, authorized orchestrator snapshot; no candidate bypass is added")
    parser.add_argument("--toolchain-read-root", type=Path, action="append", default=[])
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    load_rule()
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be a new file")
    if not args.timeout > 0 or not args.timeout < float("inf"):
        parser.error("timeout must be positive and finite")
    if not args.live:
        selftest(args.output)
        return 0
    if args.workspace is None or args.workspace.exists():
        parser.error("--live requires a new --workspace")
    workspace = args.workspace.resolve()
    orchestrator = args.orchestrator_root.resolve(strict=True)
    if workspace.is_relative_to(ROOT) or workspace.is_relative_to(orchestrator):
        parser.error("live workspace must be outside the source/orchestrator checkout")
    args.workspace.mkdir(parents=True)
    results = []
    for case in TASK_IDS:
        try:
            result = run_task(case, workspace / case, orchestrator, args.toolchain_read_root, args.timeout)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            result = {"task": case, "passed": False, "absolute_errors": [],
                      "failure_reasons": ["runner_error"], "error": str(exc), "evidence_complete": False}
        results.append(result)
        # Preserve every attempt, including incomplete/negative tasks, immediately.
        args.output.write_text(json.dumps({"mode": "live", "incomplete": True,
                                         "rule_sha256": RULE_SHA256,
                                         "tasks": results}, indent=2) + "\n")
    report = summarize(results)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
