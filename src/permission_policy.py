"""Classify denied implementer actions without executing the attempted operation."""

import os
from pathlib import Path
import re
import shlex


GIT_WRITES = frozenset({
    "commit", "push", "reset", "checkout", "switch", "rebase", "merge", "cherry-pick",
    "revert", "tag", "update-ref", "worktree", "stash", "clean", "rm", "mv", "add",
    "restore", "apply", "am", "config",
})
GIT_READS = frozenset({"status", "diff", "show", "log", "ls-files", "rev-parse", "cat-file", "ls-tree", "check-ignore"})


def _protected(value: str, root: Path, paths: tuple[Path, ...], *, ancestors: bool = False) -> bool:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    resolved = candidate.resolve(strict=False)
    return any(location.is_relative_to(bound) or (ancestors and bound.is_relative_to(location)) for location in (lexical, resolved)
               for path in paths for bound in (Path(os.path.abspath(path)), path.resolve(strict=False)))


def _shell_words(command: str, scratch: Path | None) -> list[str]:
    """Resolve simple literal assignments; reject unresolved shell authority.

    The smoke mutation uses M=$TMPDIR/mut and a function with positional
    arguments. Quoted program text and quoted heredoc bodies are inert shell
    data; the caller has already checked their literal protected-path names.
    """
    lines = iter(command.splitlines(keepends=True))
    headers = []
    for line in lines:
        headers.append(line)
        if "<<" not in line:
            continue
        marker = re.search(r"<<-?\s*(['\"])([A-Za-z_][A-Za-z_0-9]*)\1", line)
        if marker is None:
            raise ValueError("unbound heredoc")
        for body in lines:
            if body.strip() == marker.group(2):
                break
        else:
            raise ValueError("unterminated heredoc")
    plain = "".join(headers)
    # Mask single-quoted data without changing offsets used by the bindings.
    layout = re.sub(r"'[^']*'", lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", plain)
    functions = tuple(match.span() for match in re.finditer(r"\b\w+\s*\(\s*\)\s*\{[^{}]*\}", layout))
    plain = re.sub(r"'[^']*'", lambda m: m.group(0).replace("$", "_").replace("`", "_").replace("\\", "_"), plain)
    bindings = {"TMPDIR": [(-1, str(scratch))]} if scratch is not None else {}
    variable = re.compile(r"\$(?:\{([A-Za-z_]\w*|[1-9])\}|([A-Za-z_]\w*|[1-9]))")

    def expand(match, offset=0):
        name = match.group(1) or match.group(2)
        position = match.start() + offset
        if name.isdigit() and any(start < position < end for start, end in functions):
            return "/function-argument"
        bound = next((value for start, value in reversed(bindings.get(name, [])) if start < position), None)
        return bound if bound is not None else match.group(0)

    for assignment in re.finditer(r'(?<![\w])([A-Za-z_]\w*)=("[^"\n]*"|[^\s;&]+)', layout):
        value_start = assignment.start(2)
        value_end = assignment.end(2)
        # Apply expansion with original offsets so a later assignment never
        # supplies authority to an earlier variable use.
        expanded = variable.sub(lambda match: expand(match, value_start), plain[value_start:value_end]).strip('"')
        if re.fullmatch(r"[A-Za-z_0-9./:-]+", expanded) and ".." not in Path(expanded).parts:
            bindings.setdefault(assignment.group(1), []).append((assignment.start(), expanded))
        else:
            bindings.setdefault(assignment.group(1), []).append((assignment.start(), None))
    plain = variable.sub(expand, plain)
    if "$" in plain or "`" in plain or "\\" in plain:
        raise ValueError("unresolved shell expansion")
    lexer = shlex.shlex(plain, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def classify_implementer_denial(
    denial: object, repository_root: Path | None, protected_paths: tuple[Path, ...] | None,
    scratch: Path | None = None,
) -> str:
    """Return violation for a visible attempt on the boundary, else tolerated.

    Classification uses the complete unredacted input. Unknown tools and
    malformed inputs stay fail-closed; opaque shell text is a violation only
    when it names a protected path or shows git or indirect execution.
    """
    if not isinstance(denial, dict) or repository_root is None or not protected_paths:
        return "violation"
    tool = denial.get("tool_name")
    data = denial.get("tool_input", denial.get("input"))
    if not isinstance(data, dict):
        return "violation"
    try:
        if tool in {"Edit", "Write", "NotebookEdit"}:
            value = data.get("notebook_path") if tool == "NotebookEdit" else data.get("file_path", data.get("path"))
            if not isinstance(value, str) or not value or "\x00" in value or "$" in value or "~" in value:
                return "violation"
            return "violation" if _protected(value, repository_root, protected_paths) else "tolerated"
        if tool in {"Read", "Glob", "Grep"}:
            return "tolerated"
        if tool != "Bash":
            return "violation"
        command = data.get("command")
        if not isinstance(command, str) or not command.strip() or "\x00" in command:
            return "violation"
        # Names at word starts, including redirection and quoted strings.
        if re.search(r"(?<![\w.-])(?:\./)?(?:\.git|\.orchestrator|inbox|outbox)(?:[/\s\"';]|$)", command):
            return "violation"
        for path in protected_paths:
            if str(path) in command:
                return "violation"
        try:
            words = _shell_words(command, scratch)
        except ValueError:
            # Opaque shell text (unresolved expansion, escapes, heredocs) names
            # no protected path here, and the sandbox still blocks every write
            # to one. Stop only when git or indirect execution is visible.
            if re.search(r"(?<![\w.-])(?:git|eval|source)(?![\w-])", command) or re.search(
                r"(?<![\w.-])(?:ba|z|fi|da)?sh\s+-\w*c", command,
            ):
                return "violation"
            return "tolerated"
        if any(Path(word).name in {"eval", "sh", "bash", "zsh", "fish", "source"} for word in words):
            return "violation"
        for word in words:
            # Covers traversal, symlink aliases and paths after assignments or >.
            for value in re.split(r"[=<>;|&]", word):
                if value and _protected(value, repository_root, protected_paths, ancestors=True):
                    return "violation"
        for index, word in enumerate(words):
            if Path(word).name != "git":
                continue
            tail = words[index + 1:]
            while tail and tail[0] in {"--no-pager", "--paginate", "--literal-pathspecs"}:
                tail = tail[1:]
            if tail and tail[0] == "-C" and len(tail) >= 2:
                tail = tail[2:]
            operation = tail[0] if tail else None
            if operation in GIT_WRITES or operation not in GIT_READS | {"branch"}:
                return "violation"
            if operation == "branch":
                if any(part in {"-d", "-D", "-m", "-M", "--delete", "--move"} for part in tail):
                    return "violation"
                # Only explicit listing is provably read-only.
                if not tail or tail[0] != "branch" or any(not part.startswith("-") for part in tail[1:]):
                    return "violation"
                if any(part not in {"--list", "-a", "--all", "-r", "--remotes", "-v", "-vv"} for part in tail[1:]):
                    return "violation"
        return "tolerated"
    except (ValueError, OSError, RuntimeError, TypeError):
        return "violation"
