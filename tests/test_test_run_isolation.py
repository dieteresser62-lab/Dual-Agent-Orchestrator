from __future__ import annotations

from pathlib import Path

import pytest

import conftest as isolation_conftest
from conftest import (
    REPOSITORY_ROOT,
    assert_isolated_run_root,
    assert_repository_metadata_unchanged,
    assert_repository_run_roots_unchanged,
    protect_repository_metadata,
    protect_repository_run_roots,
    repository_metadata_signature,
    repository_run_root_signature,
)


def test_repository_metadata_guard_rejects_a_reintroduced_real_write(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    before = repository_metadata_signature(repository)
    record = repository / ".orchestrator/artifacts/regression/records/ar1-probe.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"probe":"real metadata write"}\n', encoding="utf-8")
    after = repository_metadata_signature(repository)

    with pytest.raises(AssertionError, match=r"added: \.orchestrator/artifacts"):
        assert_repository_metadata_unchanged(before, after)


def test_repository_metadata_guard_allows_a_tmp_path_run(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    sentinel = repository / ".orchestrator/artifacts/existing/records/ar1-sentinel.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text('{"sentinel":"existing metadata"}\n', encoding="utf-8")
    before = repository_metadata_signature(repository)
    run_root = tmp_path / "isolated" / ".orchestrator/artifacts/isolated-run"
    record = run_root / "records/ar1-probe.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"probe":"isolated metadata write"}\n', encoding="utf-8")
    after = repository_metadata_signature(repository)

    assert before
    assert_repository_metadata_unchanged(before, after)
    resolved_run_root = assert_isolated_run_root(run_root, tmp_path)
    assert resolved_run_root == run_root.resolve()


def test_per_test_run_root_guard_attributes_a_reintroduced_run(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    artifacts = repository / ".orchestrator/artifacts"
    artifacts.mkdir(parents=True)
    before = repository_run_root_signature(repository)
    (artifacts / "watch-regression").mkdir()
    after = repository_run_root_signature(repository)

    assert before == ()
    assert after == ("watch-regression",)
    with pytest.raises(AssertionError, match="added: watch-regression"):
        assert_repository_run_roots_unchanged(before, after)


def test_isolated_run_root_assertion_rejects_repository_regression(
    tmp_path: Path,
) -> None:
    regressed_root = REPOSITORY_ROOT / ".orchestrator/artifacts/regression"

    with pytest.raises(AssertionError, match="real repository metadata"):
        assert_isolated_run_root(regressed_root, tmp_path)


@pytest.mark.parametrize(
    ("guard_fixture", "expected_scope"),
    (
        (protect_repository_metadata, "session"),
        (protect_repository_run_roots, "function"),
    ),
)
def test_repository_guards_remain_autouse_with_their_bound_scopes(
    guard_fixture: object,
    expected_scope: str,
) -> None:
    marker = getattr(guard_fixture, "_pytestfixturefunction")

    assert marker.autouse is True
    assert marker.scope == expected_scope


def test_autouse_guard_chain_detects_a_write_to_declared_real_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared_repository = tmp_path / "declared-real-repository"
    declared_repository.mkdir()
    monkeypatch.setattr(
        isolation_conftest,
        "repository_metadata_signature",
        lambda: repository_metadata_signature(declared_repository),
    )
    monkeypatch.setattr(
        isolation_conftest,
        "repository_run_root_signature",
        lambda: repository_run_root_signature(declared_repository),
    )

    metadata_guard = protect_repository_metadata.__pytest_wrapped__.obj()
    run_root_guard = protect_repository_run_roots.__pytest_wrapped__.obj()
    next(metadata_guard)
    next(run_root_guard)

    record = (
        declared_repository
        / ".orchestrator/artifacts/guard-effect/records/ar1-probe.json"
    )
    record.parent.mkdir(parents=True)
    record.write_text('{"probe":"guard effect"}\n', encoding="utf-8")

    with pytest.raises(AssertionError, match="added: guard-effect"):
        next(run_root_guard)
    with pytest.raises(
        AssertionError,
        match=r"added: \.orchestrator/artifacts/guard-effect",
    ):
        next(metadata_guard)
