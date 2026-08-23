"""Closed, canonical native Claude review requests and compact repair requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from contracts import AgentRole
from native_review_contract import (
    BoundNativeReviewContext,
    NativeReviewContext,
    load_native_review_schema,
    next_native_finding_id,
    native_review_context_binding,
)
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)
from review_packets import ReviewPacket, ReviewPacketError


REQUEST_SCHEMA_VERSION = "native-agent-review-request-v1"
RESPONSE_SCHEMA_VERSION = "native-agent-review-result-v1"
CLAUDE_REVIEW_TRANSPORT = "native-claude-review-v1"
PERSISTENCE_PROTOCOL = "structured-v1"
DEFAULT_INLINE_EVIDENCE_CHARS = 24_000
REQUEST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-review-request-v1.schema.json"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
REQUEST_ID_PATTERN = re.compile(r"native-review-request-[0-9a-f]{64}")
SAFE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,199}")


class NativeReviewRequestErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    EVIDENCE_INVALID = "evidence-invalid"
    REQUEST_INVALID = "request-invalid"
    REPAIR_INVALID = "repair-invalid"


class NativeReviewRequestError(ValueError):
    def __init__(self, code: NativeReviewRequestErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


class NativeReviewKind(StrEnum):
    PLAN = "plan"
    SLICE = "slice"
    FINAL = "final"


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
class NativeReviewRequestSpec:
    context: NativeReviewContext
    review_kind: NativeReviewKind
    target_branch: str
    base_commit: str
    authorized_paths: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    evidence: tuple[NativeReviewEvidenceInput, ...]

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
        expected_operation = {
            NativeReviewKind.PLAN: "claude_plan_review",
            NativeReviewKind.SLICE: "claude_slice_review",
            NativeReviewKind.FINAL: "claude_final_review",
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
        if calculated_digest != self.bound_context.request_digest:
            raise NativeReviewRequestError(
                NativeReviewRequestErrorCode.REQUEST_INVALID,
                "request content differs from its bound digest",
            )
        expected_response_schema_digest = hashlib.sha256(
            _canonical_json(self.provider_response_schema).encode("utf-8")
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
        """Return the provider schema specialized to this request context."""
        context = self.bound_context.context
        return native_review_provider_response_schema(
            reviewer=context.reviewer,
            allow_anchors=context.anchor_origin is not None,
        )


@dataclass(frozen=True, slots=True)
class NativeReviewRepairError:
    code: str
    detail: str

    def __post_init__(self) -> None:
        _require_text(self.code, "repair error code", maximum=200)
        _require_text(self.detail, "repair error detail", maximum=3000)


def load_native_review_request_schema() -> dict[str, Any]:
    schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.SCHEMA_INVALID,
            "bundled native request schema must be an object",
        )
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


def native_review_provider_response_schema(
    reviewer: AgentRole = AgentRole.CLAUDE,
    *,
    allow_anchors: bool = True,
) -> dict[str, Any]:
    """Return the exact provider-facing schema bound by native requests.

    Claude's CLI does not accept the descriptive ``$schema`` and ``$id``
    keywords or a top-level union/composition.  A closed transport object
    therefore carries the unchanged discriminated native result below its
    required ``result`` property, where Claude accepts the union.  Extraction
    unwraps only this typed object and immediately repeats full local schema
    validation.  Exact request/reviewer equality remains a local bound-context
    invariant, which also avoids a circular digest dependency between
    request_id and a dynamically specialized schema.
    """
    if reviewer not in {AgentRole.CLAUDE, AgentRole.ANTIGRAVITY}:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.CONTEXT_INVALID,
            "native review provider schema requires a reviewer role",
        )
    schema = load_native_review_schema()
    reviewer_value = reviewer.value
    finding_prefix = "C" if reviewer is AgentRole.CLAUDE else "A"
    finding_pattern = rf"^{finding_prefix}-(0[1-9]|[1-9][0-9]*)$"
    definitions = schema["$defs"]
    definitions["common"]["properties"]["reviewer"] = {"const": reviewer_value}
    for result_name in ("review_result", "stop_request"):
        definitions[result_name]["allOf"][1]["properties"]["reviewer"] = {
            "const": reviewer_value
        }
    for definition_name in ("finding", "status_change", "reclassification"):
        definitions[definition_name]["properties"]["finding_id"] = {
            "type": "string",
            "pattern": finding_pattern,
        }
    if not allow_anchors:
        definitions["review_result"]["allOf"][1]["properties"]["anchors"][
            "maxItems"
        ] = 0
    return {
        "title": "Native agent review result v1 provider projection",
        "type": "object",
        "properties": {
            "result": {
                "oneOf": [
                    {"$ref": "#/$defs/review_result"},
                    {"$ref": "#/$defs/stop_request"},
                ]
            },
        },
        "required": ["result"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def build_native_review_request(
    spec: NativeReviewRequestSpec,
    *,
    inline_evidence_chars: int = DEFAULT_INLINE_EVIDENCE_CHARS,
) -> NativeReviewRequestBundle:
    if inline_evidence_chars < 1:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.EVIDENCE_INVALID,
            "inline evidence limit must be positive",
        )
    response_schema = native_review_provider_response_schema(
        reviewer=spec.context.reviewer,
        allow_anchors=spec.context.anchor_origin is not None,
    )
    response_schema_digest = hashlib.sha256(
        _canonical_json(response_schema).encode("utf-8")
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
    context_binding = native_review_context_binding(spec.context)
    binding: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_type": "review_request",
        "reviewer": "claude",
        "transport": CLAUDE_REVIEW_TRANSPORT,
        "persistence_protocol": PERSISTENCE_PROTOCOL,
        "run_id": spec.context.run_id,
        "work_unit_id": spec.context.work_unit_id,
        "operation": spec.context.operation,
        "review_kind": spec.review_kind.value,
        "target_branch": spec.target_branch,
        "base_commit": spec.base_commit,
        "current_fingerprint": spec.context.diff_fingerprint,
        "authorized_paths": list(spec.authorized_paths),
        "acceptance_criteria": list(spec.acceptance_criteria),
        "review_contract": {
            "approval_marker": context_binding["approval_marker"],
            "slice_id": context_binding["slice_id"],
            "round_number": context_binding["round_number"],
            "next_finding_id": next_native_finding_id(spec.context),
            "previous_findings": context_binding["previous_findings"],
            "validation_attestation": context_binding["validation_attestation"],
            "test_files": context_binding["test_files"],
            "test_changes_approved": context_binding["test_changes_approved"],
            "allow_new_observations": context_binding["allow_new_observations"],
            "anchor_origin": context_binding["anchor_origin"],
            "validation_command_prefixes": context_binding[
                "validation_command_prefixes"
            ],
        },
        "evidence_manifest": manifest,
        "response_contract": {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "schema_sha256": response_schema_digest,
        },
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
        evidence_assets=tuple(assets),
    )


def build_native_review_repair_request(
    *,
    parent: NativeReviewRequestBundle,
    rejected_response_json: str,
    errors: tuple[NativeReviewRepairError, ...],
) -> NativeReviewRequestBundle:
    _require_text(
        rejected_response_json,
        "rejected_response_json",
        maximum=120_000,
        code=NativeReviewRequestErrorCode.REPAIR_INVALID,
    )
    if not errors:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.REPAIR_INVALID,
            "repair request requires at least one typed error",
        )
    try:
        decoded = json.loads(rejected_response_json)
    except json.JSONDecodeError as exc:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.REPAIR_INVALID,
            "rejected response must be canonical JSON",
        ) from exc
    canonical_rejected = _canonical_json(decoded)
    if rejected_response_json != canonical_rejected:
        raise NativeReviewRequestError(
            NativeReviewRequestErrorCode.REPAIR_INVALID,
            "rejected response must be canonical JSON",
        )
    binding: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_type": "review_contract_repair_request",
        "reviewer": "claude",
        "transport": CLAUDE_REVIEW_TRANSPORT,
        "parent_request_id": parent.bound_context.request_id,
        "current_fingerprint": parent.document["current_fingerprint"],
        "rejected_response_sha256": _sha256_text(rejected_response_json),
        "rejected_response_json": rejected_response_json,
        "errors": [
            {"code": item.code, "detail": item.detail} for item in errors
        ],
        "response_contract": parent.document["response_contract"],
    }
    digest = hashlib.sha256(_canonical_json(binding).encode("utf-8")).hexdigest()
    request_id = f"native-review-request-{digest}"
    document = {**binding, "request_id": request_id}
    return NativeReviewRequestBundle(
        canonical_json=canonical_native_review_request_json(document),
        bound_context=BoundNativeReviewContext(
            context=parent.bound_context.context,
            request_id=request_id,
            request_digest=digest,
        ),
    )


def canonical_native_review_request_json(document: Mapping[str, Any]) -> str:
    validate_native_review_request_document(document)
    return _canonical_json(document)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
