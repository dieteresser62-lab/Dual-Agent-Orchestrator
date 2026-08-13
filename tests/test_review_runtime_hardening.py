from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_adapters import AntigravityAdapter
from agent_config import AgentSettings
from agent_runtime import create_read_only_reviewer_workspace
from audit_trail import MANAGED_SECTION_KEYS, prepare_managed_work_plan_document
from contracts import (
    AgentRole,
    ApprovalMarker,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    StepContract,
)
from orchestrator import _bound_task_control_paths, _plan_only_step_boundary
from repo_changes import collect_repository_changes
from workflow import WorkflowExecutionError, normalize_review_contract_output
from workflow_state import WorkUnitKind


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True
    )


def test_reviewer_snapshot_copies_only_git_visible_files(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    (repository / ".gitignore").write_text("node_modules/\ndist/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    (repository / "node_modules").mkdir()
    (repository / "node_modules" / "huge.js").write_text("ignored\n", encoding="utf-8")
    (repository / "dist").mkdir()
    (repository / "dist" / "bundle.js").write_text("ignored\n", encoding="utf-8")
    _git(repository, "add", "-f", ".gitignore", "tracked.txt", "node_modules/huge.js")

    workspace = create_read_only_reviewer_workspace(repository)
    try:
        assert (workspace.root / "tracked.txt").is_file()
        assert (workspace.root / "new.txt").is_file()
        assert not (workspace.root / ".git").exists()
        assert not (workspace.root / "node_modules").exists()
        assert not (workspace.root / "dist").exists()
    finally:
        workspace.cleanup()


def test_antigravity_known_contract_repair_wrapper_is_unwrapped() -> None:
    adapter = AntigravityAdapter(
        AgentSettings(
            name="antigravity",
            binary="agy",
            model="gemini-3.1-pro-high",
            timeout_seconds=30,
            effort="high",
        )
    )
    wrapped = (
        "Here is the corrected output complying with the STATE-V3 CONTRACT:\n\n"
        "```text\nREVIEWER: antigravity\nTEST_FILES_TOUCHED: NONE\n"
        "REVIEW_EVIDENCE: scope | risk | break\nPRE_MORTEM: drift\n"
        "PLAN_APPROVAL: YES\nSTATUS: DONE\n```\n"
    )

    output = adapter.extract_output(
        json.dumps({"status": "SUCCESS", "response": wrapped}), "", {}
    )

    assert output.startswith("REVIEWER: antigravity")
    assert output.endswith("STATUS: DONE")
    assert "```" not in output


def test_local_review_normalization_adds_bound_test_marker_and_drops_foreign_status() -> None:
    contract = StepContract(
        name="plan-review",
        reviewer=AgentRole.ANTIGRAVITY,
        approval_marker=ApprovalMarker.PLAN,
        slice_id="01",
        round_number=1,
    )
    claude_finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="mode",
        acceptance_test="inspect mode",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    output = "\n".join(
        (
            "REVIEWER: antigravity",
            "FINDING_STATUS: C-01 | OPEN | still applies",
            "NEW_FINDING: A-01 | OBSERVATION | browser gate late | inspect gate",
            "PRE_MORTEM: malformed HTML",
            "PLAN_APPROVAL: YES",
            "STATUS: DONE",
        )
    )

    normalized = normalize_review_contract_output(output, contract, (claude_finding,))

    assert normalized.splitlines()[1] == "TEST_FILES_TOUCHED: NONE"
    assert "FINDING_STATUS: C-01" not in normalized
    assert "NEW_FINDING: A-01" in normalized


def test_generic_work_plan_audit_is_prepared_and_fingerprint_neutral(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = repository / "docs" / "internal" / "handbuch.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Arbeitsplan\n\n## Slice 1\n\nText\n", encoding="utf-8")

    baseline = collect_repository_changes(
        repository,
        base,
        semantic_markdown_paths=("docs/internal/handbuch.md",),
    )

    prepare_managed_work_plan_document(
        repository_root=repository,
        work_plan_path="docs/internal/handbuch.md",
    )
    first = collect_repository_changes(
        repository,
        base,
        semantic_markdown_paths=("docs/internal/handbuch.md",),
    )
    assert first.fingerprint == baseline.fingerprint
    content = plan.read_text(encoding="utf-8")
    for key in MANAGED_SECTION_KEYS:
        assert content.count(f"<!-- audit:{key}:begin -->") == 1
        assert content.count(f"<!-- audit:{key}:end -->") == 1
    plan.write_text(
        content.replace(
            "<!-- audit:claude-review:begin -->",
            "<!-- audit:claude-review:begin -->\nReviewtext",
        ),
        encoding="utf-8",
    )
    second = collect_repository_changes(
        repository,
        base,
        semantic_markdown_paths=("docs/internal/handbuch.md",),
    )

    assert second.fingerprint == first.fingerprint


def test_untracked_markdown_mode_is_canonical_non_executable(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    markdown = repository / "plan.md"
    markdown.write_text("# Plan\n", encoding="utf-8")
    markdown.chmod(0o755)

    changes = collect_repository_changes(repository, base)

    entry = next(item for item in changes.fingerprint_entries if item.path == "plan.md")
    assert entry.payload_mode == 0o100644
    assert "new file mode 100644" in changes.diff_text


def test_bound_task_control_file_can_be_excluded_from_product_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    (repository / "Task.md").write_text("control\n", encoding="utf-8")
    (repository / "product.txt").write_text("product\n", encoding="utf-8")

    changes = collect_repository_changes(
        repository, base, excluded_paths=("Task.md",)
    )

    assert changes.paths == ("product.txt",)
    assert "Task.md" not in changes.diff_text


def test_bound_task_digest_uses_same_newline_normalization_as_task_contract(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    task = repository / "Inbox" / "task.md"
    task.parent.mkdir(parents=True)
    task.write_bytes(b"# Task\r\n\r\nTARGET_BRANCH: feature/test\r\n")
    normalized_text = task.read_text(encoding="utf-8")
    state = SimpleNamespace(
        task_file=str(task),
        task_digest=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
    )

    assert _bound_task_control_paths(repository, state) == ("Inbox/task.md",)

    task.write_text(normalized_text + "changed\n", encoding="utf-8")
    with pytest.raises(WorkflowExecutionError, match="bound task file changed"):
        _bound_task_control_paths(repository, state)


def test_plan_only_boundary_requires_slice_plan_only_during_planning() -> None:
    plan_state = SimpleNamespace(
        execution_mode="PLAN_ONLY",
        work_plan_path="docs/internal/plan.md",
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.PLAN),
    )
    implementation_state = SimpleNamespace(
        execution_mode="PLAN_ONLY",
        work_plan_path="docs/internal/plan.md",
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.SLICE),
        current_slice=SimpleNamespace(scope_paths=("docs/internal/plan.md",)),
    )

    planning = _plan_only_step_boundary(plan_state)
    implementation = _plan_only_step_boundary(implementation_state)

    assert "emit exactly one executable SLICE_PLAN" in planning
    assert "do not emit a SLICE_PLAN record" in implementation
    assert "persisted artifact scope: docs/internal/plan.md" in implementation
    assert "emit exactly one executable SLICE_PLAN" not in implementation
