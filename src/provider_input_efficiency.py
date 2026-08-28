"""Canonical execution packages and reproducible provider-input deltas."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Mapping, Sequence

from contracts import FindingRecord, FindingStatus
from plan_handoff import PlanHandoffError, extract_slice_requirements


SLICE_PACKAGE_SCHEMA = "native-slice-execution-package-v1"
CORRECTION_PACKAGE_SCHEMA = "native-correction-execution-package-v1"
_SLICE_HEADING = re.compile(
    r"^###\s+Slice\s+(?P<id>\d+)\s*(?:[–—-])\s*.+?$", re.MULTILINE
)
_REFERENCE_HEADING = re.compile(
    r"^(?:#{1,6}\s+Querverweise|\*\*Querverweise:?\*\*)\s*$", re.MULTILINE
)
_NEXT_SECTION = re.compile(r"^(?:#{1,6}\s+|\*\*[^*]+\*\*\s*$)", re.MULTILINE)


class ProviderInputEfficiencyError(ValueError):
    """Raised when minimized evidence cannot preserve its authoritative binding."""


@dataclass(frozen=True, slots=True)
class CanonicalExecutionPackage:
    schema_version: str
    canonical_json: str
    sha256: str

    def __post_init__(self) -> None:
        try:
            document = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ProviderInputEfficiencyError("execution package is invalid JSON") from exc
        if not isinstance(document, dict) or document.get("schema_version") != self.schema_version:
            raise ProviderInputEfficiencyError("execution package schema differs from its envelope")
        if self.canonical_json != _canonical_json(document):
            raise ProviderInputEfficiencyError("execution package is not canonical JSON")
        if self.sha256 != _sha256_text(self.canonical_json):
            raise ProviderInputEfficiencyError("execution package digest differs from content")


@dataclass(frozen=True, slots=True)
class InputComponentFingerprint:
    name: str
    chars: int
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProviderInputDelta:
    operation: str
    baseline_chars: int
    baseline_bytes: int
    current_chars: int
    current_bytes: int
    removed: tuple[InputComponentFingerprint, ...]
    added: tuple[InputComponentFingerprint, ...]
    unchanged: tuple[InputComponentFingerprint, ...]

    @property
    def char_reduction(self) -> int:
        return self.baseline_chars - self.current_chars

    @property
    def byte_reduction(self) -> int:
        return self.baseline_bytes - self.current_bytes

    def __post_init__(self) -> None:
        removed_chars = sum(item.chars for item in self.removed)
        added_chars = sum(item.chars for item in self.added)
        removed_bytes = sum(item.bytes for item in self.removed)
        added_bytes = sum(item.bytes for item in self.added)
        if self.char_reduction != removed_chars - added_chars:
            raise ProviderInputEfficiencyError("character delta is not component-complete")
        if self.byte_reduction != removed_bytes - added_bytes:
            raise ProviderInputEfficiencyError("byte delta is not component-complete")


def build_slice_execution_package(
    *,
    plan_text: str,
    source_plan_path: str,
    slice_id: int,
    authorized_paths: tuple[str, ...],
    findings: tuple[FindingRecord, ...] = (),
) -> CanonicalExecutionPackage:
    """Project one approved Slice without transporting sibling Slice content."""
    _require_repository_path(source_plan_path, "source plan path")
    _require_sorted_paths(authorized_paths, "authorized paths")
    try:
        goal, criteria = extract_slice_requirements(plan_text, slice_id)
    except PlanHandoffError as exc:
        raise ProviderInputEfficiencyError(str(exc)) from exc
    references = _extract_explicit_references(plan_text, slice_id)
    finding_by_id = {item.finding_id: item for item in findings}
    if len(finding_by_id) != len(findings):
        raise ProviderInputEfficiencyError("slice findings must be unique")
    open_findings = [
        {
            "finding_id": item.finding_id,
            "finding_class": item.finding_class.value,
            "reporter": item.origin.reporter.value,
            "summary": item.summary,
            "acceptance_test": item.acceptance_test,
        }
        for item in sorted(findings, key=lambda value: value.finding_id)
        if item.status is FindingStatus.OPEN
    ]
    document = {
        "schema_version": SLICE_PACKAGE_SCHEMA,
        "source_plan": {
            "path": source_plan_path,
            "sha256": _sha256_text(plan_text),
        },
        "slice": {
            "slice_id": slice_id,
            "goal": goal,
            "acceptance_criteria": list(criteria),
            "authorized_paths": list(authorized_paths),
            "cross_references": list(references),
            "open_findings": open_findings,
        },
    }
    return _package(SLICE_PACKAGE_SCHEMA, document)


def build_correction_execution_package(
    *,
    current_fingerprint: str,
    authorized_paths: tuple[str, ...],
    findings: tuple[FindingRecord, ...],
    current_delta: str,
) -> CanonicalExecutionPackage:
    """Bind only affected open findings, their criteria, and the current delta."""
    _require_sha256(current_fingerprint, "current fingerprint")
    _require_sorted_paths(authorized_paths, "authorized paths")
    if not current_delta.strip():
        raise ProviderInputEfficiencyError("correction package requires a current delta")
    ordered = tuple(sorted(findings, key=lambda item: item.finding_id))
    if not ordered or len({item.finding_id for item in ordered}) != len(ordered):
        raise ProviderInputEfficiencyError("correction findings must be non-empty and unique")
    if any(item.status is not FindingStatus.OPEN for item in ordered):
        raise ProviderInputEfficiencyError("correction package accepts only open findings")
    document = {
        "schema_version": CORRECTION_PACKAGE_SCHEMA,
        "current_fingerprint": current_fingerprint,
        "authorized_paths": list(authorized_paths),
        "affected_findings": [
            {
                "finding_id": item.finding_id,
                "finding_class": item.finding_class.value,
                "reporter": item.origin.reporter.value,
                "summary": item.summary,
                "acceptance_test": item.acceptance_test,
            }
            for item in ordered
        ],
        "current_delta": {
            "sha256": _sha256_text(current_delta),
            "content": current_delta,
        },
    }
    return _package(CORRECTION_PACKAGE_SCHEMA, document)


def compare_provider_input_components(
    *,
    operation: str,
    baseline_row: Mapping[str, object],
    current_components: Sequence[tuple[str, str]],
) -> ProviderInputDelta:
    """Prove one total delta from canonical removed, added, and unchanged parts."""
    raw_components = baseline_row.get("components")
    if not isinstance(raw_components, list):
        raise ProviderInputEfficiencyError("baseline components are missing")
    baseline = tuple(_baseline_component(item) for item in raw_components)
    current = tuple(_current_component(name, content) for name, content in current_components)
    _require_unique_component_names(baseline, "baseline")
    _require_unique_component_names(current, "current")
    baseline_by_name = {item.name: item for item in baseline}
    current_by_name = {item.name: item for item in current}
    unchanged = tuple(
        baseline_by_name[name]
        for name in sorted(set(baseline_by_name).intersection(current_by_name))
        if baseline_by_name[name] == current_by_name[name]
    )
    unchanged_names = {item.name for item in unchanged}
    removed = tuple(
        baseline_by_name[name]
        for name in sorted(set(baseline_by_name).difference(unchanged_names))
    )
    added = tuple(
        current_by_name[name]
        for name in sorted(set(current_by_name).difference(unchanged_names))
    )
    baseline_chars = _require_total(baseline_row, "total_chars")
    baseline_bytes = _require_total(baseline_row, "total_bytes")
    if baseline_chars != sum(item.chars for item in baseline):
        raise ProviderInputEfficiencyError("baseline character total is inconsistent")
    if baseline_bytes != sum(item.bytes for item in baseline):
        raise ProviderInputEfficiencyError("baseline byte total is inconsistent")
    return ProviderInputDelta(
        operation=operation,
        baseline_chars=baseline_chars,
        baseline_bytes=baseline_bytes,
        current_chars=sum(item.chars for item in current),
        current_bytes=sum(item.bytes for item in current),
        removed=removed,
        added=added,
        unchanged=unchanged,
    )


def _extract_explicit_references(plan_text: str, slice_id: int) -> tuple[str, ...]:
    headings = tuple(_SLICE_HEADING.finditer(plan_text))
    selected = tuple(item for item in headings if int(item.group("id")) == slice_id)
    if len(selected) != 1:
        raise ProviderInputEfficiencyError(f"approved plan must contain exactly one Slice {slice_id}")
    heading = selected[0]
    end = next((item.start() for item in headings if item.start() > heading.start()), len(plan_text))
    section = plan_text[heading.end():end]
    reference_heading = _REFERENCE_HEADING.search(section)
    if reference_heading is None:
        return ()
    after = section[reference_heading.end():]
    next_section = _NEXT_SECTION.search(after)
    block = after[: next_section.start() if next_section else len(after)]
    references = tuple(
        line[2:].strip()
        for line in block.splitlines()
        if line.startswith("- ") and line[2:].strip()
    )
    if references != tuple(dict.fromkeys(references)):
        raise ProviderInputEfficiencyError("cross references must be unique and ordered")
    return references


def _package(schema_version: str, document: Mapping[str, object]) -> CanonicalExecutionPackage:
    canonical = _canonical_json(document)
    return CanonicalExecutionPackage(schema_version, canonical, _sha256_text(canonical))


def _baseline_component(value: object) -> InputComponentFingerprint:
    if not isinstance(value, dict):
        raise ProviderInputEfficiencyError("baseline component is not an object")
    try:
        component = InputComponentFingerprint(
            name=str(value["name"]),
            chars=int(value["chars"]),
            bytes=int(value["bytes"]),
            sha256=str(value["sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderInputEfficiencyError("baseline component is malformed") from exc
    _validate_component(component)
    return component


def _current_component(name: str, content: str) -> InputComponentFingerprint:
    component = InputComponentFingerprint(
        name=name,
        chars=len(content),
        bytes=len(content.encode("utf-8")),
        sha256=_sha256_text(content),
    )
    _validate_component(component)
    return component


def _validate_component(component: InputComponentFingerprint) -> None:
    if not component.name or component.chars < 0 or component.bytes < 0:
        raise ProviderInputEfficiencyError("input component metadata is invalid")
    _require_sha256(component.sha256, "component digest")


def _require_unique_component_names(
    components: tuple[InputComponentFingerprint, ...], label: str
) -> None:
    if len({item.name for item in components}) != len(components):
        raise ProviderInputEfficiencyError(f"{label} component names are not unique")


def _require_total(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderInputEfficiencyError(f"baseline {key} is invalid")
    return value


def _require_repository_path(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or path.as_posix() != value
        or path.parts[0] == ".orchestrator"
    ):
        raise ProviderInputEfficiencyError(f"{label} must be repository-relative")


def _require_sorted_paths(values: tuple[str, ...], label: str) -> None:
    if not values or values != tuple(sorted(set(values))):
        raise ProviderInputEfficiencyError(f"{label} must be non-empty, sorted, and unique")
    for value in values:
        _require_repository_path(value, label)


def _require_sha256(value: str, label: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProviderInputEfficiencyError(f"{label} must be lowercase SHA-256")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
