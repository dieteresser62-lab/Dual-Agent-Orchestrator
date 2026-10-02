"""Index-content regressions without running a provider or mutating the host Gitdir."""

import hashlib
from pathlib import Path
import struct
import subprocess

import pytest

from agent_adapters import AgentPermissionError
from git_index import index_content
from protected_tree import ProtectedTreeGuard
from test_claude_implementer_adapter import _prepared  # allowlist:provider -- transport: fake implementer fixture
from test_codex_implementer_adapter import prepared  # allowlist:provider -- transport: fake implementer fixture


def index_bytes(*, version=2, algorithm=hashlib.sha1, paths=(b"a",), mode=0o100644,
                oid=b"x", flags=0, extended=0, cache=0, extension=b""):
    rows, previous = [], b""
    for path in paths:
        fields = [cache] * 10
        fields[6] = mode
        entry = struct.pack("!10I", *fields) + (oid if len(oid) == algorithm().digest_size else oid * algorithm().digest_size)
        entry += struct.pack("!H", len(path) | flags | (0x4000 if extended else 0))
        if extended:
            entry += struct.pack("!H", extended)
        if version == 4:
            common = 0
            while common < min(len(previous), len(path)) and previous[common] == path[common]:
                common += 1
            entry += bytes((len(previous) - common,)) + path[common:] + b"\0"
        else:
            entry += path + b"\0"
            entry += b"\0" * (-len(entry) % 8)
        rows.append(entry)
        previous = path
    body = struct.pack("!4sII", b"DIRC", version, len(paths)) + b"".join(rows) + extension
    return body + algorithm(body).digest()


@pytest.mark.parametrize("version", [2, 3, 4])
@pytest.mark.parametrize("algorithm", [hashlib.sha1, hashlib.sha256])
def test_only_stat_fields_are_ignored(version, algorithm):
    options = dict(version=version, algorithm=algorithm, paths=(b"dir/a", b"dir/b"))
    assert index_content(index_bytes(**options)) == index_content(index_bytes(**options, cache=123))


@pytest.mark.parametrize("change", [dict(paths=(b"b",)), dict(paths=(b"a", b"b")),
    dict(mode=0o100755), dict(oid=b"y"), dict(flags=0x1000), dict(flags=0x8000),
    dict(extended=0x2000), dict(extended=0x4000),
    dict(extension=struct.pack("!4sI", b"REUC", 1) + b"x"),
    dict(extension=struct.pack("!4sI", b"sdir", 0))])
def test_content_and_extensions_remain_protected(change):
    assert index_content(index_bytes(version=3)) != index_content(index_bytes(version=3, **change))


@pytest.mark.parametrize("bad", [b"broken", index_bytes()[:-1],
    index_bytes(extension=struct.pack("!4sI", b"link", 0)),
    index_bytes(extension=struct.pack("!4sI", b"TREE", 100)),
    index_bytes(version=5), index_bytes(paths=(b"z", b"a")),
    index_bytes(paths=(b"../a",)), index_bytes(version=2, extended=0x4000)])
def test_invalid_or_unsupported_index_fails_closed(bad):
    with pytest.raises((ValueError, struct.error)):
        index_content(bad)


@pytest.mark.parametrize("provider", ["codex", "claude"])  # allowlist:provider -- transport: shared guard regression
@pytest.mark.parametrize("change", ["stat-refresh", "entry", "new-entry", "broken", "extension", "config", "mode"])
def test_shared_guard_allows_only_stat_refresh(tmp_path, provider, change):
    if provider == "codex":  # allowlist:provider -- transport: fake fixture selection
        adapter, paths, _, _ = prepared(tmp_path)
        root = paths["repo"]
    else:
        root, adapter, _, _ = _prepared(tmp_path)
    target = root / ".git/index"
    target.write_bytes(index_bytes())
    try:
        adapter.before_provider_process()
        replacement = target.with_name("operator-refresh")
        replacement.write_bytes(index_bytes(cache=42))
        replacement.replace(target)
        if change == "entry": target.write_bytes(index_bytes(oid=b"y"))
        elif change == "new-entry": target.write_bytes(index_bytes(paths=(b"a", b"b")))
        elif change == "broken": target.write_bytes(b"broken")
        elif change == "extension": target.write_bytes(index_bytes(extension=struct.pack("!4sI", b"REUC", 1) + b"x"))
        elif change == "config": (root / ".git/config").write_text("changed")
        elif change == "mode": target.chmod(target.stat().st_mode ^ 0o100)
        if change == "stat-refresh":
            adapter.after_provider_process()
        else:
            with pytest.raises(AgentPermissionError, match="protected trees"):
                adapter.after_provider_process()
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("provider", ["codex", "claude"])  # allowlist:provider -- transport: shared real Git refresh regression
def test_operator_git_status_refresh_is_tolerated(tmp_path, provider):
    if provider == "codex":  # allowlist:provider -- transport: fake fixture selection
        adapter, paths, _, _ = prepared(tmp_path)
        root = paths["repo"]
    else:
        root, adapter, _, _ = _prepared(tmp_path)
    content = b"content\n"
    (root / "a").write_bytes(content)
    oid = hashlib.sha1(b"blob 8\0" + content).digest()
    target = root / ".git/index"
    before = index_bytes(oid=oid)
    target.write_bytes(before)
    try:
        adapter.before_provider_process()
        subprocess.run(["git", "status", "--short", "--untracked-files=no"], cwd=root,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_OPTIONAL_LOCKS": "1"},
                       check=True, capture_output=True)
        assert target.read_bytes() != before  # A real stat-cache write occurred.
        assert index_content(target.read_bytes()) == index_content(before)
        adapter.after_provider_process()
    finally:
        adapter.cleanup()


@pytest.mark.parametrize("linked", [False, True])
def test_only_bound_gitdir_index_receives_exemption(tmp_path, linked):
    root = tmp_path / "repo"
    root.mkdir()
    gitdir = tmp_path / "gitdir" if linked else root / ".git"
    gitdir.mkdir()
    if linked:
        (root / ".git").write_text(f"gitdir: {gitdir}\n")
    unrelated = root / "outbox"
    unrelated.mkdir()
    (unrelated / "HEAD").write_text("not a Gitdir")
    for directory in (gitdir, unrelated):
        (directory / "index").write_bytes(index_bytes())

    class Guard(ProtectedTreeGuard):
        _repository_root = root
        _protected_paths = (root / ".git", gitdir, unrelated)
        _process_evidence_path = None
        _protected_before = None
        _protection_error = AgentPermissionError
        metadata = {}

        def remove_sandbox_placeholders(self):
            return ()

    guard = Guard()
    guard.before_provider_process()
    (gitdir / "index").write_bytes(index_bytes(cache=123))
    guard.after_provider_process()
    guard.before_provider_process()
    (unrelated / "index").write_bytes(index_bytes(cache=123))
    with pytest.raises(AgentPermissionError, match="outbox/index"):
        guard.after_provider_process()
