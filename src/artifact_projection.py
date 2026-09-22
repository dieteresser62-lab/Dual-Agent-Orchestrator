"""Deterministic, read-only Markdown projection of structured artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from typing import Any, Mapping, Sequence

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    FinalReviewCompletedPayload,
    BindingPayload,
    CorrectionWorkUnitPayload,
    DiagnosticPayload,
    FindingTransitionPayload,
    GatePayload,
    PlanPayload,
    ReviewAnchorPayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    FinalReviewPreflightPayload,
    WorkUnitPayload,
)
from artifact_replay import ArtifactReplayError, ArtifactReplayResult, replay_artifacts
from finding_order import finding_id_sort_key
from finding_reducer import project_record_finding_statuses


class ArtifactProjectionError(ValueError):
    """Raised when a record sequence cannot be projected safely."""


SECTION_KEYS = (
    "claude-review",
    "codex-responses",
    "validation-attestation",
    "test-approval-premortem",
    "findings",
    "decision-table",
    "approval-status",
)


@dataclass(frozen=True, slots=True)
class ArtifactAuditProjection:
    """One stable audit view over an already validated append-only chain.

    ``slice_id`` limits work-unit-owned records and records carrying their
    implementation fingerprints to one Slice.  Agent results, diagnostics, and
    reviews are explicit fingerprint anchors because they carry a work-unit ID;
    in particular, the Codex agent result makes same-round gates and validation
    records visible before the first review exists.  The fingerprint association
    is required because gates, validation records, and bindings deliberately do
    not duplicate a work-unit identifier.  Global task, plan, and
    finding-transition records remain visible because they explain the contract
    and finding lifecycle under which that Slice ran.  A finding can span its
    originating Slice and a later correction Slice, so finding transitions
    intentionally have run-wide rather than Slice-local scope.  ``None``
    projects the complete run for the overall audit.
    """

    records: tuple[ArtifactRecord, ...]
    slice_id: str | None = None
    _accepted_replay: ArtifactReplayResult | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        replay = self._accepted_replay
        if replay is None:
            replay = _replay_for_projection(self.records)
            object.__setattr__(self, "_accepted_replay", replay)
        elif replay.records != self.records:
            raise ArtifactProjectionError("accepted replay does not match projection records")
        if self.slice_id is not None and (not self.slice_id or not self.slice_id.isdigit()):
            raise ArtifactProjectionError("slice_id must be a decimal identifier")

    @classmethod
    def from_replay(
        cls, replay: ArtifactReplayResult, *, slice_id: str | None = None
    ) -> "ArtifactAuditProjection":
        """Project an already accepted replay without reducing it again."""
        return cls(replay.records, slice_id, replay)

    @property
    def selected_records(self) -> tuple[ArtifactRecord, ...]:
        if self.slice_id is None:
            return self.records
        unit_ids = {
            record.logical_id.removeprefix("work-unit-")
            for record in self.records
            if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
            and record.payload.slice_id == self.slice_id
        }
        slice_fingerprints = {
            record.fingerprint
            for record in self.records
            if isinstance(
                record.payload,
                (
                    AgentResultPayload,
                    DiagnosticPayload,
                    ReviewPayload,
                    FinalReviewCompletedPayload,
                ),
            )
            and record.payload.work_unit_id in unit_ids
        }
        selected: list[ArtifactRecord] = []
        for record in self.records:
            payload = record.payload
            owned_unit = getattr(payload, "work_unit_id", None)
            if isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload)):
                if payload.slice_id == self.slice_id:
                    selected.append(record)
            elif owned_unit is not None:
                if str(owned_unit) in unit_ids:
                    selected.append(record)
            elif isinstance(payload, (
                TaskPayload, PlanPayload, FindingTransitionPayload,
            )):
                selected.append(record)
            elif record.fingerprint in slice_fingerprints:
                selected.append(record)
        return tuple(selected)

    @property
    def semantic_digest(self) -> str:
        return self.replay_result.subset(self.selected_records).semantic_digest

    def render_sections(self) -> Mapping[str, str]:
        return render_replay_sections(self.replay_result.subset(self.selected_records))

    @property
    def replay_result(self) -> ArtifactReplayResult:
        assert self._accepted_replay is not None
        return self._accepted_replay


def semantic_artifact_facts(records: Sequence[ArtifactRecord]) -> tuple[dict[str, Any], ...]:
    """Return formatting- and timestamp-independent facts in chain order."""
    return tuple(
        fact.to_document()
        for fact in _replay_for_projection(tuple(records)).semantic_facts
    )


def semantic_artifact_digest(records: Sequence[ArtifactRecord]) -> str:
    """Digest IDs, status, bindings and every typed payload field, not presentation."""
    return _replay_for_projection(tuple(records)).semantic_digest


def render_artifact_sections(records: Sequence[ArtifactRecord]) -> Mapping[str, str]:
    """Render managed audit bodies without parsing Markdown back into facts."""
    return render_replay_sections(_replay_for_projection(tuple(records)))


@dataclass(slots=True)
class _ReplayRendering:
    replay: ArtifactReplayResult
    final_finding_statuses: dict[str, str]
    reviews: dict[Role, list[str]]
    responses: list[str]
    validations: list[str]
    gates: list[str]
    findings: list[str]
    bindings_and_units: list[str]
    latest_attempts: dict[tuple[str, int], tuple[int, ArtifactRecord]]
    input_measurements: dict[str, ProviderInputMeasurementPayload]
    work_unit_rounds: dict[str, int]
    convergence: dict[str, dict[str, Any]]
    review_records_by_id: dict[str, ArtifactRecord]


def _new_replay_rendering(replay: ArtifactReplayResult) -> _ReplayRendering:
    work_unit_rounds: dict[str, int] = {}
    for record in replay.records:
        if (
            isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
            and record.logical_id.startswith("work-unit-")
        ):
            # The first accepted revision establishes the round identity. Later
            # revisions may enrich the work unit but must not rewrite history in
            # the human-readable convergence projection.
            work_unit_rounds.setdefault(
                record.logical_id.removeprefix("work-unit-"),
                record.payload.round_number,
            )
    return _ReplayRendering(
        replay=replay,
        final_finding_statuses=dict(project_record_finding_statuses(replay)),
        reviews={Role.CLAUDE: []},
        responses=[],
        validations=[],
        gates=[],
        findings=[],
        bindings_and_units=[],
        latest_attempts={},
        input_measurements={},
        work_unit_rounds=work_unit_rounds,
        convergence={},
        review_records_by_id={
            record.record_id: record
            for record in replay.records
            if isinstance(record.payload, ReviewPayload)
        },
    )


def _collect_provider_rendering_fact(
    rendering: _ReplayRendering,
    sequence: int,
    record: ArtifactRecord,
) -> None:
    payload = record.payload
    if isinstance(payload, ProviderAttemptPayload):
        rendering.latest_attempts[
            (payload.logical_operation_id, payload.attempt_number)
        ] = (sequence, record)
    elif isinstance(payload, ProviderInputMeasurementPayload):
        rendering.input_measurements[record.record_id] = payload


def _render_agent_result_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: AgentResultPayload,
    prefix: str,
) -> None:
    transport_schema = _safe(payload.transport_schema or "legacy-text")
    request_id = _safe(payload.request_id or "–")
    response_sha256 = payload.response_sha256 or "–"
    round_number = rendering.work_unit_rounds.get(payload.work_unit_id, "–")
    rendering.bindings_and_units.extend((
        f"### {payload.role.value.title()} · Runde {round_number} · {_safe(payload.outcome)}",
        "",
        "| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |",
        "|---|---|---:|---|---|---|---|---|---|---|",
        f"| {prefix} | `{payload.role.value}` | `{round_number}` | `{_safe(payload.outcome)}` | "
        f"`{_safe(payload.work_unit_id)}` | {_codes(payload.test_files)} | "
        f"`{transport_schema}` | `{request_id}` | `{response_sha256}` | "
        f"`{record.fingerprint.sha256}` |",
        "",
    ))


def _render_review_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: ReviewPayload,
    prefix: str,
) -> None:
    transport_schema = _safe(payload.transport_schema or "legacy-text")
    request_id = _safe(payload.request_id or "–")
    response_sha256 = payload.response_sha256 or "–"
    round_number = rendering.work_unit_rounds.get(payload.work_unit_id, "–")
    output = rendering.reviews[payload.reviewer]
    output.extend((
        f"### {payload.reviewer.value.title()} · Runde {round_number} · {_safe(payload.verdict)}",
        "",
        "| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |",
        "|---|---|---:|---|---|---|---|---|---|---|",
        f"| {prefix} | `{payload.reviewer.value}` | `{round_number}` | `{_safe(payload.verdict)}` | "
        f"`{_safe(payload.work_unit_id)}` | {_codes(payload.finding_ids)} | "
        f"`{record.fingerprint.sha256}` | `{transport_schema}` | `{request_id}` | "
        f"`{response_sha256}` |",
        "",
    ))
    if payload.review_evidence is not None:
        output.extend((
            "#### Strukturierte Reviewevidenz",
            "",
            f"- Prüfdimensionen: {_prose(payload.review_evidence.dimensions)}",
            f"- Größtes Restrisiko: {_prose(payload.review_evidence.largest_residual_risk)}",
            f"- Realistische Bruchbedingung: {_prose(payload.review_evidence.break_condition)}",
            "",
        ))
    elif payload.evidence is not None:
        output.extend((
            "#### Opake Legacy-Reviewevidenz",
            "",
            f"- Unzerlegter Bestandswert: {_prose(payload.evidence)}",
            "",
        ))
    if payload.red_state_followup_slice is not None:
        output.extend((
            "#### Red-State-Autorisierung",
            "",
            f"- Gebundene Folgeslice: `{_safe(payload.red_state_followup_slice)}`",
            "",
        ))
    output.extend((
        "#### Reviewvertrag",
        "",
        f"- Testdateien: {_codes(payload.test_files)}",
        f"- Pre-Mortem: {_prose(payload.pre_mortem or '–')}",
    ))
    if payload.stop_request is not None:
        output.extend((
            f"- Stop-Regel: `{_safe(payload.stop_request.rule_id)}`",
            f"- Stop-Begründung: {_prose(payload.stop_request.rationale)}",
            f"- Remediation-Pfade: {_codes(payload.stop_request.remediation_paths)}",
        ))
    output.append("")


def _render_final_review_completed_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: FinalReviewCompletedPayload,
    prefix: str,
) -> None:
    round_number = rendering.work_unit_rounds.get(payload.work_unit_id, "–")
    finding_ids = tuple(
        item.finding_id for item in (*payload.new_findings, *payload.occurrences)
    )
    output = rendering.reviews[payload.reviewer]
    output.extend((
        f"### {payload.reviewer.value.title()} · Runde {round_number} · completed",
        "",
        "| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint |",
        "|---|---|---:|---|---|---|---|",
        f"| {prefix} | `{payload.reviewer.value}` | `{round_number}` | "
        f"`FINAL_REVIEW_COMPLETED` | `{_safe(payload.work_unit_id)}` | "
        f"{_codes(finding_ids)} | `{record.fingerprint.sha256}` |",
        "",
        "#### Strukturierte Reviewevidenz",
        "",
        f"- Prüfdimensionen: {_prose(payload.review_evidence.dimensions)}",
        f"- Größtes Restrisiko: {_prose(payload.review_evidence.largest_residual_risk)}",
        f"- Realistische Bruchbedingung: {_prose(payload.review_evidence.break_condition)}",
        f"- Pre-Mortem: {_prose(payload.pre_mortem)}",
    ))
    if payload.scan_complete is not None:
        output.append(f"- Scan vollständig: `{'yes' if payload.scan_complete else 'no'}`")
    output.append("")


def _render_review_anchor_record(
    rendering: _ReplayRendering,
    payload: ReviewAnchorPayload,
) -> None:
    review_record = rendering.review_records_by_id[payload.review_record_id]
    review_payload = review_record.payload
    assert isinstance(review_payload, ReviewPayload)
    output = rendering.reviews[review_payload.reviewer]
    output.extend((
        "#### Review-Anker",
        "",
        f"- Gebundener Reviewrecord: `{_safe(payload.review_record_id)}`",
    ))
    if payload.anchors:
        output.extend((
            "",
            "| Anchor | Ursprung | Fixture | Erwartung | Toleranz |",
            "|---|---|---|---|---|",
        ))
        for anchor in payload.anchors:
            output.append(
                f"| `{_safe(anchor.anchor_id)}` | {_table_prose(anchor.origin)} | "
                f"{_table_prose(anchor.input_fixture)} | {_table_prose(anchor.expected)} | "
                f"{_table_prose(anchor.tolerance)} |"
            )
    else:
        output.append("- Anker: keine")
    output.append("")


def _render_review_validation_binding_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: ReviewValidationBindingPayload,
    prefix: str,
) -> None:
    rendering.validations.extend((
        "### Review-Validierungsbindung",
        "",
        "| Seq/Record | Reviewrecord | Attestierungsrecord | Fingerprint |",
        "|---|---|---|---|",
        f"| {prefix} | `{_safe(payload.review_record_id)}` | "
        f"`{_safe(payload.attestation_record_id)}` | `{record.fingerprint.sha256}` |",
        "",
    ))


def _render_finding_transition_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: FindingTransitionPayload,
    prefix: str,
) -> None:
    round_number = rendering.work_unit_rounds.get(payload.work_unit_id or "", "–")
    line = (
        f"| {prefix} | `{_safe(payload.finding_id)}` | `{payload.actor.value}` | "
        f"`{round_number}` | `{_safe(payload.action)}` | `{payload.severity.value}` | "
        f"`{_safe(payload.finding_status)}` | {_table_prose(payload.rationale)} |"
    )
    if not rendering.findings:
        rendering.findings.extend((
            "### Finding-Ereignisse",
            "",
            "| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |",
            "|---|---|---|---:|---|---|---|---|",
        ))
    rendering.findings.append(line)
    if payload.action == "responded":
        if not rendering.responses:
            rendering.responses.extend((
                "### Codex · Findingantworten",
                "",
                "| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |",
                "|---|---|---|---:|---|---|---|---|",
            ))
        rendering.responses.append(line)
    if payload.work_unit_id is None:
        return
    row = rendering.convergence.setdefault(
        payload.finding_id,
        {
            "work_units": [],
            "rounds": [],
            "fingerprints": [],
            "claude": [],
            "codex": [],
            "status": payload.finding_status,
        },
    )
    _append_unique(row["work_units"], payload.work_unit_id)
    resolved_round = rendering.work_unit_rounds.get(payload.work_unit_id)
    if resolved_round is None and payload.origin_round_number is not None:
        resolved_round = payload.origin_round_number
    if resolved_round is not None:
        _append_unique(row["rounds"], str(resolved_round))
    _append_unique(row["fingerprints"], record.fingerprint.sha256)
    if payload.actor is Role.CLAUDE:
        _append_unique(
            row["claude"],
            f"{payload.action}:{payload.finding_status}",
        )
    elif payload.actor is Role.CODEX and payload.action == "responded":
        _append_unique(row["codex"], payload.response_decision or "legacy-text")
    row["status"] = rendering.final_finding_statuses.get(
        payload.finding_id, payload.finding_status
    )



def _render_validation_request_record(
    rendering: _ReplayRendering,
    payload: ValidationRequestPayload,
    prefix: str,
) -> None:
    commands = "; ".join(_command(item.argv, item.mode) for item in payload.commands)
    rendering.validations.extend((
        "### Validierungsanforderung",
        "",
        "| Seq/Record | Rolle | Befehle mit argv-Grenzen |",
        "|---|---|---|",
        f"| {prefix} | `{payload.requested_by.value}` | {commands} |",
        "",
    ))


def _render_validation_attestation_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: ValidationAttestationPayload,
    prefix: str,
) -> None:
    rendering.validations.extend((
        "### Validierungsattestierung",
        "",
        "| Seq/Record | Rolle | Fingerprint |",
        "|---|---|---|",
        f"| {prefix} | `{payload.attested_by.value}` | `{record.fingerprint.sha256}` |",
        "",
        "| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |",
        "|---|---:|---|---|",
    ))
    for result in payload.results:
        rendering.validations.append(
            f"| `{_safe(result.outcome)}` | `{result.exit_code}` | "
            f"`{result.output_sha256}` | {_command(result.command.argv, result.command.mode)} |"
        )


def _render_provider_input_measurement_record(
    rendering: _ReplayRendering,
    payload: ProviderInputMeasurementPayload,
    prefix: str,
) -> None:
    components = ", ".join(
        f"{_safe(item.name)}={item.chars}/{item.bytes}"
        for item in payload.components
    )
    violations = ",".join(payload.violated_dimensions) or "none"
    technical_source = _safe(payload.technical_limit_source or "unknown")
    rendering.validations.append(
        f"- {prefix}: Providerinput `{payload.provider.value}/{_safe(payload.operation)}` "
        f"= `{'allowed' if payload.allowed else 'denied'}`; local_input_chars "
        f"`{payload.total_chars}/{payload.effective_limit_chars}`, local_input_bytes "
        f"`{payload.total_bytes}/{payload.effective_limit_bytes}`; local_input_digest "
        f"`{payload.input_digest}`, Policy `{payload.policy_digest}`, Übergang "
        f"`{payload.transition_fingerprint}`; technisches Limit "
        f"`{payload.technical_limit_chars}/{payload.technical_limit_bytes}` "
        f"(Quelle `{technical_source}`); Verletzung `{violations}`, Überhang "
        f"`{payload.char_overage}/{payload.byte_overage}`, "
        f"local_input_largest_component `{_safe(payload.largest_component)}`; "
        f"local_input_component_count `{len(payload.components)}`; Komponenten "
        f"`{components}`"
    )


def _render_final_review_preflight_record(
    rendering: _ReplayRendering,
    payload: FinalReviewPreflightPayload,
    prefix: str,
) -> None:
    affected_records = _codes(payload.affected_record_ids)
    affected_paths = _codes(payload.affected_paths)
    rendering.validations.append(
        f"- {prefix}: Finalreview-Preflight `{_safe(payload.operation)}` = "
        f"`{payload.outcome}`; Fehler `{_safe(payload.error_code or 'none')}`; "
        f"Kategorie `{_safe(payload.category or 'none')}`; Records "
        f"{affected_records}; Pfade {affected_paths}; Abhilfe "
        f"`{_safe(payload.remediation or 'none')}`; "
        f"Übergang `{payload.transition_fingerprint}`; Messung "
        f"`{payload.measurement_record_id}`"
    )


def _render_gate_record(
    rendering: _ReplayRendering,
    record: ArtifactRecord,
    payload: GatePayload,
    prefix: str,
) -> None:
    if not rendering.gates:
        rendering.gates.extend((
            "### Gate-Ereignisse",
            "",
            "| Seq/Record | Gate | Status | Autorität | Fingerprint | Begründung |",
            "|---|---|---|---|---|---|",
        ))
    rendering.gates.append(
        f"| {prefix} | `{_safe(payload.gate_kind)}` | `{_safe(payload.decision)}` | "
        f"`{payload.authority.value}` | `{record.fingerprint.sha256}` | "
        f"{_table_prose(payload.rationale)} |"
    )


def _render_work_unit_record(
    rendering: _ReplayRendering,
    payload: WorkUnitPayload | CorrectionWorkUnitPayload,
    prefix: str,
) -> None:
    is_correction = isinstance(payload, CorrectionWorkUnitPayload)
    kind = "Korrektur-Work-Unit" if is_correction else "Work-Unit"
    finding_ids = payload.finding_ids if is_correction else payload.open_finding_ids
    rendering.bindings_and_units.extend((
        f"### {kind} · Slice {_safe(payload.slice_id)} · Runde {payload.round_number}",
        "",
        "| Seq/Record | Typ | Slice | Runde | Pfade | Findings |",
        "|---|---|---|---:|---|---|",
        f"| {prefix} | {kind} | `{_safe(payload.slice_id)}` | `{payload.round_number}` | "
        f"{_codes(payload.paths)} | {_codes(finding_ids)} |",
        "",
    ))


def _render_binding_record(
    rendering: _ReplayRendering,
    payload: BindingPayload,
    prefix: str,
) -> None:
    rendering.bindings_and_units.extend((
        f"### Binding · {_safe(payload.binding_kind)}",
        "",
        "| Seq/Record | Art | Ziel | Attestierung | Approvals |",
        "|---|---|---|---|---|",
        f"| {prefix} | `{_safe(payload.binding_kind)}` | `{_safe(payload.target)}` | "
        f"`{_safe(payload.attestation_id)}` | {_codes(payload.approval_ids)} |",
        "",
    ))



def _render_record(
    rendering: _ReplayRendering,
    sequence: int,
    record: ArtifactRecord,
) -> None:
    payload = record.payload
    prefix = f"{sequence}. `{_safe(record.record_id)}`"
    if isinstance(payload, AgentResultPayload):
        _render_agent_result_record(rendering, record, payload, prefix)
    elif isinstance(payload, FinalReviewCompletedPayload):
        _render_final_review_completed_record(rendering, record, payload, prefix)
    elif isinstance(payload, ReviewPayload):
        _render_review_record(rendering, record, payload, prefix)
    elif isinstance(payload, ReviewAnchorPayload):
        _render_review_anchor_record(rendering, payload)
    elif isinstance(payload, ReviewValidationBindingPayload):
        _render_review_validation_binding_record(rendering, record, payload, prefix)
    elif isinstance(payload, FindingTransitionPayload):
        _render_finding_transition_record(rendering, record, payload, prefix)
    elif isinstance(payload, ValidationRequestPayload):
        _render_validation_request_record(rendering, payload, prefix)
    elif isinstance(payload, ValidationAttestationPayload):
        _render_validation_attestation_record(rendering, record, payload, prefix)
    elif isinstance(payload, ProviderInputMeasurementPayload):
        _render_provider_input_measurement_record(rendering, payload, prefix)
    elif isinstance(payload, FinalReviewPreflightPayload):
        _render_final_review_preflight_record(rendering, payload, prefix)
    elif isinstance(payload, GatePayload):
        _render_gate_record(rendering, record, payload, prefix)
    elif isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload)):
        _render_work_unit_record(rendering, payload, prefix)
    elif isinstance(payload, BindingPayload):
        _render_binding_record(rendering, payload, prefix)


def _render_provider_attempts(rendering: _ReplayRendering) -> None:
    attempts_by_operation: dict[str, list[tuple[int, ArtifactRecord]]] = {}
    for (logical_operation_id, _attempt_number), value in rendering.latest_attempts.items():
        attempts_by_operation.setdefault(logical_operation_id, []).append(value)
    usage_fields = tuple(ProviderUsagePayload.__dataclass_fields__)
    for logical_operation_id in sorted(attempts_by_operation):
        attempts = sorted(
            attempts_by_operation[logical_operation_id],
            key=lambda item: item[1].payload.attempt_number,
        )
        known_duration = sum(
            float(item.payload.duration_seconds)
            for _, item in attempts
            if isinstance(item.payload, ProviderAttemptPayload)
            and item.payload.duration_seconds is not None
        )
        duration_known = sum(
            1 for _, item in attempts
            if isinstance(item.payload, ProviderAttemptPayload)
            and item.payload.duration_seconds is not None
        )
        summaries: list[str] = []
        for field_name in usage_fields:
            values = [
                getattr(item.payload.usage, field_name)
                for _, item in attempts
                if isinstance(item.payload, ProviderAttemptPayload)
                and item.payload.usage is not None
                and getattr(item.payload.usage, field_name) is not None
            ]
            rendered_total = str(sum(values)) if values else "unknown"
            summaries.append(
                f"{field_name}=sum:{rendered_total},known:{len(values)},unknown:{len(attempts) - len(values)}"
            )
        first = attempts[0][1].payload
        assert isinstance(first, ProviderAttemptPayload)
        open_count = sum(
            item.payload.phase == "started" for _, item in attempts
            if isinstance(item.payload, ProviderAttemptPayload)
        )
        measurements = [
            rendering.input_measurements.get(item.payload.measurement_record_id)
            for _, item in attempts
            if isinstance(item.payload, ProviderAttemptPayload)
        ]
        known_measurements = tuple(item for item in measurements if item is not None)
        input_chars = (
            str(known_measurements[0].total_chars)
            if known_measurements
            and all(item.total_chars == known_measurements[0].total_chars for item in known_measurements)
            else "unknown"
        )
        input_bytes = (
            str(known_measurements[0].total_bytes)
            if known_measurements
            and all(item.total_bytes == known_measurements[0].total_bytes for item in known_measurements)
            else "unknown"
        )
        retry_status = (
            "open"
            if open_count
            else "retried"
            if len(attempts) > 1
            else "single-attempt"
        )
        rendering.validations.append(
            f"- Providerattempt-Summe Run `{_safe(rendering.replay.expected_run_id)}` / "
            f"Operation `{_safe(logical_operation_id)}` (`{first.provider.value}/"
            f"{_safe(first.operation)}`; Modell `{_safe(first.model)}`; Effort "
            f"`{_safe(first.effort)}`): Attempts `{len(attempts)}`, offen `{open_count}`, "
            f"Duration `{known_duration:.6f}` (bekannt `{duration_known}`, unbekannt "
            f"`{len(attempts) - duration_known}`); Inputzeichen `{input_chars}`, "
            f"Inputbytes `{input_bytes}`; Retrystatus `{retry_status}`; "
            + "; ".join(summaries)
        )
        for sequence, record in attempts:
            payload = record.payload
            assert isinstance(payload, ProviderAttemptPayload)
            usage = (
                ", ".join(
                    f"{name}={getattr(payload.usage, name) if getattr(payload.usage, name) is not None else 'unknown'}"
                    for name in usage_fields
                )
                if payload.usage is not None else "unknown"
            )
            measurement = rendering.input_measurements.get(payload.measurement_record_id)
            rendering.validations.append(
                f"  - {sequence}. `{record.record_id}`: Attempt `{payload.attempt_number}` "
                f"= `{payload.phase}`; Messung `{payload.measurement_record_id}`; "
                f"Modell `{_safe(payload.model)}`; Effort `{_safe(payload.effort)}`; "
                f"Inputzeichen `{measurement.total_chars if measurement is not None else 'unknown'}`; "
                f"Inputbytes `{measurement.total_bytes if measurement is not None else 'unknown'}`; "
                f"Duration `{payload.duration_seconds if payload.duration_seconds is not None else 'unknown'}`; "
                f"Fehler `{_safe(payload.failure_kind or 'none')}`; Usage `{usage}`"
            )


def _render_convergence_summary(rendering: _ReplayRendering) -> None:
    if not rendering.convergence:
        return
    rendering.findings.extend(
        (
            "",
            "### Native convergence summary",
            "",
            "| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |",
            "|---|---|---|---|---|---|---|",
        )
    )
    for finding_id in sorted(rendering.convergence, key=finding_id_sort_key):
        row = rendering.convergence[finding_id]
        rendering.findings.append(
            f"| `{_safe(finding_id)}` | {_table_values(row['work_units'])} | "
            f"{_table_values(row['rounds'])} | "
            f"{_table_values(row['fingerprints'])} | "
            f"{_table_values(row['claude'])} | "
            f"{_table_values(row['codex'])} | `{_safe(row['status'])}` |"
        )


def _render_record_ledger(records: Sequence[ArtifactRecord]) -> list[str]:
    ledger = [
        "| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for sequence, record in enumerate(records, start=1):
        ledger.append(
            f"| {sequence} | `{record.record_id}` | `{record.record_type.value}` | "
            f"`{record.status}` | `{_safe(record.logical_id)}` | {record.revision} | "
            f"`{record.fingerprint.kind.value}:{record.fingerprint.sha256}` |"
        )
    if not records:
        ledger.append("| – | – | – | – | – | – | – |")
    return ledger


def _finalize_replay_rendering(rendering: _ReplayRendering) -> Mapping[str, str]:
    header = f"Semantischer Record-Digest: `{rendering.replay.semantic_digest}`"
    sections = {
        "claude-review": _block(header, rendering.reviews[Role.CLAUDE], "Keine Claude-Review-Records."),
        "codex-responses": _block(header, rendering.responses, "Keine Codex-Findingantworten."),
        "validation-attestation": _block(header, rendering.validations, "Keine Validierungsrecords."),
        "test-approval-premortem": _block(header, rendering.gates, "Keine strukturierten Gates."),
        "findings": _block(header, rendering.findings, "Keine Finding-Übergänge."),
        "decision-table": "\n\n".join((header, "\n".join(_render_record_ledger(rendering.replay.records)))),
        "approval-status": _block(header, rendering.bindings_and_units, "Keine Work-Unit- oder Binding-Records."),
    }
    return finalize_projection_bindings(sections)


def render_replay_sections(replay: ArtifactReplayResult) -> Mapping[str, str]:
    """Render directly from one accepted, immutable replay result."""
    rendering = _new_replay_rendering(replay)
    chain = replay.records

    for sequence, record in enumerate(chain, start=1):
        _collect_provider_rendering_fact(rendering, sequence, record)
        _render_record(rendering, sequence, record)

    _render_provider_attempts(rendering)
    _render_convergence_summary(rendering)
    return _finalize_replay_rendering(rendering)


_FULL_HEX_PATTERN = re.compile(r"(?<![0-9A-Fa-f])([0-9a-f]{64}|[0-9a-f]{40})(?![0-9A-Fa-f])")
_SHORT_HEX_PATTERN = re.compile(r"(?<![0-9A-Fa-f])([0-9a-f]{12})(?![0-9A-Fa-f])")
_EVIDENCE_HEADING = "### Nachweis vollst\u00e4ndiger Bindungswerte"
_EVIDENCE_PATTERN = re.compile(
    rf"(?:\r?\n){{2}}{re.escape(_EVIDENCE_HEADING)}\r?\n\r?\n"
    r"\| Kurzreferenz \| Vollwert \| Feldarten \|\r?\n"
    r"\|---\|---\|---\|\r?\n"
    r"(?P<rows>(?:\|[^\r\n]*\|\r?\n?)*)"
)


@dataclass(slots=True)
class _BindingRegistry:
    """Document-local, first-seen registry for reproducible technical values."""

    values: dict[str, list[str]] = field(default_factory=dict)
    short_to_full: dict[str, str] = field(default_factory=dict)

    def add(self, value: str, field_type: str) -> str:
        short = value[:12]
        previous = self.short_to_full.setdefault(short, value)
        if previous != value:
            raise ArtifactProjectionError(
                f"technical short reference {short} maps to multiple full values"
            )
        kinds = self.values.setdefault(value, [])
        if field_type not in kinds:
            kinds.append(field_type)
        return short


def _field_type(text: str, start: int) -> str:
    context = text[max(0, start - 80):start].lower()
    for token, label in (
        ("record", "Record-ID"),
        ("request", "Request-ID"),
        ("response", "Response-Digest"),
        ("output", "Output-Digest"),
        ("ausgabe", "Output-Digest"),
        ("policy", "Policy-Digest"),
        ("fingerprint", "Fingerprint"),
        ("\u00fcbergang", "\u00dcbergangsfingerprint"),
        ("transition", "\u00dcbergangsfingerprint"),
        ("binding", "Bindingziel"),
        ("target", "Bindingziel"),
        ("digest", "Digest"),
        ("attest", "Attestierungsreferenz"),
        ("approval", "Approval-Referenz"),
        ("messung", "Messungsreferenz"),
    ):
        if token in context:
            return label
    return "Technischer Wert"


def _remove_and_seed_evidence(text: str, registry: _BindingRegistry) -> str:
    def remove(match: re.Match[str]) -> str:
        for row in match.group("rows").splitlines():
            cells = [cell.strip().strip("`") for cell in row.strip().strip("|").split("|")]
            if len(cells) != 3 or not _FULL_HEX_PATTERN.fullmatch(cells[1]):
                continue
            for field_type in (part.strip() for part in cells[2].split(",")):
                registry.add(cells[1], field_type or "Technischer Wert")
        return ""

    return _EVIDENCE_PATTERN.sub(remove, text)


def _shorten_bindings(text: str, registry: _BindingRegistry) -> str:
    return _FULL_HEX_PATTERN.sub(
        lambda match: registry.add(match.group(1), _field_type(text, match.start())),
        text,
    )


def _rehydrate_bindings(text: str, registry: _BindingRegistry) -> str:
    """Restore registered short references with one document-wide scan."""
    return _SHORT_HEX_PATTERN.sub(
        lambda match: registry.short_to_full.get(match.group(1), match.group(1)),
        text,
    )


def _binding_evidence(registry: _BindingRegistry) -> str:
    rows = [
        _EVIDENCE_HEADING,
        "",
        "| Kurzreferenz | Vollwert | Feldarten |",
        "|---|---|---|",
    ]
    rows.extend(
        f"| `{value[:12]}` | `{value}` | {', '.join(types)} |"
        for value, types in registry.values.items()
    )
    if not registry.values:
        rows.append("| – | – | – |")
    return "\n".join(rows)


def finalize_projection_bindings(sections: Mapping[str, str]) -> Mapping[str, str]:
    """Shorten technical values and emit exactly one document-local evidence table."""
    if set(sections) != set(SECTION_KEYS):
        raise ArtifactProjectionError("projection does not cover every managed section")
    registry = _BindingRegistry()
    cleaned = {
        key: _remove_and_seed_evidence(str(sections[key]), registry)
        for key in SECTION_KEYS
    }
    rendered = {
        key: _shorten_bindings(cleaned[key], registry)
        for key in SECTION_KEYS
    }
    rendered["decision-table"] = (
        rendered["decision-table"].rstrip("\r\n")
        + "\n\n"
        + _binding_evidence(registry)
    )
    outside = "\n".join(
        value for key, value in rendered.items() if key != "decision-table"
    )
    decision_without_evidence = _remove_and_seed_evidence(
        rendered["decision-table"], _BindingRegistry()
    )
    if _FULL_HEX_PATTERN.search(outside + "\n" + decision_without_evidence):
        raise ArtifactProjectionError("full technical value remains outside binding evidence")
    evidence = _EVIDENCE_PATTERN.search("\n\n" + rendered["decision-table"] + "\n")
    if evidence is None:
        raise ArtifactProjectionError("binding evidence is missing")
    evidence_values = _FULL_HEX_PATTERN.findall(evidence.group(0))
    if evidence_values != list(registry.values):
        raise ArtifactProjectionError("binding evidence must contain each full value exactly once")
    return rendered


def finalize_projection_document(markdown: str) -> str:
    """Apply the same binding policy after State-v3 and record views are merged."""
    prior = _BindingRegistry()
    cleaned = _remove_and_seed_evidence(markdown, prior)
    # Record sections arrive already shortened. Rehydrate their exact technical
    # values so the combined State-v3/record document can establish one true
    # first-occurrence order before shortening the whole view again.
    cleaned = _rehydrate_bindings(cleaned, prior)
    registry = _BindingRegistry()
    rendered = _shorten_bindings(cleaned, registry)
    for value, types in prior.values.items():
        for field_type in types:
            registry.add(value, field_type)
    marker = "<!-- artifact-records:decision-table:end -->"
    if rendered.count(marker) != 1:
        raise ArtifactProjectionError("document requires one decision-table record marker")
    rendered = rendered.replace(
        marker, "\n\n" + _binding_evidence(registry) + "\n" + marker, 1
    )
    without_evidence = _remove_and_seed_evidence(rendered, _BindingRegistry())
    if _FULL_HEX_PATTERN.search(without_evidence):
        raise ArtifactProjectionError("full technical value remains outside binding evidence")
    if _FULL_HEX_PATTERN.findall(rendered) != list(registry.values):
        raise ArtifactProjectionError("document must contain each full value exactly once")
    return rendered


def _replay_for_projection(records: tuple[ArtifactRecord, ...]) -> ArtifactReplayResult:
    expected_run_id = records[0].run_id if records else "empty-projection"
    try:
        return replay_artifacts(records, expected_run_id, allow_empty=True)
    except ArtifactReplayError as exc:
        raise ArtifactProjectionError(str(exc)) from exc


def _block(header: str, lines: list[str], empty: str) -> str:
    return "\n\n".join((header, "\n".join(lines) if lines else empty))


def _codes(values: Sequence[str]) -> str:
    return ", ".join(f"`{_safe(value)}`" for value in values) if values else "keine"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _table_values(values: Sequence[str]) -> str:
    return "<br>".join(f"`{_safe(value)}`" for value in values) if values else "–"


def _command(argv: Sequence[str], mode: str) -> str:
    # Each argument remains visibly bounded; no shell or Markdown parser is used.
    arguments = ", ".join(f"`{_safe(argument)}`" for argument in argv)
    return f"`{_safe(mode)}` [{arguments}]"


def _safe(value: object) -> str:
    return (
        html.escape(str(value), quote=False)
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


_INLINE_PROSE_BOUNDARY = re.compile(
    r"(?<=[.!?])[ \t]+|(?<=\S)[ \t]+(?=(?:[-*+\u2022]|[0-9]+[.)])[ \t]+\S)"
)


def _prose(value: object) -> str:
    """Escape prose and expose only documented, language-neutral boundaries."""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    structured = "\n".join(
        _INLINE_PROSE_BOUNDARY.sub("\n", line) for line in normalized.split("\n")
    )
    return _safe(structured)


def _table_prose(value: object) -> str:
    """Render structured prose without introducing a physical Markdown table row break."""
    return _prose(value).replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


__all__ = [
    "ArtifactAuditProjection",
    "ArtifactProjectionError",
    "SECTION_KEYS",
    "finalize_projection_bindings",
    "finalize_projection_document",
    "render_artifact_sections",
    "render_replay_sections",
    "semantic_artifact_digest",
    "semantic_artifact_facts",
]
