"""Versioned, strictly validated records for structured workflow artifacts.

The module deliberately keeps persistence concerns out of the domain model.  A
record is immutable, has a closed typed payload, and can be serialized to stable
canonical JSON.  JSON Schema validation is performed from the bundled schema;
no network resolver is used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, ClassVar, Mapping, Sequence, TypeAlias

SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "orchestrator-artifact-v1.schema.json"


class ArtifactValidationError(ValueError):
    """Raised when an artifact violates the v1 domain contract."""


class RecordType(StrEnum):
    TASK = "task"
    PLAN = "plan"
    WORK_UNIT = "work_unit"
    CORRECTION_WORK_UNIT = "correction_work_unit"
    AGENT_RESULT = "agent_result"
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"
    FINDING_TRANSITION = "finding_transition"
    VALIDATION_REQUEST = "validation_request"
    VALIDATION_ATTESTATION = "validation_attestation"
    GATE = "gate"
    BINDING = "binding"
    QUOTA_PAUSE = "quota_pause"
    RESUME_CHECK = "resume_check"
    WORKFLOW_COMPLETION = "workflow_completion"


class FingerprintKind(StrEnum):
    CONTRACT = "contract"
    IMPLEMENTATION = "implementation"


class Role(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"
    ORCHESTRATOR = "orchestrator"
    USER = "user"


class FindingSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    OBSERVATION = "OBSERVATION"


@dataclass(frozen=True, slots=True)
class Fingerprint:
    kind: FingerprintKind
    sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "fingerprint.sha256")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    family: str
    argv: tuple[str, ...]
    mode: str = "argv"

    def __post_init__(self) -> None:
        _require_identifier(self.family, "command.family")
        if (
            isinstance(self.argv, (str, bytes))
            or not self.argv
            or any(not isinstance(arg, str) or not arg for arg in self.argv)
        ):
            raise ArtifactValidationError("command.argv must be a non-empty list of non-empty strings")
        if self.mode not in {"argv", "legacy_shell"}:
            raise ArtifactValidationError("command.mode must be argv or legacy_shell")
        if any(any(character in arg for character in ("\x00", "\r", "\n")) for arg in self.argv):
            raise ArtifactValidationError("command.argv entries must not contain control separators")
        if self.mode == "legacy_shell" and len(self.argv) != 1:
            raise ArtifactValidationError("legacy_shell command must preserve exactly one unparsed value")


@dataclass(frozen=True, slots=True)
class SliceSpec:
    slice_id: str
    summary: str
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_text(self.summary, "summary")
        _require_paths(self.paths)


@dataclass(frozen=True, slots=True)
class TaskPayload:
    target_branch: str
    scope_paths: tuple[str, ...]
    assignment_sha256: str
    status: ClassVar[str] = "accepted"
    record_type: ClassVar[RecordType] = RecordType.TASK

    def __post_init__(self) -> None:
        _require_text(self.target_branch, "target_branch")
        _require_paths(self.scope_paths)
        _require_sha256(self.assignment_sha256, "assignment_sha256")


@dataclass(frozen=True, slots=True)
class PlanPayload:
    work_plan_path: str
    approved_plan_commit: str
    slices: tuple[SliceSpec, ...]
    status: ClassVar[str] = "approved"
    record_type: ClassVar[RecordType] = RecordType.PLAN

    def __post_init__(self) -> None:
        _require_path(self.work_plan_path)
        if not re.fullmatch(r"[0-9a-f]{40}", self.approved_plan_commit):
            raise ArtifactValidationError("approved_plan_commit must be a lowercase 40-character Git SHA")
        if not self.slices:
            raise ArtifactValidationError("plan.slices must not be empty")
        ids = tuple(item.slice_id for item in self.slices)
        if len(ids) != len(set(ids)):
            raise ArtifactValidationError("plan.slices must have unique slice_id values")


@dataclass(frozen=True, slots=True)
class WorkUnitPayload:
    slice_id: str
    round_number: int
    paths: tuple[str, ...]
    status: ClassVar[str] = "active"
    record_type: ClassVar[RecordType] = RecordType.WORK_UNIT

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_positive(self.round_number, "round_number")
        _require_paths(self.paths)


@dataclass(frozen=True, slots=True)
class CorrectionWorkUnitPayload:
    slice_id: str
    round_number: int
    paths: tuple[str, ...]
    finding_ids: tuple[str, ...]
    status: ClassVar[str] = "active"
    record_type: ClassVar[RecordType] = RecordType.CORRECTION_WORK_UNIT

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_positive(self.round_number, "round_number")
        _require_paths(self.paths)
        _require_unique_identifiers(self.finding_ids, "finding_ids")


@dataclass(frozen=True, slots=True)
class AgentResultPayload:
    role: Role
    work_unit_id: str
    outcome: str
    test_files: tuple[str, ...]
    status: ClassVar[str] = "ready"
    record_type: ClassVar[RecordType] = RecordType.AGENT_RESULT

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.outcome not in {"ready", "not_ready", "stopped"}:
            raise ArtifactValidationError("agent result outcome is invalid")
        _require_paths(self.test_files, allow_empty=True)


@dataclass(frozen=True, slots=True)
class DiagnosticPayload:
    role: Role
    work_unit_id: str
    attempt: int
    output_sha256: str
    reason: str
    status: ClassVar[str] = "failed"
    record_type: ClassVar[RecordType] = RecordType.DIAGNOSTIC

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "work_unit_id")
        _require_positive(self.attempt, "attempt")
        _require_sha256(self.output_sha256, "output_sha256")
        _require_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class ReviewPayload:
    reviewer: Role
    work_unit_id: str
    verdict: str
    finding_ids: tuple[str, ...]
    evidence: str | None
    status: ClassVar[str] = "decided"
    record_type: ClassVar[RecordType] = RecordType.REVIEW

    def __post_init__(self) -> None:
        if self.reviewer not in {Role.CLAUDE, Role.ANTIGRAVITY}:
            raise ArtifactValidationError("reviewer must be claude or antigravity")
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.verdict not in {"approved", "denied", "stop"}:
            raise ArtifactValidationError("review verdict is invalid")
        _require_unique_identifiers(self.finding_ids, "finding_ids", allow_empty=True)
        if self.evidence is not None:
            _require_text(self.evidence, "evidence")
        if self.verdict == "approved" and not self.finding_ids and self.evidence is None:
            raise ArtifactValidationError("an approval requires findings or review evidence")


@dataclass(frozen=True, slots=True)
class FindingTransitionPayload:
    finding_id: str
    reporter: Role
    actor: Role
    action: str
    severity: FindingSeverity
    finding_status: str
    rationale: str
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.FINDING_TRANSITION

    def __post_init__(self) -> None:
        _require_identifier(self.finding_id, "finding_id")
        if self.reporter not in {Role.CLAUDE, Role.ANTIGRAVITY}:
            raise ArtifactValidationError("finding reporter must be claude or antigravity")
        if self.action not in {"opened", "responded", "status_changed", "reclassified"}:
            raise ArtifactValidationError("finding action is invalid")
        if self.finding_status not in {"open", "closed"}:
            raise ArtifactValidationError("finding_status is invalid")
        if self.action in {"opened", "status_changed", "reclassified"} and self.actor != self.reporter:
            raise ArtifactValidationError("only the reporting reviewer may mutate a finding")
        if self.action == "responded" and self.actor is not Role.CODEX:
            raise ArtifactValidationError("only codex may record a finding response")
        if self.action == "responded" and self.finding_status != "open":
            raise ArtifactValidationError("a codex response cannot close a finding")
        _require_text(self.rationale, "rationale")


@dataclass(frozen=True, slots=True)
class ValidationRequestPayload:
    commands: tuple[CommandSpec, ...]
    requested_by: Role
    status: ClassVar[str] = "requested"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_REQUEST

    def __post_init__(self) -> None:
        if not self.commands:
            raise ArtifactValidationError("validation request commands must not be empty")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    command: CommandSpec
    outcome: str
    exit_code: int
    output_sha256: str

    def __post_init__(self) -> None:
        if self.outcome not in {"pass", "fail", "unavailable"}:
            raise ArtifactValidationError("validation outcome is invalid")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ArtifactValidationError("exit_code must be an integer")
        _require_sha256(self.output_sha256, "output_sha256")


@dataclass(frozen=True, slots=True)
class ValidationAttestationPayload:
    results: tuple[ValidationResult, ...]
    attested_by: Role
    status: ClassVar[str] = "attested"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_ATTESTATION

    def __post_init__(self) -> None:
        if not self.results:
            raise ArtifactValidationError("validation attestation results must not be empty")


@dataclass(frozen=True, slots=True)
class GatePayload:
    gate_kind: str
    decision: str
    authority: Role
    rationale: str
    status: ClassVar[str] = "decided"
    record_type: ClassVar[RecordType] = RecordType.GATE

    def __post_init__(self) -> None:
        _require_identifier(self.gate_kind, "gate_kind")
        if self.decision not in {"approved", "rejected", "pending"}:
            raise ArtifactValidationError("gate decision is invalid")
        _require_text(self.rationale, "rationale")


@dataclass(frozen=True, slots=True)
class BindingPayload:
    binding_kind: str
    target: str
    attestation_id: str
    approval_ids: tuple[str, ...]
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.BINDING

    def __post_init__(self) -> None:
        if self.binding_kind not in {"commit", "plan_commit", "implementation_handoff"}:
            raise ArtifactValidationError("binding_kind is invalid")
        _require_text(self.target, "target")
        _require_identifier(self.attestation_id, "attestation_id")
        _require_unique_identifiers(self.approval_ids, "approval_ids")


@dataclass(frozen=True, slots=True)
class QuotaPausePayload:
    role: Role
    repository_fingerprint: str
    retry_at: str
    status: ClassVar[str] = "paused"
    record_type: ClassVar[RecordType] = RecordType.QUOTA_PAUSE

    def __post_init__(self) -> None:
        _require_sha256(self.repository_fingerprint, "repository_fingerprint")
        _require_timestamp(self.retry_at, "retry_at")


@dataclass(frozen=True, slots=True)
class ResumeCheckPayload:
    expected_head_id: str
    repository_fingerprint: str
    outcome: str
    status: ClassVar[str] = "checked"
    record_type: ClassVar[RecordType] = RecordType.RESUME_CHECK

    def __post_init__(self) -> None:
        _require_identifier(self.expected_head_id, "expected_head_id")
        _require_sha256(self.repository_fingerprint, "repository_fingerprint")
        if self.outcome not in {"matched", "mismatch", "incomplete"}:
            raise ArtifactValidationError("resume outcome is invalid")


@dataclass(frozen=True, slots=True)
class WorkflowCompletionPayload:
    outcome: str
    final_binding_id: str | None
    status: ClassVar[str] = "completed"
    record_type: ClassVar[RecordType] = RecordType.WORKFLOW_COMPLETION

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "stopped"}:
            raise ArtifactValidationError("workflow completion outcome is invalid")
        if self.final_binding_id is not None:
            _require_identifier(self.final_binding_id, "final_binding_id")
        if self.outcome == "completed" and self.final_binding_id is None:
            raise ArtifactValidationError("completed workflow requires final_binding_id")


ArtifactPayload: TypeAlias = (
    TaskPayload | PlanPayload | WorkUnitPayload | CorrectionWorkUnitPayload
    | AgentResultPayload | DiagnosticPayload | ReviewPayload | FindingTransitionPayload
    | ValidationRequestPayload | ValidationAttestationPayload | GatePayload | BindingPayload
    | QuotaPausePayload | ResumeCheckPayload | WorkflowCompletionPayload
)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    record_id: str
    record_type: RecordType
    run_id: str
    logical_id: str
    revision: int
    status: str
    fingerprint: Fingerprint
    predecessor_ids: tuple[str, ...]
    created_at: str
    idempotency_key: str
    payload: ArtifactPayload
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ArtifactValidationError(f"unsupported schema_version: {self.schema_version!r}")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.logical_id, "logical_id")
        _require_positive(self.revision, "revision")
        _require_unique_identifiers(self.predecessor_ids, "predecessor_ids", allow_empty=True)
        _require_timestamp(self.created_at, "created_at")
        _require_identifier(self.idempotency_key, "idempotency_key")
        if self.record_type != self.payload.record_type:
            raise ArtifactValidationError("record_type does not match payload type")
        if self.status != self.payload.status:
            raise ArtifactValidationError("record status does not match payload status")
        expected = stable_record_id(self.run_id, self.record_type, self.logical_id, self.revision)
        if self.record_id != expected:
            raise ArtifactValidationError(f"record_id is not stable; expected {expected!r}")
        self.validate_schema()

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        logical_id: str,
        revision: int,
        fingerprint: Fingerprint,
        predecessor_ids: Sequence[str],
        created_at: str,
        idempotency_key: str,
        payload: ArtifactPayload,
    ) -> "ArtifactRecord":
        return cls(
            record_id=stable_record_id(run_id, payload.record_type, logical_id, revision),
            record_type=payload.record_type,
            run_id=run_id,
            logical_id=logical_id,
            revision=revision,
            status=payload.status,
            fingerprint=fingerprint,
            predecessor_ids=tuple(predecessor_ids),
            created_at=created_at,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "ArtifactRecord":
        """Rehydrate a record only after the closed JSON contract accepts it."""
        validate_artifact_document(document)
        record_type = RecordType(document["record_type"])
        payload = _payload_from_dict(record_type, document["payload"])
        fingerprint = document["fingerprint"]
        return cls(
            schema_version=document["schema_version"],
            record_id=document["record_id"],
            record_type=record_type,
            run_id=document["run_id"],
            logical_id=document["logical_id"],
            revision=document["revision"],
            status=document["status"],
            fingerprint=Fingerprint(FingerprintKind(fingerprint["kind"]), fingerprint["sha256"]),
            predecessor_ids=tuple(document["predecessor_ids"]),
            created_at=document["created_at"],
            idempotency_key=document["idempotency_key"],
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return _json_value(raw)

    def canonical_json(self) -> bytes:
        return canonical_json(self.to_dict())

    def validate_schema(self) -> None:
        validate_artifact_document(self.to_dict())


def stable_record_id(run_id: str, record_type: RecordType | str, logical_id: str, revision: int) -> str:
    """Return a deterministic opaque ID for one logical record revision."""
    kind = record_type.value if isinstance(record_type, RecordType) else record_type
    material = canonical_json([run_id, kind, logical_id, revision])
    return f"ar1-{hashlib.sha256(material).hexdigest()}"


def canonical_json(value: Any) -> bytes:
    """Serialize JSON using the RFC 8785-compatible subset used by our models."""
    return json.dumps(
        _json_value(value), ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_schema() -> dict[str, Any]:
    """Load and self-check the bundled schema without remote resolution."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ArtifactValidationError("bundled artifact schema must be a JSON object")
    _check_schema_node(schema, schema, "<schema>")
    return schema


def validate_artifact_document(document: Mapping[str, Any]) -> None:
    """Validate a document against the bundled, closed v1 schema offline.

    The repository intentionally has no runtime dependencies.  This validator
    implements the Draft 2020-12 keywords used by the bundled schema and the
    schema self-check rejects unknown keywords, preventing an unsupported
    extension from being accepted silently.
    """
    schema = load_schema()
    try:
        _validate_schema_node(document, schema, schema, ())
    except _SchemaMismatch as error:
        location = ".".join(str(part) for part in error.path) or "<record>"
        raise ArtifactValidationError(
            f"schema validation failed at {location}: {error.message}"
        ) from None


@dataclass(frozen=True, slots=True)
class _SchemaMismatch(Exception):
    path: tuple[str | int, ...]
    message: str


_SCHEMA_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_SCHEMA_KEYWORDS = {
    "$ref", "$defs", "type", "enum", "const", "pattern", "format",
    "minLength", "minimum", "required", "properties",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "allOf", "anyOf", "oneOf", "if", "then", "else",
}


def _check_schema_node(node: Any, root: Mapping[str, Any], location: str) -> None:
    if not isinstance(node, dict):
        raise ArtifactValidationError(f"schema definition at {location} must be an object")
    unknown = set(node) - _SCHEMA_ANNOTATIONS - _SCHEMA_KEYWORDS
    if unknown:
        raise ArtifactValidationError(
            f"unsupported schema keyword at {location}: {sorted(unknown)[0]}"
        )
    reference = node.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ArtifactValidationError(f"schema reference at {location} must be local")
        _resolve_schema_reference(root, reference)
    definitions = node.get("$defs", {})
    if not isinstance(definitions, dict):
        raise ArtifactValidationError(f"$defs at {location} must be an object")
    for name, child in definitions.items():
        _check_schema_node(child, root, f"{location}.$defs.{name}")
    properties = node.get("properties", {})
    if not isinstance(properties, dict):
        raise ArtifactValidationError(f"properties at {location} must be an object")
    for name, child in properties.items():
        _check_schema_node(child, root, f"{location}.properties.{name}")
    items = node.get("items")
    if items is not None:
        _check_schema_node(items, root, f"{location}.items")
    additional = node.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        _check_schema_node(additional, root, f"{location}.additionalProperties")
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = node.get(keyword, [])
        if not isinstance(branches, list):
            raise ArtifactValidationError(f"{keyword} at {location} must be an array")
        for index, child in enumerate(branches):
            _check_schema_node(child, root, f"{location}.{keyword}[{index}]")
    for keyword in ("if", "then", "else"):
        child = node.get(keyword)
        if child is not None:
            _check_schema_node(child, root, f"{location}.{keyword}")


def _resolve_schema_reference(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    current: Any = root
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ArtifactValidationError(f"unresolved local schema reference: {reference}")
        current = current[part]
    if not isinstance(current, dict):
        raise ArtifactValidationError(f"schema reference does not target an object: {reference}")
    return current


def _validate_schema_node(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> None:
    reference = schema.get("$ref")
    if reference is not None:
        _validate_schema_node(value, _resolve_schema_reference(root, reference), root, path)

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise _SchemaMismatch(path, f"must equal {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        raise _SchemaMismatch(path, f"must be one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_json_type(value, name) for name in expected_types):
            raise _SchemaMismatch(path, f"must have JSON type {' or '.join(expected_types)}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise _SchemaMismatch(path, "must not be empty")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise _SchemaMismatch(path, f"does not match pattern {pattern!r}")
        if schema.get("format") == "date-time":
            try:
                _require_timestamp(value, "timestamp")
            except ArtifactValidationError as error:
                raise _SchemaMismatch(path, str(error)) from None

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise _SchemaMismatch(path, f"must be at least {schema['minimum']}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise _SchemaMismatch(path, f"must contain at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise _SchemaMismatch(path, f"must contain at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            encoded = [canonical_json(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise _SchemaMismatch(path, "must contain unique items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, root, (*path, index))

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise _SchemaMismatch((*path, name), "is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                name = sorted(extras)[0]
                raise _SchemaMismatch((*path, name), "is not an allowed property")
        additional = schema.get("additionalProperties")
        for name, child in value.items():
            if name in properties:
                _validate_schema_node(child, properties[name], root, (*path, name))
            elif isinstance(additional, dict):
                _validate_schema_node(child, additional, root, (*path, name))

    for branch in schema.get("allOf", []):
        _validate_schema_node(value, branch, root, path)
    if "anyOf" in schema and not any(
        _schema_branch_matches(value, branch, root, path) for branch in schema["anyOf"]
    ):
        raise _SchemaMismatch(path, "does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(
            _schema_branch_matches(value, branch, root, path) for branch in schema["oneOf"]
        )
        if matches != 1:
            raise _SchemaMismatch(path, "must match exactly one allowed schema")
    condition = schema.get("if")
    if condition is not None:
        keyword = "then" if _schema_branch_matches(value, condition, root, path) else "else"
        if keyword in schema:
            _validate_schema_node(value, schema[keyword], root, path)


def _schema_branch_matches(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> bool:
    try:
        _validate_schema_node(value, schema, root, path)
    except _SchemaMismatch:
        return False
    return True


def _matches_json_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _payload_from_dict(record_type: RecordType, raw: Mapping[str, Any]) -> ArtifactPayload:
    data = dict(raw)
    if record_type is RecordType.TASK:
        return TaskPayload(data["target_branch"], tuple(data["scope_paths"]), data["assignment_sha256"])
    if record_type is RecordType.PLAN:
        slices = tuple(SliceSpec(item["slice_id"], item["summary"], tuple(item["paths"])) for item in data["slices"])
        return PlanPayload(data["work_plan_path"], data["approved_plan_commit"], slices)
    if record_type is RecordType.WORK_UNIT:
        return WorkUnitPayload(data["slice_id"], data["round_number"], tuple(data["paths"]))
    if record_type is RecordType.CORRECTION_WORK_UNIT:
        return CorrectionWorkUnitPayload(data["slice_id"], data["round_number"], tuple(data["paths"]), tuple(data["finding_ids"]))
    if record_type is RecordType.AGENT_RESULT:
        return AgentResultPayload(Role(data["role"]), data["work_unit_id"], data["outcome"], tuple(data["test_files"]))
    if record_type is RecordType.DIAGNOSTIC:
        return DiagnosticPayload(Role(data["role"]), data["work_unit_id"], data["attempt"], data["output_sha256"], data["reason"])
    if record_type is RecordType.REVIEW:
        return ReviewPayload(Role(data["reviewer"]), data["work_unit_id"], data["verdict"], tuple(data["finding_ids"]), data["evidence"])
    if record_type is RecordType.FINDING_TRANSITION:
        return FindingTransitionPayload(data["finding_id"], Role(data["reporter"]), Role(data["actor"]), data["action"], FindingSeverity(data["severity"]), data["finding_status"], data["rationale"])
    if record_type is RecordType.VALIDATION_REQUEST:
        commands = tuple(CommandSpec(item["family"], tuple(item["argv"]), item["mode"]) for item in data["commands"])
        return ValidationRequestPayload(commands, Role(data["requested_by"]))
    if record_type is RecordType.VALIDATION_ATTESTATION:
        results = tuple(
            ValidationResult(CommandSpec(item["command"]["family"], tuple(item["command"]["argv"]), item["command"]["mode"]), item["outcome"], item["exit_code"], item["output_sha256"])
            for item in data["results"]
        )
        return ValidationAttestationPayload(results, Role(data["attested_by"]))
    if record_type is RecordType.GATE:
        return GatePayload(data["gate_kind"], data["decision"], Role(data["authority"]), data["rationale"])
    if record_type is RecordType.BINDING:
        return BindingPayload(data["binding_kind"], data["target"], data["attestation_id"], tuple(data["approval_ids"]))
    if record_type is RecordType.QUOTA_PAUSE:
        return QuotaPausePayload(Role(data["role"]), data["repository_fingerprint"], data["retry_at"])
    if record_type is RecordType.RESUME_CHECK:
        return ResumeCheckPayload(data["expected_head_id"], data["repository_fingerprint"], data["outcome"])
    if record_type is RecordType.WORKFLOW_COMPLETION:
        return WorkflowCompletionPayload(data["outcome"], data["final_binding_id"])
    raise ArtifactValidationError(f"unsupported record_type: {record_type}")


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _json_value(asdict(value))
    return value


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(f"{name} must be a non-empty string")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} is not a canonical identifier")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a lowercase SHA-256 digest")


def _require_positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArtifactValidationError(f"{name} must be a positive integer")


def _require_timestamp(value: str, name: str) -> None:
    _require_text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactValidationError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ArtifactValidationError(f"{name} must include a timezone")


def _require_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ArtifactValidationError("paths must be non-empty POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactValidationError(f"path is not canonical repository-relative POSIX: {value!r}")


def _require_paths(paths: Sequence[str], *, allow_empty: bool = False) -> None:
    if isinstance(paths, (str, bytes)) or (not paths and not allow_empty):
        raise ArtifactValidationError("paths must be a JSON-style list, not Markdown or text")
    for path in paths:
        _require_path(path)
    if len(paths) != len(set(paths)):
        raise ArtifactValidationError("paths must be unique")


def _require_unique_identifiers(
    values: Sequence[str], name: str, *, allow_empty: bool = False,
) -> None:
    if isinstance(values, (str, bytes)) or (not values and not allow_empty):
        raise ArtifactValidationError(f"{name} must be a non-empty list")
    for value in values:
        _require_identifier(value, name)
    if len(values) != len(set(values)):
        raise ArtifactValidationError(f"{name} must contain unique values")
