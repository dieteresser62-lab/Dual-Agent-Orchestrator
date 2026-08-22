from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from audit_trail import (
    REQUIRED_SLICE_HEADINGS,
    AuditTrailError,
    AuditProjection,
    OverallAuditEntry,
    ReviewAuditEvent,
    REQUIRED_WORK_PLAN_HEADINGS,
    AuthorizedTestChanges,
    ValidationAuditEvent,
    project_slice_audit,
    project_overall_audit,
    project_structured_slice_audit,
    project_work_plan_audit,
    merge_structured_record_sections,
    prepare_managed_overall_document,
    semantic_audit_fingerprint,
    strip_managed_audit_sections,
    validate_slice_document,
    validate_work_plan_document,
)
from artifact_bridge import ArtifactBridge
from artifact_models import (
    AgentResultPayload,
    FingerprintKind,
    Role,
    TaskPayload,
    WorkUnitPayload,
)
from artifact_replay import replay_artifacts
from artifact_store import ArtifactStore
from contracts import (
    AgentRole,
    ContractResult,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponse,
    FindingResponseDecision,
    FindingStatus,
    ReviewEvidence,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)


SLICE_ID = 8
RELATIVE_PATH = "docs/internal/slice-orchestrator-modernization-08-audit-trail.md"


def _slice_markdown() -> str:
    managed_by_heading = {
        "Review-Feedback von Claude": "claude-review",
        "Review-Feedback von Antigravity": "antigravity-review",
        "Review-Antworten von Codex": "codex-responses",
        "Validierungsattestierung": "validation-attestation",
        "Testfreigabe und Pre-Mortem": "test-approval-premortem",  # allowlist:german
        "Findings-Lebenszyklus": "findings",  # allowlist:german
        "Entscheidungstabelle": "decision-table",  # allowlist:german
        "Freigabestatus": "approval-status",  # allowlist:german
    }
    lines = [
        "# Slice 08: Audit",
        "",
        "**Feature-Branch:** `feature/test`",
        "**GitHub-Status:** nur lokal",
        "",
    ]
    for heading in REQUIRED_SLICE_HEADINGS:
        lines.extend((f"## {heading}", ""))
        key = managed_by_heading.get(heading)
        if key is None:
            lines.extend((f"Unverwalteter Inhalt für {heading}.", ""))
        else:
            lines.extend(
                (
                    f"<!-- audit:{key}:begin -->",
                    f"alter Inhalt {key}",
                    f"<!-- audit:{key}:end -->",
                    "",
                )
            )
    return "\n".join(lines)


def _write_repository(tmp_path: Path, *, markdown: str | None = None) -> tuple[Path, Path]:
    docs = tmp_path / "docs" / "internal"
    docs.mkdir(parents=True)
    target = tmp_path / RELATIVE_PATH
    target.write_text(markdown or _slice_markdown(), encoding="utf-8")
    plan = docs / "orchestrator-modernization-work-plan.md"
    plan.write_text(
        "\n".join(
            (
                "# Work plan",
                "",
                "### 5.1 Geplante Slice-MD-Dateien",
                "",
                "| Slice | Geplanter Zielpfad |",
                "|---:|---|",
                f"| 8 | [`{RELATIVE_PATH}`](slice-orchestrator-modernization-08-audit-trail.md) |",
                "",
                "### [Slice 8 — Audit](slice-orchestrator-modernization-08-audit-trail.md)",
                "",
                "Text.",
                "",
                "### Slice 9 — Next",
                "",
            )
        ),
        encoding="utf-8",
    )
    return plan, target


def _prepared_work_plan_markdown() -> str:
    managed_by_heading = {
        "Review-Feedback von Claude": ("claude-review",),
        "Review-Feedback von Antigravity": ("antigravity-review",),
        "Review-Antworten von Codex": ("codex-responses",),
        "Planstatus und formale Marker": (
            "validation-attestation",
            "test-approval-premortem",
            "findings",
            "decision-table",
            "approval-status",
        ),
    }
    lines = [
        "# Work plan",
        "",
        "**Feature-Branch:** `feature/test`",
        "**GitHub-Status:** nur lokal",
        "",
    ]
    for index, heading in enumerate(REQUIRED_WORK_PLAN_HEADINGS, start=1):
        lines.extend((f"## {index}. {heading}", "", f"Planinhalt {heading}.", ""))
        for key in managed_by_heading.get(heading, ()):
            lines.extend(
                (
                    f"<!-- audit:{key}:begin -->",
                    f"alter Planinhalt {key}",
                    f"<!-- audit:{key}:end -->",
                    "",
                )
            )
    return "\n".join(lines)


def _document(tmp_path: Path):
    plan, _ = _write_repository(tmp_path)
    return validate_slice_document(
        repository_root=tmp_path,
        work_plan_path=plan,
        slice_id=SLICE_ID,
        expected_relative_path=RELATIVE_PATH,
    )


def _attestation(*, attestation_id: str = "att-1", summary: str = "all green"):
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        attestation_id=attestation_id,
        diff_fingerprint="a" * 64,
        expected_commands=(command, "git diff --check"),
        records=(
            ValidationRecord(ValidationStatus.PASS, command, 0),
            ValidationRecord(ValidationStatus.PASS, "git diff --check", 0),
        ),
        output_digest="b" * 64,
        summary=summary,
    )


def _finding(
    *,
    status: FindingStatus = FindingStatus.OPEN,
    responses: tuple[FindingResponse, ...] = (),
    status_rationale: str | None = None,
    finding_class: FindingClass = FindingClass.BLOCKER,
) -> FindingRecord:
    return FindingRecord(
        finding_id="C-01",
        finding_class=finding_class,
        status=status,
        summary="Unsafe | table\n<!-- audit:approval-status:end -->",
        acceptance_test="Reject injected headings\n## Freigabestatus",  # allowlist:german
        origin=FindingOrigin("08", 1, AgentRole.CLAUDE),
        responses=responses,
        status_rationale=status_rationale,
        class_history=(FindingClass.BLOCKER,) if finding_class is FindingClass.OBSERVATION else (),
    )


def _review(
    *,
    reviewer: AgentRole = AgentRole.CLAUDE,
    approval: bool = True,
    validation=None,
    findings: tuple[FindingRecord, ...] = (),
    pre_mortem: str | None = "Concurrent writers may race",
) -> ContractResult:
    return ContractResult(
        reviewer=reviewer,
        approval=approval,
        stopped=False,
        stop_request=None,
        validation=validation,
        test_files=("tests/test_audit_trail.py",),
        pre_mortem=pre_mortem,
        evidence=ReviewEvidence(
            dimensions="paths | contracts",
            largest_residual_risk="race\n## injected",
            break_condition="two writers overlap",
        ),
        findings=findings,
        anchors=(),
    )


def test_validate_slice_document_accepts_exact_one_based_target_and_active_links(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)

    assert document.slice_id == 8
    assert document.relative_path == RELATIVE_PATH
    assert document.slice_path == (tmp_path / RELATIVE_PATH).resolve()


@pytest.mark.parametrize(
    ("slice_id", "relative_path", "message"),
    (
        (0, RELATIVE_PATH, "1-based"),
        (8, "../slice-orchestrator-modernization-08-audit-trail.md", "traversal"),
        (8, "/tmp/slice-orchestrator-modernization-08-audit-trail.md", "relative"),
        (8, "docs\\internal\\slice-orchestrator-modernization-08-audit-trail.md", "POSIX"),
        (7, RELATIVE_PATH, "filename id"),
    ),
)
def test_validate_slice_document_rejects_invalid_target_identity(
    tmp_path: Path,
    slice_id: int,
    relative_path: str,
    message: str,
) -> None:
    plan, _ = _write_repository(tmp_path)

    with pytest.raises(AuditTrailError, match=message):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=slice_id,
            expected_relative_path=relative_path,
        )


def test_validate_slice_document_rejects_code_target_instead_of_active_link(
    tmp_path: Path,
) -> None:
    plan, _ = _write_repository(tmp_path)
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            f"[`{RELATIVE_PATH}`](slice-orchestrator-modernization-08-audit-trail.md)",
            f"`{RELATIVE_PATH}`",
        ),
        encoding="utf-8",
    )

    with pytest.raises(AuditTrailError, match="active Markdown link"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


def test_validate_slice_document_rejects_wrong_heading_link(tmp_path: Path) -> None:
    plan, _ = _write_repository(tmp_path)
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "### [Slice 8 — Audit](slice-orchestrator-modernization-08-audit-trail.md)",
            "### Slice 8 — Audit",
        ),
        encoding="utf-8",
    )

    with pytest.raises(AuditTrailError, match="slice heading"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


def test_validate_slice_document_rejects_missing_or_reordered_required_heading(
    tmp_path: Path,
) -> None:
    markdown = _slice_markdown().replace("## Geplante Tests", "## Testplanung")
    plan, _ = _write_repository(tmp_path, markdown=markdown)

    with pytest.raises(AuditTrailError, match="Geplante Tests"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


from conftest import can_symlink


@pytest.mark.skipif(not can_symlink(), reason="symlinks are unavailable")
def test_validate_slice_document_rejects_symlink_target(tmp_path: Path) -> None:
    plan, target = _write_repository(tmp_path)
    real = target.with_name("slice-orchestrator-modernization-08-real.md")
    target.replace(real)
    target.symlink_to(real.name)

    with pytest.raises(AuditTrailError, match="symbolic link"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


def test_projection_renders_bound_attestation_and_reviews_atomically_and_idempotently(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    attestation = _attestation()
    projection = AuditProjection(
        slice_id=8,
        events=(
            ValidationAuditEvent(1, 8, attestation),
            ReviewAuditEvent(2, 8, 1, _review(validation=attestation)),
            ReviewAuditEvent(
                3,
                8,
                1,
                _review(reviewer=AgentRole.ANTIGRAVITY, validation=attestation),
            ),
        ),
        test_approval=AuthorizedTestChanges(
            approved=True,
            paths=("tests/test_audit_trail.py",),
            approved_by="user",
            rationale="modernization-plan exception",
        ),
        implementation_ready=True,
        commit_authorized=True,
    )

    rendered = project_slice_audit(document, projection)
    stat_after_first = document.slice_path.stat().st_mtime_ns
    repeated = project_slice_audit(document, projection)

    assert repeated == rendered
    assert document.slice_path.stat().st_mtime_ns == stat_after_first
    assert "`att-1`" in rendered
    assert f"`{'a' * 64}`" in rendered
    assert f"`{'b' * 64}`" in rendered
    assert "| python3 -m pytest tests/ -v | PASS | 0 |" in rendered
    assert "- Claude-Freigabe: `YES`" in rendered  # allowlist:german
    assert "- Antigravity-Freigabe: `YES`" in rendered  # allowlist:german
    assert "- Commit autorisiert: `YES`" in rendered
    assert "Unverwalteter Inhalt für Ziel des Slice." in rendered
    assert "alter Inhalt" not in rendered


def test_structured_projection_uses_accepted_replay_and_is_a_byte_equal_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document(tmp_path)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "audit-replay"))
    bridge.append(
        TaskPayload("feature/test", (RELATIVE_PATH,), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkUnitPayload("8", 1, (RELATIVE_PATH,)),
        logical_id="work-unit-2",
        idempotency_key="work-unit-2",
        fingerprint_sha256="b" * 64,
    )
    bridge.append(
        AgentResultPayload(
            Role.CODEX,
            "2",
            "ready",
            (),
            transport_schema="native-codex-v1",
            request_id="native-codex-request-" + "c" * 64,
            response_sha256="d" * 64,
        ),
        logical_id="agent-2-codex_implementation-1",
        idempotency_key="native-agent-result",
        fingerprint_sha256="b" * 64,
    )
    replay = replay_artifacts(bridge.store.load_chain(), "audit-replay")

    monkeypatch.setattr(
        "artifact_projection.replay_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("accepted replay must not be reduced again")
        ),
    )
    rendered = project_structured_slice_audit(document, replay)
    stat_after_first = document.slice_path.stat().st_mtime_ns

    repeated = project_structured_slice_audit(document, replay)

    assert repeated == rendered
    assert document.slice_path.stat().st_mtime_ns == stat_after_first
    assert replay.semantic_digest in rendered
    assert "`native-codex-v1`" in rendered
    assert "`native-codex-request-" in rendered


def test_work_plan_uses_same_safe_projection_without_parallel_raw_log(tmp_path: Path) -> None:
    docs = tmp_path / "docs" / "internal"
    docs.mkdir(parents=True)
    path = docs / "orchestrator-modernization-work-plan.md"
    path.write_text(_prepared_work_plan_markdown(), encoding="utf-8")
    document = validate_work_plan_document(
        repository_root=tmp_path,
        work_plan_path="docs/internal/orchestrator-modernization-work-plan.md",
    )
    projection = AuditProjection(
        slice_id=1,
        events=(ReviewAuditEvent(1, 1, 1, _review()),),
        implementation_ready=True,
    )

    rendered = project_work_plan_audit(document, projection)

    assert "Planinhalt Zielbild in eigenen Worten." in rendered
    assert "### Ereignis 1: Runde 1" in rendered
    assert "alter Planinhalt" not in rendered
    assert project_work_plan_audit(document, projection) == rendered


def test_overall_audit_aggregates_plan_and_slice_reviews(tmp_path: Path) -> None:
    document = prepare_managed_overall_document(
        repository_root=tmp_path,
        audit_path="docs/internal/bug-review-12345678.md",
        task_name="Bug",
        task_file="inbox/Bug.md",
        run_id="watch-1",
        branch="codex/bug",
        task_scope=("src/bug.py",),
    )
    entries = (
        OverallAuditEntry(
            label="Work Unit 01 – Planung",
            summary="Plan erstellen",
            scope_paths=("src/bug.py",),
            projection=AuditProjection(
                slice_id=1,
                events=(ReviewAuditEvent(1, 1, 1, _review()),),
            ),
        ),
        OverallAuditEntry(
            label="Work Unit 02 – Slice 01",
            summary="Bug beheben",
            scope_paths=("src/bug.py",),
            projection=AuditProjection(slice_id=1),
        ),
    )

    rendered = project_overall_audit(document, entries)

    assert "Work Unit 01 – Planung" in rendered
    assert "Work Unit 02 – Slice 01" in rendered
    assert "### Ereignis 1: Runde 1" in rendered
    assert project_overall_audit(document, entries) == rendered


def test_projection_escapes_markdown_table_and_section_injection(tmp_path: Path) -> None:
    document = _document(tmp_path)
    finding = _finding()
    projection = AuditProjection(
        slice_id=8,
        events=(
            ReviewAuditEvent(
                1,
                8,
                1,
                _review(approval=False, findings=(finding,), validation=None),
            ),
        ),
    )

    rendered = project_slice_audit(document, projection)

    assert "Unsafe &#124; table<br>&lt;!-- audit:approval-status:end --&gt;" in rendered
    assert "Reject injected headings<br>## Freigabestatus" in rendered  # allowlist:german
    assert rendered.count("<!-- audit:approval-status:end -->") == 1
    assert rendered.count("## Freigabestatus") == 2  # allowlist:german - heading plus escaped data
    validate_slice_document(
        repository_root=tmp_path,
        work_plan_path=document.work_plan_path,
        slice_id=8,
        expected_relative_path=RELATIVE_PATH,
    )


def test_projection_renders_complete_finding_response_and_closure_lifecycle(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    attestation = _attestation()
    opened = _finding()
    response = FindingResponse(
        FindingResponseDecision.ACCEPTED,
        "Fix and add a regression test",
    )
    closed = _finding(
        status=FindingStatus.CLOSED,
        responses=(response,),
        status_rationale="Regression test is green",
        finding_class=FindingClass.OBSERVATION,
    )
    projection = AuditProjection(
        slice_id=8,
        events=(
            ValidationAuditEvent(1, 8, attestation),
            ReviewAuditEvent(
                2,
                8,
                1,
                _review(approval=False, findings=(opened,), validation=attestation),
            ),
            ReviewAuditEvent(
                3,
                8,
                2,
                _review(approval=True, findings=(closed,), validation=attestation),
            ),
        ),
    )

    rendered = project_slice_audit(document, projection)

    assert "`C-01` — `CLOSED`" in rendered
    assert "Antwort 1: **angenommen**" in rendered
    assert "| C-01 | claude |" in rendered
    assert "| OBSERVATION | angenommen | erledigt: Regression test is green |" in rendered


def test_semantic_fingerprint_handles_crlf_managed_sections() -> None:
    first = (
        "# Audit\r\n"
        "<!-- audit:findings:begin -->\r\n"
        "first projected body\r\n"
        "<!-- audit:findings:end -->\r\n"
        "semantic body\r\n"
    )
    second = first.replace("first projected body", "different projected body")

    assert strip_managed_audit_sections(first).endswith("semantic body\r\n")
    assert semantic_audit_fingerprint(first) == semantic_audit_fingerprint(second)


def test_semantic_fingerprint_ignores_only_managed_projection_bodies(tmp_path: Path) -> None:
    document = _document(tmp_path)
    before = semantic_audit_fingerprint(document.markdown)
    attestation = _attestation()
    projection = AuditProjection(
        slice_id=8,
        events=(
            ValidationAuditEvent(1, 8, attestation),
            ReviewAuditEvent(2, 8, 1, _review(validation=attestation)),
        ),
    )

    rendered = project_slice_audit(document, projection)

    assert semantic_audit_fingerprint(rendered) == before
    assert strip_managed_audit_sections(rendered).count("alter Inhalt") == 0
    changed = rendered.replace(
        "Unverwalteter Inhalt für Ziel des Slice.",
        "Fachlich geänderter Zielinhalt.",
    )
    assert semantic_audit_fingerprint(changed) != before


def test_structured_record_blocks_are_idempotent_and_cosmetic_for_fingerprint() -> None:
    markdown = _slice_markdown()
    sections = {
        key: f"Record view for {key}"
        for key in (
            "claude-review", "antigravity-review", "codex-responses",
            "validation-attestation", "test-approval-premortem", "findings",
            "decision-table", "approval-status",
        )
    }
    before = semantic_audit_fingerprint(markdown)

    rendered = merge_structured_record_sections(markdown, sections)
    repeated = merge_structured_record_sections(rendered, sections)

    assert repeated == rendered
    assert semantic_audit_fingerprint(rendered) == before
    assert rendered.count("<!-- artifact-records:findings:begin -->") == 1


def test_structured_record_blocks_diagnose_partial_manual_marker_edit() -> None:
    markdown = _slice_markdown().replace(
        "alter Inhalt findings",
        "alter Inhalt findings\n<!-- artifact-records:findings:begin -->",
    )
    sections = {
        key: "record view"
        for key in (
            "claude-review", "antigravity-review", "codex-responses",
            "validation-attestation", "test-approval-premortem", "findings",
            "decision-table", "approval-status",
        )
    }

    with pytest.raises(AuditTrailError, match="incomplete for findings"):
        merge_structured_record_sections(markdown, sections)


def test_longer_fence_is_not_closed_by_shorter_backtick_run() -> None:
    markdown = "\n".join(
        (
            "````markdown",
            "```",
            "<!-- audit:findings:begin -->",
            "semantic example body",
            "<!-- audit:findings:end -->",
            "````",
        )
    )

    stripped = strip_managed_audit_sections(markdown)

    assert stripped == markdown
    assert semantic_audit_fingerprint(
        markdown.replace("semantic example body", "changed semantic example body")
    ) != semantic_audit_fingerprint(markdown)


def test_only_standalone_top_level_lines_are_managed_markers() -> None:
    markdown = "\n".join(
        (
            "> ```markdown",
            "> <!-- audit:findings:begin -->",
            "> semantic blockquote example",
            "> <!-- audit:findings:end -->",
            "> ```",
            "text <!-- audit:claude-review:begin -->",
        )
    )

    assert strip_managed_audit_sections(markdown) == markdown
    assert semantic_audit_fingerprint(
        markdown.replace("semantic blockquote example", "changed blockquote example")
    ) != semantic_audit_fingerprint(markdown)


def test_indented_marker_examples_are_semantic_content() -> None:
    markdown = "\n".join(
        (
            "    <!-- audit:findings:begin -->",
            "    semantic indented example",
            "    <!-- audit:findings:end -->",
        )
    )

    assert strip_managed_audit_sections(markdown) == markdown
    assert semantic_audit_fingerprint(
        markdown.replace("semantic indented example", "changed indented example")
    ) != semantic_audit_fingerprint(markdown)


def test_projection_rejects_review_with_missing_or_changed_attestation_binding(
    tmp_path: Path,
) -> None:
    _document(tmp_path)
    attestation = _attestation()
    changed = _attestation(summary="different structured content")

    with pytest.raises(AuditTrailError, match="not projected first"):
        AuditProjection(
            slice_id=8,
            events=(ReviewAuditEvent(1, 8, 1, _review(validation=attestation)),),
        )

    with pytest.raises(AuditTrailError, match="differs"):
        AuditProjection(
            slice_id=8,
            events=(
                ValidationAuditEvent(1, 8, attestation),
                ReviewAuditEvent(2, 8, 1, _review(validation=changed)),
            ),
        )


def test_projection_rejects_non_contiguous_events_and_cross_slice_findings() -> None:
    with pytest.raises(AuditTrailError, match="contiguous"):
        AuditProjection(
            slice_id=8,
            events=(ReviewAuditEvent(2, 8, 1, _review()),),
        )

    foreign = replace(_finding(), origin=FindingOrigin("07", 1, AgentRole.CLAUDE))
    with pytest.raises(AuditTrailError, match="belongs to slice"):
        ReviewAuditEvent(1, 8, 1, _review(approval=False, findings=(foreign,)))


def test_review_event_accepts_explicit_final_finding_origin_for_correction() -> None:
    final_finding = replace(
        _finding(), origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE)
    )

    event = ReviewAuditEvent(
        1,
        8,
        1,
        _review(approval=False, findings=(final_finding,)),
        allowed_finding_origins=("FINAL",),
    )

    assert event.result.findings == (final_finding,)

    prior_finding = replace(
        _finding(), origin=FindingOrigin("07", 1, AgentRole.CLAUDE)
    )
    prior_event = ReviewAuditEvent(
        1,
        8,
        1,
        _review(approval=False, findings=(prior_finding,)),
        allowed_finding_origins=("07",),
    )
    assert prior_event.result.findings == (prior_finding,)

    with pytest.raises(AuditTrailError, match="1-based Slice ids"):
        ReviewAuditEvent(
            1,
            8,
            1,
            _review(approval=False, findings=(final_finding,)),
            allowed_finding_origins=("slice-07",),
        )


def test_projection_rejects_commit_authorization_without_antigravity_approval() -> None:
    with pytest.raises(AuditTrailError, match="Antigravity"):
        AuditProjection(
            slice_id=8,
            events=(ReviewAuditEvent(1, 8, 1, _review()),),
            commit_authorized=True,
        )


def test_slice_projection_rejects_approving_review_without_validation(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    projection = AuditProjection(
        slice_id=8,
        events=(ReviewAuditEvent(1, 8, 1, _review()),),
    )

    with pytest.raises(AuditTrailError, match="approving slice review requires"):
        project_slice_audit(document, projection)


def test_projection_rejects_malformed_or_unknown_managed_markers(tmp_path: Path) -> None:
    markdown = _slice_markdown().replace(
        "<!-- audit:findings:end -->",
        "<!-- audit:unknown:end -->",
    )
    plan, _ = _write_repository(tmp_path, markdown=markdown)

    with pytest.raises(AuditTrailError, match="unknown managed audit section"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


def test_slice_marker_must_stay_in_its_exact_protected_section(tmp_path: Path) -> None:
    markdown = _slice_markdown()
    block = "\n".join(
        (
            "<!-- audit:findings:begin -->",
            "alter Inhalt findings",
            "<!-- audit:findings:end -->",
        )
    )
    markdown = markdown.replace(block, "")
    markdown = markdown.replace(
        "Unverwalteter Inhalt für Offene Risiken.",  # allowlist:german
        "Unverwalteter Inhalt für Offene Risiken.\n\n" + block,  # allowlist:german
    )
    plan, _ = _write_repository(tmp_path, markdown=markdown)

    with pytest.raises(AuditTrailError, match="findings belongs below"):
        validate_slice_document(
            repository_root=tmp_path,
            work_plan_path=plan,
            slice_id=8,
            expected_relative_path=RELATIVE_PATH,
        )


def test_approving_review_rejects_incomplete_attestation() -> None:
    attestation = _attestation()
    incomplete = replace(attestation, records=attestation.records[:1])

    with pytest.raises(AuditTrailError, match="requires its validation attestation to pass"):
        ReviewAuditEvent(1, 8, 1, _review(validation=incomplete))


def test_projection_accepts_only_matching_named_complete_red_state() -> None:
    green = _attestation()
    red = replace(
        green,
        records=tuple(
            replace(record, status=ValidationStatus.FAIL, exit_code=1, output="known red")
            for record in green.records
        ),
        summary="known red pending Slice 09",
    )
    claude = replace(
        _review(validation=red), red_state_followup_slice="Slice 09"
    )
    antigravity = replace(
        _review(reviewer=AgentRole.ANTIGRAVITY, validation=red),
        red_state_followup_slice="Slice 09",
    )
    events = (
        ValidationAuditEvent(1, 8, red),
        ReviewAuditEvent(2, 8, 1, claude),
        ReviewAuditEvent(3, 8, 1, antigravity),
    )

    projection = AuditProjection(
        slice_id=8,
        events=events,
        commit_authorized=True,
        red_state_followup_slice="Slice 09",
    )

    assert projection.commit_authorized is True
    with pytest.raises(AuditTrailError, match="follow-up differs"):
        AuditProjection(
            slice_id=8,
            events=events,
            commit_authorized=True,
            red_state_followup_slice="Slice 10",
        )

    mismatched_claude = replace(claude, red_state_followup_slice="Slice 10")
    with pytest.raises(AuditTrailError, match="differs from Claude"):
        AuditProjection(
            slice_id=8,
            events=(
                events[0],
                ReviewAuditEvent(2, 8, 1, mismatched_claude),
                events[2],
            ),
            commit_authorized=True,
            red_state_followup_slice="Slice 09",
        )


def test_test_approval_paths_are_normalized_and_root_bound() -> None:
    record = AuthorizedTestChanges(
        approved=True,
        paths=("tests/z.py", "tests/a.py"),
        approved_by="user",
        rationale="approved",
    )
    assert record.paths == ("tests/a.py", "tests/z.py")

    with pytest.raises(AuditTrailError, match="repository-relative"):
        AuthorizedTestChanges(
            approved=True,
            paths=("../outside.py",),
            approved_by="user",
            rationale="approved",
        )


def test_test_approval_projection_includes_timestamp_and_bound_fingerprint(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    projection = AuditProjection(
        slice_id=8,
        test_approval=AuthorizedTestChanges(
            approved=True,
            paths=("tests/test_audit_trail.py",),
            approved_by="domain-owner",
            rationale="reviewed exact test delta",
            approved_at="2026-08-12T12:00:00+00:00",
            diff_fingerprint="c" * 64,
        ),
    )

    rendered = project_slice_audit(document, projection)

    assert "2026-08-12T12:00:00+00:00" in rendered
    assert f"`{'c' * 64}`" in rendered
