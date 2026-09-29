"""Fail-closed loader for provider/role/slot qualifications."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from agent_roles import AgentRoleName, AgentSlot, role_for_slot
from role_binding import binding_for, binding_for_role


ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = "schemas/role-provider-certifications-v1.json"
CAPABILITY_PATH = "schemas/native-provider-schema-capabilities-v2.json"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]*\Z", re.ASCII)
_TEST_FUNCTION = re.compile(r"test_[A-Za-z0-9_]+(?:\[[^\]\r\n]+\])?\Z", re.ASCII)
_CLAUDE_Q3_SHA256 = "e6292aedd72e37187dc91abe675b6cf3b1d5f6b7aecf308b574468d20c16db05"  # allowlist:provider -- certification data: saved Claude review
_BASELINE = {
    (AgentSlot.IMPLEMENTER, "codex"),
    (AgentSlot.REVIEWER, "claude"),
    (AgentSlot.FINAL_REVIEWER, "claude"),
}
_LEGACY_CANARY_FORMATS = {
    "antigravity-canary-v1": ("antigravity", "docs/evidence/antigravity/canary-v1.json"),
}
_CANARY_CHECKS = frozenset({"writer", "domain", "effective_rights", "isolation_postcheck", "no_denials"})


class CertificationErrorCode(StrEnum):
    SOURCE_INVALID = "source-invalid"
    ENTRY_INVALID = "entry-invalid"
    BASELINE_MISSING = "baseline-missing"
    DUPLICATE_ENTRY = "duplicate-entry"
    SOURCE_MISMATCH = "source-mismatch"
    EVIDENCE_INVALID = "evidence-invalid"
    NOT_CERTIFIED = "not-certified"


class CertificationError(ValueError):
    def __init__(self, code: CertificationErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_keys(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CertificationError(CertificationErrorCode.ENTRY_INVALID, f"{label} fields are invalid")
    return value


def _parse_json(data: bytes, raw: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise CertificationError(
                    CertificationErrorCode.ENTRY_INVALID, f"duplicate JSON field in {raw}: {key}"
                )
            value[key] = item
        return value

    try:
        value = json.loads(data, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"invalid JSON: {raw}") from exc
    if not isinstance(value, dict):
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"invalid JSON object: {raw}")
    return value


def _read_file(root: Path, raw: str, *, expected_digest: str | None = None) -> bytes:
    if not isinstance(raw, str) or not raw or raw == "." or PurePosixPath(raw).is_absolute():
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"unsafe source path: {raw!r}")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {".", ".."} for part in parts) or str(PurePosixPath(raw)) != raw:
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"unsafe source path: {raw!r}")
    if parts[0] == "tests":
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"runtime source under tests: {raw}")
    path = root
    for part in parts:
        path = path / part
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"missing source: {raw}") from exc
        if stat.S_ISLNK(mode):
            raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"symlink source: {raw}")
    if not stat.S_ISREG(mode):
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"non-regular source: {raw}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, f"unreadable source: {raw}") from exc
    if expected_digest is not None:
        if not isinstance(expected_digest, str) or _SHA256.fullmatch(expected_digest) is None:
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, f"invalid digest: {raw}")
        if hashlib.sha256(data).hexdigest() != expected_digest:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"evidence digest differs: {raw}")
    return data


def _json_file(root: Path, raw: str) -> dict[str, Any]:
    return _parse_json(_read_file(root, raw), raw)


def _registered_manufacturers(
    table: dict[str, Any], provider_rows: dict[str, dict[str, Any]],
) -> dict[str, str]:
    registered = table["provider_manufacturers"]
    if not isinstance(registered, dict) or not registered:
        raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "provider manufacturer registry is empty or invalid")
    if list(registered) != sorted(registered):
        raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "provider manufacturer registry is not sorted")
    for provider, manufacturer in registered.items():
        if (
            not isinstance(provider, str)
            or _IDENTIFIER.fullmatch(provider) is None
            or not isinstance(manufacturer, str)
            or _IDENTIFIER.fullmatch(manufacturer) is None
        ):
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "invalid provider or manufacturer identifier")
        if provider not in {row["provider"] for row in provider_rows.values()}:
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, f"unregistered capability provider: {provider}")
    return registered


def _validate_evidence_slots(document: dict[str, Any]) -> None:
    required = {"schema_version", "slots"}
    if document.get("schema_version") == "antigravity-capability-v1":
        required.add("measurements")
    _require_keys(document, required, "evidence document")
    if document["schema_version"] not in {"role-certification-evidence-v1", "antigravity-capability-v1"}:
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "unknown evidence version")
    slots = document["slots"]
    expected_slots = {slot.value for slot in AgentSlot}
    if (not isinstance(slots, dict) or not slots or not set(slots) <= expected_slots
        or document["schema_version"] == "role-certification-evidence-v1" and set(slots) != expected_slots):
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "evidence slot coverage differs")
    for slot, refs in slots.items():
        if not isinstance(refs, list) or not refs:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"empty evidence for {slot}")
        for ref in refs:
            reference = _require_keys(ref, {"node_id", "proves"}, "evidence test")
            node_id, proves = reference["node_id"], reference["proves"]
            if not isinstance(node_id, str) or "::" not in node_id:
                raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "invalid evidence test node ID")
            test_path, function = node_id.split("::", 1)
            path = PurePosixPath(test_path)
            if (
                not path.parts or path.parts[0] != "tests"
                or path.suffix != ".py" or str(path) != test_path
                or any(part in {".", ".."} for part in path.parts)
                or _TEST_FUNCTION.fullmatch(function) is None
                or not isinstance(proves, str) or not proves.strip()
            ):
                raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "invalid evidence test node ID or description")


def _validate_agy_canaries(root: Path, reference: dict[str, Any], *, model_family_pattern: str) -> None:
    ref = _require_keys(reference, {"path", "sha256"}, "canary reference")
    if ref["path"] != "docs/evidence/antigravity/canary-v1.json":
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "unexpected canary evidence path")
    document = _parse_json(_read_file(root, ref["path"], expected_digest=ref["sha256"]), ref["path"])
    if document.get("schema_version") != "antigravity-canary-v1":
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary evidence version differs")
    shared = document.get("shared_evidence")
    expected = {"phase_0": "phase-0-v1.json", "qualification": "qualification-series-v1.json",
                "quality": "quality-results-v1.json", "operator_decisions": "operator-decisions-v1.json"}
    if not isinstance(shared, dict) or set(shared) != set(expected):
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary shared evidence is incomplete")
    for name, filename in expected.items():
        item = _require_keys(shared[name], {"path", "sha256"}, "canary shared reference")
        if item["path"] != f"docs/evidence/antigravity/{filename}":
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary shared evidence path differs")
        _read_file(root, item["path"], expected_digest=item["sha256"])
    slots = document.get("slots")
    if document.get("status") != "passed" or not isinstance(slots, dict) or set(slots) != {
            "reviewer", "final_reviewer"}:
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "both AGY canaries must pass")
    request_ids = set()
    for slot, case in (("reviewer", "F4"), ("final_reviewer", "F5")):
        item = slots[slot]
        if (not isinstance(item, dict) or item.get("provider") != "antigravity" or
            item.get("role") != "reviewer" or item.get("case") != case or
            item.get("transport_series") != "agy-transport-s3" or item.get("status") != "passed"):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{slot} canary binding differs")
        proof = item.get("proof")
        if not isinstance(proof, dict) or proof.get("slot") != slot or proof.get("case") != case:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{slot} canary proof is missing")
        checks = proof.get("checks")
        if not isinstance(checks, dict) or set(checks) != {
                "writer", "domain", "effective_rights", "isolation_postcheck", "no_denials"} or not all(
                value is True for value in checks.values()):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{slot} canary checks failed")
        for key in ("profile_sha256", "request_sha256", "writer_sha256", "source_sha256",
                    "binary_sha256", "raw_sha256"):
            if not isinstance(proof.get(key), str) or _SHA256.fullmatch(proof[key]) is None:
                raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{slot} canary digest is invalid")
        raw = proof.get("raw")
        def wire_digest(value: object) -> str:
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
        raw_digest = wire_digest(raw) if isinstance(raw, dict) else None
        if raw_digest != proof["raw_sha256"]:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{slot} canary raw proof differs")
        request_id = proof.get("request_id")
        if not isinstance(request_id, str) or not request_id.startswith("native-review-request-") or request_id in request_ids:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary request identity is invalid or reused")
        request_ids.add(request_id)
        if raw.get("request_id") != request_id or raw.get("status") != "success":
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary raw result differs")
        evidence = raw.get("evidence")
        if (not isinstance(evidence, dict)
            or not isinstance(evidence.get("request_document"), dict)
            or not isinstance(evidence.get("writer_schema"), dict)
            or wire_digest(evidence["request_document"]) != proof["request_sha256"]
            or wire_digest(evidence["writer_schema"]) != proof["writer_sha256"]
            or evidence["request_document"].get("request_id") != request_id
            or evidence.get("envelope", {}).get("status") != "SUCCESS"
            or evidence["envelope"].get("denied_actions") not in (None, [])
            or evidence["envelope"].get("structured_output", {}).get("result", {}).get("request_id") != request_id):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary request or response proof differs")
        if not isinstance(proof.get("commit_sha"), str) or re.fullmatch(r"[0-9a-f]{40}", proof["commit_sha"]) is None:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary commit is invalid")
        if (not isinstance(proof.get("model"), str) or re.fullmatch(r"gemini-[a-z0-9.-]+", proof["model"]) is None
            or re.fullmatch(model_family_pattern.removeprefix("^").removesuffix("$"), proof["model"]) is None
            or type(proof.get("timeout_seconds")) is not int or proof["timeout_seconds"] < 0):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary profile is invalid")
        if slot == "final_reviewer" and proof.get("claude_slice_review_sha256") != _CLAUDE_Q3_SHA256:  # allowlist:provider -- certification data: saved Claude review
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "final canary lacks Claude slice evidence")  # allowlist:provider -- certification data: saved Claude review


def _wire_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_role_canaries(
    document: dict[str, Any], *, provider: str, role: AgentRoleName,
    slot: AgentSlot, model_family_pattern: str | None,
) -> None:
    _require_keys(document, {"schema_version", "status", "slots"}, "role canary document")
    if document["status"] != "passed" or not isinstance(document["slots"], dict):
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "role canary did not pass")
    slots = document["slots"]
    if not slots or not set(slots) <= {item.value for item in AgentSlot} or slot.value not in slots:
        raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"missing canary for {slot.value}")
    request_ids: set[str] = set()
    for name, raw_item in slots.items():
        item = _require_keys(raw_item, {"provider", "role", "slot", "case", "transport_series", "status", "proof"}, "role canary slot")
        if (item["provider"] != provider or item["role"] != role_for_slot(AgentSlot(name)).value
            or item["slot"] != name or item["status"] != "passed"
            or not isinstance(item["case"], str) or not item["case"].strip()
            or not isinstance(item["transport_series"], str) or not item["transport_series"].strip()):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"{name} canary binding differs")
        proof = _require_keys(item["proof"], {
            "request_id", "request_sha256", "writer_sha256", "raw_sha256",
            "profile_sha256", "model", "profile", "checks", "raw",
            "provider", "role", "slot", "case", "transport_series",
        }, "role canary proof")
        if any(proof[field] != item[field] for field in ("provider", "role", "slot", "case", "transport_series")):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary proof binding differs")
        if (not isinstance(proof["request_id"], str) or not proof["request_id"].strip()
            or proof["request_id"] in request_ids):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary request identity is invalid or reused")
        request_ids.add(proof["request_id"])
        if any(not isinstance(proof[key], str) or _SHA256.fullmatch(proof[key]) is None
               for key in ("request_sha256", "writer_sha256", "raw_sha256", "profile_sha256")):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary digest is invalid")
        checks = proof["checks"]
        if (not isinstance(checks, dict) or not _CANARY_CHECKS <= checks.keys()
            or not all(value is True for value in checks.values())):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary checks failed")
        profile, model = proof["profile"], proof["model"]
        if (not isinstance(profile, dict) or profile.get("provider") != provider
            or profile.get("model") != model or _wire_digest(profile) != proof["profile_sha256"]
            or not isinstance(model, str) or not model
            or model_family_pattern is not None and re.fullmatch(model_family_pattern, model) is None):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary model or profile differs")
        raw = proof["raw"]
        if not isinstance(raw, dict) or _wire_digest(raw) != proof["raw_sha256"]:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary raw proof differs")
        evidence = raw.get("evidence")
        if (raw.get("request_id") != proof["request_id"] or raw.get("status") != "success"
            or not isinstance(evidence, dict)
            or not isinstance(evidence.get("request_document"), dict)
            or not isinstance(evidence.get("writer_schema"), dict)
            or evidence["request_document"].get("request_id") != proof["request_id"]
            or _wire_digest(evidence["request_document"]) != proof["request_sha256"]
            or _wire_digest(evidence["writer_schema"]) != proof["writer_sha256"]):
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary request or writer proof differs")


@dataclass(frozen=True, slots=True)
class RoleProviderCertification:
    """The probe_profile is the probed transport profile from the capability register; not a model or effort filter."""

    slot: AgentSlot
    role: AgentRoleName
    provider: str
    capability_profile: str
    manufacturer: str
    status: str
    probe_profile: dict[str, Any]
    version_scope: dict[str, str]
    capability_sha256: str
    policy_sha256: str
    rights_sha256: str
    evidence_path: str
    evidence_sha256: str
    model_family_pattern: str | None = None

    @property
    def digest(self) -> str:
        return _digest(asdict(self))

    @property
    def transport_sha256(self) -> str:
        return _digest(self.probe_profile)


@dataclass(frozen=True, slots=True)
class CertificationTable:
    entries: tuple[RoleProviderCertification, ...]

    def require(self, provider: str, role: AgentRoleName, slot: AgentSlot, *, model: str | None = None) -> RoleProviderCertification:
        for entry in self.entries:
            if entry.provider == provider and entry.role == role and entry.slot == slot:
                if entry.model_family_pattern is not None and (
                    not isinstance(model, str) or re.fullmatch(entry.model_family_pattern, model) is None
                ):
                    raise CertificationError(
                        CertificationErrorCode.NOT_CERTIFIED,
                        f"slot={slot.value} provider={provider}: model {model!r} does not match manufacturer {entry.manufacturer} family",
                    )
                if entry.status in {"certified", "experimental"}:
                    return entry
                break
        raise CertificationError(
            CertificationErrorCode.NOT_CERTIFIED,
            f"slot={slot.value} provider={provider} role={role.value}: missing qualification evidence",
        )

    def require_occupancy(self, providers: dict[AgentSlot, str], *, models: dict[AgentSlot, str] | None = None) -> dict[AgentSlot, RoleProviderCertification]:
        if set(providers) != set(AgentSlot):
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "slot occupancy is incomplete")
        selected = {
            slot: self.require(providers[slot], role_for_slot(slot), slot, model=models[slot] if models is not None else None)
            for slot in AgentSlot
        }
        implementer = selected[AgentSlot.IMPLEMENTER].manufacturer
        for slot in (AgentSlot.REVIEWER, AgentSlot.FINAL_REVIEWER):
            if implementer == selected[slot].manufacturer:
                raise CertificationError(
                    CertificationErrorCode.NOT_CERTIFIED,
                    f"slot={slot.value} provider={providers[slot]}: implementer and reviewer manufacturers must differ",
                )
        return selected


def _load_role_certifications(
    *, root: Path = ROOT, table_path: str = TABLE_PATH,
) -> CertificationTable:
    root = root.resolve()
    table = _json_file(root, table_path)
    _require_keys(
        table, {"schema_version", "provider_manufacturers", "certifications"},
        "certification table",
    )
    if table["schema_version"] != "role-provider-certifications-v1":
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, "unknown certification table version")
    rows = table["certifications"]
    if not isinstance(rows, list):
        raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "certifications must be an array")
    capabilities = _json_file(root, CAPABILITY_PATH)
    if capabilities.get("schema_version") != "native-provider-schema-capabilities-v2":
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, "unknown capability register version")
    providers = capabilities.get("providers")
    if not isinstance(providers, list):
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, "capability register has no providers")
    provider_rows = {row.get("profile_id"): row for row in providers if isinstance(row, dict)}
    if len(provider_rows) != len(providers) or None in provider_rows:
        raise CertificationError(CertificationErrorCode.SOURCE_INVALID, "duplicate or invalid capability profile")
    manufacturers = _registered_manufacturers(table, provider_rows)
    entries: list[RoleProviderCertification] = []
    seen: set[tuple[AgentSlot, str]] = set()
    evidence_cache: dict[str, dict[str, Any]] = {}
    canary_cache: dict[str, dict[str, Any]] = {}
    for raw in rows:
        expected_keys = {
            "slot", "role", "provider", "capability_profile", "manufacturer", "status", "probe_profile",
            "version_scope", "capability_sha256", "policy_sha256",
            "rights_sha256", "evidence",
        }
        if isinstance(raw, dict):
            expected_keys.update({key for key in ("model_family_pattern", "canary_evidence") if key in raw})
        row = _require_keys(raw, expected_keys, "certification")
        try:
            slot = AgentSlot(row["slot"])
            role = AgentRoleName(row["role"])
        except (ValueError, TypeError) as exc:
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "unknown slot or role") from exc
        if role is not role_for_slot(slot):
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "slot and role differ")
        provider = row["provider"]
        if not isinstance(provider, str) or provider not in manufacturers:
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, f"provider has no registered manufacturer: {provider!r}")
        key = slot, provider
        if key in seen:
            raise CertificationError(CertificationErrorCode.DUPLICATE_ENTRY, f"duplicate slot/provider: {slot}/{provider}")
        seen.add(key)
        if row["status"] not in {"certified", "experimental", "candidate"}:
            raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "unknown certification status")
        if key in _BASELINE and row["status"] != "certified":
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "baseline qualification is not certified")
        manufacturer = row["manufacturer"]
        if manufacturer != manufacturers[provider]:
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "provider manufacturer differs")
        model_family_pattern = row.get("model_family_pattern")
        if model_family_pattern is not None:
            if not isinstance(model_family_pattern, str) or not model_family_pattern.startswith("^") or not model_family_pattern.endswith("$"):
                raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "model family pattern must be anchored")
            try:
                re.compile(model_family_pattern)
            except re.error as exc:
                raise CertificationError(CertificationErrorCode.ENTRY_INVALID, "invalid model family pattern") from exc
        profile_id = row["capability_profile"]
        provider_row = provider_rows.get(profile_id)
        if provider_row is None or provider_row["provider"] != provider:
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "selected capability profile differs from provider")
        expected_probe_profile = provider_row["transport_profile"]
        expected_version = {
            "cli_version": provider_row["cli_version"],
            "version_policy": provider_row["version_policy"],
        }
        if row["probe_profile"] != expected_probe_profile or row["version_scope"] != expected_version:
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "probe profile or version differs from capability register")
        if model_family_pattern is not None and (
            not isinstance(expected_probe_profile.get("model"), str)
            or re.fullmatch(model_family_pattern, expected_probe_profile["model"]) is None
        ):
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "model family differs from capability profile")
        if row["capability_sha256"] != _digest(provider_row):
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "capability digest differs")
        try:
            binding = binding_for(provider, role)
        except KeyError as exc:
            if row["status"] != "candidate":
                raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH,
                                         "missing provider/role rights binding") from exc
            binding = binding_for_role(role)
        if row["policy_sha256"] != binding.policy_sha256:
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "policy digest differs")
        if row["rights_sha256"] != binding.rights_sha256:
            raise CertificationError(CertificationErrorCode.SOURCE_MISMATCH, "rights digest differs")
        evidence = _require_keys(row["evidence"], {"path", "sha256"}, "evidence reference")
        evidence_path, evidence_digest = evidence["path"], evidence["sha256"]
        canary_ref = row.get("canary_evidence")
        if key not in _BASELINE and row["status"] != "candidate" and canary_ref is None:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "missing canary evidence")
        if canary_ref is not None:
            canary_ref = _require_keys(canary_ref, {"path", "sha256"}, "canary reference")
            canary_path = canary_ref["path"]
            if canary_path not in canary_cache:
                canary_cache[canary_path] = _parse_json(
                    _read_file(root, canary_path, expected_digest=canary_ref["sha256"]), canary_path,
                )
            else:
                _read_file(root, canary_path, expected_digest=canary_ref["sha256"])
            canary = canary_cache[canary_path]
            canary_version = canary["schema_version"]
            if canary_version in _LEGACY_CANARY_FORMATS:
                if (provider, canary_path) != _LEGACY_CANARY_FORMATS[canary_version]:
                    raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "unexpected legacy canary evidence path")
                if row["status"] != "candidate":
                    _validate_agy_canaries(root, canary_ref, model_family_pattern=model_family_pattern)
            elif canary["schema_version"] == "role-canary-v1":
                if not canary_path.startswith(f"docs/evidence/{provider}/"):
                    raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "canary evidence provider path differs")
                if row["status"] != "candidate":
                    _validate_role_canaries(canary, provider=provider, role=role, slot=slot,
                                            model_family_pattern=model_family_pattern)
            else:
                raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, "unknown canary evidence version")
        raw_evidence = _read_file(root, evidence_path, expected_digest=evidence_digest)
        if evidence_path not in evidence_cache:
            evidence_cache[evidence_path] = _parse_json(raw_evidence, evidence_path)
            _validate_evidence_slots(evidence_cache[evidence_path])
        document = evidence_cache[evidence_path]
        slot_refs = document["slots"]
        if slot.value not in slot_refs:
            raise CertificationError(CertificationErrorCode.EVIDENCE_INVALID, f"missing evidence for {slot.value}")
        entries.append(RoleProviderCertification(
            slot, role, provider, profile_id, manufacturer, row["status"],
            expected_probe_profile, expected_version, row["capability_sha256"],
            row["policy_sha256"], row["rights_sha256"],
            evidence_path, evidence_digest, model_family_pattern,
        ))
    missing = _BASELINE - seen
    if missing:
        raise CertificationError(CertificationErrorCode.BASELINE_MISSING, f"missing baseline qualifications: {sorted(missing)}")
    return CertificationTable(tuple(entries))


def load_role_certifications(
    *, root: Path = ROOT, table_path: str = TABLE_PATH,
) -> CertificationTable:
    """Validate the whole table and referenced evidence before returning any entry."""
    try:
        return _load_role_certifications(root=root, table_path=table_path)
    except CertificationError:
        raise
    except (TypeError, KeyError, AttributeError, IndexError, ValueError) as exc:
        raise CertificationError(
            CertificationErrorCode.ENTRY_INVALID,
            "malformed certification or capability field",
        ) from exc
