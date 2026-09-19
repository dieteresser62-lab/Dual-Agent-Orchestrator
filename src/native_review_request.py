"""Closed, canonical native Claude review requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from contracts import AgentRole, ApprovalMarker
from finding_responsibility import responsibility_json_schema
import native_finding_decisions
from native_review_contract import (
    BoundNativeReviewContext,
    MAX_BRANCH_DISCOVERY_NEW_FINDINGS,
    MAX_NATIVE_REVIEW_DISPOSITIONS,
    NativeReviewContext,
    NativeReviewErrorCode,
    NATIVE_REVIEW_RESPONSE_RETRY_CODES,
    native_review_disposition_capacity,
    native_review_provider_response_schema,
    next_native_finding_id,
    native_review_context_binding,
)
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    describe_one_of_failure,
    validate_schema_document,
)
from review_packets import ReviewPacket, ReviewPacketError


REQUEST_SCHEMA_VERSION = "native-agent-review-request-v2"
RESPONSE_SCHEMA_VERSION = "native-agent-review-result-v2"
CLAUDE_REVIEW_TRANSPORT = "native-claude-review-v2"
PERSISTENCE_PROTOCOL = "structured-v2"
DEFAULT_INLINE_EVIDENCE_CHARS = 24_000
PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND = "provider_input_boundary_notice"
REQUEST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-review-request-v2.schema.json"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
REQUEST_ID_PATTERN = re.compile(r"native-review-request-[0-9a-f]{64}")
SAFE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,199}")
INVOCATION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")


class NativeReviewRequestErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    EVIDENCE_INVALID = "evidence-invalid"
    REQUEST_INVALID = "request-invalid"


class NativeReviewRequestError(ValueError):
    def __init__(self, code: NativeReviewRequestErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


class NativeReviewKind(StrEnum):
    PLAN = "plan"
    SLICE = "slice"
    BRANCH_DISCOVERY = "branch_discovery"


@dataclass(frozen=True, slots=True)
class NativeReviewEvidenceInput:
    evidence_id: str
    kind: str
    content: str
    source_path: str | None = None
    semantic_digest: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence_id")
        _require_text(self.kind, "evidence kind", maximum=100)
        _require_text(self.content, "evidence content", maximum=4_000_000)
        if self.source_path is not None:
            _require_repository_path(self.source_path, "evidence source_path")
        if self.kind == "canonical_review_packet":
            _validate_review_packet_semantic_digest(self.content, self.semantic_digest)
        elif self.semantic_digest is not None:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "semantic_digest is reserved for canonical review packet evidence",
            )


@dataclass(frozen=True, slots=True)
class NativeReviewEvidenceAsset:
    path: str
    sha256: str
    byte_count: int
    content: str

    def __post_init__(self) -> None:
        _require_repository_path(self.path, "evidence asset path")
        _require_sha256(self.sha256, "evidence asset sha256")
        if self.byte_count < 1:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "evidence asset byte_count must be positive",
            )
        if len(self.content.encode("utf-8")) != self.byte_count:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "evidence asset byte_count differs from UTF-8 content",
            )
        if _sha256_text(self.content) != self.sha256:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "evidence asset digest differs from content",
            )


@dataclass(frozen=True, slots=True)
class NativeReviewRetryFeedback:
    prior_invocation_id: str
    rejection_code: NativeReviewErrorCode
    correction_instruction: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.prior_invocation_id, str)
            or INVOCATION_ID_PATTERN.fullmatch(self.prior_invocation_id) is None
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "retry feedback prior_invocation_id is not a safe identifier",
            )
        if self.rejection_code not in NATIVE_REVIEW_RESPONSE_RETRY_CODES:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "retry feedback requires a response-dependent rejection code",
            )
        _require_text(
            self.correction_instruction,
            "retry feedback correction_instruction",
            maximum=3000,
        )


@dataclass(frozen=True, slots=True)
class NativeReviewRequestSpec:
    context: NativeReviewContext
    review_kind: NativeReviewKind
    target_branch: str
    base_commit: str
    authorized_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    evidence: tuple[NativeReviewEvidenceInput, ...]
    retry_feedback: NativeReviewRetryFeedback | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, NativeReviewContext):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "request spec requires NativeReviewContext",
            )
        if self.context.reviewer is not AgentRole.CLAUDE:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "native Claude request requires reviewer=claude",
            )
        if not isinstance(self.review_kind, NativeReviewKind):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "review_kind is invalid",
            )
        _require_text(self.target_branch, "target_branch", maximum=300)
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_commit):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "base_commit must be a lowercase 40-character Git SHA",
            )
        _require_sorted_paths(self.authorized_paths, "authorized_paths")
        _require_unique_texts(
            self.acceptance_criteria,
            "acceptance_criteria",
            allow_empty=False,
            maximum=3000,
        )
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if evidence_ids != tuple(sorted(set(evidence_ids))) or not evidence_ids:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "evidence must be non-empty, sorted, and unique by evidence_id",
            )
        _require_distinct_evidence(self.evidence)
        if self.retry_feedback is not None and not isinstance(
            self.retry_feedback, NativeReviewRetryFeedback
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                "retry_feedback must be a NativeReviewRetryFeedback",
            )
        expected_operation = {
            NativeReviewKind.PLAN: "claude_plan_review",
            NativeReviewKind.SLICE: "claude_slice_review",
            NativeReviewKind.BRANCH_DISCOVERY: "claude_branch_discovery",  # allowlist:provider -- canonical operation
        }[self.review_kind]
        if self.context.operation != expected_operation:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.CONTEXT_INVALID,
                f"{self.review_kind.value} review requires operation {expected_operation}",
            )


@dataclass(frozen=True, slots=True)
class NativeReviewRequestBundle:
    canonical_json: str
    bound_context: BoundNativeReviewContext
    provider_response_schema_json: str
    evidence_assets: tuple[NativeReviewEvidenceAsset, ...] = ()

    def __post_init__(self) -> None:
        document = self.document
        validate_native_review_request_document(document)
        canonical = canonical_native_review_request_json(document)
        if self.canonical_json != canonical:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request bundle canonical_json is not canonical",
            )
        if document["request_id"] != self.bound_context.request_id:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request document and bound context ids differ",
            )
        binding = {key: value for key, value in document.items() if key != "request_id"}
        calculated_digest = hashlib.sha256(
            _canonical_json(binding).encode("utf-8")
        ).hexdigest()
        if (
            calculated_digest != self.bound_context.request_digest
            or document["request_id"]
            != f"native-review-request-{calculated_digest}"
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request content differs from its bound digest",
            )
        if document["request_type"] != "review_request":
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "only regular native review requests are supported",
            )
        expected_context = _review_context_request_projection(
            self.bound_context.context
        )
        actual_context = {key: document[key] for key in expected_context}
        if actual_context != expected_context:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request document differs from its bound review context",
            )
        try:
            provider_schema = json.loads(self.provider_response_schema_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema is invalid JSON",
            ) from exc
        if (
            not isinstance(provider_schema, dict)
            or self.provider_response_schema_json != _canonical_json(provider_schema)
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema is not a canonical object",
            )
        expected_schema = native_review_provider_response_schema(
            self.bound_context.context
        )
        if provider_schema != expected_schema:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema differs from bound context",
            )
        expected_response_schema_digest = hashlib.sha256(
            self.provider_response_schema_json.encode("utf-8")
        ).hexdigest()
        if document["response_contract"] != {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "schema_sha256": expected_response_schema_digest,
        }:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request response contract differs from provider schema",
            )
        expected_assets = {
            item["content_ref"]: (item["sha256"], item["byte_count"])
            for item in document.get("evidence_manifest", ())
            if item["delivery"] == "content_ref"
        }
        manifest = tuple(document.get("evidence_manifest", ()))
        evidence_ids = tuple(item["evidence_id"] for item in manifest)
        if evidence_ids != tuple(sorted(set(evidence_ids))):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "request evidence manifest must be sorted and unique by evidence_id",
            )
        for item in manifest:
            if item["delivery"] == "inline":
                content = item["content"]
                if (
                    _sha256_text(content) != item["sha256"]
                    or len(content.encode("utf-8")) != item["byte_count"]
                ):
                    raise NativeReviewRequestError(
                        NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                        f"inline evidence {item['evidence_id']} metadata differs from content",
                    )
                _validate_manifest_semantic_binding(item, content)
        if len(expected_assets) != sum(
            item["delivery"] == "content_ref" for item in manifest
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "request content references must be unique",
            )
        actual_assets = {
            item.path: (item.sha256, item.byte_count) for item in self.evidence_assets
        }
        if len(actual_assets) != len(self.evidence_assets):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "request evidence asset paths must be unique",
            )
        if actual_assets != expected_assets:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "request evidence assets differ from content references",
            )
        asset_content = {item.path: item.content for item in self.evidence_assets}
        for item in manifest:
            if item["delivery"] == "content_ref":
                _validate_manifest_semantic_binding(item, asset_content[item["content_ref"]])

    @property
    def document(self) -> dict[str, Any]:
        """Return a detached JSON object backed by immutable canonical bytes."""
        try:
            parsed = json.loads(self.canonical_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "canonical request must be valid JSON",
            ) from exc
        if not isinstance(parsed, dict):  # pragma: no cover - guarded in __post_init__
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "canonical request must decode to an object",
            )
        return parsed

    @property
    def provider_response_schema(self) -> dict[str, Any]:
        """Return a detached view of the immutable provider-schema bytes."""
        parsed = json.loads(self.provider_response_schema_json)
        assert isinstance(parsed, dict)
        return parsed


def load_native_review_request_schema() -> dict[str, Any]:
    schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            "bundled native request schema must be an object",
        )
    _enable_native_review_request_finding_decision_schema(schema)
    _enable_branch_discovery_request_schema(schema)
    try:
        check_schema(schema, location="<native-review-request-schema>")
    except SchemaDefinitionError as exc:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID, str(exc)
        ) from exc
    return schema


def validate_native_review_request_document(document: Mapping[str, Any]) -> None:
    try:
        validate_schema_document(document, load_native_review_request_schema())
    except SchemaMismatch as exc:
        location = ".".join(str(item) for item in exc.path) or "<request>"
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            f"schema validation failed at {location}: {exc.message}",
        ) from None
    review_contract = document.get("review_contract")
    has_plan_artifact_path = (
        isinstance(review_contract, Mapping)
        and "plan_artifact_path" in review_contract
    )
    if (document.get("review_kind") == NativeReviewKind.PLAN.value) != (
        has_plan_artifact_path
    ):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            "plan reviews alone must bind review_contract.plan_artifact_path",
        )
    has_disposition_budget = (
        isinstance(review_contract, Mapping)
        and "disposition_budget" in review_contract
    )
    if has_disposition_budget:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            "legacy final-review disposition budgets are unsupported",
        )
    has_discovery_capacity = (
        isinstance(review_contract, Mapping)
        and "max_new_findings" in review_contract
    )
    if (
        document.get("review_kind") == NativeReviewKind.BRANCH_DISCOVERY.value
    ) != has_discovery_capacity:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            "branch discovery reviews alone must bind review_contract.max_new_findings",
        )
    if has_disposition_budget:
        assert isinstance(review_contract, Mapping)
        budget = review_contract["disposition_budget"]
        if not isinstance(budget, Mapping):  # pragma: no cover - schema guarded
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.SCHEMA_INVALID,
                "final review disposition budget must be an object",
            )
        maximum_items = budget["maximum_items"]
        eligible_ids = budget["eligible_finding_ids"]
        previous_findings = review_contract["previous_findings"]
        previous_ids = [item["finding_id"] for item in previous_findings]
        if (
            maximum_items > MAX_NATIVE_REVIEW_DISPOSITIONS
            or maximum_items != len(eligible_ids)
            or eligible_ids != previous_ids
            or budget["pending_finding_count"] < maximum_items
        ):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.SCHEMA_INVALID,
                "final review disposition budget differs from its eligible finding subset",
            )


def validate_native_review_provider_response(
    document: Mapping[str, Any], bundle: NativeReviewRequestBundle
) -> None:
    """Validate one result against the exact writer schema bound to its request."""
    _validate_native_review_provider_response_schema(
        document, bundle.provider_response_schema
    )


def validate_native_review_provider_response_for_context(
    document: Mapping[str, Any], context: NativeReviewContext
) -> None:
    """Validate recovery bytes against the writer deterministically rebuilt from context."""
    _validate_native_review_provider_response_schema(
        document, native_review_provider_response_schema(context)
    )


def _validate_native_review_provider_response_schema(
    document: Mapping[str, Any], schema: Mapping[str, Any]
) -> None:
    try:
        validate_schema_document({"result": dict(document)}, schema)
    except SchemaMismatch as exc:
        location = ".".join(str(item) for item in exc.path) or "<response>"
        variants = describe_one_of_failure(
            {"result": dict(document)}, schema, path=exc.path
        )
        detail = variants or exc.message
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            f"provider response schema failed at {location}: {detail}",
        ) from None


def build_native_review_request(
    spec: NativeReviewRequestSpec,
    *,
    inline_evidence_chars: int = DEFAULT_INLINE_EVIDENCE_CHARS,
) -> NativeReviewRequestBundle:
    _validate_plan_disposition_capacity(spec)
    if inline_evidence_chars < 1:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "inline evidence limit must be positive",
        )
    response_schema = native_review_provider_response_schema(spec.context)
    response_schema_json = _canonical_json(response_schema)
    response_schema_digest = hashlib.sha256(
        response_schema_json.encode("utf-8")
    ).hexdigest()
    manifest: list[dict[str, Any]] = []
    assets: list[NativeReviewEvidenceAsset] = []
    for evidence in spec.evidence:
        digest = _sha256_text(evidence.content)
        byte_count = len(evidence.content.encode("utf-8"))
        common: dict[str, Any] = {
            "evidence_id": evidence.evidence_id,
            "kind": evidence.kind,
            "source_path": evidence.source_path,
            "sha256": digest,
            "byte_count": byte_count,
        }
        if evidence.semantic_digest is not None:
            common["semantic_digest"] = evidence.semantic_digest
        if len(evidence.content) <= inline_evidence_chars:
            manifest.append(
                {**common, "delivery": "inline", "content": evidence.content}
            )
        else:
            reference = f"evidence/{evidence.evidence_id}-{digest[:16]}.txt"
            manifest.append(
                {**common, "delivery": "content_ref", "content_ref": reference}
            )
            assets.append(
                NativeReviewEvidenceAsset(
                    path=reference,
                    sha256=digest,
                    byte_count=byte_count,
                    content=evidence.content,
                )
            )
    context_projection = _review_context_request_projection(spec.context)
    binding: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_type": "review_request",
        "transport": CLAUDE_REVIEW_TRANSPORT,
        "persistence_protocol": PERSISTENCE_PROTOCOL,
        **context_projection,
        "target_branch": spec.target_branch,
        "base_commit": spec.base_commit,
        "authorized_paths": list(spec.authorized_paths),
        "acceptance_criteria": list(spec.acceptance_criteria),
        "evidence_manifest": manifest,
        "response_contract": {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "schema_sha256": response_schema_digest,
        },
    }
    if spec.retry_feedback is not None:
        binding["retry_feedback"] = {
            "prior_invocation_id": spec.retry_feedback.prior_invocation_id,
            "rejection_code": spec.retry_feedback.rejection_code.value,
            "correction_instruction": spec.retry_feedback.correction_instruction,
        }
    request_digest = hashlib.sha256(
        _canonical_json(binding).encode("utf-8")
    ).hexdigest()
    request_id = f"native-review-request-{request_digest}"
    document = {**binding, "request_id": request_id}
    canonical = canonical_native_review_request_json(document)
    return NativeReviewRequestBundle(
        canonical_json=canonical,
        bound_context=BoundNativeReviewContext(
            context=spec.context,
            request_id=request_id,
            request_digest=request_digest,
        ),
        provider_response_schema_json=response_schema_json,
        evidence_assets=tuple(assets),
    )


def _review_context_request_projection(
    context: NativeReviewContext,
) -> dict[str, Any]:
    context_binding = native_review_context_binding(context)
    review_kind = {
        "claude_plan_review": NativeReviewKind.PLAN.value,
        "claude_slice_review": NativeReviewKind.SLICE.value,
        "claude_branch_discovery": NativeReviewKind.BRANCH_DISCOVERY.value,
    }.get(context.operation)
    if review_kind is None:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.CONTEXT_INVALID,
            "native review context has no supported review operation",
        )
    review_contract = {
        "approval_marker": context_binding["approval_marker"],
        "slice_id": context_binding["slice_id"],
        "round_number": context_binding["round_number"],
        "next_finding_id": next_native_finding_id(context),
        "previous_findings": context_binding["previous_findings"],
        "known_open_finding_signatures": context_binding[
            "known_open_finding_signatures"
        ],
        "validation_attestation": context_binding["validation_attestation"],
        "test_files": context_binding["test_files"],
        "test_changes_approved": context_binding["test_changes_approved"],
        "allow_new_observations": context_binding["allow_new_observations"],
        "anchor_origin": context_binding["anchor_origin"],
        "validation_command_prefixes": context_binding[
            "validation_command_prefixes"
        ],
        "red_state_followup_slice": context_binding[
            "red_state_followup_slice"
        ],
        "pre_change_fingerprint": context_binding[
            "pre_change_fingerprint"
        ],
    }
    if review_kind == NativeReviewKind.PLAN.value:
        review_contract["plan_artifact_path"] = context_binding[
            "plan_artifact_path"
        ]
    if review_kind == NativeReviewKind.BRANCH_DISCOVERY.value:
        review_contract["max_new_findings"] = context_binding[
            "max_new_findings"
        ]
    review_contract["implementer_responsibility_proposals"] = context_binding[
        "implementer_responsibility_proposals"
    ]
    review_contract["planned_slices"] = context_binding["planned_slices"]
    review_contract["plan_treatments"] = context_binding["plan_treatments"]
    review_contract["closed_finding_bindings"] = context_binding[
        "closed_finding_bindings"
    ]
    return {
        "reviewer": "claude",
        "run_id": context.run_id,
        "work_unit_id": context.work_unit_id,
        "operation": context.operation,
        "review_kind": review_kind,
        "current_fingerprint": context.diff_fingerprint,
        "review_contract": review_contract,
    }


def canonical_native_review_request_json(document: Mapping[str, Any]) -> str:
    validate_native_review_request_document(document)
    return _canonical_json(document)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_plan_disposition_capacity(spec: NativeReviewRequestSpec) -> None:
    if spec.review_kind is not NativeReviewKind.PLAN:
        return
    disposition_count = native_review_disposition_capacity(spec.context)
    if disposition_count > MAX_NATIVE_REVIEW_DISPOSITIONS:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.CONTEXT_INVALID,
            "PLAN_DISPOSITION_LIMIT: plan review requires "
            f"{disposition_count} status, reclassification, or routing decisions; "
            f"the bound maximum is {MAX_NATIVE_REVIEW_DISPOSITIONS}",
        )


def _enable_closed_finding_binding_request_schema(
    definitions: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    definitions["no_code_evidence_anchor"] = {
        "type": "object",
        "properties": {
            "rejection_reason": {
                "type": "string",
                "enum": ["no_defect", "out_of_scope", "already_fixed"],
            },
            "provenance_fingerprint": {"$ref": "#/$defs/sha256"},
            "evidence_paths": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1000,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/safe_path"},
            },
            "evidence_content_sha256": {"$ref": "#/$defs/sha256"},
            "task_sha256": {
                "oneOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]
            },
            "scope_sha256": {
                "oneOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]
            },
            "affected_paths": {
                "type": "array",
                "maxItems": 1000,
                "uniqueItems": True,
                "items": {"$ref": "#/$defs/safe_path"},
            },
            "affected_content_sha256": {
                "oneOf": [{"$ref": "#/$defs/sha256"}, {"type": "null"}]
            },
            "stability_sha256": {"$ref": "#/$defs/sha256"},
        },
        "required": [
            "rejection_reason",
            "provenance_fingerprint",
            "evidence_paths",
            "evidence_content_sha256",
            "task_sha256",
            "scope_sha256",
            "affected_paths",
            "affected_content_sha256",
            "stability_sha256",
        ],
        "additionalProperties": False,
    }
    definitions["closed_finding_binding"] = {
        "type": "object",
        "properties": {
            "finding_id": {
                "type": "string",
                "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
            },
            "signature": {"$ref": "#/$defs/sha256"},
            "source_plan_assignment_record_id": {
                "type": "string",
                "pattern": "^ar1-[0-9a-f]{64}$",
            },
            "original_anchor": {"$ref": "#/$defs/no_code_evidence_anchor"},
            "current_anchor": {"$ref": "#/$defs/no_code_evidence_anchor"},
        },
        "required": [
            "finding_id",
            "signature",
            "source_plan_assignment_record_id",
            "original_anchor",
            "current_anchor",
        ],
        "additionalProperties": False,
    }
    contract["properties"]["closed_finding_bindings"] = {
        "type": "array",
        "maxItems": 128,
        "items": {"$ref": "#/$defs/closed_finding_binding"},
    }
    contract["required"].append("closed_finding_bindings")


def _enable_native_review_request_finding_decision_schema(
    schema: dict[str, Any],
) -> None:
    definitions = schema["$defs"]
    _enable_finding_acceptance_measurement_request_schema(definitions)
    definitions["acceptance_criterion"] = {
        "type": "object",
        "properties": {
            "criterion_id": {
                "type": "string",
                "pattern": "^ac-[0-9a-f]{64}$",
            },
            "text": {"$ref": "#/$defs/safe_text"},
            "measured_against": {
                "type": "string",
                "enum": ["SOURCE", "BUILD_OUTPUT", "RUNNING_PRODUCT"]
            },
        },
        "required": ["criterion_id", "text", "measured_against"],
        "additionalProperties": False,
    }
    definitions["planned_slice"] = {
        "type": "object",
        "properties": {
            "slice_id": {"type": "integer", "minimum": 1},
            "summary": {"$ref": "#/$defs/safe_text"},
            "scope_paths": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1000,
                "items": {"$ref": "#/$defs/safe_path"},
            },
            "acceptance_criteria": {
                "type": "array",
                "maxItems": 256,
                "items": {"$ref": "#/$defs/acceptance_criterion"},
            },
        },
        "required": [
            "slice_id",
            "summary",
            "scope_paths",
            "acceptance_criteria",
        ],
        "additionalProperties": False,
    }
    definitions["finding_responsibility"] = responsibility_json_schema(
        union_keyword="oneOf"
    )
    definitions["responsibility_proposal"] = {
        "type": "object",
        "properties": {
            "finding_id": {
                "type": "string",
                "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
            },
            "responsibility": {"$ref": "#/$defs/finding_responsibility"},
            "rationale": {"$ref": "#/$defs/safe_text"},
        },
        "required": ["finding_id", "responsibility", "rationale"],
        "additionalProperties": False,
    }
    contract = definitions["review_contract"]
    contract["properties"]["pre_change_fingerprint"] = {
        "oneOf": [
            {"$ref": "#/$defs/sha256"},
            {"type": "null"},
        ]
    }
    contract["required"].append("pre_change_fingerprint")
    contract["properties"]["implementer_responsibility_proposals"] = {
        "type": "array",
        "maxItems": 128,
        "items": {"$ref": "#/$defs/responsibility_proposal"},
    }
    contract["required"].append("implementer_responsibility_proposals")
    contract["properties"]["planned_slices"] = {
        "type": "array",
        "maxItems": 64,
        "items": {"$ref": "#/$defs/planned_slice"},
    }
    contract["required"].append("planned_slices")
    definitions["plan_treatment_proposal"] = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "signature": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "finding_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 128,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
                        },
                    },
                    "treatment_kind": {
                        "type": "string",
                        "const": "implementation",
                    },
                    "closing_slice_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "integer", "minimum": 1},
                    },
                },
                "required": [
                    "signature",
                    "finding_ids",
                    "treatment_kind",
                    "closing_slice_ids",
                ],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "signature": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "finding_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 128,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "pattern": "^C-(0[1-9]|[1-9][0-9]*)$",
                        },
                    },
                    "treatment_kind": {
                        "type": "string",
                        "const": "no_code",
                    },
                    "no_code_reason": {
                        "type": "string",
                        "enum": ["no_defect", "out_of_scope", "already_fixed"],
                    },
                    "evidence": {"$ref": "#/$defs/safe_text"},
                    "evidence_paths": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1000,
                        "uniqueItems": True,
                        "items": {"$ref": "#/$defs/safe_path"},
                    },
                    "affected_paths": {
                        "type": "array",
                        "maxItems": 1000,
                        "uniqueItems": True,
                        "items": {"$ref": "#/$defs/safe_path"},
                    },
                },
                "required": [
                    "signature",
                    "finding_ids",
                    "treatment_kind",
                    "no_code_reason",
                    "evidence",
                    "evidence_paths",
                    "affected_paths",
                ],
                "additionalProperties": False,
            },
        ]
    }
    contract["properties"]["plan_treatments"] = {
        "type": "array",
        "maxItems": 128,
        "items": {"$ref": "#/$defs/plan_treatment_proposal"},
    }
    contract["required"].append("plan_treatments")
    _enable_closed_finding_binding_request_schema(definitions, contract)


def _enable_finding_acceptance_measurement_request_schema(
    definitions: dict[str, Any],
) -> None:
    definitions["finding_acceptance_measurement"] = {
        "type": "object",
        "properties": {
            "fingerprint": {"$ref": "#/$defs/sha256"},
            "argv": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": "^[^\\u0000\\r\\n]+$",
                },
            },
            "status": {"enum": ["PASS", "FAIL"]},
            "exit_code": {"type": "integer"},
            "output_sha256": {"$ref": "#/$defs/sha256"},
            "attestation_id": {"type": "string", "minLength": 1},
        },
        "required": [
            "fingerprint",
            "argv",
            "status",
            "exit_code",
            "output_sha256",
            "attestation_id",
        ],
        "additionalProperties": False,
    }
    definitions["finding"]["properties"]["acceptance_measurements"] = {
        "type": "array",
        "maxItems": 128,
        "items": {"$ref": "#/$defs/finding_acceptance_measurement"},
    }
    definitions["finding"]["required"].append("acceptance_measurements")


def _enable_branch_discovery_request_schema(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    contract = definitions["review_contract"]
    contract["properties"]["approval_marker"]["enum"].append(
        ApprovalMarker.BRANCH_DISCOVERY.value
    )
    contract["properties"]["max_new_findings"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_BRANCH_DISCOVERY_NEW_FINDINGS,
    }
    request = definitions["review_request"]
    review_kinds = request["properties"]["review_kind"]["enum"]
    if NativeReviewKind.BRANCH_DISCOVERY.value not in review_kinds:
        review_kinds.append(NativeReviewKind.BRANCH_DISCOVERY.value)


def _validate_manifest_semantic_binding(item: Mapping[str, Any], content: str) -> None:
    semantic_digest = item.get("semantic_digest")
    if item.get("kind") == "canonical_review_packet":
        if semantic_digest is not None and not isinstance(semantic_digest, str):
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "canonical review packet semantic_digest must be a string",
            )
        _validate_review_packet_semantic_digest(content, semantic_digest)
    elif semantic_digest is not None:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "non-packet evidence cannot carry semantic_digest",
        )


def _validate_review_packet_semantic_digest(
    content: str, semantic_digest: str | None
) -> None:
    canonical = content.encode("utf-8")
    try:
        packet = ReviewPacket.restore(canonical, hashlib.sha256(canonical).hexdigest())
    except ReviewPacketError as exc:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            f"canonical review packet evidence is invalid: {exc}",
        ) from exc
    manifest_digest = packet.manifest.diff_coverage_digest
    if manifest_digest is None:
        if semantic_digest is not None:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.EVIDENCE_INVALID,
                "legacy canonical review packet cannot carry semantic_digest",
            )
        return
    if semantic_digest is None:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "canonical review packet evidence requires semantic_digest",
        )
    _require_sha256(semantic_digest, "review packet semantic_digest")
    if manifest_digest != semantic_digest:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "canonical review packet semantic_digest differs from packet manifest",
        )


def _require_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or SAFE_ID_PATTERN.fullmatch(value) is None:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            f"{label} is not a safe identifier",
        )


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            f"{label} must be lowercase SHA-256",
        )


def _require_text(
    value: object,
    label: str,
    *,
    maximum: int,
    code: NativeReviewRequestErrorCode = NativeReviewRequestErrorCode.CONTEXT_INVALID,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise NativeReviewRequestError(
            code, f"{label} must be non-blank, NUL-free, and at most {maximum} characters"
        )


def _require_repository_path(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or path.as_posix() != value
        or any(not part or part in {".", ".."} for part in path.parts)
    ):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            f"{label} must be a canonical repository-relative path",
        )


def _require_distinct_evidence(
    evidence: tuple[NativeReviewEvidenceInput, ...],
) -> None:
    source_paths = tuple(item.source_path for item in evidence if item.source_path is not None)
    if len(source_paths) != len(set(source_paths)):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "evidence source_path values must be unique",
        )
    content_digests = tuple(_sha256_text(item.content) for item in evidence)
    if len(content_digests) != len(set(content_digests)):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "evidence content and digests must be unique within one request",
        )


def _require_sorted_paths(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))) or not values:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.CONTEXT_INVALID,
            f"{label} must be non-empty, sorted, and unique",
        )
    for value in values:
        _require_repository_path(value, label)


def _require_unique_texts(
    values: tuple[str, ...],
    label: str,
    *,
    allow_empty: bool,
    maximum: int,
) -> None:
    if len(values) != len(set(values)) or (not allow_empty and not values):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.CONTEXT_INVALID,
            f"{label} must be unique" + ("" if allow_empty else " and non-empty"),
        )
    for value in values:
        _require_text(value, label, maximum=maximum)
