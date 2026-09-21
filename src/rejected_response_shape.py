"""Provider-free structural evidence for rejected native responses.

The extractor in this module deliberately copies no open-ended response value.
It retains only repository-owned field names and enum values, canonical Finding
and responsibility identifiers, bounded counts, and boolean release decisions.
Provider-authored prose, paths, summaries, rationales, and evidence never enter
the returned model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping


_FINDING_ID_RE = re.compile(r"^C-(0[1-9]|[1-9][0-9]*)$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_KNOWN_FIELDS = frozenset(
    {
        "anchors",
        "decision",
        "finding_dispositions",
        "new_findings",
        "occurrences",
        "plan_completion",
        "plan_treatment_decisions",
        "plan_treatments",
        "pre_mortem",
        "rationale",
        "reclassifications",
        "ready",
        "remediation_paths",
        "request_id",
        "responsibility_routes",
        "result_type",
        "review_evidence",
        "reviewer",
        "rule_id",
        "scan_complete",
        "schema_version",
        "slice_plan",
        "status_changes",
        "test_files",
    }
)
_VALUE_KINDS = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_RESULT_TYPES = frozenset(
    {
        "branch_discovery_completed",
        "correction_result",
        "implementation_result",
        "plan_result",
        "review_result",
        "stop_request",
        "stop_result",
    }
)
_RELEASE_DECISIONS = frozenset(
    {
        "approved",
        "denied",
        "not_ready",
        "ready",
        "scan_complete",
        "scan_incomplete",
    }
)
_FINDING_DECISIONS = frozenset({"accepted", "rejected"})
_FINDING_STATUSES = frozenset({"CLOSED", "OPEN"})
_FINDING_CLASSES = frozenset({"BLOCKER", "OBSERVATION"})
_CLOSURE_KINDS = frozenset({"fixed", "partial", "rejected"})
_REJECTION_REASONS = frozenset(
    {"already_fixed", "no_defect", "out_of_scope"}
)
_RESPONSIBILITY_KINDS = frozenset(
    {"BRANCH_PLANNING", "PLAN_REVISION", "SLICE"}
)
_PLAN_TREATMENT_KINDS = frozenset({"implementation", "no_code"})
_PLAN_TREATMENT_DECISIONS = frozenset({"accepted", "rejected"})


def _require_enum(value: object, allowed: frozenset[str], label: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} is not in its closed vocabulary")


def _require_optional_enum(
    value: object, allowed: frozenset[str], label: str
) -> None:
    if value is not None:
        _require_enum(value, allowed, label)


def _require_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical identifier")


def _require_finding_id(value: object, label: str) -> None:
    if not isinstance(value, str) or _FINDING_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical Finding ID")


def _require_positive(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RejectedResponseFieldShape:
    name: str
    value_kind: str
    item_count: int | None = None

    def __post_init__(self) -> None:
        _require_enum(self.name, _KNOWN_FIELDS, "rejected response field name")
        _require_enum(
            self.value_kind, _VALUE_KINDS, "rejected response field value kind"
        )
        if self.value_kind == "array":
            if (
                isinstance(self.item_count, bool)
                or not isinstance(self.item_count, int)
                or self.item_count < 0
            ):
                raise ValueError(
                    "rejected response array field requires a non-negative item count"
                )
        elif self.item_count is not None:
            raise ValueError(
                "rejected response non-array field cannot carry an item count"
            )


@dataclass(frozen=True, slots=True)
class RejectedResponseTargetShape:
    responsibility_kind: str
    target_run_id: str | None = None
    slice_id: str | None = None
    family_id: str | None = None
    cycle_number: int | None = None
    run_id: str | None = None
    revision: int | None = None

    def __post_init__(self) -> None:
        _require_enum(
            self.responsibility_kind,
            _RESPONSIBILITY_KINDS,
            "rejected response responsibility kind",
        )
        if self.responsibility_kind == "SLICE":
            _require_identifier(self.target_run_id, "SLICE target_run_id")
            _require_identifier(self.slice_id, "SLICE slice_id")
            if any(
                value is not None
                for value in (
                    self.family_id,
                    self.cycle_number,
                    self.run_id,
                    self.revision,
                )
            ):
                raise ValueError("SLICE target carries foreign target fields")
            return
        if self.responsibility_kind == "BRANCH_PLANNING":
            _require_identifier(self.family_id, "BRANCH_PLANNING family_id")
            _require_positive(self.cycle_number, "BRANCH_PLANNING cycle_number")
            if any(
                value is not None
                for value in (
                    self.target_run_id,
                    self.slice_id,
                    self.run_id,
                    self.revision,
                )
            ):
                raise ValueError("BRANCH_PLANNING target carries foreign target fields")
            return
        _require_identifier(self.run_id, "PLAN_REVISION run_id")
        _require_positive(self.revision, "PLAN_REVISION revision")
        if any(
            value is not None
            for value in (
                self.target_run_id,
                self.slice_id,
                self.family_id,
                self.cycle_number,
            )
        ):
            raise ValueError("PLAN_REVISION target carries foreign target fields")


@dataclass(frozen=True, slots=True)
class RejectedFindingDispositionShape:
    finding_id: str
    decision: str | None
    responsibility_proposal: RejectedResponseTargetShape | None = None

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "rejected disposition finding_id")
        _require_optional_enum(
            self.decision,
            _FINDING_DECISIONS,
            "rejected disposition decision",
        )
        if self.responsibility_proposal is not None and not isinstance(
            self.responsibility_proposal, RejectedResponseTargetShape
        ):
            raise ValueError("rejected disposition proposal must be typed")


@dataclass(frozen=True, slots=True)
class RejectedStatusChangeShape:
    finding_id: str
    status: str | None
    closure_kind: str | None
    rejection_reason: str | None

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "rejected status change finding_id")
        _require_optional_enum(
            self.status, _FINDING_STATUSES, "rejected status change status"
        )
        _require_optional_enum(
            self.closure_kind,
            _CLOSURE_KINDS,
            "rejected status change closure kind",
        )
        _require_optional_enum(
            self.rejection_reason,
            _REJECTION_REASONS,
            "rejected status change rejection reason",
        )
        if self.rejection_reason is not None and self.closure_kind != "rejected":
            raise ValueError(
                "rejected status change reason requires a rejected closure"
            )


@dataclass(frozen=True, slots=True)
class RejectedReclassificationShape:
    finding_id: str
    finding_class: str | None

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "reclassification finding_id")
        _require_optional_enum(
            self.finding_class,
            _FINDING_CLASSES,
            "reclassification finding class",
        )


@dataclass(frozen=True, slots=True)
class RejectedResponsibilityRouteShape:
    finding_id: str
    target: RejectedResponseTargetShape | None

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "responsibility route finding_id")
        if self.target is not None and not isinstance(
            self.target, RejectedResponseTargetShape
        ):
            raise ValueError("responsibility route target must be typed")


@dataclass(frozen=True, slots=True)
class RejectedPlanTreatmentShape:
    signature: str | None
    finding_ids: tuple[str, ...]
    treatment_kind: str | None
    closing_slice_ids: tuple[int, ...]
    rejection_reason: str | None

    def __post_init__(self) -> None:
        if self.signature is not None and _SHA256_RE.fullmatch(self.signature) is None:
            raise ValueError("plan treatment signature must be a lowercase SHA-256")
        for finding_id in self.finding_ids:
            _require_finding_id(finding_id, "plan treatment finding_id")
        _require_optional_enum(
            self.treatment_kind,
            _PLAN_TREATMENT_KINDS,
            "plan treatment kind",
        )
        for slice_id in self.closing_slice_ids:
            _require_positive(slice_id, "plan treatment closing_slice_id")
        _require_optional_enum(
            self.rejection_reason,
            _REJECTION_REASONS,
            "plan treatment rejection reason",
        )


@dataclass(frozen=True, slots=True)
class RejectedPlanTreatmentDecisionShape:
    signature: str
    decision: str | None

    def __post_init__(self) -> None:
        if _SHA256_RE.fullmatch(self.signature) is None:
            raise ValueError(
                "plan treatment decision signature must be a lowercase SHA-256"
            )
        _require_optional_enum(
            self.decision,
            _PLAN_TREATMENT_DECISIONS,
            "plan treatment decision",
        )


@dataclass(frozen=True, slots=True)
class RejectedNativeResponseShape:
    fields: tuple[RejectedResponseFieldShape, ...]
    unknown_field_count: int
    result_type: str | None
    release_decision: str | None
    finding_dispositions: tuple[RejectedFindingDispositionShape, ...]
    status_changes: tuple[RejectedStatusChangeShape, ...]
    reclassifications: tuple[RejectedReclassificationShape, ...]
    responsibility_routes: tuple[RejectedResponsibilityRouteShape, ...]
    plan_treatments: tuple[RejectedPlanTreatmentShape, ...]
    plan_treatment_decisions: tuple[RejectedPlanTreatmentDecisionShape, ...]

    def __post_init__(self) -> None:
        if (
            tuple(sorted(item.name for item in self.fields))
            != tuple(item.name for item in self.fields)
            or len({item.name for item in self.fields}) != len(self.fields)
        ):
            raise ValueError(
                "rejected response fields must be sorted and unique"
            )
        if (
            isinstance(self.unknown_field_count, bool)
            or not isinstance(self.unknown_field_count, int)
            or self.unknown_field_count < 0
        ):
            raise ValueError(
                "rejected response unknown_field_count must be non-negative"
            )
        if not self.fields and self.unknown_field_count == 0:
            raise ValueError("rejected response shape must describe at least one field")
        _require_optional_enum(
            self.result_type, _RESULT_TYPES, "rejected response result_type"
        )
        _require_optional_enum(
            self.release_decision,
            _RELEASE_DECISIONS,
            "rejected response release decision",
        )


def _value_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _safe_enum(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _safe_identifier(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and _IDENTIFIER_RE.fullmatch(value) is not None
        else None
    )


def _safe_finding_id(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and _FINDING_ID_RE.fullmatch(value) is not None
        else None
    )


def _safe_positive(value: object) -> int | None:
    return value if not isinstance(value, bool) and isinstance(value, int) and value > 0 else None


def _target_shape(raw: object) -> RejectedResponseTargetShape | None:
    if not isinstance(raw, Mapping):
        return None
    kind = _safe_enum(raw.get("responsibility_kind"), _RESPONSIBILITY_KINDS)
    try:
        if kind == "SLICE":
            target_run_id = _safe_identifier(raw.get("target_run_id"))
            slice_id = _safe_identifier(raw.get("slice_id"))
            if target_run_id is None or slice_id is None:
                return None
            return RejectedResponseTargetShape(
                kind, target_run_id=target_run_id, slice_id=slice_id
            )
        if kind == "BRANCH_PLANNING":
            family_id = _safe_identifier(raw.get("family_id"))
            cycle_number = _safe_positive(raw.get("cycle_number"))
            if family_id is None or cycle_number is None:
                return None
            return RejectedResponseTargetShape(
                kind, family_id=family_id, cycle_number=cycle_number
            )
        if kind == "PLAN_REVISION":
            run_id = _safe_identifier(raw.get("run_id"))
            revision = _safe_positive(raw.get("revision"))
            if run_id is None or revision is None:
                return None
            return RejectedResponseTargetShape(
                kind, run_id=run_id, revision=revision
            )
    except ValueError:
        return None
    return None


def _mapping_items(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def extract_rejected_native_response_shape(
    document: Mapping[str, object] | None,
) -> RejectedNativeResponseShape | None:
    """Reduce one rejected response to closed, provider-free structure."""

    if not isinstance(document, Mapping):
        return None
    fields = tuple(
        RejectedResponseFieldShape(
            name,
            _value_kind(document[name]),
            len(document[name]) if isinstance(document[name], list) else None,
        )
        for name in sorted(set(document) & _KNOWN_FIELDS)
    )
    unknown_field_count = len(set(document) - _KNOWN_FIELDS)
    if not fields and unknown_field_count == 0:
        return None

    release_decision = _safe_enum(document.get("decision"), frozenset({"approved", "denied"}))
    if release_decision is None and isinstance(document.get("ready"), bool):
        release_decision = "ready" if document["ready"] else "not_ready"
    if release_decision is None and isinstance(document.get("scan_complete"), bool):
        release_decision = (
            "scan_complete" if document["scan_complete"] else "scan_incomplete"
        )

    finding_dispositions: list[RejectedFindingDispositionShape] = []
    for item in _mapping_items(document.get("finding_dispositions")):
        finding_id = _safe_finding_id(item.get("finding_id"))
        if finding_id is None:
            continue
        finding_dispositions.append(
            RejectedFindingDispositionShape(
                finding_id,
                _safe_enum(item.get("decision"), _FINDING_DECISIONS),
                _target_shape(item.get("responsibility_proposal")),
            )
        )

    status_changes: list[RejectedStatusChangeShape] = []
    for item in _mapping_items(document.get("status_changes")):
        finding_id = _safe_finding_id(item.get("finding_id"))
        if finding_id is None:
            continue
        closure = item.get("closure")
        closure_mapping = closure if isinstance(closure, Mapping) else {}
        closure_kind = _safe_enum(closure_mapping.get("kind"), _CLOSURE_KINDS)
        rejection_reason = (
            _safe_enum(closure_mapping.get("rejection_reason"), _REJECTION_REASONS)
            if closure_kind == "rejected"
            else None
        )
        status_changes.append(
            RejectedStatusChangeShape(
                finding_id,
                _safe_enum(item.get("status"), _FINDING_STATUSES),
                closure_kind,
                rejection_reason,
            )
        )

    reclassifications: list[RejectedReclassificationShape] = []
    for item in _mapping_items(document.get("reclassifications")):
        finding_id = _safe_finding_id(item.get("finding_id"))
        if finding_id is not None:
            reclassifications.append(
                RejectedReclassificationShape(
                    finding_id,
                    _safe_enum(item.get("finding_class"), _FINDING_CLASSES),
                )
            )

    responsibility_routes: list[RejectedResponsibilityRouteShape] = []
    for item in _mapping_items(document.get("responsibility_routes")):
        finding_id = _safe_finding_id(item.get("finding_id"))
        if finding_id is not None:
            responsibility_routes.append(
                RejectedResponsibilityRouteShape(
                    finding_id, _target_shape(item.get("responsibility"))
                )
            )

    plan_treatments: list[RejectedPlanTreatmentShape] = []
    for item in _mapping_items(document.get("plan_treatments")):
        signature = item.get("signature")
        safe_signature = (
            signature
            if isinstance(signature, str) and _SHA256_RE.fullmatch(signature)
            else None
        )
        raw_finding_ids = item.get("finding_ids")
        finding_ids = (
            tuple(
                finding_id
                for value in raw_finding_ids
                if (finding_id := _safe_finding_id(value)) is not None
            )
            if isinstance(raw_finding_ids, list)
            else ()
        )
        raw_closing_slice_ids = item.get("closing_slice_ids")
        closing_slice_ids = (
            tuple(
                slice_id
                for value in raw_closing_slice_ids
                if (slice_id := _safe_positive(value)) is not None
            )
            if isinstance(raw_closing_slice_ids, list)
            else ()
        )
        treatment_kind = _safe_enum(
            item.get("treatment_kind"), _PLAN_TREATMENT_KINDS
        )
        rejection_reason = _safe_enum(
            item.get("no_code_reason"), _REJECTION_REASONS
        )
        if any(
            (
                safe_signature is not None,
                bool(finding_ids),
                treatment_kind is not None,
                bool(closing_slice_ids),
                rejection_reason is not None,
            )
        ):
            plan_treatments.append(
                RejectedPlanTreatmentShape(
                    safe_signature,
                    finding_ids,
                    treatment_kind,
                    closing_slice_ids,
                    rejection_reason,
                )
            )

    plan_treatment_decisions: list[RejectedPlanTreatmentDecisionShape] = []
    for item in _mapping_items(document.get("plan_treatment_decisions")):
        signature = item.get("signature")
        if not isinstance(signature, str) or _SHA256_RE.fullmatch(signature) is None:
            continue
        plan_treatment_decisions.append(
            RejectedPlanTreatmentDecisionShape(
                signature,
                _safe_enum(item.get("decision"), _PLAN_TREATMENT_DECISIONS),
            )
        )

    return RejectedNativeResponseShape(
        fields=fields,
        unknown_field_count=unknown_field_count,
        result_type=_safe_enum(document.get("result_type"), _RESULT_TYPES),
        release_decision=release_decision,
        finding_dispositions=tuple(finding_dispositions),
        status_changes=tuple(status_changes),
        reclassifications=tuple(reclassifications),
        responsibility_routes=tuple(responsibility_routes),
        plan_treatments=tuple(plan_treatments),
        plan_treatment_decisions=tuple(plan_treatment_decisions),
    )


def rejected_native_response_shape_document(
    shape: RejectedNativeResponseShape,
) -> dict[str, Any]:
    """Return the exact JSON-compatible persistence document."""

    if not isinstance(shape, RejectedNativeResponseShape):
        raise ValueError("rejected response shape must be typed")
    def json_value(value: object) -> Any:
        if isinstance(value, Mapping):
            return {str(key): json_value(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_value(child) for child in value]
        return value

    document = json_value(asdict(shape))
    assert isinstance(document, dict)
    return document


def rejected_native_response_shape_from_document(
    raw: Mapping[str, Any],
) -> RejectedNativeResponseShape:
    """Parse the closed persistence document without accepting extra fields."""

    required = {
        "fields",
        "unknown_field_count",
        "result_type",
        "release_decision",
        "finding_dispositions",
        "status_changes",
        "reclassifications",
        "responsibility_routes",
        "plan_treatments",
        "plan_treatment_decisions",
    }
    if set(raw) != required:
        raise ValueError("rejected response shape fields differ from its contract")

    def mappings(name: str) -> tuple[Mapping[str, Any], ...]:
        value = raw[name]
        if not isinstance(value, list):
            raise ValueError(f"rejected response shape {name} must be an array")
        if any(not isinstance(item, Mapping) for item in value):
            raise ValueError(f"rejected response shape {name} entries must be objects")
        return tuple(value)

    def exact(item: Mapping[str, Any], keys: set[str], label: str) -> None:
        if set(item) != keys:
            raise ValueError(f"{label} fields differ from its contract")

    def target(value: object) -> RejectedResponseTargetShape | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("rejected response target must be an object or null")
        target_keys = {
            "responsibility_kind",
            "target_run_id",
            "slice_id",
            "family_id",
            "cycle_number",
            "run_id",
            "revision",
        }
        exact(value, target_keys, "rejected response target")
        return RejectedResponseTargetShape(**value)

    fields: list[RejectedResponseFieldShape] = []
    for item in mappings("fields"):
        exact(item, {"name", "value_kind", "item_count"}, "response field shape")
        fields.append(RejectedResponseFieldShape(**item))

    dispositions: list[RejectedFindingDispositionShape] = []
    for item in mappings("finding_dispositions"):
        exact(
            item,
            {"finding_id", "decision", "responsibility_proposal"},
            "finding disposition shape",
        )
        dispositions.append(
            RejectedFindingDispositionShape(
                item["finding_id"],
                item["decision"],
                target(item["responsibility_proposal"]),
            )
        )

    statuses: list[RejectedStatusChangeShape] = []
    for item in mappings("status_changes"):
        exact(
            item,
            {"finding_id", "status", "closure_kind", "rejection_reason"},
            "status change shape",
        )
        statuses.append(RejectedStatusChangeShape(**item))

    reclassifications: list[RejectedReclassificationShape] = []
    for item in mappings("reclassifications"):
        exact(item, {"finding_id", "finding_class"}, "reclassification shape")
        reclassifications.append(RejectedReclassificationShape(**item))

    routes: list[RejectedResponsibilityRouteShape] = []
    for item in mappings("responsibility_routes"):
        exact(item, {"finding_id", "target"}, "responsibility route shape")
        routes.append(
            RejectedResponsibilityRouteShape(
                item["finding_id"], target(item["target"])
            )
        )

    treatments: list[RejectedPlanTreatmentShape] = []
    for item in mappings("plan_treatments"):
        exact(
            item,
            {
                "signature",
                "finding_ids",
                "treatment_kind",
                "closing_slice_ids",
                "rejection_reason",
            },
            "plan treatment shape",
        )
        if not isinstance(item["finding_ids"], list) or not isinstance(
            item["closing_slice_ids"], list
        ):
            raise ValueError("plan treatment shape identifiers must be arrays")
        treatments.append(
            RejectedPlanTreatmentShape(
                item["signature"],
                tuple(item["finding_ids"]),
                item["treatment_kind"],
                tuple(item["closing_slice_ids"]),
                item["rejection_reason"],
            )
        )

    treatment_decisions: list[RejectedPlanTreatmentDecisionShape] = []
    for item in mappings("plan_treatment_decisions"):
        exact(item, {"signature", "decision"}, "plan treatment decision shape")
        treatment_decisions.append(RejectedPlanTreatmentDecisionShape(**item))

    return RejectedNativeResponseShape(
        fields=tuple(fields),
        unknown_field_count=raw["unknown_field_count"],
        result_type=raw["result_type"],
        release_decision=raw["release_decision"],
        finding_dispositions=tuple(dispositions),
        status_changes=tuple(statuses),
        reclassifications=tuple(reclassifications),
        responsibility_routes=tuple(routes),
        plan_treatments=tuple(treatments),
        plan_treatment_decisions=tuple(treatment_decisions),
    )


__all__ = [
    "RejectedFindingDispositionShape",
    "RejectedNativeResponseShape",
    "RejectedPlanTreatmentDecisionShape",
    "RejectedPlanTreatmentShape",
    "RejectedReclassificationShape",
    "RejectedResponseFieldShape",
    "RejectedResponseTargetShape",
    "RejectedResponsibilityRouteShape",
    "RejectedStatusChangeShape",
    "extract_rejected_native_response_shape",
    "rejected_native_response_shape_document",
    "rejected_native_response_shape_from_document",
]
