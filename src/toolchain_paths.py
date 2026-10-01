"""Validation shared by profile loading and sandbox transport normalization."""

from pathlib import Path
import stat
import tempfile


def create_private_scratch() -> Path:
    """Fresh per-call scratch; never inherit a provider-selected TMPDIR."""
    scratch = Path(tempfile.mkdtemp(prefix="dao-implementer-scratch-", dir="/tmp"))
    scratch.chmod(0o700)
    return scratch


# Paths relative to HOME; test both aliases and resolved locations.
CREDENTIAL_LOCATIONS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".config", ".codex", ".claude",  # allowlist:provider -- profile configuration: credential path denylist
    ".claude.json", ".gemini", ".docker", ".kube", ".netrc", ".git-credentials",  # allowlist:provider -- profile configuration: credential path denylist
    ".password-store", ".local/share/keyrings", ".npmrc", ".pypirc",
)
AGENT_CONFIG_LOCATIONS = (".claude", ".codex", ".gemini", ".agents")  # allowlist:provider -- profile configuration: repository agent configuration


def _overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def validate_private_scratch(
    scratch: Path, repository_root: Path, protected_paths: tuple[Path, ...],
    tool_roots: tuple[str, ...],
) -> None:
    """Require a private regular directory outside every authority root."""
    if not scratch.is_absolute() or "," in str(scratch) or any(c.isspace() for c in str(scratch)):
        raise ValueError("private scratch path is unsafe")
    roots = (repository_root, Path.home(), *protected_paths, *(Path(p) for p in tool_roots))
    try:
        resolved = scratch.resolve(strict=True)
        metadata = scratch.lstat()
        if (resolved != scratch or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or any(_overlaps(candidate, root) for candidate in (scratch, resolved)
                   for path in roots for root in (path.absolute(), path.resolve()))):
            raise ValueError("private scratch overlaps an authority root or is not private")
    except (OSError, RuntimeError) as exc:
        raise ValueError("private scratch cannot be resolved") from exc


def validate_toolchain_read_roots(
    values: object, repository_root: Path, protected_paths: tuple[Path, ...] = (),
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > 8:
        raise ValueError("toolchain_read_roots must be a list of at most 8 directories")
    repository = repository_root.resolve()
    personal = Path.home().resolve()
    protected = tuple(path.resolve() for path in protected_paths)
    result = []
    for value in values:
        if (not isinstance(value, str) or not Path(value).is_absolute()
            or "," in value or any(char.isspace() for char in value)):
            raise ValueError("toolchain_read_roots requires absolute paths without rule separators")
        lexical = Path(value)
        if (lexical.is_relative_to(repository) or repository.is_relative_to(lexical)
            or personal.is_relative_to(lexical)):
            raise ValueError("toolchain_read_roots overlaps HOME or repository")
        try:
            root = lexical.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("toolchain_read_roots directory is missing or unresolvable") from exc
        if not root.is_dir():
            raise ValueError("toolchain_read_roots requires directories")
        if (root == Path("/") or personal.is_relative_to(root)
            or repository.is_relative_to(root) or root.is_relative_to(repository)
            or any(root.is_relative_to(path) or path.is_relative_to(root) for path in protected)
            or "," in str(root) or any(char.isspace() for char in str(root))):
            raise ValueError("toolchain_read_roots overlaps HOME, repository or protected paths")
        credentials = tuple(personal / name for name in CREDENTIAL_LOCATIONS)
        if any(_overlaps(candidate, location) for candidate in (lexical, root)
               for path in credentials for location in (path, path.resolve())):
            raise ValueError("toolchain_read_roots overlaps HOME credentials")
        if str(root) in result:
            raise ValueError("toolchain_read_roots contains duplicate resolved directories")
        result.append(str(root))
    return tuple(result)
