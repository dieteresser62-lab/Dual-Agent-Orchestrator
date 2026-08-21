"""Deterministic, read-only Markdown projection of structured artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
from typing import Any, Mapping, Sequence

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CorrectionWorkUnitPayload,
    DiagnosticPayload,
    FindingTransitionPayload,
    GatePayload,
    PlanPayload,
    ReviewPayload,
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


class ArtifactProjectionError(ValueError):
    """Raised when a record sequence cannot be projected safely."""


SECTION_KEYS = (
    "claude-review",
    "antigravity-review",
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
                (AgentResultPayload, DiagnosticPayload, ReviewPayload),
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
            elif isinstance(payload, (TaskPayload, PlanPayload, FindingTransitionPayload)):
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


def render_replay_sections(replay: ArtifactReplayResult) -> Mapping[str, str]:
    """Render directly from one accepted, immutable replay result."""
    chain = replay.records
    digest = replay.semantic_digest
    reviews = {
        Role.CLAUDE: [],
        Role.ANTIGRAVITY: [],
    }
    responses: list[str] = []
    validations: list[str] = []
    gates: list[str] = []
    findings: list[str] = []
    bindings_and_units: list[str] = []
    latest_attempts: dict[tuple[str, int], tuple[int, ArtifactRecord]] = {}

    for sequence, record in enumerate(chain, start=1):
        payload = record.payload
        prefix = f"{sequence}. `{_safe(record.record_id)}`"
        if isinstance(payload, ProviderAttemptPayload):
            latest_attempts[(payload.logical_operation_id, payload.attempt_number)] = (
                sequence, record
            )
        if isinstance(payload, ReviewPayload):
            reviews[payload.reviewer].append(
                f"- {prefix}: `{_safe(payload.verdict)}`; Work-Unit "
                f"`{_safe(payload.work_unit_id)}`; Findings {_codes(payload.finding_ids)}; "
                f"Fingerprint `{record.fingerprint.sha256}`"
            )
        elif isinstance(payload, FindingTransitionPayload):
            line = (
                f"- {prefix}: `{_safe(payload.finding_id)}` "
                f"`{_safe(payload.action)}` durch `{payload.actor.value}`; "
                f"`{payload.severity.value}` / `{_safe(payload.finding_status)}` — "
                f"{_safe(payload.rationale)}"
            )
            findings.append(line)
            if payload.action == "responded":
                responses.append(line)
        elif isinstance(payload, ValidationRequestPayload):
            commands = "; ".join(_command(item.argv, item.mode) for item in payload.commands)
            validations.append(f"- {prefix}: Anforderung durch `{payload.requested_by.value}`: {commands}")
        elif isinstance(payload, ValidationAttestationPayload):
            validations.append(
                f"- {prefix}: Attestierung durch `{payload.attested_by.value}`; "
                f"Fingerprint `{record.fingerprint.sha256}`"
            )
            for result in payload.results:
                validations.append(
                    f"  - `{_safe(result.outcome)}` / Exit `{result.exit_code}` / "
                    f"Output `{result.output_sha256}`: {_command(result.command.argv, result.command.mode)}"
                )
        elif isinstance(payload, ProviderInputMeasurementPayload):
            components = ", ".join(
                f"{_safe(item.name)}={item.chars}/{item.bytes}"
                for item in payload.components
            )
            violations = ",".join(payload.violated_dimensions) or "none"
            technical_source = _safe(payload.technical_limit_source or "unknown")
            validations.append(
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
        elif isinstance(payload, FinalReviewPreflightPayload):
            affected_records = _codes(payload.affected_record_ids)
            affected_paths = _codes(payload.affected_paths)
            validations.append(
                f"- {prefix}: Finalreview-Preflight `{_safe(payload.operation)}` = "
                f"`{payload.outcome}`; Fehler `{_safe(payload.error_code or 'none')}`; "
                f"Kategorie `{_safe(payload.category or 'none')}`; Records "
                f"{affected_records}; Pfade {affected_paths}; Abhilfe "
                f"`{_safe(payload.remediation or 'none')}`; "
                f"Übergang `{payload.transition_fingerprint}`; Messung "
                f"`{payload.measurement_record_id}`"
            )
        elif isinstance(payload, GatePayload):
            gates.append(
                f"- {prefix}: `{_safe(payload.gate_kind)}` = `{_safe(payload.decision)}` "
                f"durch `{payload.authority.value}`; Fingerprint `{record.fingerprint.sha256}` — "
                f"{_safe(payload.rationale)}"
            )
        elif isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload)):
            kind = "Korrektur-Work-Unit" if isinstance(payload, CorrectionWorkUnitPayload) else "Work-Unit"
            extra = (
                f"; Findings {_codes(payload.finding_ids)}"
                if isinstance(payload, CorrectionWorkUnitPayload)
                else ""
            )
            bindings_and_units.append(
                f"- {prefix}: {kind} Slice `{_safe(payload.slice_id)}`, Runde "
                f"`{payload.round_number}`; Pfade {_codes(payload.paths)}{extra}"
            )
        elif isinstance(payload, BindingPayload):
            bindings_and_units.append(
                f"- {prefix}: Binding `{_safe(payload.binding_kind)}` auf "
                f"`{_safe(payload.target)}`; Attestierung `{_safe(payload.attestation_id)}`; "
                f"Approvals {_codes(payload.approval_ids)}"
            )

    attempts_by_operation: dict[str, list[tuple[int, ArtifactRecord]]] = {}
    for (logical_operation_id, _attempt_number), value in latest_attempts.items():
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
            total = sum(values) if values else 0
            summaries.append(
                f"{field_name}=sum:{total},known:{len(values)},unknown:{len(attempts) - len(values)}"
            )
        first = attempts[0][1].payload
        assert isinstance(first, ProviderAttemptPayload)
        open_count = sum(
            item.payload.phase == "started" for _, item in attempts
            if isinstance(item.payload, ProviderAttemptPayload)
        )
        validations.append(
            f"- Providerattempt-Summe Run `{_safe(replay.expected_run_id)}` / "
            f"Operation `{_safe(logical_operation_id)}` (`{first.provider.value}/"
            f"{_safe(first.operation)}`): Attempts `{len(attempts)}`, offen `{open_count}`, "
            f"Duration `{known_duration:.6f}` (bekannt `{duration_known}`, unbekannt "
            f"`{len(attempts) - duration_known}`); " + "; ".join(summaries)
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
            validations.append(
                f"  - {sequence}. `{record.record_id}`: Attempt `{payload.attempt_number}` "
                f"= `{payload.phase}`; Messung `{payload.measurement_record_id}`; "
                f"Duration `{payload.duration_seconds if payload.duration_seconds is not None else 'unknown'}`; "
                f"Fehler `{_safe(payload.failure_kind or 'none')}`; Usage `{usage}`"
            )

    header = f"Semantischer Record-Digest: `{digest}`"
    ledger = [
        "| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for sequence, record in enumerate(chain, start=1):
        ledger.append(
            f"| {sequence} | `{record.record_id}` | `{record.record_type.value}` | "
            f"`{record.status}` | `{_safe(record.logical_id)}` | {record.revision} | "
            f"`{record.fingerprint.kind.value}:{record.fingerprint.sha256}` |"
        )
    if not chain:
        ledger.append("| – | – | – | – | – | – | – |")

    return {
        "claude-review": _block(header, reviews[Role.CLAUDE], "Keine Claude-Review-Records."),
        "antigravity-review": _block(header, reviews[Role.ANTIGRAVITY], "Keine Antigravity-Review-Records."),
        "codex-responses": _block(header, responses, "Keine Codex-Findingantworten."),
        "validation-attestation": _block(header, validations, "Keine Validierungsrecords."),
        "test-approval-premortem": _block(header, gates, "Keine strukturierten Gates."),
        "findings": _block(header, findings, "Keine Finding-Übergänge."),
        "decision-table": "\n\n".join((header, "\n".join(ledger))),
        "approval-status": _block(header, bindings_and_units, "Keine Work-Unit- oder Binding-Records."),
    }


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


__all__ = [
    "ArtifactAuditProjection",
    "ArtifactProjectionError",
    "SECTION_KEYS",
    "render_artifact_sections",
    "render_replay_sections",
    "semantic_artifact_digest",
    "semantic_artifact_facts",
]
