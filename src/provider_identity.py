"""Bind a provider launch to the executable and interpreter actually inspected."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CommandRunner = Callable[[list[str]], tuple[int, str, str]]
WINDOWS_REJECTION = "not permitted (Windows/DrvFS), not inspected"
WRAPPER_REJECTION = "not permitted (unchecked wrapper), not inspected"
WINDOWS_LAUNCHER_SUFFIXES = frozenset({".cmd", ".bat", ".ps1"})
SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})


@dataclass(frozen=True)
class ProviderIdentity:
    entry_path: str
    real_path: str
    version: str
    sha256: str | None
    interpreter_entry_path: str | None
    interpreter_real_path: str | None
    interpreter_version: str | None
    interpreter_args: tuple[str, ...] = ()
    interpreter_sha256: str | None = None
    kind: str = "verified"
    native_binary_path: str | None = None
    native_binary_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"verified", "dry_run"}:
            raise ValueError("provider identity kind is invalid")
        if not all(isinstance(value, str) and value for value in (self.entry_path, self.real_path, self.version)):
            raise ValueError("provider identity paths and version are required")
        if self.sha256 is None:
            raise ValueError("provider identity requires a binary SHA-256")
        if self.interpreter_real_path is None:
            if any(value is not None for value in (self.interpreter_entry_path, self.interpreter_version, self.interpreter_sha256)) or self.interpreter_args:
                raise ValueError("provider identity has an incomplete interpreter binding")
        elif not all(isinstance(value, str) and value for value in (self.interpreter_entry_path, self.interpreter_real_path, self.interpreter_version, self.interpreter_sha256)):
            raise ValueError("provider identity requires a complete interpreter binding")
        if (self.native_binary_path is None) != (self.native_binary_sha256 is None):
            raise ValueError("provider native binary binding is incomplete")
        if self.native_binary_path is not None and (not isinstance(self.native_binary_path, str) or not Path(self.native_binary_path).is_absolute()):
            raise ValueError("provider native binary path is invalid")
        for value in (self.sha256, self.interpreter_sha256, self.native_binary_sha256):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("provider identity SHA-256 is invalid")
        if not isinstance(self.interpreter_args, tuple) or any(not isinstance(arg, str) for arg in self.interpreter_args):
            raise ValueError("provider interpreter arguments are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind, "entry_path": self.entry_path, "real_path": self.real_path,
            "version": self.version, "sha256": self.sha256,
            "interpreter_entry_path": self.interpreter_entry_path,
            "interpreter_real_path": self.interpreter_real_path,
            "interpreter_version": self.interpreter_version,
            "interpreter_args": list(self.interpreter_args),
            "interpreter_sha256": self.interpreter_sha256,
            **({"native_binary_path": self.native_binary_path,
                "native_binary_sha256": self.native_binary_sha256} if self.native_binary_path is not None else {}),
        }

    @classmethod
    def from_dict(cls, value: object) -> ProviderIdentity:
        if not isinstance(value, dict) or set(value) - {"native_binary_path", "native_binary_sha256"} != {
            "kind", "entry_path", "real_path", "version", "sha256",
            "interpreter_entry_path", "interpreter_real_path", "interpreter_version",
            "interpreter_args", "interpreter_sha256",
        } or not isinstance(value["interpreter_args"], list):
            raise ValueError("provider identity fields are missing or unknown")
        return cls(
            entry_path=value["entry_path"], real_path=value["real_path"],
            version=value["version"], sha256=value["sha256"],
            interpreter_entry_path=value["interpreter_entry_path"],
            interpreter_real_path=value["interpreter_real_path"],
            interpreter_version=value["interpreter_version"],
            interpreter_args=tuple(value["interpreter_args"]),
            interpreter_sha256=value["interpreter_sha256"], kind=value["kind"],
            native_binary_path=value.get("native_binary_path"), native_binary_sha256=value.get("native_binary_sha256"),
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def dry_run(cls, slot: str) -> ProviderIdentity:
        marker = f"dry-run:{slot}"
        return cls(marker, marker, "dry-run", hashlib.sha256(marker.encode()).hexdigest(), None, None, None, kind="dry_run")

    @property
    def launch_prefix(self) -> tuple[str, ...]:
        if self.interpreter_real_path is None:
            return (self.real_path,)
        return (self.interpreter_real_path, *self.interpreter_args, self.real_path)


def executable_candidates(binary: str, path: str | None = None) -> tuple[str, ...]:
    """Return executable PATH entries in search order, retaining distinct links."""
    expanded = Path(binary).expanduser()
    if expanded.is_absolute() or "/" in binary or "\\" in binary:
        paths = (expanded.absolute(),)
    else:
        search = os.environ.get("PATH", os.defpath) if path is None else path
        paths = tuple(Path(part or ".") / binary for part in search.split(os.pathsep))
    result: list[str] = []
    for candidate in paths:
        absolute = str(candidate.absolute())
        if absolute not in result and candidate.is_file() and os.access(candidate, os.X_OK):
            result.append(absolute)
    return tuple(result)


def _real_file(entry: str) -> Path:
    try:
        target = Path(entry).resolve(strict=True)
        if not target.is_file() or not os.access(target, os.X_OK):
            raise ValueError(f"CLI target is not executable: {entry}")
        return target
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"CLI target cannot be resolved: {entry}") from exc


def _unescape_mount_path(value: str) -> Path:
    decoded = re.sub(
        r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value,
    )
    return Path(os.path.normpath(decoded))


def _windows_mount_points() -> tuple[Path, ...] | None:
    """Read Linux mount types; unknown mount tables must not authorize a launch."""
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    windows: list[Path] = []
    parsed = 0
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields, tail = before.split(), after.split()
        if not separator or len(fields) < 5 or not tail:
            continue
        parsed += 1
        if tail[0].casefold() in {"9p", "drvfs"}:
            windows.append(_unescape_mount_path(fields[4]))
    if parsed:
        return tuple(windows)
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        parsed += 1
        if fields[2].casefold() in {"9p", "drvfs"}:
            windows.append(_unescape_mount_path(fields[1]))
    return tuple(windows) if parsed else None


def _on_windows_mount(path: Path, mounts: tuple[Path, ...] | None) -> bool:
    normalized = Path(os.path.abspath(path))
    if mounts is not None:
        return any(normalized == mount or normalized.is_relative_to(mount) for mount in mounts)
    # Standard WSL and Docker Desktop drive mappings remain recognizable when
    # both kernel mount tables are unavailable; other paths fail closed below.
    parts = normalized.parts
    return (
        len(parts) >= 3 and parts[:2] == ("/", "mnt")
        and len(parts[2]) == 1 and parts[2].isalpha()
    ) or (
        len(parts) >= 6 and parts[:5] == ("/", "run", "desktop", "mnt", "host")
        and len(parts[5]) == 1 and parts[5].isalpha()
    )


def _reject_windows_target(entry: str, real: Path, mounts: tuple[Path, ...] | None) -> None:
    candidates = (("entry", Path(entry)), ("realpath", real))
    for label, candidate in candidates:
        if _on_windows_mount(candidate, mounts):
            raise ValueError(f"{WINDOWS_REJECTION}: {label}={candidate} lies on a Windows/DrvFS mount")
    for label, candidate in candidates:
        if candidate.suffix.casefold() in WINDOWS_LAUNCHER_SUFFIXES:
            raise ValueError(f"{WINDOWS_REJECTION}: {label}={candidate} has a Windows launcher suffix")
    if mounts is None:
        raise ValueError(f"{WINDOWS_REJECTION}: mount type cannot be verified for {real}")
    with real.open("rb") as stream:
        magic = stream.read(4)
    if magic.startswith(b"MZ"):
        raise ValueError(f"{WINDOWS_REJECTION}: realpath={real} starts with PE magic MZ")
    if any(candidate.suffix.casefold() == ".exe" for _, candidate in candidates) and magic != b"\x7fELF":
        raise ValueError(f"{WINDOWS_REJECTION}: realpath={real} has an .exe suffix but is not ELF")


def _interpreter(
    script: Path, path: str, mounts: tuple[Path, ...] | None,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    with script.open("rb") as stream:
        first = stream.readline(512)
    if not first.startswith(b"#!"):
        return None, None, ()
    try:
        words = shlex.split(first[2:].decode("utf-8").strip())
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid script interpreter: {script}") from exc
    if not words or not Path(words[0]).is_absolute():
        raise ValueError(f"script interpreter must be absolute: {script}")
    if Path(words[0]).name == "env":
        tail = words[1:]
        if tail[:1] == ["-S"]:
            tail = tail[1:]
        elif len(tail) != 1:
            raise ValueError(f"env shebang with arguments requires -S: {script}")
        if not tail or tail[0].startswith("-"):
            raise ValueError(f"unsupported env shebang: {script}")
        entries = executable_candidates(tail[0], path)
        if not entries:
            raise ValueError(f"script interpreter is missing: {tail[0]}")
        entry, args = entries[0], tuple(tail[1:])
    else:
        entry, args = words[0], tuple(words[1:])
    target = _real_file(entry)
    _reject_windows_target(entry, target, mounts)
    with target.open("rb") as stream:
        if stream.read(2) == b"#!":
            raise ValueError(f"nested script interpreter is unsupported: {entry}")
    if Path(entry).name in SHELL_INTERPRETERS or target.name in SHELL_INTERPRETERS:
        raise ValueError(f"{WRAPPER_REJECTION}: {script} uses {entry}")
    return entry, str(target), args


def check_provider_candidate(
    entry: str, *, path: str | None = None,
) -> tuple[Path, str | None, str | None, tuple[str, ...]]:
    """Reject unsafe targets and interpreters without invoking either one."""
    if not sys.platform.startswith("linux"):
        raise ValueError("unsupported platform: only Linux and WSL2 are supported")
    search_path = os.environ.get("PATH", os.defpath) if path is None else path
    mounts = _windows_mount_points()
    real = _real_file(entry)
    try:
        _reject_windows_target(entry, real, mounts)
        interpreter_entry, interpreter_real, interpreter_args = _interpreter(
            real, search_path, mounts,
        )
    except OSError as exc:
        raise ValueError(f"CLI target cannot be read: {entry}") from exc
    return real, interpreter_entry, interpreter_real, interpreter_args


def _version(command: list[str], run: CommandRunner) -> str:
    rc, out, err = run(command)
    value = (out or err).strip()
    if rc != 0 or not value:
        raise ValueError(
            f"cannot determine version using {command[0]}: {(err or out).strip() or 'empty output'}"
        )
    return value


def capture_provider_identity(
    entry: str, version_args: tuple[str, ...], run: CommandRunner,
    *, path: str | None = None, expected: ProviderIdentity | None = None,
) -> ProviderIdentity:
    """Inspect one entry and probe precisely the launch prefix returned below."""
    real, interpreter_entry, interpreter_real, interpreter_args = check_provider_candidate(
        entry, path=path,
    )
    try:
        digest = hashlib.sha256(real.read_bytes()).hexdigest()
        interpreter_digest = (
            hashlib.sha256(Path(interpreter_real).read_bytes()).hexdigest()
            if interpreter_real is not None else None
        )
        native_path, native_digest = _native_binary_identity(real)
    except OSError as exc:
        raise ValueError(f"CLI target cannot be read: {entry}") from exc
    if expected is not None and (
        entry != expected.entry_path or str(real) != expected.real_path
        or digest != expected.sha256
        or interpreter_entry != expected.interpreter_entry_path
        or interpreter_real != expected.interpreter_real_path
        or interpreter_args != expected.interpreter_args
        or interpreter_digest != expected.interpreter_sha256
        or native_path != expected.native_binary_path or native_digest != expected.native_binary_sha256
    ):
        raise ValueError("on-disk binary or interpreter identity differs from the bound identity")
    prefix = (
        (str(real),) if interpreter_real is None else
        (interpreter_real, *interpreter_args, str(real))
    )
    version = _version([*prefix, *version_args], run)
    interpreter_version = (
        _version([interpreter_real, "--version"], run)
        if interpreter_real is not None else None
    )
    return ProviderIdentity(
        entry, str(real), version, digest,
        interpreter_entry, interpreter_real, interpreter_version, interpreter_args,
        interpreter_digest,
        native_binary_path=native_path, native_binary_sha256=native_digest,
    )


def _native_binary_identity(real: Path) -> tuple[str | None, str | None]:
    """Bind the npm dispatcher's native executable without invoking it."""
    if real.name != "codex.js" or real.parent.name != "bin":  # allowlist:provider -- profile configuration: native npm dispatcher
        return None, None
    root = real.parent.parent
    if root.parts[-3:] != ("node_modules", "@openai", "codex"):  # allowlist:provider -- profile configuration: native npm dispatcher
        return None, None
    from native_provider_schema import validate_codex_review_package_root  # allowlist:provider -- profile configuration: native package validation
    validate_codex_review_package_root(root)  # allowlist:provider -- profile configuration: native package executable
    candidates = (*root.glob("vendor/*/bin/codex"), *root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"))  # allowlist:provider -- profile configuration: native package executable
    native = candidates[0].resolve(strict=True)
    return str(native), hashlib.sha256(native.read_bytes()).hexdigest()


def inspect_provider_installations(
    binary: str, version_args: tuple[str, ...], run: CommandRunner,
    *, path: str | None = None,
) -> tuple[tuple[ProviderIdentity, ...], tuple[str, ...]]:
    identities: list[ProviderIdentity] = []
    failures: list[str] = []
    for entry in executable_candidates(binary, path):
        try:
            identities.append(capture_provider_identity(entry, version_args, run, path=path))
        except ValueError as exc:
            failures.append(f"{entry}: {exc}")
    return tuple(identities), tuple(failures)
