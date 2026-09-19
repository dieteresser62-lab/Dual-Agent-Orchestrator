"""Canonical, digest-bound native Codex requests and evidence assets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

import plan_handoff
from contracts import ReadinessMarker, ValidationAttestation
from finding_reducer import project_open_set
from native_codex_contract import (
    BoundNativeCodexContext,
    NativeCodexContext,
    NativeCodexRequestKind,
    native_codex_provider_response_schema,
)
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)


REQUEST_SCHEMA_VERSION = "native-agent-codex-request-v2"
RESPONSE_SCHEMA_VERSION = "native-agent-codex-result-v2"
NATIVE_CODEX_TRANSPORT = "native-codex-v2"
DEFAULT_INLINE_EVIDENCE_CHARS = 24_000
REQUEST_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "native-agent-codex-request-v2.schema.json"
)
SAFE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,199}")


class NativeCodexRequestErrorCode(StrEnum):
    SCHEMA_INVALID = "schema-invalid"
    CONTEXT_INVALID = "context-invalid"
    EVIDENCE_INVALID = "evidence-invalid"
    REQUEST_INVALID = "request-invalid"


class NativeCodexRequestError(ValueError):
    def __init__(self, code: NativeCodexRequestErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True, slots=True)
class NativeCodexEvidenceInput:
    evidence_id: str
    kind: str
    content: str
    source_path: str | None = None

    def __post_init__(self) -> None:
        if SAFE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "evidence_id is not a safe identifier",
            )
        _require_text(
            self.kind,
            "evidence kind",
            100,
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
        )
        _require_text(
            self.content,
            "evidence content",
            4_000_000,
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
        )
        if self.source_path is not None:
            _require_repository_path(
                self.source_path,
                "evidence source_path",
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
            )


@dataclass(frozen=True, slots=True)
class NativeCodexEvidenceAsset:
    path: str
    sha256: str
    byte_count: int
    content: str

    def __post_init__(self) -> None:
        _require_internal_asset_path(self.path)
        _require_sha256(
            self.sha256,
            "evidence asset sha256",
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
        )
        if self.byte_count != len(self.content.encode("utf-8")) or self.byte_count < 1:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "evidence asset byte_count differs from content",
            )
        if self.sha256 != _sha256_text(self.content):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "evidence asset digest differs from content",
            )


@dataclass(frozen=True, slots=True)
class NativeCodexRequestSpec:
    context: NativeCodexContext
    target_branch: str
    base_commit: str
    authorized_paths: tuple[str, ...]
    assignment: str
    work_context: str
    evidence: tuple[NativeCodexEvidenceInput, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.context, NativeCodexContext):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.CONTEXT_INVALID,
                "request spec requires NativeCodexContext",
            )
        _require_text(
            self.target_branch,
            "target_branch",
            300,
            NativeCodexRequestErrorCode.CONTEXT_INVALID,
        )
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_commit):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.CONTEXT_INVALID,
                "base_commit must be lowercase 40-character Git SHA",
            )
        _require_sorted_paths(
            self.authorized_paths,
            "authorized_paths",
            NativeCodexRequestErrorCode.CONTEXT_INVALID,
            allow_empty=False,
        )
        _require_text(
            self.assignment,
            "assignment",
            120_000,
            NativeCodexRequestErrorCode.CONTEXT_INVALID,
        )
        _require_text(
            self.work_context,
            "work_context",
            1_000_000,
            NativeCodexRequestErrorCode.CONTEXT_INVALID,
        )
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if evidence_ids != tuple(sorted(set(evidence_ids))) or not evidence_ids:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "evidence must be non-empty, sorted, and unique",
            )
        _require_distinct_evidence(
            self.evidence,
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
        )


@dataclass(frozen=True, slots=True)
class NativeCodexRequestBundle:
    canonical_json: str
    bound_context: BoundNativeCodexContext
    provider_response_schema_json: str
    evidence_assets: tuple[NativeCodexEvidenceAsset, ...] = ()

    def __post_init__(self) -> None:
        try:
            document = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle canonical_json is invalid",
            ) from exc
        validate_native_codex_request_document(document)
        if self.canonical_json != _canonical_json(document):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle canonical_json is not canonical",
            )
        if document["request_id"] != self.bound_context.request_id:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle request id differs from bound context",
            )
        binding = {key: value for key, value in document.items() if key != "request_id"}
        calculated_digest = _sha256_text(_canonical_json(binding))
        if (
            calculated_digest != self.bound_context.request_digest
            or document["request_id"] != "native-codex-request-" + calculated_digest
        ):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "request content differs from its bound digest",
            )
        expected_context = _codex_context_request_projection(
            self.bound_context.context
        )
        actual_context = {key: document[key] for key in expected_context}
        if actual_context != expected_context:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "request document differs from its bound Codex context projection",
            )
        try:
            provider_schema = json.loads(self.provider_response_schema_json)
        except json.JSONDecodeError as exc:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema is invalid JSON",
            ) from exc
        if not isinstance(provider_schema, dict) or self.provider_response_schema_json != _canonical_json(provider_schema):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema is not a canonical object",
            )
        expected_schema = native_codex_provider_response_schema(
            self.bound_context.context
        )
        if provider_schema != expected_schema:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle provider response schema differs from bound context",
            )
        schema_digest = _sha256_text(self.provider_response_schema_json)
        if document["response_contract"]["schema_sha256"] != schema_digest:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.REQUEST_INVALID,
                "bundle response contract differs from provider schema",
            )
        manifest = tuple(document["evidence_manifest"])
        evidence_ids = tuple(item["evidence_id"] for item in manifest)
        if evidence_ids != tuple(sorted(set(evidence_ids))):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "request evidence manifest must be sorted and unique",
            )
        expected_assets: dict[str, tuple[str, int]] = {}
        for item in manifest:
            if item["delivery"] == "inline":
                content = item["content"]
                if (
                    _sha256_text(content) != item["sha256"]
                    or len(content.encode("utf-8")) != item["byte_count"]
                ):
                    raise NativeCodexRequestError(
                        NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                        f"inline evidence {item['evidence_id']} metadata differs from content",
                    )
            else:
                reference = item["content_ref"]
                if reference in expected_assets:
                    raise NativeCodexRequestError(
                        NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                        "request content references must be unique",
                    )
                expected_assets[reference] = (item["sha256"], item["byte_count"])
        actual_assets = {
            item.path: (item.sha256, item.byte_count) for item in self.evidence_assets
        }
        if len(actual_assets) != len(self.evidence_assets):
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "request evidence asset paths must be unique",
            )
        if actual_assets != expected_assets:
            raise NativeCodexRequestError(
                NativeCodexRequestErrorCode.EVIDENCE_INVALID,
                "request evidence assets differ from content references",
            )

    @property
    def document(self) -> Mapping[str, Any]:
        document = json.loads(self.canonical_json)
        assert isinstance(document, dict)
        return document

    @property
    def provider_response_schema(self) -> Mapping[str, Any]:
        document = json.loads(self.provider_response_schema_json)
        assert isinstance(document, dict)
        return document


def load_native_codex_request_schema() -> dict[str, Any]:
    schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.SCHEMA_INVALID,
            "bundled native Codex request schema must be an object",
        )
    try:
        check_schema(schema, location="<native-codex-request-schema>")
    except SchemaDefinitionError as exc:
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.SCHEMA_INVALID, str(exc)
        ) from exc
    return schema


def validate_native_codex_request_document(document: Mapping[str, Any]) -> None:
    try:
        validate_schema_document(document, load_native_codex_request_schema())
    except SchemaMismatch as exc:
        location = ".".join(str(part) for part in exc.path) or "<request>"
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.SCHEMA_INVALID,
            f"schema validation failed at {location}: {exc.message}",
        ) from None


def validate_native_codex_provider_response(
    document: Mapping[str, Any], bundle: NativeCodexRequestBundle
) -> None:
    """Validate one result against the exact writer schema bound to its request."""
    try:
        validate_schema_document(
            {"result": dict(document)}, bundle.provider_response_schema
        )
    except SchemaMismatch as exc:
        location = ".".join(str(part) for part in exc.path) or "<response>"
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.SCHEMA_INVALID,
            f"provider response schema failed at {location}: {exc.message}",
        ) from None


def build_native_codex_request(
    spec: NativeCodexRequestSpec,
    *,
    inline_evidence_chars: int = DEFAULT_INLINE_EVIDENCE_CHARS,
) -> NativeCodexRequestBundle:
    if inline_evidence_chars < 1:
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
            "inline evidence limit must be positive",
        )
    manifest: list[dict[str, Any]] = []
    assets: list[NativeCodexEvidenceAsset] = []
    for item in spec.evidence:
        digest = _sha256_text(item.content)
        byte_count = len(item.content.encode("utf-8"))
        common: dict[str, Any] = {
            "evidence_id": item.evidence_id,
            "kind": item.kind,
            "source_path": item.source_path,
            "sha256": digest,
            "byte_count": byte_count,
        }
        if len(item.content) <= inline_evidence_chars:
            manifest.append({**common, "delivery": "inline", "content": item.content})
        else:
            path = (
                ".orchestrator/artifacts/native-codex-evidence/"
                f"{item.evidence_id}-{digest}.txt"
            )
            manifest.append({**common, "delivery": "content_ref", "content_ref": path})
            assets.append(NativeCodexEvidenceAsset(path, digest, byte_count, item.content))

    context = spec.context
    context_projection = _codex_context_request_projection(context)
    response_schema = native_codex_provider_response_schema(context)
    response_schema_json = _canonical_json(response_schema)
    response_schema_digest = _sha256_text(response_schema_json)
    binding: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "transport": NATIVE_CODEX_TRANSPORT,
        **context_projection,
        "target_branch": spec.target_branch,
        "base_commit": spec.base_commit,
        "authorized_paths": list(spec.authorized_paths),
        "assignment": spec.assignment,
        "work_context": spec.work_context,
        "evidence_manifest": manifest,
        "response_contract": {
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "schema_sha256": response_schema_digest,
        },
    }
    request_digest = _sha256_text(_canonical_json(binding))
    request_id = "native-codex-request-" + request_digest
    document = {**binding, "request_id": request_id}
    canonical = canonical_native_codex_request_json(document)
    return NativeCodexRequestBundle(
        canonical_json=canonical,
        bound_context=BoundNativeCodexContext(context, request_id, request_digest),
        provider_response_schema_json=response_schema_json,
        evidence_assets=tuple(assets),
    )


def _codex_context_request_projection(
    context: NativeCodexContext,
) -> dict[str, Any]:
    """Return the complete context projection actually transported to Codex.

    Closed findings deliberately remain authoritative only in the workflow record
    chain; the native request transports the open subset Codex may disposition.
    """
    return {
        "request_type": context.request_kind.value,
        "run_id": context.run_id,
        "work_unit_id": context.work_unit_id,
        "operation": context.operation,
        "current_fingerprint": context.current_fingerprint,
        "codex_contract": _contract_document(context),
        "open_findings": [
            {
                "finding_id": item.finding_id,
                "finding_class": item.finding_class.value,
                "summary": item.summary,
                "acceptance_test": item.acceptance_test,
                "reporter": item.origin.reporter.value,
            }
            for item in project_open_set(context.previous_findings).findings
        ],
    }


def canonical_native_codex_request_json(document: Mapping[str, Any]) -> str:
    validate_native_codex_request_document(document)
    return _canonical_json(document)


def _contract_document(context: NativeCodexContext) -> dict[str, Any]:
    contract = context.contract
    readiness_kind = {
        ReadinessMarker.PLAN: "plan",
        ReadinessMarker.IMPLEMENTATION: "implementation",
    }[contract.readiness_marker]
    document = {
        "name": contract.name,
        "readiness_kind": readiness_kind,
        "slice_id": contract.slice_id,
        "round_number": contract.round_number,
        "require_test_files_record": contract.require_test_files_record,
        "expected_test_files": list(contract.expected_test_files),
        "test_changes_approved": contract.test_changes_approved,
        "require_slice_plan": contract.require_slice_plan,
        "plan_artifact_path": contract.plan_artifact_path,
        "enforce_expected_test_files": contract.enforce_expected_test_files,
        "review_fingerprint": contract.review_fingerprint,
        "validation_attestation": _attestation_document(
            contract.validation_attestation
        ),
    }
    if contract.plan_artifact_path is not None:
        document["plan_artifact_format_contract"] = (
            plan_handoff.render_plan_artifact_format_contract()
        )
    return document


def _attestation_document(
    attestation: ValidationAttestation | None,
) -> dict[str, Any] | None:
    if attestation is None:
        return None
    return {
        "attestation_id": attestation.attestation_id,
        "diff_fingerprint": attestation.diff_fingerprint,
        "expected_commands": list(attestation.expected_commands),
        "records": [
            {
                "status": item.status.value,
                "command": item.command,
                "exit_code": item.exit_code,
                "output": item.output,
            }
            for item in attestation.records
        ],
        "output_digest": attestation.output_digest,
        "summary": attestation.summary,
        "command_specs": [
            {"argv": list(item.argv), "legacy_shell": item.legacy_shell}
            for item in attestation.command_specs
        ],
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_text(
    value: object,
    label: str,
    maximum: int,
    code: NativeCodexRequestErrorCode,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise NativeCodexRequestError(
            code, f"{label} must be non-blank, NUL-free, and at most {maximum} characters"
        )


def _require_sha256(
    value: object, label: str, code: NativeCodexRequestErrorCode
) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise NativeCodexRequestError(code, f"{label} must be lowercase SHA-256")


def _require_repository_path(
    value: str, label: str, code: NativeCodexRequestErrorCode
) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
        or path.parts[0] == ".orchestrator"
    ):
        raise NativeCodexRequestError(
            code, f"{label} must be a canonical repository-relative path"
        )


def _require_sorted_paths(
    values: tuple[str, ...],
    label: str,
    code: NativeCodexRequestErrorCode,
    *,
    allow_empty: bool,
) -> None:
    if values != tuple(sorted(set(values))) or (not allow_empty and not values):
        raise NativeCodexRequestError(
            code, f"{label} must be sorted and unique" + ("" if allow_empty else " and non-empty")
        )
    for value in values:
        _require_repository_path(value, label, code)


def _require_distinct_evidence(
    evidence: tuple[NativeCodexEvidenceInput, ...],
    code: NativeCodexRequestErrorCode,
) -> None:
    source_paths = tuple(item.source_path for item in evidence if item.source_path is not None)
    if len(source_paths) != len(set(source_paths)):
        raise NativeCodexRequestError(code, "evidence source_path values must be unique")
    content_digests = tuple(_sha256_text(item.content) for item in evidence)
    if len(content_digests) != len(set(content_digests)):
        raise NativeCodexRequestError(
            code,
            "evidence content and digests must be unique within one request",
        )


def _require_internal_asset_path(value: str) -> None:
    path = PurePosixPath(value)
    expected_prefix = (".orchestrator", "artifacts", "native-codex-evidence")
    if (
        path.is_absolute()
        or tuple(path.parts[:3]) != expected_prefix
        or len(path.parts) != 4
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
    ):
        raise NativeCodexRequestError(
            NativeCodexRequestErrorCode.EVIDENCE_INVALID,
            "evidence asset path must use the native Codex artifact namespace",
        )
