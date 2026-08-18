"""Deterministic, read-only Markdown projection of structured artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
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
    WorkUnitPayload,
    canonical_json,
)


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

    def __post_init__(self) -> None:
        _validate_sequence(self.records)
        if self.slice_id is not None and (not self.slice_id or not self.slice_id.isdigit()):
            raise ArtifactProjectionError("slice_id must be a decimal identifier")

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
        return semantic_artifact_digest(self.selected_records)

    def render_sections(self) -> Mapping[str, str]:
        return render_artifact_sections(self.selected_records)


def semantic_artifact_facts(records: Sequence[ArtifactRecord]) -> tuple[dict[str, Any], ...]:
    """Return formatting- and timestamp-independent facts in chain order."""
    chain = tuple(records)
    _validate_sequence(chain)
    return tuple(
        {
            "record_id": record.record_id,
            "record_type": record.record_type.value,
            "logical_id": record.logical_id,
            "revision": record.revision,
            "status": record.status,
            "fingerprint": {
                "kind": record.fingerprint.kind.value,
                "sha256": record.fingerprint.sha256,
            },
            "predecessor_ids": list(record.predecessor_ids),
            "payload": asdict(record.payload),
        }
        for record in chain
    )


def semantic_artifact_digest(records: Sequence[ArtifactRecord]) -> str:
    """Digest IDs, status, bindings and every typed payload field, not presentation."""
    return hashlib.sha256(canonical_json(semantic_artifact_facts(records))).hexdigest()


def render_artifact_sections(records: Sequence[ArtifactRecord]) -> Mapping[str, str]:
    """Render managed audit bodies without parsing Markdown back into facts."""
    chain = tuple(records)
    _validate_sequence(chain)
    digest = semantic_artifact_digest(chain)
    reviews = {
        Role.CLAUDE: [],
        Role.ANTIGRAVITY: [],
    }
    responses: list[str] = []
    validations: list[str] = []
    gates: list[str] = []
    findings: list[str] = []
    bindings_and_units: list[str] = []

    for sequence, record in enumerate(chain, start=1):
        payload = record.payload
        prefix = f"{sequence}. `{_safe(record.record_id)}`"
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


def _validate_sequence(records: tuple[ArtifactRecord, ...]) -> None:
    if not records:
        return
    run_id = records[0].run_id
    positions = {record.record_id: index for index, record in enumerate(records)}
    if len(positions) != len(records):
        raise ArtifactProjectionError("record sequence contains duplicate IDs")
    for index, record in enumerate(records):
        if record.run_id != run_id:
            raise ArtifactProjectionError("one projection cannot mix run identifiers")
        for predecessor in record.predecessor_ids:
            predecessor_position = positions.get(predecessor)
            if predecessor_position is not None and predecessor_position >= index:
                raise ArtifactProjectionError("record sequence is not in append order")


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
    "semantic_artifact_digest",
    "semantic_artifact_facts",
]
