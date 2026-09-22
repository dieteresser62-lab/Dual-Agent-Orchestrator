from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from native_review_contract import NativeReviewErrorCode
from orchestrator_diagnostics import ORCHESTRATOR_DIAGNOSTIC_TEXTS


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@dataclass(frozen=True, slots=True)
class _TranslatedStaticRaiseScope:
    """One producer whose literal ValueErrors retain their text at a review boundary."""

    path: str
    owner: str
    code: NativeReviewErrorCode
    detail_prefix: str = ""
    detail_argument_index: int = 0


# These scopes are the producers behind the ValueError-to-review-code catchers in
# native_review_contract.py.  A scope is included only when its literal detail is
# preserved without a provider-authored value.  Catchers that add finding ids,
# schema locations, invalid values, or other runtime text deliberately remain on
# their code-level fallback diagnostic and therefore do not appear here.
_TRANSLATED_STATIC_RAISE_SCOPES = (
    _TranslatedStaticRaiseScope(
        "contracts.py", "FindingOrigin", NativeReviewErrorCode.FINDING_ID_INVALID
    ),
    _TranslatedStaticRaiseScope(
        "contracts.py", "FindingRecord", NativeReviewErrorCode.FINDING_ID_INVALID
    ),
    _TranslatedStaticRaiseScope(
        "contracts.py",
        "ReviewEvidence",
        NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
    ),
    _TranslatedStaticRaiseScope(
        "contracts.py", "StopRequest", NativeReviewErrorCode.STOP_CONTENT_INVALID
    ),
    _TranslatedStaticRaiseScope(
        "contracts.py", "AnchorRecord", NativeReviewErrorCode.ANCHOR_INVALID
    ),
    _TranslatedStaticRaiseScope(
        "contracts.py",
        "apply_reviewer_finding_update",
        NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
    ),
    _TranslatedStaticRaiseScope(
        "finding_reducer.py",
        "apply_reviewer_events",
        NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
    ),
    _TranslatedStaticRaiseScope(
        "finding_reducer.py",
        "_canonical_findings",
        NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
    ),
    _TranslatedStaticRaiseScope(
        "schema_validation.py",
        "_check_schema_node",
        NativeReviewErrorCode.SCHEMA_INVALID,
    ),
    _TranslatedStaticRaiseScope(
        "schema_validation.py",
        "_resolve_schema_reference",
        NativeReviewErrorCode.SCHEMA_INVALID,
    ),
    _TranslatedStaticRaiseScope(
        "native_review_request.py",
        "_validate_native_review_provider_response_schema",
        NativeReviewErrorCode.SCHEMA_INVALID,
        detail_argument_index=1,
    ),
)

_TRANSLATION_ENTRYPOINTS = frozenset(
    {
        "AnchorRecord",
        "FindingOrigin",
        "FindingRecord",
        "ReviewEvidence",
        "StopRequest",
        "apply_reviewer_events",
        "check_schema",
    }
)

_EXPECTED_PASSTHROUGH_BOUNDARIES = Counter(
    {
        (
            "load_native_review_schema",
            NativeReviewErrorCode.SCHEMA_INVALID,
            "",
            ("check_schema",),
        ): 1,
        (
            "_parse_native_review_response",
            NativeReviewErrorCode.REVIEW_CONTENT_MISSING,
            "",
            ("ReviewEvidence",),
        ): 2,
        (
            "_native_response_to_contract_result",
            NativeReviewErrorCode.STOP_CONTENT_INVALID,
            "",
            ("StopRequest",),
        ): 1,
        (
            "_merge_findings",
            NativeReviewErrorCode.FINDING_ID_INVALID,
            "",
            ("FindingOrigin", "FindingRecord"),
        ): 1,
        (
            "_merge_findings",
            NativeReviewErrorCode.FINDING_REFERENCE_UNKNOWN,
            "",
            ("apply_reviewer_events",),
        ): 1,
        (
            "_convert_anchors",
            NativeReviewErrorCode.ANCHOR_INVALID,
            "",
            ("AnchorRecord",),
        ): 1,
    }
)


def _source(path: str, overrides: Mapping[str, str]) -> str:
    return overrides.get(path, (SRC / path).read_text(encoding="utf-8"))


def _owner(tree: ast.Module, name: str) -> ast.FunctionDef | ast.ClassDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name
    ]
    assert len(matches) == 1, f"expected one source owner named {name!r}"
    return matches[0]


def _literal_raise_details(
    owner: ast.FunctionDef | ast.ClassDef,
    argument_index: int,
) -> tuple[tuple[int, str], ...]:
    details: list[tuple[int, str]] = []
    for node in ast.walk(owner):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        if len(node.exc.args) <= argument_index:
            continue
        detail = node.exc.args[argument_index]
        if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
            details.append((node.lineno, detail.value))
    return tuple(details)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _passthrough_prefix(expression: ast.expr, exception_name: str) -> str | None:
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "str"
        and len(expression.args) == 1
        and isinstance(expression.args[0], ast.Name)
        and expression.args[0].id == exception_name
    ):
        return ""
    if (
        isinstance(expression, ast.BinOp)
        and isinstance(expression.op, ast.Add)
        and isinstance(expression.left, ast.Constant)
        and isinstance(expression.left.value, str)
    ):
        suffix = _passthrough_prefix(expression.right, exception_name)
        if suffix is not None:
            return expression.left.value + suffix
    if isinstance(expression, ast.JoinedStr):
        prefix: list[str] = []
        saw_exception = False
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if saw_exception and value.value:
                    return None
                prefix.append(value.value)
                continue
            if (
                not saw_exception
                and isinstance(value, ast.FormattedValue)
                and isinstance(value.value, ast.Name)
                and value.value.id == exception_name
                and value.conversion == -1
                and value.format_spec is None
            ):
                saw_exception = True
                continue
            return None
        if saw_exception:
            return "".join(prefix)
    return None


def _native_review_passthrough_boundaries() -> Counter[tuple[object, ...]]:
    path = SRC / "native_review_contract.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    boundaries: Counter[tuple[object, ...]] = Counter()
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or handler.name is None:
            continue
        translated = [
            node
            for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
            if isinstance(node, ast.Call)
            and _call_name(node) == "NativeReviewContractError"
            and len(node.args) >= 2
            and _passthrough_prefix(node.args[1], handler.name) is not None
        ]
        for call in translated:
            code_node = call.args[0]
            assert (
                isinstance(code_node, ast.Attribute)
                and code_node.attr in NativeReviewErrorCode.__members__
            )
            owner: ast.AST = handler
            while not isinstance(owner, ast.FunctionDef):
                owner = parents[owner]
            try_node = parents[handler]
            assert isinstance(try_node, ast.Try)
            entrypoints = tuple(
                sorted(
                    {
                        name
                        for node in ast.walk(
                            ast.Module(body=try_node.body, type_ignores=[])
                        )
                        if isinstance(node, ast.Call)
                        and (name := _call_name(node)) in _TRANSLATION_ENTRYPOINTS
                    }
                )
            )
            boundaries[
                (
                    owner.name,
                    NativeReviewErrorCode[code_node.attr],
                    _passthrough_prefix(call.args[1], handler.name),
                    entrypoints,
                )
            ] += 1
    return boundaries


def _direct_native_review_static_diagnostics(
    overrides: Mapping[str, str],
) -> tuple[tuple[str, int, str], ...]:
    diagnostics: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        tree = ast.parse(_source(relative, overrides), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if function_name != "NativeReviewContractError":
                continue
            keywords = {item.arg: item.value for item in node.keywords if item.arg}
            code_node = node.args[0] if node.args else keywords.get("code")
            detail_node = (
                node.args[1] if len(node.args) >= 2 else keywords.get("detail")
            )
            if not (
                isinstance(code_node, ast.Attribute)
                and isinstance(code_node.value, ast.Name)
                and code_node.value.id == "NativeReviewErrorCode"
                and code_node.attr in NativeReviewErrorCode.__members__
                and isinstance(detail_node, ast.Constant)
                and isinstance(detail_node.value, str)
            ):
                continue
            code = NativeReviewErrorCode[code_node.attr]
            diagnostics.append(
                (relative, node.lineno, f"{code.value}: {detail_node.value}")
            )
    return tuple(diagnostics)


def _translated_external_static_diagnostics(
    overrides: Mapping[str, str],
) -> tuple[tuple[str, int, str], ...]:
    trees: dict[str, ast.Module] = {}
    diagnostics: list[tuple[str, int, str]] = []
    for scope in _TRANSLATED_STATIC_RAISE_SCOPES:
        tree = trees.setdefault(
            scope.path,
            ast.parse(
                _source(scope.path, overrides),
                filename=str(SRC / scope.path),
            ),
        )
        for line, detail in _literal_raise_details(
            _owner(tree, scope.owner), scope.detail_argument_index
        ):
            diagnostics.append(
                (
                    scope.path,
                    line,
                    f"{scope.code.value}: {scope.detail_prefix}{detail}",
                )
            )
    return tuple(diagnostics)


def _missing_static_review_diagnostics(
    overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[str, int, str], ...]:
    sources = {} if overrides is None else overrides
    sites = (
        *_direct_native_review_static_diagnostics(sources),
        *_translated_external_static_diagnostics(sources),
    )
    return tuple(site for site in sites if site[2] not in ORCHESTRATOR_DIAGNOSTIC_TEXTS)


def test_every_static_review_rejection_throw_site_has_a_precise_diagnostic() -> None:
    assert _missing_static_review_diagnostics() == ()


def test_review_passthrough_catchers_match_the_cross_file_source_inventory() -> None:
    assert _native_review_passthrough_boundaries() == _EXPECTED_PASSTHROUGH_BOUNDARIES


def test_new_unbound_static_throw_in_a_translated_external_scope_is_detected() -> None:
    source = (SRC / "contracts.py").read_text(encoding="utf-8")
    needle = '            raise ValueError("finding summary must not be empty")'
    replacement = (
        needle
        + '\n        if False:\n'
        + '            raise ValueError("synthetic unbound review diagnostic")'
    )
    assert needle in source

    missing = _missing_static_review_diagnostics(
        {"contracts.py": source.replace(needle, replacement, 1)}
    )

    assert [item[2] for item in missing] == [
        "finding-id-invalid: synthetic unbound review diagnostic"
    ]
