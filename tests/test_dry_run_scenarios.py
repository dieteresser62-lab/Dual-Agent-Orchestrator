from __future__ import annotations

import json
from pathlib import Path

import pytest

from dry_run_scenarios import (
    DryRunScenario,
    DryRunScenarioError,
    ScriptedAgentEvent,
    load_dry_run_scenario,
)
from contracts import AgentRole
from workflow_state import WorkflowStep


def _native_plan_result() -> dict[str, object]:
    return {
        "schema_version": "native-agent-codex-result-v2",
        "request_id": "$BOUND_REQUEST_ID",
        "result_type": "plan_result",
        "ready": True,
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Execute the native Slice.",
                "scope_paths": ["src/workflow.py"],
            }
        ],
        "finding_dispositions": [],
    }


def _scenario_document() -> dict[str, object]:
    return {
        "version": 1,
        "name": "native-plan",
        "agent_events": [
            {
                "role": "codex",
                "work_unit_id": 1,
                "request_sequence": 1,
                "step": "codex_plan",
                "output": _native_plan_result(),
            }
        ],
        "changes": [],
    }


def test_scenario_accepts_only_native_json_agent_documents() -> None:
    scenario = DryRunScenario.from_dict(_scenario_document())
    event = scenario.agent_events[0]
    assert event.output == _native_plan_result()
    assert event.role is AgentRole.CODEX
    assert event.step is WorkflowStep.CODEX_PLAN


def test_scenario_accepts_legacy_round_spelling_as_request_sequence() -> None:
    document = _scenario_document()
    event = document["agent_events"][0]  # type: ignore[index]
    event["round_number"] = event.pop("request_sequence")  # type: ignore[union-attr]

    parsed = DryRunScenario.from_dict(document).agent_events[0]

    assert parsed.request_sequence == 1


def test_scenario_rejects_retired_marker_output() -> None:
    document = _scenario_document()
    document["agent_events"][0]["output"] = "PLAN_READY: YES\nSTATUS: DONE"  # type: ignore[index]
    with pytest.raises(DryRunScenarioError, match="output must be an object"):
        DryRunScenario.from_dict(document)


def test_scenario_rejects_retired_contract_repair_channel() -> None:
    document = _scenario_document()
    document["repair_outputs"] = ["repaired"]
    with pytest.raises(DryRunScenarioError, match="unknown key 'repair_outputs'"):
        DryRunScenario.from_dict(document)


def test_scenario_rejects_retired_productive_path_count_option() -> None:
    document = _scenario_document()
    retired_option = "max_productive" + "_files"
    document["context"] = {retired_option: 15}

    with pytest.raises(DryRunScenarioError, match=f"unknown key '{retired_option}'"):
        DryRunScenario.from_dict(document)


def test_scripted_event_requires_exactly_one_native_output_or_failure() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ScriptedAgentEvent(AgentRole.CODEX, 1, 1, WorkflowStep.CODEX_PLAN)


def test_load_scenario_preserves_native_document_bytes_semantically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(_scenario_document()), encoding="utf-8")
    loaded = load_dry_run_scenario(path)
    assert loaded.agent_events[0].output == _native_plan_result()


def test_scenario_parses_exact_gate_identity_expectation() -> None:
    document = _scenario_document()
    document["expect"] = {
        "exit_code": 4,
        "gate": {
            "status": "awaiting_user_decision",
            "reason": "unexpected_file",
            "rule_id": "HEAD-DRIFT",
            "kind": "user",
            "fingerprint": "d" * 64,
            "paths": ["src/workflow.py"],
            "resume_step": "slice_commit",
        },
    }

    expected = DryRunScenario.from_dict(document).expect

    assert expected is not None
    assert expected.gate is not None
    assert expected.gate.rule_id == "HEAD-DRIFT"
    assert expected.gate.paths == ("src/workflow.py",)
    assert expected.gate.resume_step is WorkflowStep.SLICE_COMMIT


def test_scenario_rejects_noncanonical_gate_rule_identity() -> None:
    document = _scenario_document()
    document["expect"] = {
        "exit_code": 4,
        "gate": {
            "status": "awaiting_user_decision",
            "reason": "unexpected_file",
            "rule_id": "prefix HEAD-DRIFT suffix",
            "kind": "user",
            "paths": ["src/workflow.py"],
        },
    }

    with pytest.raises(DryRunScenarioError, match="exact uppercase rule id"):
        DryRunScenario.from_dict(document)
