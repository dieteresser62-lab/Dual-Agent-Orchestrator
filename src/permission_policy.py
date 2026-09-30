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


def _read_only_branch(arguments: list[str]) -> bool:
    listing = "--list" in arguments
    index = 0
    flags = {"--show-current", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
    revisions = {"--contains", "--no-contains", "--merged", "--no-merged"}
    while index < len(arguments):
        value = arguments[index]
        if value in revisions:
            if index + 1 < len(arguments) and not arguments[index + 1].startswith("-"):
                index += 1
        elif value.startswith("--format=") or any(value.startswith(flag + "=") for flag in revisions):
            pass
        elif value not in flags:
            if not listing or value.startswith("-"):
                return False
        index += 1
    return True


def _read_only_git(arguments: list[str]) -> bool:
    while arguments:
        if arguments[0] in {"--no-pager", "--paginate", "--literal-pathspecs", "--no-optional-locks", "--no-replace-objects"}:
            arguments = arguments[1:]
        elif arguments[0] in {"-C", "--git-dir", "--work-tree"} and len(arguments) >= 2:
            arguments = arguments[2:]
        elif arguments[0].startswith(("--git-dir=", "--work-tree=")):
            arguments = arguments[1:]
        else:
            break
    if not arguments or any(value == "--output" or value.startswith("--output=") for value in arguments):
        return False
    return (_read_only_branch(arguments[1:]) if arguments[0] == "branch" else arguments[0] in GIT_READS)


def _shell_layout(command: str) -> str:
    """Keep newlines as separators unless quoted; never evaluate shell text."""
    result, quote, escaped = [], None, False
    for char in command:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        result.append(";" if char == "\n" and quote is None else char)
    return "".join(result)


def _read_tokens(command: str) -> list[tuple[str, bool]]:
    """Distinguish shell operators from quoted/escaped literal arguments."""
    tokens, word, quote, index = [], [], None, 0
    def flush():
        if word:
            values = shlex.split("".join(word))
            if len(values) != 1:
                raise ValueError("ambiguous shell word")
            tokens.append((values[0], False))
            word.clear()
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            if index + 1 >= len(command):
                raise ValueError("incomplete shell escape")
            if command[index + 1] != "\n":
                word.extend((char, command[index + 1]))
            index += 2
            continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
            word.append(char)
        elif quote is not None:
            word.append(char)
        elif command[index:index + 2] == "${":
            end, depth = index + 2, 1
            while end < len(command) and depth:
                depth += (command[end] == "{") - (command[end] == "}")
                end += 1
            if depth:
                raise ValueError("incomplete parameter expansion")
            word.append(command[index:end])
            index = end
            continue
        elif char == "#" and not word:
            index = command.find("\n", index)
            if index == -1:
                break
            continue
        elif char.isspace():
            flush()
            if char == "\n":
                tokens.append((";", True))
        elif char in ";&|()<>":
            flush()
            end = index + 1
            while end < len(command) and command[end] in ";&|()<>":
                end += 1
            tokens.append((command[index:end], True))
            index = end
            continue
        else:
            word.append(char)
        index += 1
    flush()
    return tokens


def _read_only_shell(command: str) -> bool:
    # Substitutions execute commands; parameter expansions alone do not.
    active = re.sub(r"'[^']*'", "''", command)
    if re.search(r"\$\(|`|[<>]\(", active) or "<<" in active:
        return False
    segments, segment = [], []
    for token, operator in _read_tokens(command):
        if operator and token in {";", "&&", "||", "|"}:
            segments.append(segment)
            segment = []
        else:
            segment.append((token, operator))
    segments.append(segment)
    commands = 0
    for segment in segments:
        words, index = [], 0
        while index < len(segment):
            raw, operator = segment[index]
            fd = None
            if raw.isdigit() and index + 1 < len(segment) and segment[index + 1] in {(value, True) for value in (">", ">>", "<", ">&", "<&")}:
                fd, index = raw, index + 1
                raw, operator = segment[index]
            if operator and raw in {">", ">>", "<", ">&", "<&", "&>", "&>>"}:
                if index + 1 >= len(segment):
                    return False
                target, target_operator = segment[index + 1]
                if target_operator:
                    return False
                if not (raw in {">", ">>", "<", "&>", "&>>"} and target == "/dev/null" or raw == ">&" and fd == "2" and target == "1"):
                    return False
                index += 2
                continue
            if operator:
                return False
            word = raw
            if ("$" in word or word.startswith("~")) and re.search(r"(?:^|/)(?:\.git|\.orchestrator|inbox|outbox)(?:/|$)", word):
                return False
            words.append(word)
            index += 1
        while words and words[0] in {"if", "then", "elif", "else", "!"}:
            words = words[1:]
        if not words or words == ["fi"]:
            continue
        commands += 1
        name = Path(words[0]).name
        if name == "git":
            if not _read_only_git(words[1:]):
                return False
        elif name == "find":
            if any(value in {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fls"} or value.startswith("-fprint") for value in words[1:]):
                return False
        elif name in {"grep", "rg"}:
            if any(value in {"--output", "--pre"} or value.startswith(("--output=", "--pre=")) for value in words[1:]):
                return False
        elif name == "tree":
            if any(value.startswith("--output") or value.startswith("-") and not value.startswith("--") and "o" in value[1:] for value in words[1:]):
                return False
        elif name == "file":
            if "--compile" in words[1:] or any(value.startswith("-") and not value.startswith("--") and "C" in value[1:] for value in words[1:]):
                return False
        elif name not in {"ls", "cat", "head", "tail", "stat", "wc", "file", "du", "tree", "echo", "printf",
                          "test", "[", "pwd", "cd", "true", "realpath", "readlink"}:
            return False
    return bool(commands)


def _opaque_git_reads(command: str) -> bool:
    lexer = shlex.shlex(_shell_layout(command), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    for index, word in enumerate(words):
        if Path(word).name != "git":
            continue
        tail = words[index + 1:]
        tail = tail[:next((i for i, part in enumerate(tail) if part in {";", "&&", "||", "|", "&", ")"}), len(tail))]
        if not _read_only_git(tail):
            return False
    # Quoted command substitutions remain single lexer tokens.
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        if not _opaque_git_reads(inner[0] or inner[1]):
            return False
    return True


def _protected(value: str, root: Path, paths: tuple[Path, ...], *, ancestors: bool = False, resolve_symlinks: bool = True) -> bool:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    resolved = candidate.resolve(strict=False) if resolve_symlinks else lexical
    return any(location.is_relative_to(bound) or (ancestors and bound.is_relative_to(location)) for location in (lexical, resolved)
               for path in paths for bound in (Path(os.path.abspath(path)), path.resolve(strict=False) if resolve_symlinks else Path(os.path.abspath(path))))


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

    for assignment in re.finditer(r'(?<![\w"\'])([A-Za-z_]\w*)=("[^"\n]*"|[^\s;&]+)', layout):
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
    scratch: Path | None = None, *, resolve_symlinks: bool = True,
) -> str:
    """Return violation for a visible attempt on the boundary, else tolerated.

    Classification uses the complete unredacted input. Unknown tools and
    malformed inputs stay fail-closed; opaque shell text is a violation only
    when it names a protected path, non-reading git or indirect execution.
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
            return "violation" if _protected(value, repository_root, protected_paths, resolve_symlinks=resolve_symlinks) else "tolerated"
        if tool in {"Read", "Glob", "Grep"}:
            return "tolerated"
        if tool != "Bash":
            return "violation"
        command = data.get("command")
        if not isinstance(command, str) or not command.strip() or "\x00" in command:
            return "violation"
        try:
            if _read_only_shell(command):
                return "tolerated"
        except (ValueError, IndexError):
            pass  # Continue through the conservative opaque/normal fallback.
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
            if re.search(r"(?<![\w.-])(?:eval|source)(?![\w-])", command) or re.search(
                r"(?<![\w.-])(?:ba|z|fi|da)?sh\s+-\w*c", command,
            ):
                return "violation"
            if re.search(r"(?<![\w.-])git(?![\w-])", command) and not _opaque_git_reads(command):
                return "violation"
            return "tolerated"
        if any(Path(word).name in {"eval", "sh", "bash", "zsh", "fish", "source"} for word in words):
            return "violation"
        for index, word in enumerate(words):
            # A read-only git -C repository query names an ancestor of protected
            # trees, but does not modify that ancestor. Git writes are checked below.
            if (index > 1 and words[index - 1] == "-C" and
                any(Path(part).name == "git" for part in words[max(0, index - 4):index - 1]) and
                Path(os.path.abspath(word)) == repository_root):
                continue
            # Covers traversal, symlink aliases and paths after assignments or >.
            for value in re.split(r"[=<>;|&]", word):
                if value and _protected(value, repository_root, protected_paths, ancestors=True, resolve_symlinks=resolve_symlinks):
                    return "violation"
        for index, word in enumerate(words):
            if Path(word).name != "git":
                continue
            tail = words[index + 1:]
            tail = tail[:next((i for i, part in enumerate(tail) if part in {";", "&&", "||", "|", "&"}), len(tail))]
            if not _read_only_git(tail):
                return "violation"
        return "tolerated"
    except (ValueError, OSError, RuntimeError, TypeError):
        return "violation"
