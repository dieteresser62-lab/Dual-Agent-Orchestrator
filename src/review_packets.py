from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from contracts import FindingRecord, FindingStatus, ValidationAttestation
from plan_handoff import (
    PlanHandoffError,
    extract_slice_requirements as extract_plan_slice_requirements,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ReviewPacketError(ValueError):
    """Raised when authoritative review inputs cannot form one canonical packet."""


@dataclass(frozen=True)
class ReviewPacketManifest:
    paths: tuple[str, ...]
    additional_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.paths or self.paths != tuple(sorted(set(self.paths))):
            raise ReviewPacketError("review packet paths must be sorted, unique, and non-empty")
        if self.additional_dependencies:
            raise ReviewPacketError("release 1.1B does not allow implicit review dependencies")
        for path in self.paths:
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ReviewPacketError(f"unsafe review packet path: {path!r}")


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

    @property
    def text(self) -> str:
        return self.canonical_bytes.decode("utf-8")


def extract_slice_requirements(plan_text: str, slice_id: int) -> tuple[str, tuple[str, ...]]:
    """Expose the shared plan-handoff requirement parser to packet callers."""
    try:
        return extract_plan_slice_requirements(plan_text, slice_id)
    except PlanHandoffError as exc:
        raise ReviewPacketError(str(exc)) from exc


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
    manifest = ReviewPacketManifest(paths=paths)
    review_diff = _filter_diff_to_manifest(review_diff, manifest.paths)
    if not review_diff.strip():
        raise ReviewPacketError("review packet diff contains no manifest path")
    finding_by_id = {item.finding_id: item for item in findings}
    if len(finding_by_id) != len(findings):
        raise ReviewPacketError("review packet findings must be unique")
    affected = tuple(sorted(set(affected_finding_ids)))
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
        # Correction Slices are created by the workflow after a final-review
        # denial and therefore need not exist in the originally approved plan.
        # Their fingerprint-bound finding set is the authoritative correction
        # contract: it supplies both the goal and the exact acceptance tests.
        goal = "Resolve reviewer findings " + ", ".join(affected)
        criteria = tuple(
            f"{item.finding_id}: {item.acceptance_test}" for item in selected
        )
    else:
        goal, criteria = extract_slice_requirements(plan_text, slice_id)
    active = [
        {
            "id": item.finding_id,
            "class": item.finding_class.value,
            "owner": item.origin.reporter.value,
            "status": item.status.value,
            "summary": item.summary,
            "acceptance_test": item.acceptance_test,
        }
        for item in sorted(selected, key=lambda value: value.finding_id)
        if item.status is FindingStatus.OPEN
    ]
    closures = []
    for item in sorted(selected, key=lambda value: value.finding_id):
        if item.status is not FindingStatus.CLOSED:
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
        "schema": "review-packet-v1",
        "purpose": purpose,
        "fingerprint": fingerprint,
        "start_fingerprint": start_fingerprint,
        "manifest": {
            "paths": list(manifest.paths),
            "additional_dependencies": [],
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


def _filter_diff_to_manifest(review_diff: str, paths: tuple[str, ...]) -> str:
    """Retain complete git diff sections for manifest paths, without parsing hunks."""
    marker = re.compile(r"(?m)^diff --git a/(.+?) b/(.+?)$")
    matches = list(marker.finditer(review_diff))
    if not matches:
        # Synthetic drivers use compact delta labels rather than unified diffs.
        return review_diff
    allowed = set(paths)
    sections: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(review_diff)
        if match.group(1) in allowed or match.group(2) in allowed:
            sections.append(review_diff[match.start():end].rstrip())
    return "\n".join(sections)


def _compact_one_line(value: str, maximum: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= maximum:
        return compact
    return compact[: maximum - 12].rstrip() + " …[truncated]"


__all__ = [
    "ReviewPacket", "ReviewPacketError", "ReviewPacketManifest",
    "build_review_packet", "extract_slice_requirements",
]
