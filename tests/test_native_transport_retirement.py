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
