from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
GIT_BINARY_PROBE_BYTES = 8_000


def _tracked_regular_files() -> tuple[Path, ...]:
    output = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    paths: list[Path] = []
    for entry in output.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0]
        if mode not in {b"100644", b"100755"}:
            continue
        path = ROOT / os.fsdecode(raw_path)
        if path.is_file():
            paths.append(path)
    return tuple(paths)


def _is_binary_for_git(data: bytes) -> bool:
    return b"\0" in data[:GIT_BINARY_PROBE_BYTES]


def _tracked_text_files_with_cr() -> tuple[str, ...]:
    affected: list[str] = []
    for path in _tracked_regular_files():
        data = path.read_bytes()
        if not _is_binary_for_git(data) and b"\r" in data:
            affected.append(path.relative_to(ROOT).as_posix())
    return tuple(affected)


def test_all_tracked_text_files_in_worktree_are_cr_free() -> None:
    affected = _tracked_text_files_with_cr()

    assert not affected, "tracked text files containing CR bytes:\n" + "\n".join(
        affected
    )


def test_checkout_attributes_force_lf_without_forcing_binary_text() -> None:
    attributes = subprocess.run(
        [
            "git",
            "check-attr",
            "text",
            "eol",
            "--",
            ".gitattributes",
            "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json",
            "run_task",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert attributes == [
        ".gitattributes: text: auto",
        ".gitattributes: eol: lf",
        "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json: text: auto",
        "tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json: eol: lf",
        "run_task: text: set",
        "run_task: eol: lf",
    ]


def test_checkout_policy_preserves_binary_bytes_with_autocrlf_enabled(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"], cwd=tmp_path, check=True
    )
    (tmp_path / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    text_bytes = b"text with CRLF\r\n"
    binary_bytes = b"binary\0bytes\r\n"
    (tmp_path / "sample.txt").write_bytes(text_bytes)
    (tmp_path / "sample.bin").write_bytes(binary_bytes)

    subprocess.run(
        ["git", "add", ".gitattributes", "sample.txt", "sample.bin"],
        cwd=tmp_path,
        check=True,
    )

    assert subprocess.run(
        ["git", "show", ":sample.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    ).stdout == b"text with CRLF\n"
    assert subprocess.run(
        ["git", "show", ":sample.bin"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    ).stdout == binary_bytes

    (tmp_path / "sample.txt").unlink()
    (tmp_path / "sample.bin").unlink()
    subprocess.run(
        ["git", "checkout-index", "--force", "--", "sample.txt", "sample.bin"],
        cwd=tmp_path,
        check=True,
    )

    assert (tmp_path / "sample.txt").read_bytes() == b"text with CRLF\n"
    assert (tmp_path / "sample.bin").read_bytes() == binary_bytes
