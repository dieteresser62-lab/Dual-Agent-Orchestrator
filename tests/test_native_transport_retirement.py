from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_adapters import (
    NativeClaudeReviewAdapter,
    NativeCodexAdapter,
    build_agent_registry,
)
from agent_config import AgentSettings
from cli import build_parser, parse_args
from workflow_state import ProtocolBinding, WorkflowStateValidationError


ROOT = Path(__file__).resolve().parents[1]
RETIRED_RUNTIME_SYMBOLS = (
    "validate_codex_response",
    "validate_review_response",
    "parse_finding_responses",
    "parse_anchors",
    "normalize_codex_contract_output",
    "normalize_review_contract_output",
    "repair_review_contract",
    "recover_failed_reviewer_output",
    "claude_contract_repair",
    "class CodexAdapter",
    "class ClaudeAdapter",
    '"workflow-prompt"',
)
RETIRED_ROOT_RESULT_MARKERS = (
    "PLAN_READY:",
    "IMPLEMENTATION_READY:",
    "FINAL_REPORT_READY:",
    "REVIEWER:",
    "NEW_FINDING:",
    "FINDING_STATUS:",
    "FINDING_RESPONSE:",
    "PLAN_APPROVAL:",
    "SLICE_APPROVAL:",
    "FINAL_APPROVAL:",
    "STATUS: DONE",
)
REQUIRED_FIELDS = {
    "AgentResultPayload": ("transport_schema", "request_id", "response_sha256"),
    "ReviewPayload": ("transport_schema", "request_id", "response_sha256"),
}
POSITIONAL_INDEX = {
    "AgentResultPayload": {
        "transport_schema": 4,
        "request_id": 5,
        "response_sha256": 6,
    },
    "ReviewPayload": {
        "transport_schema": 5,
        "request_id": 6,
        "response_sha256": 7,
    },
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_payload_construction_inventory_requires_complete_native_binding() -> None:
    violations: list[str] = []
    for source_root in (ROOT / "src", ROOT / "tests"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name not in REQUIRED_FIELDS:
                    continue
                keywords = {item.arg for item in node.keywords if item.arg is not None}
                starred_at = next(
                    (
                        index
                        for index, argument in enumerate(node.args)
                        if isinstance(argument, ast.Starred)
                    ),
                    None,
                )
                missing = []
                for field in REQUIRED_FIELDS[name]:
                    position = POSITIONAL_INDEX[name][field]
                    supplied_positionally = len(node.args) > position or (
                        starred_at is not None and starred_at <= position
                    )
                    if field not in keywords and not supplied_positionally:
                        missing.append(field)
                if missing:
                    relative = path.relative_to(ROOT).as_posix()
                    violations.append(
                        f"{relative}:{node.lineno}: {name} missing {', '.join(missing)}"
                    )

    assert violations == [], "\n".join(violations)


def test_runtime_registry_contains_only_native_transports() -> None:
    registry = build_agent_registry(
        {
            "codex": AgentSettings("codex", "codex", "gpt-5.6-sol", 60, "medium"),
            "claude": AgentSettings("claude", "claude", "sonnet", 60, "high"),
        }
    )

    assert type(registry["codex"]) is NativeCodexAdapter
    assert type(registry["claude"]) is NativeClaudeReviewAdapter


def test_retired_result_grammar_and_repair_symbols_cannot_reenter_runtime() -> None:
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src").rglob("*.py"))
    )
    assert [
        symbol for symbol in RETIRED_RUNTIME_SYMBOLS if symbol in runtime_source
    ] == []


def test_active_root_contracts_do_not_describe_retired_result_markers() -> None:
    root_contracts = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("AGENTS.md", "CLAUDE.md", "CODEX.md", "README.md")
    )
    assert [
        marker for marker in RETIRED_ROOT_RESULT_MARKERS if marker in root_contracts
    ] == []


@pytest.mark.parametrize(
    "flag",
    (
        "--native-codex-results",
        "--no-native-codex-results",
        "--native-claude-reviews",
        "--no-native-claude-reviews",
    ),
)
def test_retired_transport_flags_are_unknown(flag: str, tmp_path: Path) -> None:
    assert flag not in build_parser().format_help()
    with pytest.raises(SystemExit) as caught:
        parse_args([flag], cwd=tmp_path, environ={})
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "document",
    (
        {
            "mode": "structured-v2",
            "schema_version": "2",
            "claude_review_transport": None,
            "codex_result_transport": None,
            "codex_profile": {"model": "gpt-5.6-sol", "effort": "medium"},
            "claude_profile": {"model": "sonnet", "effort": "high"},
        },
        {
            "mode": "structured-v2",
            "schema_version": "2",
            "claude_review_transport": "native-claude-review-v2",
            "codex_result_transport": None,
            "codex_profile": {"model": "gpt-5.6-sol", "effort": "medium"},
            "claude_profile": {"model": "sonnet", "effort": "high"},
        },
    ),
)
def test_incomplete_persisted_transport_binding_is_rejected(
    document: dict[str, object],
) -> None:
    with pytest.raises(
        WorkflowStateValidationError, match="transport.*string|complete native"
    ):
        ProtocolBinding.from_dict(document)
