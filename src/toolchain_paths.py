"""Validation shared by profile loading and sandbox transport normalization."""

from pathlib import Path


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
        if str(root) in result:
            raise ValueError("toolchain_read_roots contains duplicate resolved directories")
        result.append(str(root))
    return tuple(result)
