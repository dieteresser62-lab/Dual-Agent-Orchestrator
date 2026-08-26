from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json"
LOCK = FIXTURE.with_name("native-only-cutover-baseline-v1.lock.json")
REVIEWED_BASELINE_SHA256 = "3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc"
REVIEWED_LOCK_SHA256 = "11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_reviewed_baseline_and_lock_are_immutable() -> None:
    baseline_bytes = FIXTURE.read_bytes()
    lock_bytes = LOCK.read_bytes()
    lock = json.loads(lock_bytes)

    assert _sha256(baseline_bytes) == REVIEWED_BASELINE_SHA256
    assert _sha256(lock_bytes) == REVIEWED_LOCK_SHA256
    assert lock["baseline_sha256"] == REVIEWED_BASELINE_SHA256
    assert lock["fixture_version"] == "native-only-cutover-baseline-v1"
    assert lock["expected_removed_evidence_ids"] == [
        "approved-plan",
        "workflow-prompt",
    ]


def test_frozen_baseline_component_bindings_are_internally_complete() -> None:
    document = json.loads(FIXTURE.read_bytes())
    assert document["fixture_version"] == "native-only-cutover-baseline-v1"
    operations = document["operations"]
    assert [item["operation"] for item in operations] == [
        "codex_plan",
        "codex_plan_revision",
        "codex_implementation",
        "codex_correction",
        "codex_final_review",
        "codex_final_correction",
    ]
    for row in operations:
        components = {item["name"]: item for item in row["components"]}
        assert set(components) >= {"stdin_prompt", "response_schema"}
        assert row["total_chars"] == sum(item["chars"] for item in components.values())
        assert row["total_bytes"] == sum(item["bytes"] for item in components.values())
        for binding in row["evidence_bindings"]:
            component = components[binding["component_name"]]
            assert component["sha256"] == binding["content_sha256"]
            assert binding["manifest_entry_chars"] > 0
            assert binding["manifest_entry_bytes"] >= binding["manifest_entry_chars"]


def test_current_native_request_builder_does_not_restore_workflow_prompt() -> None:
    workflow_source = (ROOT / "src/workflow.py").read_text(encoding="utf-8")
    assert '"workflow-prompt"' not in workflow_source
    assert '"native-policy", "system_policy"' in workflow_source
