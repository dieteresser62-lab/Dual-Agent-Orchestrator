"""Closed, provider-free diagnostics that may be shown to an operator."""

from __future__ import annotations

from enum import StrEnum


STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE = (
    "error_max_structured_output_retries"
)
STRUCTURED_OUTPUT_DIAGNOSTIC_CODE = "PROVIDER-STRUCTURED-OUTPUT"


class OrchestratorDiagnostic(StrEnum):
    """Diagnostics whose complete rendered text is owned by this repository."""

    CLASSIFIED_HALT_RULE = (
        "classified-halt: a repository-owned error rule halted the run"
    )
    PROVIDER_BUDGET_CONFIG_RULE = (
        "provider-budget-config: provider input budget configuration is incomplete or invalid"
    )

    WORKFLOW_EXECUTION_RULE = (
        "workflow-execution: a deterministic workflow rule halted the run"
    )
    WORKFLOW_REVIEW_CONTEXT_MISSING = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=context"
    )
    WORKFLOW_REVIEW_CONTEXT_WORK_UNIT_ID = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=work_unit_id"
    )
    WORKFLOW_REVIEW_CONTEXT_DIFF_FINGERPRINT = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=diff_fingerprint"
    )
    WORKFLOW_REVIEW_CONTEXT_ROUND_NUMBER = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=round_number"
    )
    WORKFLOW_REVIEW_CONTEXT_REQUEST_SEQUENCE = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=request_sequence"
    )
    WORKFLOW_REVIEW_CONTEXT_REVIEWER = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=reviewer"
    )
    WORKFLOW_REVIEW_CONTEXT_VALIDATION_ATTESTATION = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=validation_attestation"
    )
    WORKFLOW_REVIEW_CONTEXT_TEST_FILES = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=test_files"
    )
    WORKFLOW_REVIEW_CONTEXT_RED_STATE_FOLLOWUP_SLICE = (
        "workflow-execution: native review persistence differs from its exact review context: "
        "field=red_state_followup_slice"
    )
    WORKFLOW_REVIEW_VALIDATION_ATTESTATION_MISSING = (
        "workflow-execution: native review persistence lacks its validation attestation"
    )
    WORKFLOW_REVIEW_VALIDATION_RECORD_NOT_UNIQUE = (
        "workflow-execution: native review persistence has no unique earlier validation record"
    )
    WORKFLOW_REVIEW_BINDING_MISSING = (
        "workflow-execution: native review persistence lacks its immutable Claude binding"  # allowlist:provider -- closed static diagnostic
    )
    WORKFLOW_REVIEW_CONTENT_DIGEST_MISMATCH = (
        "workflow-execution: native reviewer content digest differs from its review binding"
    )
    WORKFLOW_IMPLEMENTER_BINDING_MISSING = (
        "workflow-execution: native Codex persistence lacks its immutable transport binding"  # allowlist:provider -- closed static diagnostic
    )
    WORKFLOW_IMPLEMENTER_LOGICAL_BINDING_MISMATCH = (
        "workflow-execution: native agent result logical binding differs"
    )
    WORKFLOW_IMPLEMENTER_CONTENT_DIGEST_MISMATCH = (
        "workflow-execution: native agent content digest differs from its result binding"
    )
    WORKFLOW_BRANCH_DISCOVERY_BINDING_MISSING = (
        "workflow-execution: branch discovery completion lacks its dedicated run binding"
    )
    WORKFLOW_BRANCH_DISCOVERY_HEAD_MISSING = (
        "workflow-execution: branch discovery completion lacks its reviewed HEAD"
    )

    IMPLEMENTER_SCHEMA_INVALID = (
        "schema-invalid: native implementer output must satisfy its closed result schema"
    )
    IMPLEMENTER_CONTEXT_INVALID = (
        "context-invalid: native implementer context must satisfy its bound contract"
    )
    IMPLEMENTER_REQUEST_MISMATCH = (
        "request-mismatch: native implementer output must match its bound request"
    )
    IMPLEMENTER_RESULT_KIND_MISMATCH = (
        "result-kind-mismatch: native implementer result kind must match its bound request"
    )
    IMPLEMENTER_RESULT_CONTENT_INVALID = (
        "result-content-invalid: native implementer result content must satisfy its closed contract"
    )
    IMPLEMENTER_FINDING_REFERENCE_INVALID = (
        "finding-reference-invalid: native implementer finding references must match the offered open findings"
    )
    IMPLEMENTER_TEST_FILES_INVALID = (
        "test-files-invalid: native implementer test files must satisfy the step contract"
    )
    IMPLEMENTER_SLICE_PLAN_INVALID = (
        "slice-plan-invalid: native implementer slice plan must satisfy the planning contract"
    )
    IMPLEMENTER_STOP_CONTENT_INVALID = (
        "stop-content-invalid: native implementer stop content must satisfy the selected stop rule"
    )
    IMPLEMENTER_DORMANT_FINDING_DECISION_FIELD = (
        "dormant-finding-decision-field: native implementer output must not use dormant finding decision fields"
    )

    REVIEW_SCHEMA_INVALID = (
        "schema-invalid: native review output must satisfy its closed result schema"
    )
    REVIEW_CONTEXT_INVALID = (
        "context-invalid: native review context must satisfy its bound contract"
    )
    REVIEW_REQUEST_MISMATCH = (
        "request-mismatch: native review output must match its bound request"
    )
    REVIEW_REVIEWER_MISMATCH = (
        "reviewer-mismatch: native review output must name its bound reviewer"
    )
    REVIEW_FINDING_ID_INVALID = (
        "finding-id-invalid: native review finding ids must satisfy their bound namespace"
    )
    REVIEW_FINDING_REFERENCE_UNKNOWN = (
        "finding-reference-unknown: native review finding references must name known findings"
    )
    REVIEW_FINDING_REFERENCE_NOT_OPEN = (
        "finding-reference-not-open: native review finding updates must name open findings"
    )
    REVIEW_FINDING_EVENT_CONFLICT = (
        "finding-event-conflict: native review finding events must be mutually consistent"
    )
    REVIEW_FINDING_UPDATE_MISSING = (
        "missing-own-finding-update: native review output must update every required owned finding"
    )
    REVIEW_FINDING_CONTENT_INVALID = (
        "finding-content-invalid: native review finding content must satisfy its closed contract"
    )
    REVIEW_FINDING_SIGNATURE_DUPLICATE = (
        "finding-signature-duplicate: native review findings must not duplicate a known signature"
    )
    REVIEW_ACCEPTANCE_INVALID = (
        "acceptance-invalid: native review acceptance evidence must satisfy its bound contract"
    )
    REVIEW_ANCHOR_INVALID = (
        "anchor-invalid: native review anchors must satisfy their bound contract"
    )
    REVIEW_CONTENT_MISSING = (
        "review-content-missing: native review output must contain the required review content"
    )
    REVIEW_STOP_CONTENT_INVALID = (
        "stop-content-invalid: native review stop content must satisfy the selected stop rule"
    )
    REVIEW_APPROVAL_INVALID = (
        "approval-invalid: native review decision must satisfy its approval contract"
    )
    REVIEW_DORMANT_FINDING_DECISION_FIELD = (
        "dormant-finding-decision-field: native review output must not use dormant finding decision fields"
    )

    IMPLEMENTER_CONTEXT_REQUEST_KIND_INVALID = "context-invalid: request_kind is invalid"
    IMPLEMENTER_CONTEXT_REQUIRES_TYPED_CONTRACT = (
        "context-invalid: native Co"
        "dex context requires Co"
        "dexStepContract and a non-empty "
        "frozenset of known stop rule ids"
    )
    IMPLEMENTER_CONTEXT_PREVIOUS_FINDINGS_SORTED = (
        "context-invalid: previous findings must be sorted and unique"
    )
    IMPLEMENTER_CONTEXT_BOUND_CONTEXT_TYPED = (
        "context-invalid: bound native Co"
        "dex context requires NativeCo"
        "dexContext"
    )
    IMPLEMENTER_CONTEXT_BOUND_REQUEST_ID = (
        "context-invalid: bound request_id must contain request_digest"
    )
    IMPLEMENTER_SCHEMA_BUNDLED_OBJECT = (
        "schema-invalid: bundled native Co"
        "dex schema must be an object"
    )
    IMPLEMENTER_CONTEXT_PROVIDER_PROJECTION = (
        "context-invalid: provider schema projection requires NativeCo"
        "dexContext"
    )
    IMPLEMENTER_CONTEXT_PARSING_BOUND = (
        "context-invalid: native parsing requires BoundNativeCo"
        "dexContext"
    )
    IMPLEMENTER_RESPONSE_REQUEST_MISMATCH = (
        "request-mismatch: response request_id does not match bound request"
    )
    SLICE_PLAN_PATHS_INVALID = (
        "slice-plan-invalid: "
        "planned slice paths must be sorted, unique, and non-empty"
    )
    IMPLEMENTER_SLICE_PLAN_IDS_INVALID = (
        "slice-plan-invalid: slice plan ids must be contiguous and 1-based"
    )
    IMPLEMENTER_PARSED_RESPONSE_REQUEST_MISMATCH = (
        "request-mismatch: parsed response does not match bound request"
    )
    IMPLEMENTER_SLICE_PLAN_CONTRACT_REQUIRED = (
        "slice-plan-invalid: plan result requires a slice plan contract"
    )
    IMPLEMENTER_RESPONSE_VARIANT_UNSUPPORTED = (
        "result-kind-mismatch: unsupported Co"
        "dex response variant"
    )
    IMPLEMENTER_FINDING_DISPOSITIONS_SORTED = (
        "finding-reference-invalid: finding dispositions must be sorted and unique"
    )
    IMPLEMENTER_PLAN_TREATMENTS_SORTED = (
        "slice-plan-invalid: plan treatments must be sorted and unique by signature"
    )
    IMPLEMENTER_TEST_FILES_UNEXPECTED = (
        "test-files-invalid: unexpected test_files for this step"
    )
    IMPLEMENTER_TEST_FILES_CONTRACT_MISMATCH = (
        "test-files-invalid: test_files do not match the step contract"
    )
    IMPLEMENTER_TEST_CHANGES_REQUIRE_APPROVAL = (
        "test-files-invalid: ready result with test changes requires prior approval"
    )

    IMPLEMENTER_PLAN_TREATMENT_SIGNATURE_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: plan treatment signature "
        "must be a lowercase SHA-256 digest"
    )
    IMPLEMENTER_PLAN_TREATMENT_FINDING_IDS_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: plan treatment finding_ids "
        "must be non-empty, sorted, and unique"
    )
    IMPLEMENTER_PLAN_TREATMENT_KIND_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: plan treatment kind must be typed"
    )
    IMPLEMENTER_IMPLEMENTATION_TREATMENT_CLOSING_SLICE_REQUIRED = (
        "slice-plan-invalid: plan treatment is invalid: implementation treatment "
        "requires exactly one closing Slice"
    )
    IMPLEMENTER_PLAN_TREATMENT_CLOSING_SLICE_ID_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: closing Slice id must be a positive integer"
    )
    IMPLEMENTER_IMPLEMENTATION_TREATMENT_FORBIDS_NO_CODE_FIELDS = (
        "slice-plan-invalid: plan treatment is invalid: implementation treatment "
        "forbids No-Code disposition fields"
    )
    IMPLEMENTER_NO_CODE_TREATMENT_FORBIDS_CLOSING_SLICES = (
        "slice-plan-invalid: plan treatment is invalid: No-Code disposition forbids closing Slice ids"
    )
    IMPLEMENTER_NO_CODE_TREATMENT_REASON_REQUIRED = (
        "slice-plan-invalid: plan treatment is invalid: No-Code disposition requires a typed reason"
    )
    IMPLEMENTER_NO_CODE_TREATMENT_EVIDENCE_REQUIRED = (
        "slice-plan-invalid: plan treatment is invalid: No-Code disposition evidence must not be empty"
    )
    IMPLEMENTER_NO_CODE_TREATMENT_EVIDENCE_PATHS_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: No-Code disposition evidence_paths "
        "must be non-empty, sorted, and unique"
    )
    IMPLEMENTER_NO_CODE_TREATMENT_EVIDENCE_PATH_UNSAFE = (
        "slice-plan-invalid: plan treatment is invalid: No-Code disposition evidence_paths "
        "contains a non-canonical repository path"
    )
    IMPLEMENTER_ALREADY_FIXED_TREATMENT_AFFECTED_PATHS_INVALID = (
        "slice-plan-invalid: plan treatment is invalid: already-fixed disposition affected_paths "
        "must be non-empty, sorted, and unique"
    )
    IMPLEMENTER_ALREADY_FIXED_TREATMENT_AFFECTED_PATH_UNSAFE = (
        "slice-plan-invalid: plan treatment is invalid: already-fixed disposition affected_paths "
        "contains a non-canonical repository path"
    )
    IMPLEMENTER_ONLY_ALREADY_FIXED_MAY_NAME_AFFECTED_PATHS = (
        "slice-plan-invalid: plan treatment is invalid: only an already-fixed disposition "
        "may name affected_paths"
    )
    IMPLEMENTER_SIGNATURE_GROUPS_SORTED = (
        "slice-plan-invalid: canonical signature groups must be sorted and unique"
    )
    IMPLEMENTER_PLAN_TREATMENT_COVERAGE_SORTED = (
        "slice-plan-invalid: plan treatments must contain exactly one sorted treatment per signature"
    )
    IMPLEMENTER_IMPLEMENTATION_SLICES_CONTIGUOUS = (
        "slice-plan-invalid: implementation Slices must be contiguous and 1-based"
    )
    IMPLEMENTER_IMPLEMENTATION_CLOSING_SLICE_PRESENT = (
        "slice-plan-invalid: implementation treatment closing Slice is absent from the plan"
    )
    IMPLEMENTER_PLAN_COMPLETION_TYPED = (
        "slice-plan-invalid: plan completion must be typed"
    )
    IMPLEMENTER_NO_IMPLEMENTATION_FORBIDS_TREATMENTS = (
        "slice-plan-invalid: NO_IMPLEMENTATION_REQUIRED forbids implementation treatments"
    )
    IMPLEMENTER_NO_IMPLEMENTATION_FORBIDS_SLICES = (
        "slice-plan-invalid: NO_IMPLEMENTATION_REQUIRED forbids implementation Slices"
    )
    IMPLEMENTER_IMPLEMENTATION_REQUIRES_SLICE = (
        "slice-plan-invalid: IMPLEMENTATION_REQUIRED requires at least one Slice"
    )

    REVIEW_CONTEXT_DISCOVERY_CAPACITY_SCOPE = (
        "context-invalid: max_new_findings is valid only for a branch discovery review"
    )
    REVIEW_CONTEXT_ROUND_NUMBER_INVALID = (
        "context-invalid: round_number must be 1-based"
    )
    REVIEW_CONTEXT_REVIEWER_INVALID = (
        "context-invalid: reviewer must be clau"
        "de"
    )
    REVIEW_CONTEXT_DIFF_FINGERPRINT_INVALID = (
        "context-invalid: diff_fingerprint must be lowercase SHA-256"
    )
    REVIEW_CONTEXT_ATTESTATION_FINGERPRINT_MISMATCH = (
        "context-invalid: validation attestation fingerprint does not match context"
    )
    REVIEW_CONTEXT_PRE_CHANGE_FINGERPRINT_INVALID = (
        "context-invalid: pre_change_fingerprint must be lowercase SHA-256"
    )
    REVIEW_CONTEXT_PREVIOUS_FINDINGS_SORTED = (
        "context-invalid: previous findings must be sorted and unique"
    )
    REVIEW_CONTEXT_AUTHORITATIVE_IDS_SORTED = (
        "context-invalid: authoritative finding ids must be sorted and unique"
    )
    REVIEW_CONTEXT_AUTHORITATIVE_IDS_NAMESPACE = (
        "context-invalid: authoritative finding ids must use the C-01 namespace"
    )
    REVIEW_CONTEXT_OFFERED_FINDINGS_AUTHORITATIVE = (
        "context-invalid: offered findings must belong to the authoritative finding set"
    )
    REVIEW_CONTEXT_KNOWN_OPEN_SORTED = (
        "context-invalid: known open findings must be sorted and unique"
    )
    REVIEW_CONTEXT_KNOWN_OPEN_OPEN = (
        "context-invalid: known open findings must all be open"
    )
    REVIEW_CONTEXT_KNOWN_OPEN_AUTHORITATIVE = (
        "context-invalid: known open findings must belong to the authoritative finding set"
    )
    REVIEW_CONTEXT_OFFERED_OPEN_MATCH = (
        "context-invalid: offered open findings must match the known open finding set"
    )
    REVIEW_CONTEXT_TEST_FILES_INVALID = (
        "context-invalid: test files must be sorted, unique, and non-empty"
    )
    REVIEW_CONTEXT_PLANNED_SLICES_TYPED = (
        "context-invalid: planned slices must contain only typed PlannedSlice values"
    )
    REVIEW_CONTEXT_PLANNED_SLICE_IDS_INVALID = (
        "context-invalid: planned slice ids must be contiguous and 1-based"
    )
    REVIEW_CONTEXT_ANCHOR_ORIGIN_INVALID = (
        "context-invalid: anchor_origin must be non-empty when present"
    )
    REVIEW_CONTEXT_RED_STATE_SLICE_INVALID = (
        "context-invalid: red_state_followup_slice must be non-empty when present"
    )
    REVIEW_CONTEXT_PLAN_ARTIFACT_PATH_INVALID = (
        "context-invalid: plan_artifact_path must be one exact repository-relative Markdown path"
    )
    REVIEW_CONTEXT_PLAN_ARTIFACT_SCOPE_INVALID = (
        "context-invalid: plan_artifact_path is valid only for a plan review"
    )
    REVIEW_CONTEXT_LEGACY_FINAL_COUNT_UNSUPPORTED = (
        "context-invalid: legacy final_review_pending_count is unsupported"
    )
    REVIEW_CONTEXT_FINAL_COUNT_SCOPE_INVALID = (
        "context-invalid: final_review_pending_count is valid only for a final review"
    )
    REVIEW_CONTEXT_VALIDATION_PREFIXES_INVALID = (
        "context-invalid: validation command prefixes must be unique safe argv prefixes"
    )
    REVIEW_CONTEXT_IMPLEMENTER_PROPOSALS_INVALID = (
        "context-invalid: implementer responsibility proposals must be typed, sorted, and unique"
    )
    REVIEW_CONTEXT_PLAN_TREATMENTS_TYPED = (
        "context-invalid: plan treatments must be typed"
    )
    REVIEW_CONTEXT_PLAN_TREATMENTS_SCOPE_INVALID = (
        "context-invalid: plan treatments are valid only for a plan review"
    )
    REVIEW_CONTEXT_PLAN_TREATMENTS_SORTED = (
        "context-invalid: plan treatments must be sorted and unique by signature"
    )
    REVIEW_CONTEXT_PLAN_TREATMENTS_COVERAGE = (
        "context-invalid: plan treatments must cover every canonical open signature exactly once"
    )
    REVIEW_CONTEXT_PLAN_TREATMENT_FINDING_IDS = (
        "context-invalid: plan treatment Finding IDs differ from their canonical signature group"
    )
    REVIEW_CONTEXT_CLOSED_BINDINGS_TYPED = (
        "context-invalid: closed Finding bindings must be typed"
    )
    REVIEW_CONTEXT_CLOSED_BINDINGS_SCOPE_INVALID = (
        "context-invalid: closed Finding bindings are valid only for branch discovery"
    )
    REVIEW_CONTEXT_CLOSED_BINDINGS_SORTED = (
        "context-invalid: closed Finding bindings must be sorted and unique"
    )
    REVIEW_CONTEXT_CLOSED_BINDING_TARGET_INVALID = (
        "context-invalid: closed Finding binding does not reference a closed offered Finding"
    )
    REVIEW_CONTEXT_CLOSED_BINDING_SIGNATURE_INVALID = (
        "context-invalid: closed Finding binding signature differs from the Finding"
    )
    REVIEW_CONTEXT_BOUND_CONTEXT_TYPED = (
        "context-invalid: bound review context requires a NativeReviewContext"
    )
    REVIEW_CONTEXT_BOUND_REQUEST_DIGEST_INVALID = (
        "context-invalid: bound request_digest must be lowercase SHA-256"
    )
    REVIEW_CONTEXT_BOUND_REQUEST_ID_INVALID = (
        "context-invalid: bound request_id must contain request_digest"
    )
    REVIEW_SCHEMA_BUNDLED_OBJECT = (
        "schema-invalid: bundled native review schema must be an object"
    )
    REVIEW_CONTEXT_PROVIDER_PROJECTION = (
        "context-invalid: provider schema projection requires NativeReviewContext"
    )
    REVIEW_CONTEXT_REVIEWER_WRITER = (
        "context-invalid: native Clau"
        "de writer schema requires reviewer=clau"
        "de"
    )
    REVIEW_RESPONSE_REQUEST_MISMATCH = (
        "request-mismatch: response request_id does not match bound context"
    )
    REVIEW_RESPONSE_REVIEWER_MISMATCH = (
        "reviewer-mismatch: response reviewer does not match bound context"
    )
    REVIEW_DISCOVERY_REQUEST_REQUIRED = (
        "approval-invalid: BRANCH_DISCOVERY_COMPLETED requires a branch discovery request"
    )
    REVIEW_DISCOVERY_RESULT_KIND_INVALID = (
        "approval-invalid: branch discovery cannot use approved or denied review_result"
    )
    REVIEW_PARSED_REQUEST_MISMATCH = (
        "request-mismatch: parsed response does not match bound context"
    )
    REVIEW_PARSED_REVIEWER_MISMATCH = (
        "reviewer-mismatch: parsed response reviewer does not match bound context"
    )
    REVIEW_CONTEXT_LIVE_PARSING_BOUND = (
        "context-invalid: live native review parsing requires BoundNativeReviewContext"
    )
    REVIEW_REJECTED_CLOSURE_REASON_REQUIRED = (
        "finding-content-invalid: rejected closure is missing required field rejection_reason"
    )
    REVIEW_REJECTED_CLOSURE_EVIDENCE_REQUIRED = (
        "finding-content-invalid: rejected closure requires named evidence"
    )
    REVIEW_IMPLEMENTATION_TREATMENT_CLOSURE_FORBIDDEN = (
        "finding-content-invalid: implementation treatment cannot close its Finding in plan review"
    )
    REVIEW_CONTENT_EVENT_OR_EVIDENCE_REQUIRED = (
        "review-content-missing: review requires at least one finding event or review evidence"
    )
    REVIEW_FINDING_EVENT_UNIQUE = (
        "finding-event-conflict: finding id occurs in more than one event"
    )
    REVIEW_FINDING_ID_REUSE_FORBIDDEN = (
        "finding-event-conflict: new finding reuses a previous finding id"
    )
    REVIEW_OBSERVATION_VALIDATION_FORBIDDEN = (
        "acceptance-invalid: OBSERVATION cannot request a validation command"
    )
    REVIEW_VALIDATION_COMMAND_FAMILY_INVALID = (
        "acceptance-invalid: validation command is outside configured families"
    )
    REVIEW_ANCHOR_ORIGIN_REQUIRED = (
        "anchor-invalid: native anchors require a bound anchor_origin"
    )
    REVIEW_PROSE_ACCEPTANCE_PREFIX_RESERVED = (
        "acceptance-invalid: prose acceptance must not use the reserved typed VALIDATE prefix"
    )
    REVIEW_DISCOVERY_MARKER_REQUIRED = (
        "approval-invalid: BRANCH_DISCOVERY_COMPLETED requires its dedicated request marker"
    )
    REVIEW_DISCOVERY_SCAN_COMPLETE_REQUIRED = (
        "approval-invalid: branch discovery completion requires scan_complete=true"
    )
    REVIEW_DISCOVERY_PASS_ATTESTATION_REQUIRED = (
        "approval-invalid: branch discovery completion requires a complete PASS attestation"
    )
    REVIEW_DISCOVERY_TEST_APPROVAL_REQUIRED = (
        "approval-invalid: branch discovery completion with test changes requires prior approval"
    )
    REVIEW_DISCOVERY_OCCURRENCES_UNIQUE = (
        "finding-event-conflict: branch discovery occurrences must reference each finding at most once"
    )
    REVIEW_DISCOVERY_OCCURRENCE_KNOWN = (
        "finding-reference-unknown: branch discovery occurrence references an unknown Finding"
    )
    REVIEW_OPEN_OCCURRENCE_CLOSED_ANCHOR_FORBIDDEN = (
        "finding-content-invalid: open Finding occurrence forbids a closed-disposition anchor"
    )
    REVIEW_CLOSED_OCCURRENCE_UNCHANGED_ANCHOR_REQUIRED = (
        "finding-event-conflict: closed Finding occurrence requires an unchanged evidence anchor"
    )
    REVIEW_CLOSED_OCCURRENCE_ANCHOR_MISMATCH = (
        "finding-content-invalid: closed Finding occurrence carries another evidence anchor"
    )
    REVIEW_CLOSED_PREDECESSOR_MATCH_REQUIRED = (
        "finding-reference-unknown: new Finding names no matching closed predecessor"
    )
    REVIEW_REDISCOVERY_PREDECESSOR_REQUIRED = (
        "finding-reference-unknown: rediscovered closed signature requires predecessor_finding_ref"
    )
    REVIEW_UNCHANGED_SIGNATURE_REQUIRES_OCCURRENCE = (
        "finding-signature-duplicate: unchanged closed signature must be recorded as an occurrence"
    )
    REVIEW_NEW_GENERATION_ANCHOR_MISMATCH = (
        "finding-content-invalid: new Finding generation carries another evidence anchor"
    )
    REVIEW_ANCHOR_IDS_UNIQUE = "anchor-invalid: native anchor ids must be unique"
    REVIEW_OBSERVATION_CHANGE_FORBIDDEN = (
        "approval-invalid: review cannot introduce or reclassify to OBSERVATION"
    )
    REVIEW_DENIAL_REQUIRES_BLOCKER = (
        "approval-invalid: denied review requires an open own BLOCKER"
    )
    REVIEW_APPROVAL_ATTESTATION_REQUIRED = (
        "approval-invalid: approval requires a complete PASS attestation or named red-state follow-up"
    )
    REVIEW_APPROVAL_TEST_CHANGES_REQUIRE_APPROVAL = (
        "approval-invalid: approval with test changes requires prior approval"
    )
    REVIEW_APPROVAL_PRE_MORTEM_REQUIRED = (
        "approval-invalid: approval requires pre_mortem"
    )
    REVIEW_APPROVAL_OPEN_BLOCKER_FORBIDDEN = (
        "approval-invalid: approval is invalid while an own BLOCKER is open"
    )

    @property
    def detail(self) -> str:
        _code, separator, detail = self.value.partition(": ")
        if not separator:
            raise AssertionError("orchestrator diagnostic must contain a code prefix")
        return detail

    @property
    def text(self) -> str:
        return self.value


ORCHESTRATOR_DIAGNOSTIC_TEXTS = frozenset(
    item.value for item in OrchestratorDiagnostic
)
