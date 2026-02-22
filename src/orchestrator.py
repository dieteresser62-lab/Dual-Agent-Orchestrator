#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime import (
    AGENT_REGISTRY,
    OrchestratorConfig,
    QuotaReachedError,
    collect_file_snapshots,
    preflight as runtime_preflight,
    repo_snapshot as runtime_repo_snapshot,
    run_agent_checked as runtime_run_agent_checked,
    run_tests_snapshot as runtime_run_tests_snapshot,
)
from inbox_watcher import watch_inbox
from prompts import (
    build_phase1_claude_confirm_prompt,
    build_phase1_claude_plan_prompt,
    build_phase1_codex_review_prompt,
    build_phase2_claude_review_prompt,
    build_phase2_codex_implement_prompt,
    build_test_failure_block,
)
from state_io import (
    FINDING_ID_PATTERN,
    append_markdown,
    build_artifact_paths as state_build_artifact_paths,
    checkpoint_path as state_checkpoint_path,
    ensure_state_shape as state_ensure_state_shape,
    init_state as state_init_state,
    load_cycle_checkpoint as state_load_cycle_checkpoint,
    load_state as state_load_state,
    new_run_id as state_new_run_id,
    now_iso as state_now_iso,
    read_file,
    save_state as state_save_state,
    write_cycle_checkpoint as state_write_cycle_checkpoint,
    write_file,
)

DEFAULT_TASK_FILE = "task.md"
ARTIFACT_ROOT_DIR = Path(".orchestrator")
ARTIFACT_RUNS_DIR = ARTIFACT_ROOT_DIR / "runs"
LATEST_RUN_FILE = ARTIFACT_ROOT_DIR / "LATEST_RUN.txt"
STATE_DIR = Path(".orchestrator")
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
CHECKPOINT_DIR = STATE_DIR / "checkpoints"
MAX_ERROR_CHARS = 1800
MIN_AGENT_OUTPUT_MAX_CHARS = 200
MAX_DIFF_CHARS = 14000
TEST_TIMEOUT_SECONDS = 300
MAX_SHARED_CHARS = 30000
DELIMITED_SECTION_PATTERN = re.compile(
    r"<<<\s*([A-Z_]+)_BEGIN\s*>>>.*?<<<\s*\1_END\s*>>>",
    re.IGNORECASE | re.DOTALL,
)
logger = logging.getLogger(__name__)

@dataclass
class RunContext:
    config: OrchestratorConfig
    test_command: str = ""
    artifact_root_dir: Path = ARTIFACT_ROOT_DIR
    artifact_runs_dir: Path = ARTIFACT_RUNS_DIR
    latest_run_file: Path = LATEST_RUN_FILE
    state_dir: Path = STATE_DIR
    state_file: Path = STATE_FILE
    log_dir: Path = LOG_DIR
    checkpoint_dir: Path = CHECKPOINT_DIR
    run_artifact_dir: Path = field(init=False)
    task_snapshot_file: Path = field(init=False)
    phase1_shared_file: Path = field(init=False)
    phase2_shared_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.run_artifact_dir = self.artifact_root_dir
        self.task_snapshot_file = self.run_artifact_dir / "00_task.md"
        self.phase1_shared_file = self.run_artifact_dir / "10_phase1_plan.md"
        self.phase2_shared_file = self.run_artifact_dir / "20_phase2_implementation.md"

    def init_dirs(self) -> None:
        self.artifact_root_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_runs_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def configure_artifacts(self, artifacts: dict[str, str | Path]) -> None:
        self.run_artifact_dir = Path(artifacts["run_dir"])
        self.task_snapshot_file = Path(artifacts["task"])
        self.phase1_shared_file = Path(artifacts["phase1_shared"])
        self.phase2_shared_file = Path(artifacts["phase2_shared"])

    def build_artifact_paths(self, run_id: str) -> dict[str, str | Path]:
        return state_build_artifact_paths(run_id, self.artifact_runs_dir)

    def new_run_id(self) -> str:
        return state_new_run_id()

    def load_state(self) -> dict:
        return state_load_state(self.state_file)

    def save_state(self, state: dict) -> None:
        state_save_state(self.state_file, state)

    def init_state(
        self,
        task_file: Path,
        phase1_max_cycles: int,
        phase2_max_cycles: int,
        artifacts: dict[str, str | Path],
    ) -> dict:
        return state_init_state(task_file, phase1_max_cycles, phase2_max_cycles, artifacts)

    def ensure_state_shape(self, state: dict, task_file: Path, args: argparse.Namespace) -> dict:
        return state_ensure_state_shape(
            state,
            task_file,
            args.phase1_max_cycles,
            args.phase2_max_cycles,
            self.artifact_runs_dir,
        )

    def run_tests_snapshot(self) -> tuple[int, str]:
        return runtime_run_tests_snapshot(
            config=self.config,
            test_command=self.test_command,
            test_timeout_seconds=TEST_TIMEOUT_SECONDS,
            shorten=shorten,
        )

    def repo_snapshot(self) -> str:
        return runtime_repo_snapshot(MAX_DIFF_CHARS)

    def run_agent_checked(
        self,
        *,
        agent_key: str,
        prompt: str,
        log_prefix: str,
        max_retries: int,
        required_flags: list[str] | None = None,
        output_validator=None,
    ) -> str:
        return runtime_run_agent_checked(
            agent_key=agent_key,
            prompt=prompt,
            log_prefix=log_prefix,
            max_retries=max_retries,
            required_flags=required_flags,
            output_validator=output_validator,
            config=self.config,
            agents=AGENT_REGISTRY,
            log_dir=self.log_dir,
            write_file=write_file,
            shorten=shorten,
            parse_flag=parse_flag,
            validate_done_marker=validate_done_marker,
        )

    def preflight(self, required_agents: list[str], strict: bool, *, skip_git_check: bool = False) -> bool:
        return runtime_preflight(
            required_agents,
            strict,
            AGENT_REGISTRY,
            skip_git_check=skip_git_check,
        )

    def checkpoint_cycle_state(self, phase: str, cycle: int, state: dict) -> None:
        path = state_write_cycle_checkpoint(self.checkpoint_dir, phase, cycle, state)
        logger.info("[CHECKPOINT] saved %s", path)

    def recover_state_from_checkpoint(self, state: dict) -> dict:
        phase = str(state.get("phase", "phase1"))
        if phase not in ("phase1", "phase2"):
            return state
        phase_state = state.get(phase, {})
        if phase_state.get("status") not in ("running", "frozen"):
            return state

        cycle = int(phase_state.get("cycle", 0))
        if cycle <= 0:
            return state

        recovered = state_load_cycle_checkpoint(self.checkpoint_dir, phase, cycle)
        if recovered is None:
            path = state_checkpoint_path(self.checkpoint_dir, phase, cycle)
            logger.warning("[RECOVERY] no checkpoint found at %s; continuing without rollback.", path)
            return state

        logger.info("[RECOVERY] restored checkpoint for %s cycle=%s.", phase, cycle)
        return recovered


def format_duration(total_seconds: float) -> str:
    seconds = max(0, int(total_seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def shorten(text: str | None, limit: int = MAX_ERROR_CHARS) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]} ...[truncated]"


def truncate_shared(text: str, limit: int) -> str:
    if limit <= 0:
        return "...[earlier history truncated]"
    if len(text) <= limit:
        return text
    return "...[earlier history truncated]\n\n" + text[-limit:]


def parse_changed_files_from_impl_report(impl_report: str) -> list[str]:
    lines = (impl_report or "").splitlines()
    changed: list[str] = []
    in_section = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not in_section:
            if re.match(r"^#+\s*Changed Files\s*$", stripped, re.IGNORECASE):
                in_section = True
            continue

        if not stripped:
            if changed:
                break
            continue
        if re.match(r"^#+\s+", stripped):
            break
        if stripped.startswith("IMPLEMENTATION_READY:") or stripped.startswith("STATUS:"):
            break

        match = re.match(r"^[-*]\s+(`?)([^`]+)\1\s*$", stripped)
        if match:
            candidate = match.group(2).strip()
            if candidate and re.match(r"^[^\s|:][^|:\s]*$", candidate):
                changed.append(candidate)
    return changed


def print_summary_report(state: dict) -> None:
    started_raw = str(state.get("started_at", "")).strip()
    started_at = None
    try:
        if started_raw:
            started_at = datetime.fromisoformat(started_raw)
    except ValueError:
        started_at = None

    ended_at = datetime.now(timezone.utc)
    duration = "unknown"
    if started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        duration = format_duration((ended_at - started_at).total_seconds())

    p1 = state.get("phase1", {})
    p2 = state.get("phase2", {})
    p1_cycles = int(p1.get("cycle", 0))
    p2_cycles = int(p2.get("cycle", 0))

    closed_ids: set[str] = set()
    open_ids: set[str] = set()
    for phase_key in ("phase1", "phase2"):
        phase_state = state.get(phase_key, {})
        for fid, value in dict(phase_state.get("finding_history", {})).items():
            fid_up = str(fid).upper()
            if not FINDING_ID_PATTERN.match(fid_up):
                continue
            status = str(value).upper()
            if status == "CLOSED":
                closed_ids.add(fid_up)
            elif status == "OPEN":
                open_ids.add(fid_up)
        for fid in phase_state.get("open_findings", []):
            fid_up = str(fid).upper()
            if FINDING_ID_PATTERN.match(fid_up):
                open_ids.add(fid_up)

    # If an ID is both open and closed over time, current open state wins.
    closed_ids -= open_ids

    logger.info("Run Summary:")
    logger.info("  - duration: %s", duration)
    logger.info("  - phase1 cycles: %s", p1_cycles)
    logger.info("  - phase2 cycles: %s", p2_cycles)
    logger.info("  - closed findings: %s", len(closed_ids))
    logger.info("  - open findings: %s", len(open_ids))


def find_task_file(explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"Task file not found: {path}")

    path = Path(DEFAULT_TASK_FILE)
    if path.exists():
        return path
    raise FileNotFoundError(
        f"Task file missing. Expected: {DEFAULT_TASK_FILE}"
    )


def validate_done_marker(text: str) -> bool:
    lines = [line.strip() for line in strip_delimited_sections(text).splitlines() if line.strip()]
    if not lines:
        return False
    return lines[-1] == "STATUS: DONE"


def strip_delimited_sections(text: str) -> str:
    return DELIMITED_SECTION_PATTERN.sub("", text or "")


def parse_flag(text: str, key: str) -> str | None:
    contract_text = strip_delimited_sections(text)
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(YES|NO)\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(pattern.finditer(contract_text))
    if not matches:
        return None
    return matches[-1].group(1).upper()


def format_findings_list(finding_ids: list[str]) -> str:
    if not finding_ids:
        return "NONE"
    return ", ".join(finding_ids)


def parse_open_findings(text: str) -> list[str] | None:
    contract_text = strip_delimited_sections(text)
    pattern = re.compile(r"^\s*OPEN_FINDINGS\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(pattern.finditer(contract_text))
    if not matches:
        return None
    raw = matches[-1].group(1).strip()
    if raw.upper() == "NONE":
        return []
    finding_ids = [part.strip().upper() for part in raw.split(",") if part.strip()]
    return finding_ids


def parse_finding_status_map(text: str) -> dict[str, str]:
    contract_text = strip_delimited_sections(text)
    pattern = re.compile(
        r"^\s*FINDING_STATUS\s*:\s*([A-Za-z0-9_-]+)\s*\|\s*(OPEN|CLOSED)\s*\|.+$",
        re.IGNORECASE | re.MULTILINE,
    )
    status_map: dict[str, str] = {}
    for match in pattern.finditer(contract_text):
        finding_id = match.group(1).strip().upper()
        status = match.group(2).strip().upper()
        status_map[finding_id] = status
    return status_map


def parse_new_findings(text: str) -> dict[str, str]:
    contract_text = strip_delimited_sections(text)
    pattern = re.compile(
        r"^\s*NEW_FINDING\s*:\s*([A-Za-z0-9_-]+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    found: dict[str, str] = {}
    for match in pattern.finditer(contract_text):
        finding_id = match.group(1).strip().upper()
        summary = match.group(2).strip()
        acceptance = match.group(3).strip()
        found[finding_id] = f"{summary} | {acceptance}"
    return found


def validate_agent_contract(
    output: str,
    previous_open_findings: list[str],
    approval_key: str,
) -> tuple[str | None, list[str] | None]:
    approval = parse_flag(output, approval_key)
    if approval not in ("YES", "NO"):
        return f"missing or invalid {approval_key} marker", None

    open_findings = parse_open_findings(output)
    if open_findings is None:
        return "missing OPEN_FINDINGS marker", None

    for finding_id in open_findings:
        if not FINDING_ID_PATTERN.match(finding_id):
            return f"invalid finding id '{finding_id}' (expected format F-001)", None
    if len(open_findings) != len(set(open_findings)):
        return "OPEN_FINDINGS contains duplicate finding ids", None

    if approval == "YES" and open_findings:
        return f"{approval_key}: YES is only allowed when OPEN_FINDINGS: NONE", None
    if approval == "NO" and not open_findings:
        return f"{approval_key}: NO requires at least one open finding", None

    status_map = parse_finding_status_map(output)
    for finding_id in status_map:
        if not FINDING_ID_PATTERN.match(finding_id):
            return (
                f"invalid finding id '{finding_id}' in FINDING_STATUS (expected format F-001)",
                None,
            )
    for finding_id in previous_open_findings:
        if finding_id not in status_map:
            return f"missing FINDING_STATUS line for previous open finding {finding_id}", None

    new_findings = parse_new_findings(output)
    for finding_id in new_findings:
        if not FINDING_ID_PATTERN.match(finding_id):
            return (
                f"invalid finding id '{finding_id}' in NEW_FINDING (expected format F-001)",
                None,
            )
    for finding_id in open_findings:
        if finding_id not in previous_open_findings and finding_id not in new_findings:
            return (
                f"new open finding {finding_id} requires NEW_FINDING: {finding_id} | <summary> | <acceptance>",
                None,
            )

    return None, open_findings


def validate_codex_phase1_contract(output: str, previous_open_findings: list[str]) -> tuple[str | None, list[str] | None]:
    return validate_agent_contract(output, previous_open_findings, "CODEX_APPROVAL")


def validate_claude_phase2_contract(output: str, previous_open_findings: list[str]) -> tuple[str | None, list[str] | None]:
    return validate_agent_contract(output, previous_open_findings, "CLAUDE_APPROVAL")


def validate_codex_phase1_contract_error(output: str, previous_open_findings: list[str]) -> str | None:
    return validate_codex_phase1_contract(output, previous_open_findings)[0]


def validate_claude_phase2_contract_error(output: str, previous_open_findings: list[str]) -> str | None:
    return validate_claude_phase2_contract(output, previous_open_findings)[0]


def approval_gate(message: str) -> bool:
    print(f"\n{'=' * 60}")
    print(message)
    print(f"{'=' * 60}")
    try:
        response = input("Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return False
    return response == "y"


def freeze_current_phase(state: dict, error: QuotaReachedError, ctx: RunContext) -> None:
    phase_key = str(state.get("phase", "phase1"))
    if phase_key not in ("phase1", "phase2"):
        phase_key = "phase1"
    phase_state = state.get(phase_key, {})
    cycle = int(phase_state.get("cycle", 0))
    if cycle > 0:
        phase_state["cycle"] = cycle - 1
    phase_state["status"] = "frozen"
    phase_state["error"] = str(error)
    state["phase"] = phase_key
    state["updated_at"] = state_now_iso()
    ctx.save_state(state)

    logger.error("Run frozen due to quota/rate limit on %s.", error.agent_key)
    logger.error("%s", error)
    logger.info("Resume later with --resume after quota/rate-limit resets.")


def run_phase1(task_text: str, state: dict, args: argparse.Namespace, ctx: RunContext) -> None:
    phase1 = state["phase1"]
    phase1["status"] = "running"
    state["phase"] = "phase1"
    state["updated_at"] = state_now_iso()
    ctx.save_state(state)

    start_cycle = int(phase1.get("cycle", 0)) + 1
    max_cycles = int(phase1.get("max_cycles", args.phase1_max_cycles))

    for cycle in range(start_cycle, max_cycles + 1):
        ctx.checkpoint_cycle_state("phase1", cycle, state)
        phase1["cycle"] = cycle
        phase1["error"] = None
        state["updated_at"] = state_now_iso()
        ctx.save_state(state)

        logger.info("=== PHASE 1 | cycle %s/%s: Claude plan ===", cycle, max_cycles)
        shared = truncate_shared(read_file(ctx.phase1_shared_file), args.max_shared_chars)
        previous_open_findings = [str(item).upper() for item in phase1.get("open_findings", [])]
        claude_plan = ctx.run_agent_checked(
            agent_key="claude",
            prompt=build_phase1_claude_plan_prompt(
                task_text=task_text,
                shared_text=shared,
                cycle=cycle,
                open_block=format_findings_list(previous_open_findings),
            ),
            log_prefix=f"phase1-cycle{cycle}-claude-plan",
            max_retries=max(args.max_agent_retries, 0),
            required_flags=["CLAUDE_APPROVAL"],
        )
        append_markdown(ctx.phase1_shared_file, f"Phase 1 / Cycle {cycle} / Claude Plan", claude_plan)

        logger.info("=== PHASE 1 | cycle %s/%s: Codex review ===", cycle, max_cycles)
        shared = truncate_shared(read_file(ctx.phase1_shared_file), args.max_shared_chars)
        codex_review = ctx.run_agent_checked(
            agent_key="codex",
            prompt=build_phase1_codex_review_prompt(
                task_text=task_text,
                shared_text=shared,
                cycle=cycle,
                previous_open_block=format_findings_list(previous_open_findings),
            ),
            log_prefix=f"phase1-cycle{cycle}-codex-review",
            max_retries=max(args.max_agent_retries, 0),
            required_flags=["CODEX_APPROVAL"],
            output_validator=functools.partial(
                validate_codex_phase1_contract_error,
                previous_open_findings=list(previous_open_findings),
            ),
        )
        append_markdown(ctx.phase1_shared_file, f"Phase 1 / Cycle {cycle} / Codex Review", codex_review)

        codex_approval = parse_flag(codex_review, "CODEX_APPROVAL") or "NO"
        parsed_open_findings = parse_open_findings(codex_review) or []
        phase1["open_findings"] = parsed_open_findings

        status_map = parse_finding_status_map(codex_review)
        new_finding_map = parse_new_findings(codex_review)
        finding_history = dict(phase1.get("finding_history", {}))
        for finding_id, value in status_map.items():
            finding_history[finding_id] = value
        for finding_id, value in new_finding_map.items():
            finding_history[finding_id] = value
        phase1["finding_history"] = finding_history

        logger.info("=== PHASE 1 | cycle %s/%s: Claude confirmation ===", cycle, max_cycles)
        shared = truncate_shared(read_file(ctx.phase1_shared_file), args.max_shared_chars)
        claude_confirm = ctx.run_agent_checked(
            agent_key="claude",
            prompt=build_phase1_claude_confirm_prompt(
                task_text=task_text,
                shared_text=shared,
                cycle=cycle,
                open_block=format_findings_list(parsed_open_findings),
                codex_approval=codex_approval,
            ),
            log_prefix=f"phase1-cycle{cycle}-claude-confirm",
            max_retries=max(args.max_agent_retries, 0),
            required_flags=["CLAUDE_APPROVAL"],
        )
        append_markdown(ctx.phase1_shared_file, f"Phase 1 / Cycle {cycle} / Claude Confirm", claude_confirm)

        claude_approval = parse_flag(claude_confirm, "CLAUDE_APPROVAL") or "NO"
        if codex_approval == "NO" and claude_approval == "YES":
            logger.warning("Claude approval overridden to NO because Codex has open findings.")
            claude_approval = "NO"

        phase1["codex_approval"] = codex_approval
        phase1["claude_approval"] = claude_approval
        state["updated_at"] = state_now_iso()
        ctx.save_state(state)

        if codex_approval == "YES" and claude_approval == "YES":
            phase1["status"] = "completed"
            phase1["completed_at"] = state_now_iso()
            state["phase"] = "phase2"
            state["updated_at"] = state_now_iso()
            ctx.save_state(state)
            logger.info("[OK] PHASE 1 completed: both Claude and Codex approved the plan.")
            return

        logger.info(
            "PHASE 1 not approved yet. CODEX_APPROVAL=%s, CLAUDE_APPROVAL=%s, OPEN_FINDINGS=%s.",
            codex_approval,
            claude_approval,
            format_findings_list(parsed_open_findings),
        )

    phase1["status"] = "failed"
    phase1["error"] = "Phase 1 reached max cycles without dual approval."
    state["updated_at"] = state_now_iso()
    ctx.save_state(state)
    raise RuntimeError(phase1["error"])


def run_phase2(task_text: str, plan_text: str, state: dict, args: argparse.Namespace, ctx: RunContext) -> None:
    phase2 = state["phase2"]
    phase2["status"] = "running"
    state["phase"] = "phase2"
    state["updated_at"] = state_now_iso()
    ctx.save_state(state)

    start_cycle = int(phase2.get("cycle", 0)) + 1
    max_cycles = int(phase2.get("max_cycles", args.phase2_max_cycles))

    for cycle in range(start_cycle, max_cycles + 1):
        ctx.checkpoint_cycle_state("phase2", cycle, state)
        phase2["cycle"] = cycle
        phase2["error"] = None
        state["updated_at"] = state_now_iso()
        ctx.save_state(state)

        logger.info("=== PHASE 2 | cycle %s/%s: Codex implementation ===", cycle, max_cycles)
        shared = truncate_shared(read_file(ctx.phase2_shared_file), args.max_shared_chars)
        previous_open_findings = [str(item).upper() for item in phase2.get("open_findings", [])]
        last_test_exit = phase2.get("last_test_exit")
        last_test_snapshot = str(phase2.get("last_test_snapshot", "") or "")
        test_failure_context = ""
        if isinstance(last_test_exit, int) and last_test_exit != 0 and last_test_snapshot.strip():
            test_failure_context = build_test_failure_block(last_test_snapshot, ctx.test_command)
        impl_report = ctx.run_agent_checked(
            agent_key="codex",
            prompt=build_phase2_codex_implement_prompt(
                task_text,
                plan_text,
                shared,
                cycle,
                format_findings_list(previous_open_findings),
                test_failure_context=test_failure_context,
            ),
            log_prefix=f"phase2-cycle{cycle}-codex-implement",
            max_retries=max(args.max_agent_retries, 0),
            required_flags=["IMPLEMENTATION_READY"],
        )
        append_markdown(ctx.phase2_shared_file, f"Phase 2 / Cycle {cycle} / Codex Implement", impl_report)

        logger.info("=== PHASE 2 | cycle %s/%s: local test snapshot ===", cycle, max_cycles)
        test_exit, test_snapshot = ctx.run_tests_snapshot()
        phase2["last_test_exit"] = test_exit
        phase2["last_test_snapshot"] = test_snapshot
        append_markdown(
            ctx.phase2_shared_file,
            f"Phase 2 / Cycle {cycle} / Orchestrator Tests",
            test_snapshot,
        )

        logger.info("=== PHASE 2 | cycle %s/%s: Claude review ===", cycle, max_cycles)
        shared = truncate_shared(read_file(ctx.phase2_shared_file), args.max_shared_chars)
        changed_files = parse_changed_files_from_impl_report(impl_report)
        if not changed_files:
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    changed_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            except Exception:
                changed_files = []
        file_snapshots = collect_file_snapshots(
            changed_files=changed_files,
            max_lines=args.file_snapshot_max_lines,
            max_files=args.file_snapshot_max_files,
        )
        claude_review = ctx.run_agent_checked(
            agent_key="claude",
            prompt=build_phase2_claude_review_prompt(
                task_text=task_text,
                plan_text=plan_text,
                shared_text=shared,
                file_snapshots=file_snapshots,
                test_snapshot=test_snapshot,
                cycle=cycle,
                previous_open_block=format_findings_list(previous_open_findings),
                snapshot=ctx.repo_snapshot(),
            ),
            log_prefix=f"phase2-cycle{cycle}-claude-review",
            max_retries=max(args.max_agent_retries, 0),
            required_flags=["CLAUDE_APPROVAL"],
            output_validator=functools.partial(
                validate_claude_phase2_contract_error,
                previous_open_findings=list(previous_open_findings),
            ),
        )
        append_markdown(ctx.phase2_shared_file, f"Phase 2 / Cycle {cycle} / Claude Review", claude_review)

        implementation_ready = parse_flag(impl_report, "IMPLEMENTATION_READY") or "NO"
        claude_approval = parse_flag(claude_review, "CLAUDE_APPROVAL") or "NO"
        parsed_open_findings = parse_open_findings(claude_review) or []
        phase2["open_findings"] = parsed_open_findings
        status_map = parse_finding_status_map(claude_review)
        new_finding_map = parse_new_findings(claude_review)
        finding_history = dict(phase2.get("finding_history", {}))
        for finding_id, value in status_map.items():
            finding_history[finding_id] = value
        for finding_id, value in new_finding_map.items():
            finding_history[finding_id] = value
        phase2["finding_history"] = finding_history
        phase2["implementation_ready"] = implementation_ready
        phase2["claude_approval"] = claude_approval
        state["updated_at"] = state_now_iso()
        ctx.save_state(state)

        if implementation_ready == "YES" and test_exit == 0 and claude_approval == "YES":
            phase2["status"] = "completed"
            phase2["completed_at"] = state_now_iso()
            state["phase"] = "done"
            state["updated_at"] = state_now_iso()
            ctx.save_state(state)
            logger.info("[OK] PHASE 2 completed: implementation approved and tests passed.")
            return

        logger.info(
            "PHASE 2 not approved yet. IMPLEMENTATION_READY=%s, test_exit=%s, CLAUDE_APPROVAL=%s, OPEN_FINDINGS=%s.",
            implementation_ready,
            test_exit,
            claude_approval,
            format_findings_list(parsed_open_findings),
        )

    phase2["status"] = "failed"
    phase2["error"] = "Phase 2 reached max cycles without approval/pass condition."
    state["updated_at"] = state_now_iso()
    ctx.save_state(state)
    raise RuntimeError(phase2["error"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestrates a dual-agent workflow with two phases and shared Markdown artifacts."
    )
    parser.add_argument("--task-file", help="Path to task file (default: task.md).")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing .orchestrator/state.json.",
    )
    parser.add_argument(
        "--force-overwrite-state",
        action="store_true",
        help="Overwrite existing orchestrator state without confirmation prompt.",
    )
    parser.add_argument(
        "--from-phase",
        choices=["phase1", "phase2"],
        help="Force starting phase (default: based on state).",
    )
    parser.add_argument(
        "--max-agent-retries",
        type=int,
        default=1,
        help="Retries per agent call after first failure (default: 1).",
    )
    parser.add_argument(
        "--phase1-max-cycles",
        type=int,
        default=4,
        help="Maximum Claude<->Codex planning cycles in phase 1 (default: 4).",
    )
    parser.add_argument(
        "--phase2-max-cycles",
        type=int,
        default=6,
        help="Maximum implementation/review cycles in phase 2 (default: 6).",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Fail preflight if DNS resolution fails for provider hosts.",
    )
    parser.add_argument(
        "--skip-git-check",
        action="store_true",
        help="Skip git cleanliness check in preflight (not recommended).",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        default=True,
        help="Deprecated: phase transition gate is skipped by default.",
    )
    parser.add_argument(
        "--manual-gate",
        action="store_true",
        help="Require manual confirmation before starting phase 2.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate agent responses and tests to validate workflow wiring.",
    )
    parser.add_argument(
        "--test-command",
        default="",
        help="Shell command for tests (e.g. 'npm test', 'pytest'). Empty = skip tests.",
    )
    parser.add_argument(
        "--max-shared-chars",
        type=int,
        default=MAX_SHARED_CHARS,
        help=f"Max chars from shared history included in prompts (default: {MAX_SHARED_CHARS}).",
    )
    parser.add_argument(
        "--file-snapshot-max-lines",
        type=int,
        default=500,
        help="Max lines per changed file snapshot for Claude review (default: 500).",
    )
    parser.add_argument(
        "--file-snapshot-max-files",
        type=int,
        default=10,
        help="Max number of changed files included in snapshot (default: 10).",
    )
    parser.add_argument(
        "--no-recover",
        action="store_true",
        help="Disable automatic rollback to last cycle checkpoint after crashes.",
    )
    parser.add_argument(
        "--agent-output",
        choices=["none", "summary", "full"],
        default="summary",
        help="Show agent replies during execution (default: summary).",
    )
    parser.add_argument(
        "--agent-output-max-chars",
        type=int,
        default=1800,
        help="Max chars shown per agent reply in summary mode (default: 1800).",
    )
    parser.add_argument(
        "--agent-live-stream",
        action="store_true",
        help="Stream agent CLI stdout/stderr live while they are running.",
    )
    parser.add_argument(
        "--agent-live-stream-mode",
        choices=["compact", "full"],
        default="compact",
        help="Verbosity for live stream (default: compact).",
    )
    parser.add_argument(
        "--agent-live-stream-channels",
        choices=["both", "stdout", "stderr"],
        default="both",
        help="Which channels to print in live stream (default: both).",
    )
    parser.add_argument(
        "--allow-fallback-to-gemini",
        action="store_true",
        help="If Claude hits quota/rate limits, retry that step with Gemini.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch inbox directory for .md tasks and process continuously.",
    )
    parser.add_argument(
        "--inbox-dir",
        default="inbox",
        help="Directory to watch for task .md files in watch mode (default: inbox).",
    )
    parser.add_argument(
        "--outbox-dir",
        default="outbox",
        help="Directory where processed task files are moved in watch mode (default: outbox).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds for watch mode (default: 5.0).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retries per inbox task in watch mode before poison-pill move (default: 3).",
    )
    level_group = parser.add_mutually_exclusive_group()
    level_group.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    level_group.add_argument(
        "--quiet",
        action="store_true",
        help="Show warnings and errors only.",
    )
    return parser.parse_args()


def run_pipeline(task_file: Path, args: argparse.Namespace, force_new: bool = False) -> int:
    config = OrchestratorConfig(
        dry_run=bool(args.dry_run),
        agent_output_mode=args.agent_output,
        agent_output_max_chars=max(MIN_AGENT_OUTPUT_MAX_CHARS, int(args.agent_output_max_chars)),
        agent_live_stream=bool(args.agent_live_stream),
        agent_live_stream_mode=args.agent_live_stream_mode,
        agent_live_stream_channels=args.agent_live_stream_channels,
        allow_fallback_to_gemini=bool(args.allow_fallback_to_gemini),
    )
    ctx = RunContext(config=config, test_command=str(args.test_command or ""))

    ctx.init_dirs()

    if args.resume and ctx.state_file.exists() and not force_new:
        state = ctx.ensure_state_shape(ctx.load_state(), task_file, args)
        if not args.no_recover:
            state = ctx.recover_state_from_checkpoint(state)
            ctx.save_state(state)
        logger.info("Loaded state from %s", ctx.state_file)
        ctx.configure_artifacts(state["artifacts"])
        write_file(ctx.latest_run_file, str(state["artifacts"]["run_dir"]))
    else:
        if (
            ctx.state_file.exists()
            and not args.resume
            and not args.force_overwrite_state
            and not force_new
        ):
            logger.warning("Existing state at %s will be overwritten.", ctx.state_file)
            try:
                confirm = input("Continue and overwrite? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                logger.info("Aborted.")
                return 1
            if confirm != "y":
                logger.info("Use --resume to continue the existing run.")
                return 0
        artifacts = ctx.build_artifact_paths(ctx.new_run_id())
        ctx.configure_artifacts(artifacts)
        state = ctx.init_state(task_file, args.phase1_max_cycles, args.phase2_max_cycles, artifacts)
        ctx.save_state(state)
        logger.info("Initialized new state at %s", ctx.state_file)
        write_file(ctx.latest_run_file, str(artifacts["run_dir"]))

    ctx.run_artifact_dir.mkdir(parents=True, exist_ok=True)

    task_text = read_file(task_file)
    write_file(ctx.task_snapshot_file, task_text)

    if args.from_phase:
        state["phase"] = args.from_phase
        state["updated_at"] = state_now_iso()
        ctx.save_state(state)

    required_agents = ["claude", "codex"]
    if ctx.config.allow_fallback_to_gemini:
        required_agents.append("gemini")
    if not ctx.preflight(
        required_agents,
        strict=args.strict_preflight,
        skip_git_check=bool(args.skip_git_check),
    ):
        return 1

    current_phase = state.get("phase", "phase1")

    if current_phase in ("phase1", "phase2") and state["phase1"].get("status") != "completed":
        try:
            run_phase1(task_text, state, args, ctx)
        except QuotaReachedError as exc:
            freeze_current_phase(state, exc, ctx)
            return 2

    if state["phase1"].get("status") != "completed":
        logger.error("Phase 1 is not completed. Stopping before implementation.")
        return 1

    if args.manual_gate and state["phase2"].get("status") != "completed":
        if not approval_gate("PHASE 1 completed. Start PHASE 2 (implementation)?"):
            logger.info("Pipeline aborted at phase transition gate.")
            return 1

    if state["phase2"].get("status") != "completed":
        plan_text = read_file(ctx.phase1_shared_file)
        try:
            run_phase2(task_text, plan_text, state, args, ctx)
        except QuotaReachedError as exc:
            freeze_current_phase(state, exc, ctx)
            return 2

    if state["phase2"].get("status") != "completed":
        logger.error("Phase 2 did not complete successfully.")
        return 1

    logger.info("Pipeline completed successfully.")
    logger.info("Artifacts:")
    logger.info("  - %s", ctx.task_snapshot_file)
    logger.info("  - %s", ctx.phase1_shared_file)
    logger.info("  - %s", ctx.phase2_shared_file)
    logger.info("  - latest pointer: %s", ctx.latest_run_file)
    logger.info("State: %s", ctx.state_file)
    print_summary_report(state)
    return 0


def main() -> int:
    args = parse_args()
    log_level = logging.INFO
    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet:
        log_level = logging.WARNING
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    if args.watch:
        if args.task_file:
            logger.warning("--task-file is ignored in --watch mode.")
        return watch_inbox(
            inbox_dir=Path(args.inbox_dir),
            outbox_dir=Path(args.outbox_dir),
            poll_interval=max(0.1, float(args.poll_interval)),
            max_retries=max(0, int(args.max_retries)),
            args=args,
            process_task=run_pipeline,
        )

    task_file = find_task_file(args.task_file)
    return run_pipeline(task_file, args)


if __name__ == "__main__":
    raise SystemExit(main())
