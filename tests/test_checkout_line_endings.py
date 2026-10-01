from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _tracked_regular_files(root: Path = ROOT) -> tuple[Path, ...]:
    output = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
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
        path = root / os.fsdecode(raw_path)
        if path.is_file():
            paths.append(path)
    return tuple(paths)


def _git_binary_paths(root: Path = ROOT) -> frozenset[str]:
    """Use Git's own decision: binary content (i/-text) or a binary attribute.

    Git also treats lone CR bytes as binary, so a NUL probe alone misclassifies
    files such as PDFs whose first NUL lies beyond the probed prefix.
    """
    output = subprocess.run(
        ["git", "ls-files", "--eol", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    binary: set[str] = set()
    for entry in output.split(b"\0"):
        if not entry:
            continue
        info, raw_path = entry.split(b"\t", 1)
        fields = info.split()
        if b"i/-text" in fields or b"attr/-text" in fields:
            binary.add(os.fsdecode(raw_path))
    return frozenset(binary)


def _tracked_text_files_with_cr(root: Path = ROOT) -> tuple[str, ...]:
    binary = _git_binary_paths(root)
    affected: list[str] = []
    for path in _tracked_regular_files(root):
        relative = path.relative_to(root).as_posix()
        if relative not in binary and b"\r" in path.read_bytes():
            affected.append(relative)
    return tuple(affected)


def test_all_tracked_text_files_in_worktree_are_cr_free() -> None:
    affected = _tracked_text_files_with_cr()

    assert not affected, "tracked text files containing CR bytes:\n" + "\n".join(
        affected
    )


def test_cr_guard_follows_git_binary_detection(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    # Lone CR makes Git treat this as binary although its first NUL lies
    # beyond 8,000 bytes; the PDF is binary by attribute.
    (tmp_path / "late-nul.dat").write_bytes(b"%PDF-1.4\n" + b"x" * 8_500 + b"\rstream\0")
    (tmp_path / "attribute.pdf").write_bytes(b"%PDF-1.4\r" + b"x" * 8_500)
    (tmp_path / "text.md").write_bytes(b"line\r\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)

    assert _git_binary_paths(tmp_path) == {"late-nul.dat", "attribute.pdf"}
    assert _tracked_text_files_with_cr(tmp_path) == ("text.md",)


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
