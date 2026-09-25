from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from artifact_models import (
    ArtifactRecord, BlobReference, CommandSpec, FindingSeverity,
    FinalReviewCompletedPayload, FinalReviewFindingPayload,
    CorrectionWorkUnitPayload, FindingTransitionPayload, Fingerprint, FingerprintKind,
    GateTransitionPayload, PlanPayload, ProviderContentPayload, ScopeExtensionPathPayload,
    ScopeExtensionPayload,
    ReviewEvidencePayload, ReviewPayload, Role, RunIdentityPayload,
    SliceSpec, ValidationAttestationPayload, ValidationContentPayload,
    ValidationOutputContent, ValidationResult, WorkUnitPayload,
    WorkflowEventPayload,
)
from audit_document_contract import PLAN_SECTIONS, SLICE_SECTIONS
from readable_audit import (
    AuditFacts, _prose, authored_slice_sections, render_overall,
    render_plan_appendix, render_slice,
)
from semantic_markdown import canonical_semantic_markdown, parse_semantic_markdown


RUN = "readable-test-run"
PLAN = """# Arbeitsplan

### Slice 1 - Erster Slice

**Ziel**
Zeile eins mit < und &.
Zeile zwei.

**Exakter Änderungspfad**
- `src/one.py`

**Akzeptanzkriterien**
- `python3 -m pytest` prüft `a|b`.
- Eine zweite Bedingung.

### Slice 2 - Zweiter Slice

**Ziel**
Zweites Ziel.

**Exakter Änderungspfad**
- `src/two.py`

**Akzeptanzkriterien**
- Zweite Abnahme.
"""


def _facts(*, slices: int = 2, red: bool = False, hostile: bool = False, plan_only: bool = False, resumed_halt: bool = False) -> AuditFacts:
    records: list[ArtifactRecord] = []
    fingerprint = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)

    def add(payload: object, logical: str) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id=RUN, logical_id=logical, revision=1, fingerprint=fingerprint,
            predecessor_ids=(records[-1].record_id,) if records else (),
            created_at="2026-09-24T12:00:00+00:00", idempotency_key=logical,
            payload=payload,
        )
        records.append(record)
        return record

    identity = RunIdentityPayload("inbox/task.md", "feature/test", "b" * 40, "b" * 40, "PLAN_ONLY" if plan_only else "IMPLEMENT", "docs/internal/overall.md")
    add(identity, "run-identity")
    specs = tuple(SliceSpec(str(index), f"Slice {index}", (f"src/{index}.py", f"docs/internal/slice-test-{index:02d}-test.md")) for index in range(1, slices + 1))
    add(PlanPayload("docs/internal/plan.md", "c" * 40, specs), "plan")
    blob = ("old\n" * 60 + "LAST < & | &&\n").encode()
    ref = BlobReference("d" * 64, len(blob))
    for index in range(1, slices + 1):
        key = str(index)
        add(WorkUnitPayload(key, 1, specs[index - 1].paths), f"work-unit-{key}")
        prose = "Marker:\n<!-- audit:findings:end -->\n## false heading\n```\n| < & `" if hostile and index == 1 else f"Befund nur in Slice {index}."
        add(FindingTransitionPayload(
            f"C-{index:02d}", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.FINDING, "open", prose, key,
            prose, "Prüfe < und & | ` mit &&.", f"{index:02d}", 1,
        ), f"finding-{index}")
        command = CommandSpec("pytest", ("sh", "-c", "echo a && echo b"))
        content = add(ValidationContentPayload(
            f"validation-{key}", "ar1-" + "e" * 64,
            "raw-output-v1", "f" * 64, "summary",
            (ValidationOutputContent(command, "fail" if red else "pass", 1 if red else 0, ref, BlobReference("0" * 64, 0), ref, len(blob)),),
        ), f"content-{key}")
        att = add(ValidationAttestationPayload(
            (ValidationResult(command, "fail" if red else "pass", 1 if red else 0, "f" * 64),),
            Role.ORCHESTRATOR, "f" * 64, content.record_id,
        ), f"validation-{key}")
        add(WorkflowEventPayload("validation", key, key, 1, (att.record_id,)), f"validation-event-{key}")
        review = add(ReviewPayload(
            Role.CLAUDE, key, "denied" if red else "approved", (f"C-{index:02d}",),
            None, "native-claude-review-v2", "native-review-request-" + "1" * 64,
            "2" * 64, ReviewEvidencePayload("Scope < checked.", "Risk & fallback.", "Break | condition."),
            pre_mortem="A later change could break this.",
        ), f"review-{key}")
        add(WorkflowEventPayload("review", key, key, 1, (review.record_id,)), f"review-event-{key}")
        if resumed_halt and index == 1:
            add(GateTransitionPayload(key, "awaiting_user_decision", "stop_request", "RULE | Needed input.", None, (), None, None, ()), "halt")
            add(GateTransitionPayload(key, "clear", "none", None, None, (), None, None, ()), "resume")
    replay = SimpleNamespace(records=tuple(records), run_identity=identity)
    return AuditFacts(replay, read_blob=lambda _ref: blob, read_plan=lambda _commit, _path: PLAN)


def _assert_readable(markdown: str) -> None:
    assert "ar1-" not in markdown
    assert "<br>" not in markdown
    assert "artifact-records" not in markdown
    assert "NOT_RECORDED" not in markdown
    assert not re.search(r"&(?:amp|lt|gt|#\d+);", markdown)
    without_commits = re.sub(r"\b[0-9a-f]{8}\b", "", markdown)
    assert not re.search(r"(?<![A-Za-z0-9])[0-9a-f]{12,}(?![A-Za-z0-9])", without_commits)
    parse_semantic_markdown(markdown, require_managed=True)


def test_readable_documents_preserve_plan_and_slice_ownership() -> None:
    facts = _facts()
    one = render_slice(facts, 1)
    two = render_slice(facts, 2)
    overall = render_overall(facts, task="Task", branch="feature/test")
    appendix = render_plan_appendix(_facts(plan_only=True))
    for document in (one, two, overall, appendix):
        _assert_readable(document)
    assert "Zeile eins mit < und &.\nZeile zwei." in one
    assert "- `python3 -m pytest` prüft `a|b`." in one
    assert one.count("### C-01") == 1
    assert "### C-02" not in one
    assert two.count("### C-02") == 1
    assert "### C-01" not in two
    assert overall.count("Befund nur in Slice 1") == 1
    assert "| C-01 | Slice 1 | Befund | offen |" in overall
    assert "Klasse: Befund · Stand: offen" in one
    assert "Vorab-Risikoanalyse:" in one
    assert "Vorab-Risikoanalyse:" in appendix
    assert len(render_overall(_facts(slices=4), task="Task", branch="feature/test")) <= 2.2 * len(overall)


def test_final_review_uses_german_labels_for_new_findings_and_risk() -> None:
    facts = _facts()
    facts.final_review = FinalReviewCompletedPayload(
        reviewer=Role.CLAUDE,
        work_unit_id="1",
        new_findings=(FinalReviewFindingPayload(
            "C-03", FindingSeverity.FINDING, "Neue Abweichung.", "Prüfe den Pfad."
        ),),
        occurrences=(),
        review_evidence=ReviewEvidencePayload("Geprüft.", "Risiko.", "Bruch."),
        pre_mortem="Ein Fehler kann wiederkehren.",
        validation_attestation_record_id="ar1-" + "e" * 64,
        reviewed_head_commit="a" * 40,
        transport_schema="native-claude-review-v2",
        request_id="native-review-request-" + "1" * 64,
        response_sha256="2" * 64,
        scan_complete=True,
    )
    overall = render_overall(facts, task="Aufgabe", branch="feature/test")
    assert "| C-03 | Abnahme | Befund | offen |" in overall
    assert "Klasse: Befund · Stand: offen" in overall
    assert "Vorab-Risikoanalyse:" in overall


def test_agent_text_cannot_break_markers_or_headings() -> None:
    one = render_slice(_facts(hostile=True), 1)
    assert "> <!-- audit:findings:end -->" in one
    assert "> ## false heading" in one
    assert "> | < & `" in one
    assert canonical_semantic_markdown(one) == canonical_semantic_markdown(render_slice(_facts(), 1))


def test_red_validation_has_only_last_40_lines_and_green_has_no_output() -> None:
    red = render_slice(_facts(red=True), 1)
    green = render_slice(_facts(red=False), 1)
    assert "LAST < & | &&" in red
    assert red.count("old") <= 39
    assert "LAST < & | &&" not in green
    _assert_readable(red)


def test_slice_ownership_excludes_other_slice_findings() -> None:
    assert "### C-02" not in render_slice(_facts(), 1)


def test_prose_removes_long_hashes_and_record_ids() -> None:
    source = "Prüferbeleg: " + "a" * 64 + " und ar1-" + "b" * 64 + "."
    assert _prose(source) == "Prüferbeleg: [Hash ausgelassen] und [Recordbezug ausgelassen]."


def test_authored_implementation_survives_two_projections_with_old_heading() -> None:
    original = 'Erste Zeile mit "Zitat".\n> Zweite Zeile bleibt wörtlich.'
    legacy = "# Slice 1\n\n## Durchgeführte Änderungen\n\n" + original + "\n\n## Abweichungen vom Plan\n\nKeine.\n"
    first = render_slice(_facts(), 1, implementation=authored_slice_sections(legacy)[0])
    implementation, deviations = authored_slice_sections(first)
    second = render_slice(_facts(), 1, implementation=implementation, deviations=deviations)
    assert implementation == original
    assert deviations == "Keine."
    assert second == first


def test_overview_escapes_pipe_in_slice_title() -> None:
    facts = _facts(slices=1)
    facts.plan = replace(facts.plan, slices=(replace(facts.plan.slices[0], summary="Titel | Zusatz"),))
    overall = render_overall(facts, task="Task", branch="feature/test")
    assert "| 1 | Titel \\| Zusatz | freigegeben |" in overall


def test_failed_validation_is_red_in_history_and_detail() -> None:
    rendered = render_slice(_facts(slices=1, red=True), 1)
    assert "Runde 1: Umsetzung · Validierung rot · Prüfurteil abgelehnt" in rendered
    assert "· rot · Exitcode 1." in rendered


def test_long_authored_implementation_does_not_stop_rendering() -> None:
    rendered = render_slice(_facts(slices=1), 1, implementation="W" * 70000)
    assert "W" * 70000 in rendered
    overall = render_overall(_facts(slices=1), task="T" * 140000, branch="feature/test")
    assert "T" * 140000 in overall
    plan_facts = _facts(slices=1, plan_only=True)
    plan_facts.openings["C-01"] = replace(plan_facts.openings["C-01"], rationale="Z" * 70000)
    assert "Z" * 70000 in render_plan_appendix(plan_facts)


def _round_facts(*, correction: bool = False, scope_extension: bool = False) -> AuditFacts:
    base = _facts(slices=1)
    records = list(base.records[:-2] if correction else base.records)
    fingerprint = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)

    def add(payload: object, logical: str) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id=RUN, logical_id=logical, revision=1, fingerprint=fingerprint,
            predecessor_ids=(records[-1].record_id,),
            created_at="2026-09-24T12:00:00+00:00", idempotency_key=logical,
            payload=payload,
        )
        records.append(record)
        return record

    call_1 = ProviderContentPayload(Role.CODEX, "1", 1, "implementation", "request-1", "0" * 64, "agent_result", 0, BlobReference("0" * 64, 0))
    call_2 = replace(call_1, round_number=2, request_id="request-2")
    inserted = [
        ArtifactRecord.create(run_id=RUN, logical_id="content-call-1", revision=1, fingerprint=fingerprint,
                              predecessor_ids=(records[2].record_id,), created_at="2026-09-24T12:00:00+00:00",
                              idempotency_key="content-call-1", payload=call_1),
        ArtifactRecord.create(run_id=RUN, logical_id="halt-1", revision=1, fingerprint=fingerprint,
                              predecessor_ids=(records[2].record_id,), created_at="2026-09-24T12:00:00+00:00",
                              idempotency_key="halt-1", payload=GateTransitionPayload("1", "awaiting_user_decision", "stop_request", "Packages missing.", None, (), None, None, ())),
    ]
    if scope_extension:
        inserted.append(ArtifactRecord.create(
            run_id=RUN, logical_id="scope-1", revision=1, fingerprint=fingerprint,
            predecessor_ids=(records[2].record_id,), created_at="2026-09-24T12:00:00+00:00",
            idempotency_key="scope-1", payload=ScopeExtensionPayload(
                "1", "1", "native-codex-request-" + "1" * 64, "scope_extension",
                "Zusätzlicher Pfad", (ScopeExtensionPathPayload("src/extra.py", "productive"),),
            ),
        ))
    inserted.append(ArtifactRecord.create(
        run_id=RUN, logical_id="content-call-2", revision=1, fingerprint=fingerprint,
        predecessor_ids=(records[2].record_id,), created_at="2026-09-24T12:00:00+00:00",
        idempotency_key="content-call-2", payload=call_2,
    ))
    records[3:3] = inserted
    if correction:
        review = add(replace(base.records[-2].payload, verdict="denied"), "review-denied")
        add(WorkflowEventPayload("review", "1", "1", 1, (review.record_id,)), "review-event-denied")
        add(CorrectionWorkUnitPayload("1", 2, ("src/1.py",), ("C-01",)), "work-unit-2")
        add(FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CODEX, "responded", FindingSeverity.FINDING,
            "open", "Korrektur umgesetzt.", "2", response_decision="accepted",
        ), "finding-response")
        add(FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "status_changed", FindingSeverity.FINDING,
            "closed", "Korrektur geprüft.", "2", closure_kind="fixed",
        ), "finding-closed")
        attestation = add(ValidationAttestationPayload(
            (ValidationResult(CommandSpec("pytest", ("-q",)), "pass", 0, "f" * 64),),
            Role.ORCHESTRATOR, "f" * 64, base.records[4].record_id,
        ), "validation-2")
        add(WorkflowEventPayload("validation", "2", "1", 2, (attestation.record_id,)), "validation-event-2")
        approved = add(replace(base.records[-2].payload, work_unit_id="2", verdict="approved"), "review-approved")
        add(WorkflowEventPayload("review", "2", "1", 2, (approved.record_id,)), "review-event-approved")
    replay = SimpleNamespace(records=tuple(records), run_identity=base.replay.run_identity)
    return AuditFacts(replay, read_blob=base.read_blob, read_plan=lambda _commit, _path: PLAN)


@pytest.mark.parametrize("scope_extension", (False, True))
def test_resumed_implementation_call_and_scope_extension_stay_in_round_one(scope_extension: bool) -> None:
    facts = _round_facts(scope_extension=scope_extension)
    history = facts.round_history("1")
    assert history.count("- Runde 1: Umsetzung") == 1
    assert "Runde 2" not in history
    assert "| 1 | Slice 1 | freigegeben | – | 1 | 1 |" in render_overall(facts, task="Task", branch="feature/test")
    if scope_extension:
        assert "Runde 1: Umfang erweitert um src/extra.py." in history


def test_finding_changes_follow_review_and_correction_rounds() -> None:
    facts = _round_facts(correction=True)
    history = facts.round_history("1")
    assert "Runde 1: Umsetzung · Validierung grün · Prüfurteil abgelehnt · 1 neu, 0 geschlossen." in history
    assert "Runde 2: Korrektur · Validierung grün · Prüfurteil freigegeben · 0 neu, 1 geschlossen." in history
    assert "Runde 3" not in history
    assert "| 1 | Slice 1 | freigegeben | – | 2 | 1 |" in render_overall(facts, task="Task", branch="feature/test")


def test_short_fingerprint_in_agent_prose_is_not_mistaken_for_a_commit() -> None:
    assert _prose("The fingerprint cc1b4d2c passed.") == "The fingerprint [Hash ausgelassen] passed."


def test_cleared_halt_is_reported_as_resumed_without_inventing_approval() -> None:
    overall = render_overall(_facts(resumed_halt=True), task="Task", branch="feature/test")
    assert "Anlass Stoppanforderung. Grund: Needed input. Entscheidung: fortgesetzt." in overall
    assert "Keine gesonderte Entscheidung aufgezeichnet." in overall
    assert "Entscheidung: ausstehend" not in overall
