from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from contracts import FindingRecord, ValidationAttestation
from finding_order import finding_id_sort_key, sorted_finding_ids
from finding_reducer import project_open_set, project_request_subset
from plan_handoff import (
    PlanHandoffError,
    extract_slice_requirements as extract_plan_slice_requirements,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReviewPacketError(ValueError):
    """Raised when authoritative review inputs cannot form one canonical packet."""


@dataclass(frozen=True)
class BinaryFileMetadata:
    path: str
    change_type: str
    old_size: int | None
    old_sha256: str | None
    new_size: int | None
    new_sha256: str | None

    def __post_init__(self) -> None:
        _validate_repository_path(self.path)
        if self.change_type not in {"added", "modified", "deleted"}:
            raise ReviewPacketError("binary change type is invalid")
        for size, digest in ((self.old_size, self.old_sha256), (self.new_size, self.new_sha256)):
            if (size is None) != (digest is None):
                raise ReviewPacketError("binary size and SHA-256 must occur together")
            if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
                raise ReviewPacketError("binary size is invalid")
            if digest is not None and (
                not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None
            ):
                raise ReviewPacketError("binary SHA-256 is invalid")
        if (self.old_size is None) != (self.change_type == "added") or (
            (self.new_size is None) != (self.change_type == "deleted")
        ):
            raise ReviewPacketError("binary sides contradict change type")


@dataclass(frozen=True)
class DiffCoverageEntry:
    path: str
    change_type: str
    section_sha256: str
    hunk_headers: tuple[str, ...]
    old_size: int | None = None
    old_sha256: str | None = None
    new_size: int | None = None
    new_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_repository_path(self.path)
        if self.change_type not in {"added", "modified", "deleted"}:
            raise ReviewPacketError("diff coverage change type is invalid")
        if not SHA256_PATTERN.fullmatch(self.section_sha256):
            raise ReviewPacketError("diff coverage section digest must be SHA-256")
        for header in self.hunk_headers:
            if not _HUNK_HEADER.fullmatch(header):
                raise ReviewPacketError(f"invalid diff hunk header: {header!r}")
        if not self.hunk_headers:
            BinaryFileMetadata(self.path, self.change_type, self.old_size,
                               self.old_sha256, self.new_size, self.new_sha256)
        elif any(value is not None for value in (self.old_size, self.old_sha256,
                                                  self.new_size, self.new_sha256)):
            raise ReviewPacketError("text coverage cannot carry binary metadata")

    @property
    def is_binary(self) -> bool:
        return not self.hunk_headers


@dataclass(frozen=True)
class ReviewPacketManifest:
    paths: tuple[str, ...]
    additional_dependencies: tuple[str, ...] = ()
    diff_coverage: tuple[DiffCoverageEntry, ...] = ()
    diff_coverage_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.paths or self.paths != tuple(sorted(set(self.paths))):
            raise ReviewPacketError("review packet paths must be sorted, unique, and non-empty")
        if self.additional_dependencies:
            raise ReviewPacketError("release 1.1B does not allow implicit review dependencies")
        for path in self.paths:
            _validate_repository_path(path)
        if self.diff_coverage:
            coverage_paths = tuple(item.path for item in self.diff_coverage)
            if coverage_paths != self.paths:
                raise ReviewPacketError("diff coverage must match all manifest paths exactly")
            actual = _coverage_digest(self.diff_coverage)
            if self.diff_coverage_digest != actual:
                raise ReviewPacketError("diff coverage manifest digest does not match entries")
        elif self.diff_coverage_digest is not None:
            raise ReviewPacketError("legacy manifest cannot carry a coverage digest")

    @property
    def snapshot_paths(self) -> tuple[str, ...]:
        if not self.diff_coverage:
            return self.paths
        return tuple(
            item.path for item in self.diff_coverage
            if not item.is_binary and item.change_type != "deleted"
        )


@dataclass(frozen=True)
class ReviewPacket:
    purpose: str
    fingerprint: str
    manifest: ReviewPacketManifest
    canonical_bytes: bytes
    digest: str

    def __post_init__(self) -> None:
        if self.purpose not in {"slice", "correction"}:
            raise ReviewPacketError("review packet purpose must be slice or correction")
        if not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise ReviewPacketError("review packet requires a SHA-256 fingerprint")
        actual = hashlib.sha256(self.canonical_bytes).hexdigest()
        if self.digest != actual:
            raise ReviewPacketError("review packet digest does not match canonical bytes")
        _validate_packet_document(self)

    @property
    def text(self) -> str:
        return self.canonical_bytes.decode("utf-8")

    @classmethod
    def restore(cls, canonical_bytes: bytes, digest: str) -> ReviewPacket:
        """Restore manifest metadata from authoritative canonical packet bytes."""
        try:
            payload = json.loads(canonical_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewPacketError("review packet canonical bytes are invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ReviewPacketError("review packet canonical bytes must contain an object")
        manifest_raw = payload.get("manifest")
        if not isinstance(manifest_raw, dict):
            raise ReviewPacketError("review packet manifest is missing")
        paths_raw = manifest_raw.get("paths")
        if not isinstance(paths_raw, list) or not all(isinstance(p, str) for p in paths_raw):
            raise ReviewPacketError("review packet manifest paths are invalid")
        coverage: tuple[DiffCoverageEntry, ...] = ()
        coverage_digest: str | None = None
        if payload.get("schema") == "review-packet-v2":
            coverage_raw = manifest_raw.get("diff_coverage")
            coverage_digest = manifest_raw.get("diff_coverage_digest")
            if not isinstance(coverage_raw, list) or not isinstance(coverage_digest, str):
                raise ReviewPacketError("review packet v2 coverage manifest is missing")
            coverage = tuple(_coverage_entry_from_dict(item) for item in coverage_raw)
        return cls(
            purpose=str(payload.get("purpose", "")),
            fingerprint=str(payload.get("fingerprint", "")),
            manifest=ReviewPacketManifest(
                paths=tuple(paths_raw),
                diff_coverage=coverage,
                diff_coverage_digest=coverage_digest,
            ),
            canonical_bytes=canonical_bytes,
            digest=digest,
        )


def extract_slice_requirements(plan_text: str, slice_id: int) -> tuple[str, tuple[str, ...]]:
    """Expose the shared plan-handoff requirement parser to packet callers."""
    try:
        return extract_plan_slice_requirements(plan_text, slice_id)
    except PlanHandoffError as exc:
        raise ReviewPacketError(str(exc)) from exc


def derive_correction_requirements(
    findings: tuple[FindingRecord, ...],
    finding_ids: tuple[str, ...],
) -> tuple[str, tuple[str, ...], tuple[FindingRecord, ...]]:
    """Derive the exact local contract for a correction absent from the plan."""
    affected = sorted_finding_ids(finding_ids)
    if not affected:
        raise ReviewPacketError(
            "correction requirements need at least one affected finding"
        )
    try:
        projection = project_request_subset(
            findings,
            finding_ids=affected,
            open_only=True,
        )
    except ValueError as exc:
        raise ReviewPacketError(str(exc)) from exc
    if projection.finding_ids != affected:
        raise ReviewPacketError(
            "correction requirements differ from the exact affected open finding set"
        )
    goal = "Resolve reviewer findings " + ", ".join(affected)
    criteria = tuple(
        f"{item.finding_id}: {item.acceptance_test}"
        for item in projection.findings
    )
    return goal, criteria, projection.findings


def build_review_packet(
    *,
    purpose: str,
    fingerprint: str,
    start_fingerprint: str,
    paths: tuple[str, ...],
    review_diff: str,
    plan_text: str,
    slice_id: int,
    attestation: ValidationAttestation,
    findings: tuple[FindingRecord, ...],
    affected_finding_ids: tuple[str, ...] = (),
    binary_metadata: tuple[BinaryFileMetadata, ...] = (),
) -> ReviewPacket:
    """Build the role-neutral, content-addressed Slice/correction evidence packet."""
    if purpose not in {"slice", "correction"}:
        raise ReviewPacketError("review packet purpose must be slice or correction")
    if not SHA256_PATTERN.fullmatch(fingerprint) or not SHA256_PATTERN.fullmatch(start_fingerprint):
        raise ReviewPacketError("review packet boundaries require SHA-256 fingerprints")
    if not review_diff.strip():
        raise ReviewPacketError("review packet requires a non-empty diff or correction delta")
    if attestation.diff_fingerprint != fingerprint or not attestation.complete:
        raise ReviewPacketError(
            "review packet requires a fingerprint-bound complete attestation"
        )
    base_manifest = ReviewPacketManifest(paths=paths)
    expected_binary = {item.path: item for item in binary_metadata}
    if len(expected_binary) != len(binary_metadata):
        raise ReviewPacketError("duplicate binary metadata path")
    review_diff, coverage = _canonicalize_diff(
        review_diff, base_manifest.paths, expected_binary=expected_binary
    )
    if {item.path for item in coverage if item.is_binary} != set(expected_binary):
        raise ReviewPacketError("binary metadata does not match diff coverage")
    manifest = ReviewPacketManifest(
        paths=paths,
        diff_coverage=coverage,
        diff_coverage_digest=_coverage_digest(coverage),
    )
    finding_by_id = {item.finding_id: item for item in findings}
    if len(finding_by_id) != len(findings):
        raise ReviewPacketError("review packet findings must be unique")
    affected = sorted_finding_ids(affected_finding_ids)
    if purpose == "correction" and not affected:
        raise ReviewPacketError("correction packet requires affected findings")
    if affected and any(item not in finding_by_id for item in affected):
        raise ReviewPacketError("correction packet references an unknown finding")
    selected = (
        tuple(finding_by_id[item] for item in affected)
        if purpose == "correction"
        else tuple(findings)
    )
    if purpose == "correction":
        # Final-review corrections are not Slices from the approved plan. Their
        # exact open finding set is the authoritative local work contract.
        goal, criteria, selected = derive_correction_requirements(
            findings,
            affected,
        )
    else:
        goal, criteria = extract_slice_requirements(plan_text, slice_id)
    open_projection = project_open_set(selected)
    active = [
        {
            "id": item.finding_id,
            "class": item.finding_class.value,
            "owner": item.origin.reporter.value,
            "status": item.status.value,
            "summary": item.summary,
            "acceptance_test": item.acceptance_test,
        }
        for item in open_projection.findings
    ]
    closures = []
    open_ids = frozenset(open_projection.finding_ids)
    for item in sorted(
        selected, key=lambda value: finding_id_sort_key(value.finding_id)
    ):
        if item.finding_id in open_ids:
            continue
        fact = f"{item.finding_id}|CLOSED|{item.status_rationale or ''}"
        closures.append(
            {
                "id": item.finding_id,
                "status": "CLOSED",
                "closure_digest": hashlib.sha256(fact.encode("utf-8")).hexdigest(),
                "summary": _compact_one_line(item.status_rationale or ""),
            }
        )

    payload = {
        "schema": "review-packet-v2",
        "purpose": purpose,
        "fingerprint": fingerprint,
        "start_fingerprint": start_fingerprint,
        "manifest": {
            "paths": list(manifest.paths),
            "additional_dependencies": [],
            "diff_coverage": [_coverage_entry_to_dict(item) for item in coverage],
            "diff_coverage_digest": manifest.diff_coverage_digest,
        },
        "slice": {"id": slice_id, "goal": goal, "acceptance_criteria": list(criteria)},
        "diff": review_diff,
        "attestation": {
            "id": attestation.attestation_id,
            "fingerprint": attestation.diff_fingerprint,
            "status": attestation.status.value,
            "output_digest": attestation.output_digest,
            "commands": [
                {"command": record.command, "status": record.status.value, "exit_code": record.exit_code}
                for record in attestation.records
            ],
        },
        "open_findings": active,
        "closure_references": closures,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False
    ).encode("utf-8")
    return ReviewPacket(
        purpose=purpose,
        fingerprint=fingerprint,
        manifest=manifest,
        canonical_bytes=canonical,
        digest=hashlib.sha256(canonical).hexdigest(),
    )


_DIFF_HEADER = re.compile(r"^diff --git a/([^\s]+) b/([^\s]+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$")
_UNTRACKED_BINARY = re.compile(r"^Binary file; size=(0|[1-9][0-9]*); sha256=([0-9a-f]{64})$")
_CANONICAL_BINARY = re.compile(
    r"^Binary file; old_size=(-|0|[1-9][0-9]*); old_sha256=(-|[0-9a-f]{64}); "
    r"new_size=(-|0|[1-9][0-9]*); new_sha256=(-|[0-9a-f]{64})$"
)
_AUDIT_EDGE = re.compile(
    r"^<!-- audit:(?P<key>[a-z0-9-]+):(?P<edge>begin|end) -->$"
)


def _canonicalize_diff(
    review_diff: str, paths: tuple[str, ...],
    *, expected_binary: Mapping[str, BinaryFileMetadata] | None = None,
    allow_canonical_binary: bool = False,
) -> tuple[str, tuple[DiffCoverageEntry, ...]]:
    normalized = review_diff.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\n").startswith("diff --git ")]
    if not starts or starts[0] != 0:
        raise ReviewPacketError("review packet requires a marker-delimited unified Git diff")
    sections: list[tuple[str, str, DiffCoverageEntry]] = []
    allowed = set(paths)
    seen: set[str] = set()
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        section_lines = lines[start:end]
        header = section_lines[0].rstrip("\n")
        match = _DIFF_HEADER.fullmatch(header)
        if match is None:
            raise ReviewPacketError(f"unsafe or unsupported diff header: {header!r}")
        old_path, new_path = match.groups()
        _validate_repository_path(old_path)
        _validate_repository_path(new_path)
        if old_path != new_path:
            raise ReviewPacketError("rename, copy, or mismatched diff paths are unsupported")
        path = old_path
        if path not in allowed:
            raise ReviewPacketError(f"diff path is not authorized: {path}")
        if path in seen:
            raise ReviewPacketError(f"duplicate diff section for path: {path}")
        seen.add(path)
        text_lines = [line.rstrip("\n") for line in section_lines]
        _validate_regular_modes(text_lines)
        if (expected_binary is not None and path in expected_binary) or any(
               line == "GIT binary patch" or line.startswith("Binary file;")
               or line.startswith("Binary files ") for line in text_lines):
            section, entry = _canonicalize_binary_section(
                path, text_lines, expected_binary, allow_canonical_binary
            )
            sections.append((path, section, entry))
            continue
        hunk_indexes = [index for index, line in enumerate(text_lines) if line.startswith("@@")]
        if not hunk_indexes:
            raise ReviewPacketError("text diff section requires valid hunk headers")
        prelude = text_lines[:hunk_indexes[0]]
        forbidden = ("rename from ", "rename to ", "copy from ", "copy to ", "Binary files ")
        if any(line.startswith(forbidden) or line == "GIT binary patch" for line in prelude):
            raise ReviewPacketError("rename, copy, and binary diffs are unsupported")
        new_markers = [line for line in prelude if line.startswith("new file mode ")]
        deleted_markers = [line for line in prelude if line.startswith("deleted file mode ")]
        if len(new_markers) > 1 or len(deleted_markers) > 1 or (new_markers and deleted_markers):
            raise ReviewPacketError("diff contains contradictory change metadata")
        old_headers = [line for line in prelude if line.startswith("--- ")]
        new_headers = [line for line in prelude if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise ReviewPacketError("diff section requires exactly one old/new file header pair")
        if new_markers:
            change_type = "added"
            expected_headers = ("--- /dev/null", f"+++ b/{path}")
        elif deleted_markers:
            change_type = "deleted"
            expected_headers = (f"--- a/{path}", "+++ /dev/null")
        else:
            change_type = "modified"
            expected_headers = (f"--- a/{path}", f"+++ b/{path}")
        if (old_headers[0], new_headers[0]) != expected_headers:
            raise ReviewPacketError("diff file headers contradict its change type or path")
        hunk_headers = tuple(line for line in text_lines if line.startswith("@@"))
        if not hunk_headers or any(_HUNK_HEADER.fullmatch(line) is None for line in hunk_headers):
            raise ReviewPacketError("text diff section requires valid hunk headers")
        if path.startswith("docs/internal/") and path.endswith(".md"):
            _validate_semantic_marker_hunks(text_lines[hunk_indexes[0] :])
        section = "".join(section_lines).rstrip("\n") + "\n"
        entry = DiffCoverageEntry(
            path=path,
            change_type=change_type,
            section_sha256=hashlib.sha256(section.encode("utf-8")).hexdigest(),
            hunk_headers=hunk_headers,
        )
        sections.append((path, section, entry))
    if seen != allowed:
        missing = ", ".join(sorted(allowed - seen))
        raise ReviewPacketError(f"diff is missing manifest path coverage: {missing}")
    sections.sort(key=lambda item: item[0])
    canonical_diff = "".join(item[1] for item in sections)
    return canonical_diff, tuple(item[2] for item in sections)


def _validate_regular_modes(lines: list[str]) -> None:
    for line in lines[1:]:
        if line.startswith(("new file mode ", "deleted file mode ", "old mode ", "new mode ")):
            if line.rsplit(" ", 1)[-1] not in {"100644", "100755"}:
                raise ReviewPacketError("special file modes are unsupported")
        elif line.startswith("index "):
            parts = line.split(" ")
            if len(parts) == 3 and parts[-1].isdigit() and parts[-1] not in {"100644", "100755"}:
                raise ReviewPacketError("special file modes are unsupported")


def _canonicalize_binary_section(
    path: str, lines: list[str],
    expected: Mapping[str, BinaryFileMetadata] | None,
    allow_canonical: bool,
) -> tuple[str, DiffCoverageEntry]:
    while lines and not lines[-1]:
        lines = lines[:-1]
    if any(line.startswith(("rename from ", "rename to ", "copy from ", "copy to "))
           for line in lines):
        raise ReviewPacketError("rename and copy sections are unsupported")
    added = [line for line in lines if line.startswith("new file mode ")]
    deleted = [line for line in lines if line.startswith("deleted file mode ")]
    if len(added) > 1 or len(deleted) > 1 or (added and deleted):
        raise ReviewPacketError("binary change metadata is contradictory")
    if any(line not in {"new file mode 100644", "new file mode 100755",
                        "deleted file mode 100644", "deleted file mode 100755"}
           for line in added + deleted):
        raise ReviewPacketError("binary special files are unsupported")
    change_type = "added" if added else "deleted" if deleted else "modified"
    metadata_lines = [line for line in lines if line.startswith("Binary file;")]
    canonical_lines = [line for line in metadata_lines if _CANONICAL_BINARY.fullmatch(line)]
    if allow_canonical and len(lines) == 2 and len(canonical_lines) == 1:
        canonical_match = _CANONICAL_BINARY.fullmatch(canonical_lines[0])
        assert canonical_match is not None
        change_type = (
            "added" if canonical_match.group(1) == "-" else
            "deleted" if canonical_match.group(3) == "-" else "modified"
        )
    source = expected.get(path) if expected is not None else None
    if source is None and not (allow_canonical and len(lines) == 2 and len(canonical_lines) == 1):
        raise ReviewPacketError("binary section lacks authoritative metadata")
    if source is not None:
        if source.change_type != change_type:
            raise ReviewPacketError("binary change type differs from authoritative metadata")
        if len(metadata_lines) > 1:
            raise ReviewPacketError("binary section has multiple metadata lines")
        if metadata_lines:
            raw = _UNTRACKED_BINARY.fullmatch(metadata_lines[0])
            if raw is None or change_type != "added" or (
                int(raw.group(1)), raw.group(2)
            ) != (source.new_size, source.new_sha256):
                raise ReviewPacketError("binary metadata line is missing or manipulated")
            if lines != [
                f"diff --git a/{path} b/{path}", added[0],
                "--- /dev/null", f"+++ b/{path}", metadata_lines[0],
            ]:
                raise ReviewPacketError("untracked binary section has unsupported content")
        elif "GIT binary patch" not in lines and not any(
            line.startswith("Binary files ") for line in lines
        ):
            old_header = "--- /dev/null" if change_type == "added" else f"--- a/{path}"
            new_header = "+++ /dev/null" if change_type == "deleted" else f"+++ b/{path}"
            if old_header not in lines or new_header not in lines or not any(
                _HUNK_HEADER.fullmatch(line) for line in lines
            ):
                raise ReviewPacketError("binary section lacks a valid Git diff body")
        metadata = source
    else:
        raw = _CANONICAL_BINARY.fullmatch(canonical_lines[0])
        assert raw is not None
        old_size, old_digest, new_size, new_digest = raw.groups()
        metadata = BinaryFileMetadata(
            path, change_type,
            None if old_size == "-" else int(old_size),
            None if old_digest == "-" else old_digest,
            None if new_size == "-" else int(new_size),
            None if new_digest == "-" else new_digest,
        )
    def value(item: object) -> str:
        return "-" if item is None else str(item)
    canonical = (
        f"diff --git a/{path} b/{path}\n"
        f"Binary file; old_size={value(metadata.old_size)}; "
        f"old_sha256={value(metadata.old_sha256)}; "
        f"new_size={value(metadata.new_size)}; "
        f"new_sha256={value(metadata.new_sha256)}\n"
    )
    entry = DiffCoverageEntry(
        path, metadata.change_type, hashlib.sha256(canonical.encode()).hexdigest(), (),
        metadata.old_size, metadata.old_sha256, metadata.new_size, metadata.new_sha256,
    )
    return canonical, entry


def _validate_semantic_marker_hunks(lines: list[str]) -> None:
    """Reject review diffs that expose bytes inside a managed projection body."""
    for prefixes in ((" ", "-"), (" ", "+")):
        active: str | None = None
        for line in lines:
            if line.startswith("@@"):
                active = None
                continue
            if not line.startswith(prefixes) or line.startswith(("+++ ", "--- ")):
                continue
            content = line[1:]
            marker = _AUDIT_EDGE.fullmatch(content)
            if marker is not None:
                key = marker.group("key")
                if marker.group("edge") == "begin":
                    active = key
                elif active == key:
                    active = None
                continue
            if active is not None and content.strip():
                raise ReviewPacketError(
                    "review diff contains non-semantic managed audit projection bytes"
                )


def exclude_review_diff_paths(review_diff: str, excluded_paths: tuple[str, ...]) -> str:
    """Remove only explicitly managed, structurally delimited diff sections."""
    if not excluded_paths:
        return review_diff
    excluded = set(excluded_paths)
    normalized = review_diff.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\n").startswith("diff --git ")]
    if not starts or starts[0] != 0:
        return review_diff
    retained: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        match = _DIFF_HEADER.fullmatch(lines[start].rstrip("\n"))
        if match is None or match.group(1) != match.group(2) or match.group(1) not in excluded:
            retained.extend(lines[start:end])
    return "".join(retained)


def _coverage_entry_to_dict(item: DiffCoverageEntry) -> dict[str, object]:
    result = {
        "path": item.path,
        "change_type": item.change_type,
        "section_sha256": item.section_sha256,
        "hunk_headers": list(item.hunk_headers),
    }
    if item.is_binary:
        result.update(old_size=item.old_size, old_sha256=item.old_sha256,
                      new_size=item.new_size, new_sha256=item.new_sha256)
    return result


def _coverage_entry_from_dict(raw: object) -> DiffCoverageEntry:
    common = {"path", "change_type", "section_sha256", "hunk_headers"}
    binary = {"old_size", "old_sha256", "new_size", "new_sha256"}
    if not isinstance(raw, dict) or set(raw) not in (common, common | binary):
        raise ReviewPacketError("diff coverage entry has invalid fields")
    headers = raw["hunk_headers"]
    if not isinstance(headers, list) or not all(isinstance(item, str) for item in headers):
        raise ReviewPacketError("diff coverage hunk headers are invalid")
    return DiffCoverageEntry(str(raw["path"]), str(raw["change_type"]),
                             str(raw["section_sha256"]), tuple(headers),
                             *(raw[key] for key in ("old_size", "old_sha256", "new_size", "new_sha256"))
                             if set(raw) == common | binary else ())


def _coverage_digest(entries: tuple[DiffCoverageEntry, ...]) -> str:
    canonical = json.dumps(
        {"schema": "diff-coverage-manifest-v1", "entries": [_coverage_entry_to_dict(item) for item in entries]},
        ensure_ascii=False, separators=(",", ":"), sort_keys=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_repository_path(path: str) -> None:
    if (
        not path or path.startswith("/") or ".." in path.split("/")
        or path != path.replace("\\", "/") or any(char in path for char in "\x00\r\n")
        or any(part in {"", "."} for part in path.split("/"))
    ):
        raise ReviewPacketError(f"unsafe review packet path: {path!r}")


def _validate_packet_document(packet: ReviewPacket) -> None:
    try:
        payload = json.loads(packet.canonical_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewPacketError("review packet canonical bytes are invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("purpose") != packet.purpose or payload.get("fingerprint") != packet.fingerprint:
        raise ReviewPacketError("review packet metadata differs from canonical bytes")
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False
    ).encode("utf-8")
    if canonical != packet.canonical_bytes:
        raise ReviewPacketError("review packet bytes are not canonical JSON")
    schema = payload.get("schema")
    manifest_raw = payload.get("manifest")
    if not isinstance(manifest_raw, dict) or manifest_raw.get("paths") != list(packet.manifest.paths):
        raise ReviewPacketError("review packet manifest differs from canonical bytes")
    if schema == "review-packet-v1":
        if packet.manifest.diff_coverage:
            raise ReviewPacketError("legacy packet cannot claim diff coverage")
        return
    if schema != "review-packet-v2":
        raise ReviewPacketError("unsupported review packet schema")
    if not packet.manifest.diff_coverage:
        raise ReviewPacketError("review packet v2 requires diff coverage")
    if manifest_raw.get("diff_coverage_digest") != packet.manifest.diff_coverage_digest:
        raise ReviewPacketError("review packet coverage digest differs from canonical bytes")
    if manifest_raw.get("diff_coverage") != [_coverage_entry_to_dict(item) for item in packet.manifest.diff_coverage]:
        raise ReviewPacketError("review packet coverage entries differ from canonical bytes")
    canonical_diff, coverage = _canonicalize_diff(
        str(payload.get("diff", "")), packet.manifest.paths,
        allow_canonical_binary=True,
    )
    if canonical_diff != payload.get("diff") or coverage != packet.manifest.diff_coverage:
        raise ReviewPacketError("review packet diff coverage differs from canonical diff")


def _compact_one_line(value: str, maximum: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= maximum:
        return compact
    return compact[: maximum - 12].rstrip() + " …[truncated]"


__all__ = [
    "BinaryFileMetadata", "DiffCoverageEntry", "ReviewPacket", "ReviewPacketError", "ReviewPacketManifest",
    "build_review_packet", "exclude_review_diff_paths", "extract_slice_requirements",
]
