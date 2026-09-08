from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
UPPER_PATH = ROOT / "src/workflow_git_commit.py"
LOWER_PATH = ROOT / "src/git_service.py"
STATIC_BASELINE = ROOT / "tests/fixtures/commit-boundary-pre-b49-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/commit-boundary-runtime-post-b50-v1.json"
PRE_CUT_BASELINE = ROOT / "tests/fixtures/commit-boundary-pre-b50-v1.json"
SOURCE_COMMIT = "da1aa24b30f369a86600ec257418b9e517ac6e86"
UPPER_BLOB = "afcfffc6baa0704b8f44fbc771f7c314ec1bdfb6"
LOWER_BLOB = "2c3dc4b973551dd7ea18439e300000a6abf3f5b0"
FINGERPRINT = "f" * 64
OTHER_FINGERPRINT = "e" * 64
START_HEAD = "1" * 40
DRIFT_HEAD = "2" * 40
TREE = "3" * 40
COMMIT_HASH = "4" * 40


def _function(source: str, owner: str | None, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    body: list[ast.stmt] = tree.body
    if owner is not None:
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == owner
        )
        body = class_node.body
    return next(
        node
        for node in body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _ordered(function: ast.FunctionDef, kind: type[ast.AST]) -> list[ast.AST]:
    return sorted(
        (node for node in ast.walk(function) if isinstance(node, kind)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _read_names(node: ast.AST) -> list[str]:
    return sorted(
        {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
    )


def _exception_type(node: ast.Raise) -> str:
    if node.exc is None:
        return "bare-reraise"
    call = node.exc
    if isinstance(call, ast.Call):
        call = call.func
    if isinstance(call, ast.Name):
        return call.id
    if isinstance(call, ast.Attribute):
        return call.attr
    return ast.unparse(call)


def _exception_message(node: ast.Raise) -> str | None:
    if not isinstance(node.exc, ast.Call) or not node.exc.args:
        return None
    return ast.unparse(node.exc.args[0])


def _static_layer(
    path: Path, owner: str | None, source: str | None = None
) -> dict[str, object]:
    source = source if source is not None else path.read_text(encoding="utf-8")
    function = _function(source, owner, "commit_slice")
    if owner is not None:
        context = _function(source, owner, "_prepare_commit_context")
        operation = _function(source, owner, "_prepare_git_operation")
        binding = _function(source, owner, "_resolve_structured_binding")
        helper_names = {
            "_prepare_commit_context",
            "_prepare_git_operation",
            "_resolve_structured_binding",
        }
        helper_calls = [
            node.func.attr
            for node in _ordered(function, ast.Call)
            if isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr in helper_names
        ]
        assert helper_calls == [
            "_prepare_commit_context",
            "_prepare_git_operation",
            "_resolve_structured_binding",
        ]
        decisions = [
            *(_ordered(context, ast.If)),
            *(_ordered(operation, ast.If)),
            *(_ordered(binding, ast.If)),
            *(_ordered(function, ast.If)),
        ]
        raises = [
            *(_ordered(context, ast.Raise)),
            *(_ordered(operation, ast.Raise)),
            *(_ordered(binding, ast.Raise)),
            *(_ordered(function, ast.Raise)),
        ]
    else:
        staging = _function(source, None, "_stage_slice_transaction")
        function_decisions = _ordered(function, ast.If)
        function_raises = _ordered(function, ast.Raise)
        staging_calls = [
            node
            for node in _ordered(function, ast.Call)
            if isinstance(node.func, ast.Name)
            and node.func.id == "_stage_slice_transaction"
        ]
        assert len(staging_calls) == 1
        staging_call = staging_calls[0]
        decision_split = sum(
            node.lineno < staging_call.lineno for node in function_decisions
        )
        raise_split = sum(node.lineno < staging_call.lineno for node in function_raises)
        decisions = [
            *function_decisions[:decision_split],
            *(_ordered(staging, ast.If)),
            *function_decisions[decision_split:],
        ]
        raises = [
            *function_raises[:raise_split],
            *(_ordered(staging, ast.Raise)),
            *function_raises[raise_split:],
        ]
    returns = _ordered(function, ast.Return)
    catchers = _ordered(function, ast.ExceptHandler)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "owner": f"{owner + '.' if owner else ''}commit_slice",
        "conditions": [
            {
                "ordinal": index,
                "expression": ast.unparse(node.test),
                "read_names": _read_names(node.test),
            }
            for index, node in enumerate(decisions, 1)
        ],
        "aborts": [
            {
                "ordinal": index,
                "exception_type": _exception_type(node),
                "message_expression": _exception_message(node),
            }
            for index, node in enumerate(raises, 1)
        ],
        "returns": [
            {
                "ordinal": index,
                "expression": ast.unparse(node.value) if node.value else None,
            }
            for index, node in enumerate(returns, 1)
        ],
        "catchers": [
            {
                "ordinal": index,
                "exception_type": ast.unparse(node.type) if node.type else None,
                "body": [ast.unparse(statement) for statement in node.body],
            }
            for index, node in enumerate(catchers, 1)
        ],
    }


def _static_document() -> dict[str, object]:
    return {
        "schema_version": "commit-boundary-pre-b49-v1",
        "source_commit": SOURCE_COMMIT,
        "layers": [
            {
                "source_blob": UPPER_BLOB,
                **_static_layer(UPPER_PATH, "WorkflowGitCommit"),
            },
            {
                "source_blob": LOWER_BLOB,
                **_static_layer(LOWER_PATH, None),
            },
        ],
    }


class _ReviewPayload:
    verdict = "approved"

    def __init__(self, red_state_followup_slice: str | None = None) -> None:
        self.work_unit_id = "1"
        self.red_state_followup_slice = red_state_followup_slice


class _Bridge:
    def __init__(self, chain: list[object]) -> None:
        self.store = SimpleNamespace(
            load_chain=lambda: tuple(chain), current_chain=lambda: tuple(chain)
        )
        self.bindings: list[object] = []

    def append(self, payload: object, **_kwargs: object) -> object:
        self.bindings.append(payload)
        return payload


class _RecordingExecutor:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = payloads

    def execute(self, spec: object, *, reconcile: object, perform: object) -> object:
        from artifact_models import SideEffectPayload

        self.payloads.append(
            SideEffectPayload(
                effect_key=spec.effect_key,
                effect_class=spec.effect_class,
                work_unit_id=spec.work_unit_id,
                operation=spec.operation,
                phase="intent",
                result=None,
            )
        )
        value, result = perform()
        self.payloads.append(
            SideEffectPayload(
                effect_key=spec.effect_key,
                effect_class=spec.effect_class,
                work_unit_id=spec.work_unit_id,
                operation=spec.operation,
                phase="result",
                result=result,
            )
        )
        return value


UPPER_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "upper-no-active-state",
        "config": {"no_state": True},
        "type": "WorkflowExecutionError",
        "message": "slice commit has no active state",
    },
    {
        "scenario_id": "upper-no-persisted-boundary",
        "config": {"no_boundary": True},
        "type": "WorkflowExecutionError",
        "message": "slice commit has no persisted Git boundary",
    },
    {
        "scenario_id": "upper-no-repository-evidence",
        "config": {"no_changes": True},
        "type": "WorkflowExecutionError",
        "message": "slice commit has no canonical repository evidence for its fingerprint",
    },
    {
        "scenario_id": "upper-unapproved-path",
        "config": {"external": True},
        "type": "WorkflowExecutionError",
        "message": "slice commit has unapproved paths outside its persisted scope",
    },
    {
        "scenario_id": "upper-pending-effect-other-fingerprint",
        "config": {"pending_other": True},
        "type": "WorkflowExecutionError",
        "message": "pending Slice commit belongs to another reviewed fingerprint",
    },
    {
        "scenario_id": "upper-head-drift-without-approval",
        "config": {"head": DRIFT_HEAD},
        "type": "WorkflowCommitApprovalRequired",
        "message": (
            "HEAD-DRIFT | the Slice HEAD changed after its persisted start; "
            f"approve the exact reviewed fingerprint {FINGERPRINT} and current "
            f"HEAD {DRIFT_HEAD} before committing"
        ),
    },
    {
        "scenario_id": "upper-missing-structured-records",
        "config": {"structured": "missing"},
        "type": "WorkflowExecutionError",
        "message": "structured commit binding requires persisted attestation and approvals",
    },
    {
        "scenario_id": "upper-attestation-mismatch",
        "config": {"structured": "attestation-mismatch"},
        "type": "WorkflowExecutionError",
        "message": "structured commit attestation differs from the commit request",
    },
    {
        "scenario_id": "upper-review-mismatch",
        "config": {"structured": "review-mismatch"},
        "type": "WorkflowExecutionError",
        "message": "structured commit review differs from the commit request",
    },
    {
        "scenario_id": "upper-red-state-mismatch",
        "config": {"structured": "red-state-mismatch"},
        "type": "WorkflowExecutionError",
        "message": "red-state commit lacks its fingerprint-bound review record authorization",
    },
    {
        "scenario_id": "upper-structured-binding-xor",
        "config": {"structured": "broken-binding-helper"},
        "type": "WorkflowExecutionError",
        "message": "structured commit binding was not established before the Git transaction",
    },
)


LOWER_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "lower-authorization-other-slice",
        "config": {"authorization_slice": 2},
        "type": "GitTransactionError",
        "message": "commit authorization belongs to a different slice",
    },
    {
        "scenario_id": "lower-no-reviewed-change",
        "config": {"reviewed_paths": ()},
        "type": "GitTransactionError",
        "message": "slice commit requires at least one changed path",
    },
    {
        "scenario_id": "lower-foreign-staged-path",
        "config": {"staged_before": ("foreign.py",)},
        "type": "GitTransactionError",
        "message": "foreign staged paths block the slice commit: foreign.py",
    },
    {
        "scenario_id": "lower-unapproved-reviewed-path",
        "config": {"reviewed_paths": ("scope.py", "external.py")},
        "type": "GitTransactionError",
        "message": (
            "reviewed paths outside the Slice scope lack an exact user approval: "
            "external.py"
        ),
    },
    {
        "scenario_id": "lower-head-not-descendant",
        "config": {"head": DRIFT_HEAD, "ancestry_returncode": 1},
        "type": "GitTransactionError",
        "message": "slice HEAD no longer descends from its persisted start",
    },
    {
        "scenario_id": "lower-drift-without-exact-approval",
        "config": {"head": DRIFT_HEAD},
        "type": "GitTransactionError",
        "message": "slice HEAD drift lacks an exact fingerprint-bound user approval",
    },
    {
        "scenario_id": "lower-no-transaction-after-drift",
        "config": {
            "head": DRIFT_HEAD,
            "approved_head": DRIFT_HEAD,
            "transaction_paths": (),
        },
        "type": "GitTransactionError",
        "message": "slice commit has no uncommitted transaction after approved HEAD drift",
    },
    {
        "scenario_id": "lower-transaction-without-scope-path",
        "config": {
            "head": DRIFT_HEAD,
            "approved_head": DRIFT_HEAD,
            "reviewed_paths": ("scope.py", "external.py"),
            "approved_external": ("external.py",),
            "transaction_paths": ("external.py",),
        },
        "type": "GitTransactionError",
        "message": "slice commit transaction contains no path from the persisted Slice scope",
    },
    {
        "scenario_id": "lower-invalid-title",
        "config": {"title": "\n"},
        "type": "GitTransactionError",
        "message": "slice commit title must be one non-empty line",
    },
    {
        "scenario_id": "lower-staged-set-mismatch",
        "config": {"staged_after": ("other.py",)},
        "type": "GitTransactionError",
        "message": "staged paths do not exactly match the slice transaction: other.py",
    },
    {
        "scenario_id": "lower-fingerprint-changed-during-staging",
        "config": {"final_fingerprint": OTHER_FINGERPRINT},
        "type": "GitTransactionError",
        "message": "slice fingerprint changed during exact staging",
    },
    {
        "scenario_id": "lower-created-commit-path-mismatch",
        "config": {"committed_paths": ("other.py",)},
        "type": "GitTransactionError",
        "message": "created commit path list differs from the reviewed slice",
    },
)


def _upper_chain(mode: str | None) -> tuple[_Bridge | None, object, object]:
    passed = mode != "red-state-mismatch"
    attestation = SimpleNamespace(attestation_id="att", passed=passed)
    red_state = "requested-red" if not passed else None
    if mode is None:
        return None, attestation, red_state
    if mode == "missing":
        return _Bridge([]), attestation, red_state
    content = SimpleNamespace(content_record_id="content")
    attestation_record = SimpleNamespace(
        record_type=SimpleNamespace(value="validation_attestation"),
        fingerprint=SimpleNamespace(sha256=FINGERPRINT),
        logical_id="att",
        payload=content,
        record_id="attestation-record",
    )
    review_payload = _ReviewPayload(
        "persisted-red" if mode == "red-state-mismatch" else red_state
    )
    review_record = SimpleNamespace(
        record_type=SimpleNamespace(value="review"),
        fingerprint=SimpleNamespace(sha256=FINGERPRINT),
        logical_id="review",
        payload=review_payload,
        record_id="review-record",
    )
    return _Bridge([attestation_record, review_record]), attestation, red_state


def _run_upper(
    scenario: dict[str, Any], module: ModuleType | None = None
) -> dict[str, object]:
    import workflow_git_commit as production
    from repo_changes import ChangedPath, RepositoryChanges
    from side_effects import SideEffectSpec

    target = module or production
    config = scenario["config"]
    current = SimpleNamespace(
        slice_id=1,
        start_commit=None if config.get("no_boundary") else START_HEAD,
        start_fingerprint=None if config.get("no_boundary") else FINGERPRINT,
        scope_paths=("scope.py",),
    )
    work_unit = SimpleNamespace(
        gate_decisions=[],
        has_gate_approval=lambda *_args: False,
    )
    state = SimpleNamespace(
        current_slice=current,
        branch="feature/backlog-followups",
        planned_slices=[SimpleNamespace(slice_id=1, summary="commit boundary")],
        current_work_unit=work_unit,
        current_work_unit_id=1,
        run_id="b49-run",
    )
    active_state = None if config.get("no_state") else state
    paths = ("scope.py", "external.py") if config.get("external") else ("scope.py",)
    changes = RepositoryChanges(
        ROOT,
        START_HEAD,
        tuple(ChangedPath(path, "modified", " M", working_tree=True) for path in paths),
        "provider-free",
        FINGERPRINT,
    )
    bridge, attestation, red_state = _upper_chain(config.get("structured"))
    if config.get("pending_other"):
        bridge = _Bridge([])
    payloads: list[object] = []
    commit_count = 0

    def fake_commit_slice(**_kwargs: object) -> object:
        nonlocal commit_count
        commit_count += 1
        return SimpleNamespace(commit_hash=COMMIT_HASH)

    pending = SimpleNamespace(
        effect_class="git_commit",
        work_unit_id="1",
        operation=(
            "slice_commit",
            "1",
            START_HEAD,
            TREE,
            OTHER_FINGERPRINT,
            "a" * 64,
        ),
        result=None,
    )
    replay = SimpleNamespace(
        side_effects=(pending,) if config.get("pending_other") else ()
    )
    deps = SimpleNamespace(
        root=lambda: ROOT,
        active_state=lambda: active_state,
        artifact_bridge=lambda: bridge,
        assert_structured_decision_context=lambda: None,
        repository_changes=lambda _fingerprint: (
            None if config.get("no_changes") else changes
        ),
        mark_completed_side_effect=lambda _key: None,
        side_effect_executor=lambda _bridge: _RecordingExecutor(payloads),
        side_effect_spec=lambda effect_class, operation, fingerprint: SideEffectSpec(
            effect_class, "1", operation, fingerprint
        ),
        bound_task_control_paths=lambda _root, _state: (),
    )
    request = SimpleNamespace(
        slice_id=1,
        fingerprint=FINGERPRINT,
        attestation=attestation,
        claude_review=SimpleNamespace(verdict="approved"),
        findings=(),
        red_state_followup_slice=red_state,
    )
    error: BaseException | None = None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(target, "ReviewPayload", _ReviewPayload)
        patch.setattr(target, "inspect_repository", lambda _root: SimpleNamespace(head=config.get("head", START_HEAD)))
        patch.setattr(target, "preview_commit_tree", lambda *_args, **_kwargs: TREE)
        patch.setattr(target, "collect_repository_changes", lambda *_args, **_kwargs: changes)
        patch.setattr(target, "replay_artifacts", lambda *_args: replay)
        patch.setattr(target, "commit_slice", fake_commit_slice)
        patch.setattr(target, "attestation_payload", lambda *_args: object() if config.get("structured") == "attestation-mismatch" else bridge.store.load_chain()[0].payload)
        patch.setattr(target, "review_payload_matches_result", lambda *_args: config.get("structured") != "review-mismatch")
        if config.get("structured") == "broken-binding-helper":
            patch.setattr(
                target.WorkflowGitCommit,
                "_resolve_structured_binding",
                lambda *_args: (None, None),
            )
        try:
            target.WorkflowGitCommit(deps).commit_slice(request)
        except BaseException as caught:  # corpus records mutant mismatches too
            error = caught
    return {
        "scenario_id": scenario["scenario_id"],
        "layer": "workflow_git_commit",
        "trigger_mode": (
            "helper-contract-injection"
            if config.get("structured") == "broken-binding-helper"
            else "production-input"
        ),
        "reachable": True,
        "structural_reason": None,
        "expected_error": {"type": scenario["type"], "message": scenario["message"]},
        "actual_error": (
            {"type": type(error).__name__, "message": str(error)} if error else None
        ),
        "commit_count": commit_count,
        "side_effect_payloads": [
            {"phase": payload.phase, "result": payload.result} for payload in payloads
        ],
    }


def _changes(paths: tuple[str, ...], fingerprint: str = FINGERPRINT) -> object:
    from repo_changes import ChangedPath, RepositoryChanges

    return RepositoryChanges(
        ROOT,
        START_HEAD,
        tuple(ChangedPath(path, "modified", " M", working_tree=True) for path in paths),
        "provider-free",
        fingerprint,
    )


def _run_lower(
    scenario: dict[str, Any], module: ModuleType | None = None
) -> dict[str, object]:
    import git_service as production

    target = module or production
    config = scenario["config"]
    head = config.get("head", START_HEAD)
    reviewed_paths = config.get("reviewed_paths", ("scope.py",))
    transaction_paths = config.get("transaction_paths", reviewed_paths)
    reviewed = _changes(reviewed_paths)
    transaction = _changes(transaction_paths)
    final = _changes(transaction_paths, config.get("final_fingerprint", FINGERPRINT))
    collected = [reviewed]
    if head != START_HEAD:
        collected.append(transaction)
    collected.append(final)
    collect_index = 0
    staged_index = 0
    commit_count = 0

    def collect(*_args: object, **_kwargs: object) -> object:
        nonlocal collect_index
        result = collected[min(collect_index, len(collected) - 1)]
        collect_index += 1
        return result

    staged = [config.get("staged_before", ()), config.get("staged_after", transaction_paths)]

    def staged_paths(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        nonlocal staged_index
        result = staged[min(staged_index, len(staged) - 1)]
        staged_index += 1
        return result

    def git(_root: Path, *args: str, **_kwargs: object) -> object:
        nonlocal commit_count
        if args and args[0] == "merge-base":
            return SimpleNamespace(returncode=config.get("ancestry_returncode", 0), stdout=b"")
        if args and args[0] == "write-tree":
            return SimpleNamespace(returncode=0, stdout=(TREE + "\n").encode())
        if "commit" in args:
            commit_count += 1
            return SimpleNamespace(returncode=0, stdout=b"")
        if args and args[0] == "rev-parse":
            value = COMMIT_HASH if commit_count else head
            return SimpleNamespace(returncode=0, stdout=(value + "\n").encode())
        if args and args[0] == "diff-tree":
            paths = config.get("committed_paths", transaction_paths)
            return SimpleNamespace(
                returncode=0,
                stdout=("\0".join(paths) + ("\0" if paths else "")).encode(),
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    boundary = SimpleNamespace(
        slice_id=1,
        branch="feature/backlog-followups",
        start_commit=START_HEAD,
        start_fingerprint=FINGERPRINT,
        scope_paths=("scope.py",),
        semantic_markdown_paths=(),
        excluded_control_paths=(),
    )
    authorization = SimpleNamespace(
        slice_id=config.get("authorization_slice", 1),
        approved_external_paths=config.get("approved_external", ()),
        approved_head_commit=config.get("approved_head"),
        diff_fingerprint=FINGERPRINT,
    )
    identity = SimpleNamespace(
        repository_root=ROOT,
        branch=boundary.branch,
        head=head,
    )
    error: BaseException | None = None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(target, "_require_expected_feature_branch", lambda *_args: identity)
        patch.setattr(target, "_collect_boundary_changes", collect)
        patch.setattr(target, "_staged_paths", staged_paths)
        patch.setattr(target, "_validate_authorization", lambda *_args: None)
        patch.setattr(target, "_git", git)
        try:
            target.commit_slice(
                repository_root=ROOT,
                boundary=boundary,
                authorization=authorization,
                title=config.get("title", "commit boundary"),
            )
        except BaseException as caught:  # corpus records mutant mismatches too
            error = caught
    return {
        "scenario_id": scenario["scenario_id"],
        "layer": "git_service",
        "trigger_mode": "production-input",
        "reachable": True,
        "structural_reason": None,
        "expected_error": {"type": scenario["type"], "message": scenario["message"]},
        "actual_error": (
            {"type": type(error).__name__, "message": str(error)} if error else None
        ),
        "commit_count": commit_count,
        "side_effect_payloads": [],
    }


def _successful_upper_bracket() -> dict[str, object]:
    scenario = {
        "scenario_id": "upper-successful-ledger-bracket",
        "config": {"structured": "valid"},
        "type": "",
        "message": "",
    }
    result = _run_upper(scenario)
    result["expected_error"] = None
    return result


@pytest.fixture(scope="session")
def runtime_corpus() -> dict[str, object]:
    global _RUNTIME_BUILD_COUNT
    _RUNTIME_BUILD_COUNT += 1
    scenarios = [*(_run_upper(item) for item in UPPER_SCENARIOS)]
    scenarios.extend(_run_lower(item) for item in LOWER_SCENARIOS)
    scenarios.append(_successful_upper_bracket())
    return {
        "schema_version": "commit-boundary-runtime-post-b50-v1",
        "source_commit": "864363a608840ab42db97bee551670d02c42c993",
        "source_blobs": {
            "src/workflow_git_commit.py": UPPER_BLOB,
            "src/git_service.py": LOWER_BLOB,
        },
        "scenarios": scenarios,
    }


def _load_mutant(path: Path, transform: object) -> ModuleType:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    transform(tree)
    ast.fix_missing_locations(tree)
    name = f"_b49_mutant_{path.stem}_{id(tree)}"
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(tree, str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


_RUNTIME_BUILD_COUNT = 0


def _remove_first_condition(owner: str | None, function_name: str) -> object:
    def transform(tree: ast.Module) -> None:
        body: list[ast.stmt] = tree.body
        if owner:
            body = next(
                node.body
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == owner
            )
        function = next(
            node
            for node in body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        assert isinstance(function.body[0], ast.If)
        del function.body[0]

    return transform


def _remove_first_raising_condition(owner: str | None, function_name: str) -> object:
    def transform(tree: ast.Module) -> None:
        body: list[ast.stmt] = tree.body
        if owner:
            body = next(
                node.body
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == owner
            )
        function = next(
            node
            for node in body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        index = next(
            index
            for index, statement in enumerate(function.body)
            if isinstance(statement, ast.If)
            and any(isinstance(node, ast.Raise) for node in ast.walk(statement))
        )
        del function.body[index]

    return transform


def _swap_first_two_rejections(tree: ast.Module) -> None:
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_prepare_commit_context"
    )
    raises = _ordered(function, ast.Raise)
    raises[0].exc, raises[1].exc = copy.deepcopy(raises[1].exc), copy.deepcopy(raises[0].exc)


def test_static_corpus_is_cleartext_complete_and_source_bound() -> None:
    baseline = json.loads(STATIC_BASELINE.read_text(encoding="utf-8"))
    assert _static_document() == baseline
    upper, lower = baseline["layers"]
    assert len(upper["conditions"]) == 19
    assert len(upper["aborts"]) == 11
    assert upper["catchers"] == []
    assert len(lower["conditions"]) == 19
    assert len(lower["aborts"]) == 13  # twelve explicit errors plus one bare rethrow
    assert lower["catchers"][0]["exception_type"] == "GitTransactionError"
    assert all(item["expression"] for item in (*upper["conditions"], *lower["conditions"]))
    assert all("read_names" in item for item in (*upper["conditions"], *lower["conditions"]))


def test_b49_vorstate_anchor_resolves_both_original_product_blobs() -> None:
    for path, expected_blob in ((UPPER_PATH, UPPER_BLOB), (LOWER_PATH, LOWER_BLOB)):
        anchored_blob = subprocess.run(
            ["git", "rev-parse", f"{SOURCE_COMMIT}:{path.relative_to(ROOT).as_posix()}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert anchored_blob == expected_blob


def test_b50_pre_cut_anchor_binds_immediate_source_commit_and_both_blobs() -> None:
    baseline = json.loads(PRE_CUT_BASELINE.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == "commit-boundary-pre-b50-v1"
    for relative, expected_blob in baseline["sources"].items():
        anchored_blob = subprocess.run(
            ["git", "rev-parse", f'{baseline["source_commit"]}:{relative}'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert anchored_blob == expected_blob, relative


def test_runtime_corpus_is_built_once_and_every_reachable_rejection_matches(
    runtime_corpus: dict[str, object],
) -> None:
    baseline = json.loads(RUNTIME_BASELINE.read_text(encoding="utf-8"))
    assert runtime_corpus["schema_version"] == baseline["schema_version"]
    assert runtime_corpus["source_commit"] == baseline["source_commit"]
    assert runtime_corpus["source_blobs"] == baseline["source_blobs"]
    actual_by_id = {
        item["scenario_id"]: item for item in runtime_corpus["scenarios"]
    }
    expected_by_id = {
        item["scenario_id"]: item for item in baseline["scenarios"]
    }
    assert actual_by_id.keys() == expected_by_id.keys()
    for scenario_id, expected in expected_by_id.items():
        assert actual_by_id[scenario_id] == expected, scenario_id
    assert _RUNTIME_BUILD_COUNT == 1
    scenarios = runtime_corpus["scenarios"]
    rejections = [item for item in scenarios if item["expected_error"] is not None]
    assert len(rejections) == 23
    upper_static, lower_static = _static_document()["layers"]
    assert [item["expected_error"]["type"] for item in rejections[:11]] == [
        item["exception_type"] for item in upper_static["aborts"]
    ]
    assert [item["expected_error"]["type"] for item in rejections[11:]] == [
        item["exception_type"]
        for item in lower_static["aborts"]
        if item["exception_type"] != "bare-reraise"
    ]
    for scenario in rejections:
        if scenario["reachable"]:
            assert scenario["actual_error"] == scenario["expected_error"]
        else:
            assert scenario["actual_error"] is None
            assert scenario["structural_reason"]
        assert isinstance(scenario["commit_count"], int)
        assert isinstance(scenario["side_effect_payloads"], list)
        phases = [item["phase"] for item in scenario["side_effect_payloads"]]
        assert phases in ([], ["intent"], ["intent", "result"])


def test_success_path_records_exactly_one_intent_result_pair(
    runtime_corpus: dict[str, object],
) -> None:
    assert _RUNTIME_BUILD_COUNT == 1
    success = next(
        item
        for item in runtime_corpus["scenarios"]
        if item["scenario_id"] == "upper-successful-ledger-bracket"
    )
    assert success["actual_error"] is None
    assert success["commit_count"] == 1
    assert success["side_effect_payloads"] == [
        {"phase": "intent", "result": None},
        {"phase": "result", "result": COMMIT_HASH},
    ]


def test_removing_one_upper_condition_makes_its_scenario_red() -> None:
    mutant = _load_mutant(
        UPPER_PATH,
        _remove_first_condition("WorkflowGitCommit", "_prepare_commit_context"),
    )
    observed = _run_upper(UPPER_SCENARIOS[0], mutant)
    assert observed["actual_error"] != observed["expected_error"]


def test_removing_one_lower_condition_makes_its_scenario_red() -> None:
    mutant = _load_mutant(
        LOWER_PATH,
        _remove_first_raising_condition(None, "_stage_slice_transaction"),
    )
    scenario = next(
        item
        for item in LOWER_SCENARIOS
        if item["scenario_id"] == "lower-staged-set-mismatch"
    )
    observed = _run_lower(scenario, mutant)
    assert observed["actual_error"] != observed["expected_error"]


def test_swapping_two_rejections_is_detected() -> None:
    mutant = _load_mutant(UPPER_PATH, _swap_first_two_rejections)
    observed = _run_upper(UPPER_SCENARIOS[0], mutant)
    assert observed["actual_error"] != observed["expected_error"]


def test_moving_a_condition_between_layers_is_detected() -> None:
    upper_tree = ast.parse(UPPER_PATH.read_text(encoding="utf-8"))
    upper_class = next(
        node
        for node in upper_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkflowGitCommit"
    )
    context = next(
        node
        for node in upper_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_prepare_commit_context"
    )
    moved_condition = context.body.pop(0)
    assert isinstance(moved_condition, ast.If)

    lower_tree = ast.parse(LOWER_PATH.read_text(encoding="utf-8"))
    lower_commit = next(
        node
        for node in lower_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "commit_slice"
    )
    lower_commit.body.insert(0, moved_condition)
    ast.fix_missing_locations(upper_tree)
    ast.fix_missing_locations(lower_tree)

    upper = _static_layer(UPPER_PATH, "WorkflowGitCommit", ast.unparse(upper_tree))
    lower = _static_layer(LOWER_PATH, None, ast.unparse(lower_tree))
    baseline_upper, baseline_lower = _static_document()["layers"]
    assert upper != baseline_upper
    assert lower != baseline_lower
    assert len(upper["conditions"]) == 18
    assert len(lower["conditions"]) == 20


def test_commit_entrypoints_shrink_below_the_b32_threshold() -> None:
    for path, owner in ((UPPER_PATH, "WorkflowGitCommit"), (LOWER_PATH, None)):
        function = _function(path.read_text(encoding="utf-8"), owner, "commit_slice")
        assert function.end_lineno is not None
        assert function.end_lineno - function.lineno + 1 < 200


def test_lower_extracted_helper_remains_inside_the_original_catcher() -> None:
    source = LOWER_PATH.read_text(encoding="utf-8")
    function = _function(source, None, "commit_slice")
    catchers = _ordered(function, ast.ExceptHandler)
    assert len(catchers) == 1
    assert ast.unparse(catchers[0].type) == "GitTransactionError"
    guarded_try = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_stage_slice_transaction"
            for statement in node.body
            for child in ast.walk(statement)
        )
    )
    assert guarded_try.handlers == catchers


def test_baseline_facts_are_cleartext_not_digest_only() -> None:
    static_text = STATIC_BASELINE.read_text(encoding="utf-8")
    runtime_text = RUNTIME_BASELINE.read_text(encoding="utf-8")
    for message in (
        "slice commit has no active state",
        "structured commit binding was not established before the Git transaction",
        "created commit path list differs from the reviewed slice",
    ):
        assert message in static_text
        assert message in runtime_text
