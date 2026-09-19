"""Dormant projection of native reviewer decisions into Finding records.

The joint 67/68 switch is the sole activation boundary.  Until it is enabled,
this module rejects projection and no production caller writes the new
decisions.  Implementer responsibility proposals are not an input to this
projection and therefore cannot acquire reviewer authority here.
"""

from __future__ import annotations

from typing import Sequence

from artifact_models import FindingSeverity, FindingTransitionPayload, Role
from contracts import FindingRecord
from finding_reducer import is_closed_finding_status
import native_finding_decisions
from native_review_contract import NativeReviewResult


def project_native_review_decision_payloads(
    response: NativeReviewResult,
    previous_findings: Sequence[FindingRecord],
    *,
    work_unit_id: int | str,
) -> tuple[FindingTransitionPayload, ...]:
    """Project authoritative reviewer closures and routes without writing them.

    The native response has already passed its request-bound validation.  This
    additional boundary still checks its exact runtime type and references so
    that an implementer proposal cannot be accidentally treated as a reviewer
    decision by a future caller.
    """

    if not isinstance(response, NativeReviewResult):
        raise TypeError(
            "finding decision projection requires a parsed NativeReviewResult"
        )

    findings = {item.finding_id: item for item in previous_findings}
    payloads: list[FindingTransitionPayload] = []
    for update in response.status_changes:
        if not is_closed_finding_status(update.status):
            continue
        finding = _referenced_open_finding(findings, update.finding_id)
        closure = update.closure
        if closure is None:
            raise ValueError(
                f"closed finding {update.finding_id} lacks its typed closure"
            )
        payloads.append(
            FindingTransitionPayload(
                finding_id=update.finding_id,
                reporter=Role(response.reviewer.value),
                actor=Role(response.reviewer.value),
                action="status_changed",
                severity=FindingSeverity(finding.finding_class.value),
                finding_status="closed",
                rationale=update.rationale,
                work_unit_id=str(work_unit_id),
                closure_kind=closure.kind.value,
                rejection_reason=(
                    None
                    if closure.rejection_reason is None
                    else closure.rejection_reason.value
                ),
                closure_evidence=closure.evidence,
            )
        )

    for route in response.responsibility_routes:
        finding = _referenced_open_finding(findings, route.finding_id)
        payloads.append(
            FindingTransitionPayload(
                finding_id=route.finding_id,
                reporter=Role(response.reviewer.value),
                actor=Role(response.reviewer.value),
                action="routed",
                severity=FindingSeverity(finding.finding_class.value),
                finding_status="open",
                rationale=route.rationale,
                work_unit_id=str(work_unit_id),
                responsibility=route.responsibility,
            )
        )
    return tuple(payloads)


def _referenced_open_finding(
    findings: dict[str, FindingRecord], finding_id: str
) -> FindingRecord:
    finding = findings.get(finding_id)
    if finding is None:
        raise ValueError(f"review decision references unknown finding {finding_id}")
    if is_closed_finding_status(finding.status):
        raise ValueError(f"review decision references closed finding {finding_id}")
    return finding


__all__ = ["project_native_review_decision_payloads"]
