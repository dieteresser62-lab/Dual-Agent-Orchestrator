"""Classify denied implementer actions without executing the attempted operation."""

import os
from pathlib import Path
from toolchain_paths import CREDENTIAL_LOCATIONS, AGENT_CONFIG_LOCATIONS
import re
import shlex
import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class DenialClassification:
    disposition: str
    rule: str
    fragment: str


def _violation(rule, fragment):
    return DenialClassification("violation", rule, str(fragment))


def _tolerated(rule, fragment):
    return DenialClassification("tolerated", rule, str(fragment))


GIT_WRITES = frozenset({
    "commit", "push", "reset", "checkout", "switch", "rebase", "merge", "cherry-pick",
    "revert", "tag", "update-ref", "worktree", "stash", "clean", "rm", "mv", "add",
    "restore", "apply", "am", "config",
})
GIT_READS = frozenset({"status", "diff", "show", "log", "ls-files", "rev-parse", "cat-file", "ls-tree", "check-ignore"})
SHELL_OUTPUT_DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
SHELL_SPECIAL_PARAMETER = re.compile(r"\$(?:[?!#$@*0-9-]|\{[?!#$@*0-9-]\})")


def _channel_duplication(operator, target):
    return operator in {">&", "<&"} and re.fullmatch(r"(?:[0-9]+|-)", target) is not None


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


def _git_arguments(arguments: list[str]) -> list[str]:
    while arguments:
        if arguments[0] in {"--no-pager", "--paginate", "--literal-pathspecs", "--no-optional-locks", "--no-replace-objects"}:
            arguments = arguments[1:]
        elif arguments[0] in {"-C", "--git-dir", "--work-tree"} and len(arguments) >= 2:
            arguments = arguments[2:]
        elif arguments[0].startswith(("--git-dir=", "--work-tree=")):
            arguments = arguments[1:]
        else:
            break
    return arguments


def _read_only_git(arguments: list[str]) -> bool:
    arguments = _git_arguments(arguments)
    if not arguments or any(value == "--output" or value.startswith("--output=") for value in arguments):
        return False
    return (_read_only_branch(arguments[1:]) if arguments[0] == "branch" else arguments[0] in GIT_READS)


def _shell_layout(command: str) -> str:
    """Keep newlines as separators unless quoted; never evaluate shell text."""
    result, quote, escaped, continuation = [], None, False, False
    for char in command:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if not char.isspace():
            continuation = quote is None and char in "&|"
        result.append(";" if char == "\n" and quote is None and not continuation else char)
    return "".join(result)


def _read_tokens(command: str) -> list[tuple[str, bool]]:
    from shell_inspection import tokens
    return [(item.value, item.operator) for item in tokens(command)]


def _read_only_shell(command: str) -> bool:
    # Substitutions execute commands; parameter expansions alone do not.
    from shell_inspection import tokens
    inspected = tokens(command)
    if any(item.substitution for item in inspected) or any(item.operator and "<<" in item.value for item in inspected):
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
                if not (raw in {">", ">>", "&>", "&>>"} and target in SHELL_OUTPUT_DEVICES
                        or raw == "<" and target == "/dev/null" or _channel_duplication(raw, target)):
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


def _opaque_git_violation(command: str) -> str | None:
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
            return "git " + " ".join(_git_arguments(tail))
    # Quoted command substitutions remain single lexer tokens.
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        violation = _opaque_git_violation(inner[0] or inner[1])
        if violation is not None:
            return violation
    return None


def _opaque_git_reads(command: str) -> bool:
    return _opaque_git_violation(command) is None


def _protected(value: str, root: Path, paths: tuple[Path, ...], *, ancestors: bool = False, resolve_symlinks: bool = True) -> bool:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        resolved = candidate.resolve(strict=False) if resolve_symlinks else lexical
    except OSError:
        resolved = lexical
    return any(location.is_relative_to(bound) or (ancestors and bound.is_relative_to(location)) for location in (lexical, resolved)
               for path in paths for bound in (Path(os.path.abspath(path)), path.resolve(strict=False) if resolve_symlinks else Path(os.path.abspath(path))))


def _shell_words(command: str, scratch: Path | None, *, allow_unknown: bool = False) -> list[str]:
    """Resolve simple literal assignments; reject unresolved shell authority.

    The smoke mutation uses M=$TMPDIR/mut and a function with positional
    arguments. Quoted program text and quoted heredoc bodies are inert shell
    data; the caller has already checked their literal protected-path names.
    """
    from shell_inspection import tokens, quoted_heredoc_layout
    if any(item.substitution for item in tokens(command)):
        raise ValueError("active substitution requires separate inspection")
    plain = quoted_heredoc_layout(command)
    # Mask single-quoted data without changing offsets used by the bindings.
    layout = re.sub(r"'[^']*'", lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", plain)
    functions = tuple(match.span() for match in re.finditer(r"\b\w+\s*\(\s*\)\s*\{[^{}]*\}", layout))
    plain = re.sub(r"'[^']*'", lambda m: m.group(0).replace("$", "_").replace("`", "_").replace("\\", "_"), plain)
    bindings = {"HOME": [(-1, str(Path.home()))]}
    if scratch is not None:
        bindings["TMPDIR"] = [(-1, str(scratch))]
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
    authority = SHELL_SPECIAL_PARAMETER.sub("", plain)
    if allow_unknown:
        authority = variable.sub("", authority)
    if "$" in authority or "`" in plain or "\\" in plain:
        raise ValueError("unresolved shell expansion")
    lexer = shlex.shlex(_shell_layout(plain), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _credential(value, root, *, resolve_symlinks=True):
    home = str(Path.home())
    value = value.replace("${HOME}", home).replace("$HOME", home)
    if value.startswith("~"):
        value = home + value[1:]
    candidate = Path(value) if value.startswith("/") else root / value
    locations = tuple(Path.home() / name for name in CREDENTIAL_LOCATIONS)
    if any(char in value for char in '*?['):
        import fnmatch
        if any(fnmatch.fnmatchcase(str(location), str(part)) for part in (candidate, *candidate.parents) for location in locations): return True
    if _protected(str(candidate), root, locations, resolve_symlinks=resolve_symlinks):
        return True
    if re.search(r"/proc/(?:[^/\s]+/)*environ(?:$|[\s/\"'])", value):
        return True
    if not any(part == ".env" or part.startswith((".env.", ".env*", ".env?", ".env[")) for part in candidate.parts): return False
    resolved = candidate.resolve(strict=False) if resolve_symlinks else Path(os.path.abspath(candidate))
    return any(part == ".env" or part.startswith((".env.", ".env*", ".env?", ".env[")) for part in candidate.parts) and not resolved.is_relative_to(root)


def _credential_text(command, root, resolve_symlinks):
    text = command.replace("${HOME}", str(Path.home())).replace("$HOME", str(Path.home())).replace("~/", str(Path.home()) + "/")
    match = re.search(r"/proc/(?:[^/\s]+/)*environ(?:$|[\s/\"'])", text)
    if match:
        return match.group()
    for name in CREDENTIAL_LOCATIONS:
        if str(Path.home() / name) in text:
            return str(Path.home() / name)
    from shell_inspection import tokens
    cwd, start, directory_next = root, True, False
    for item in tokens(command):
        if item.operator:
            start, directory_next = True, False
            continue
        if _credential(item.value, cwd or root, resolve_symlinks=resolve_symlinks): return item.value
        if directory_next:
            value = item.value.replace("${HOME}", str(Path.home())).replace("$HOME", str(Path.home()))
            value = str(Path.home()) + value[1:] if value.startswith("~") else value
            cwd = Path(os.path.abspath(cwd / value)) if cwd and not any(char in value for char in "$`*?[") else None
            directory_next = False
        if start:
            directory_next = item.value == "cd"
            start = False
    return None


def _safe_write(value, root, paths, scratch, *, resolve_symlinks=True):
    if value == "/dev/null": return True
    if not value or any(char in value for char in "$`*?[\\") or root is None: return False
    value = value.replace("~", str(Path.home()), 1) if value.startswith("~") else value
    candidate = Path(os.path.abspath(root / value))
    if resolve_symlinks and any(path.is_symlink() for path in (candidate, *candidate.parents)): return False
    protected = (*paths, *(root / name for name in AGENT_CONFIG_LOCATIONS))
    if _protected(str(candidate), root, protected, ancestors=True, resolve_symlinks=resolve_symlinks): return False
    resolved = candidate.resolve(strict=False) if resolve_symlinks else candidate
    return any(candidate.is_relative_to(bound) and resolved.is_relative_to(bound.resolve(strict=False) if resolve_symlinks else Path(os.path.abspath(bound)))
               for bound in (root, scratch) if bound is not None)


def _write_violation(value, root, paths, scratch, resolve_symlinks):
    if root is not None and _safe_write(value, root, paths, scratch, resolve_symlinks=resolve_symlinks):
        return None
    if root is not None and value and not any(char in value for char in "$`*?[\\"):
        if _protected(value, root, (*paths, *(root / name for name in AGENT_CONFIG_LOCATIONS)), ancestors=True,
                      resolve_symlinks=resolve_symlinks):
            return _violation("protected-path", value)
    return _violation("outside-write", value)


def _bash_write_target(value, cwd, root, paths, scratch, resolve_symlinks):
    """Inspect visible authority; an ordinary unknown parameter grants none."""
    value = os.path.expanduser(value) if value.startswith("~") else value
    if any(char in value for char in "`\\") or any(part in value for part in ("$(", "<(", ">(", "$'")):
        return _violation("opaque-write", value)
    if "$" in value:
        # A literal outside directory is observable even if its final filename
        # is unknown. A variable-only root does not identify a write location.
        if value.startswith("/"):
            literal = value.split("$", 1)[0]
            prefix = Path(literal) if literal.endswith("/") else Path(literal).parent
            prefix = Path(os.path.abspath(prefix))
            if resolve_symlinks and any(parent.is_symlink() for parent in (prefix, *prefix.parents)):
                return _violation("outside-write", value)
            if not any(prefix.is_relative_to(bound) for bound in (root, scratch) if bound is not None):
                return _violation("outside-write", value)
        return _tolerated("opaque-unknown-target", value)
    if not Path(value).is_absolute() and cwd is None:
        # Preserve the existing guard against writing a literal relative name
        # after an indirect cd (including git-dir command substitution).
        return _violation("indirect-exec", value)
    return _write_violation(str((cwd or root) / value), root, paths, scratch, resolve_symlinks)


def _write_boundary(words, root, paths, scratch, resolve_symlinks):
    separators = {";", "&&", "||", "|", "&", "(", ")"}
    segments, current = [], []
    for word in words:
        if word in separators:
            if current: segments.append(current)
            current = []
        else: current.append(word)
    if current: segments.append(current)
    cwd, unknown = root, None
    for segment in segments:
        while segment and (segment[0] in {"then", "if", "elif", "else", "!", "do", "while", "until"} or re.match(r"^[A-Za-z_]\w*=", segment[0])):
            segment = segment[1:]
        if not segment: continue
        name = Path(segment[0]).name
        targets, arguments, index = [], [], 1
        while index < len(segment):
            word = segment[index]
            if word.isdigit() and index+1 < len(segment) and segment[index+1] in {">", ">>", ">&", "<", "<&"}:
                index += 1
                word = segment[index]
            if word in {">", ">>", "&>", "&>>", "<", ">&", "<&"}:
                if index+1 >= len(segment):
                    unknown = _tolerated("opaque-unknown-target", word)
                    break
                target = segment[index+1]
                if not _channel_duplication(word, target) and word in {">", ">>", "&>", "&>>", ">&"}:
                    if target not in SHELL_OUTPUT_DEVICES: targets.append(target)
                index += 2
            else:
                arguments.append(word)
                index += 1
        operands = [word for word in arguments if not word.startswith("-")]
        if name in {"cp", "install", "rsync", "ln"}:
            targets += operands[-1:]
            for index, word in enumerate(segment[1:], 1):
                if word in {"-t", "--target-directory"} and index+1 < len(segment): targets.append(segment[index+1])
                elif word.startswith("--target-directory="): targets.append(word.split("=",1)[1])
            if not targets: unknown = _tolerated("opaque-unknown-target", name)
        elif name in {"mv", "rm", "touch", "mkdir", "chmod", "tee"}:
            targets += operands
            if not targets: unknown = _tolerated("opaque-unknown-target", name)
        for value in targets:
            result = _bash_write_target(value, cwd, root, paths, scratch, resolve_symlinks)
            if result is not None:
                if result.disposition == "violation": return result
                unknown = result
        if name == "cd":
            target = operands[0] if len(operands) == 1 else ""
            cwd = Path(os.path.abspath(cwd / target)) if cwd and target and not any(c in target for c in "$`*?[~") else None
    return unknown



def _subcommands_violate(items, root, paths, scratch, resolve_symlinks):
    cwd, start, next_directory = root, True, False
    for item in items:
        if item.operator:
            start, next_directory = True, False
            continue
        prefix = "cd " + (shlex.quote(str(cwd)) if cwd is not None else "$UNBOUND") + "; "
        for inner in item.subcommands:
            rejected = _classify_bash(prefix + inner, root, paths, scratch, resolve_symlinks)
            if rejected.disposition == "violation": return rejected
        if next_directory:
            value = item.value.replace("${TMPDIR}", str(scratch)).replace("$TMPDIR", str(scratch)) if scratch else item.value
            cwd = Path(os.path.abspath(cwd / value)) if cwd and not item.substitution and not any(char in value for char in "$`*?[~") else None
            next_directory = False
        if start:
            next_directory = item.value == "cd"
            start = False
    return None

def _classify_bash(command, root, paths, scratch, resolve_symlinks):
    from shell_inspection import tokens, protected_glob, indirect_command, HeredocError
    names = {path.name for path in paths} | set(AGENT_CONFIG_LOCATIONS)
    try:
        inspected = tokens(command)
        indirect = indirect_command(inspected)
        if indirect is not None:
            return _violation("indirect-exec", indirect.raw)
        if protected_glob(inspected, names):
            return _violation("glob-protected", next(item.raw for item in inspected
                if any(fnmatch.fnmatchcase(name, component) for component in item.globs for name in names)))
        credential = _credential_text(command, root, resolve_symlinks)
        if credential: return _violation("credential-read", credential)
        rejected = _subcommands_violate(inspected, root, paths, scratch, resolve_symlinks)
        if rejected: return rejected
        substitution = any(item.substitution for item in inspected)
    except HeredocError as error:
        return _violation("heredoc-ambiguous", str(error))
    except (ValueError, IndexError):
        inspected, substitution = [], True
        for name in CREDENTIAL_LOCATIONS:
            if str(Path.home() / name) in command: return _violation("credential-read", str(Path.home() / name))
    protected_names = re.search(r"(?<![\w.-])(?:\./)?(?:\.git|\.orchestrator|inbox|outbox)(?:[/\s\"';`)\}]|$)", command)
    protected_text = protected_names or any(str(path) in command for path in paths)
    if substitution and (protected_text or inspected and not _opaque_git_reads(command)):
        return _violation("substitution", next((item.raw for item in inspected if item.substitution), command))
    try:
        if _read_only_shell(command): return _tolerated("read-only", command)
    except (ValueError, IndexError): pass
    if protected_names: return _violation("protected-name", protected_names.group().rstrip())
    if protected_text: return _violation("protected-path", next(str(path) for path in paths if str(path) in command))
    for name in AGENT_CONFIG_LOCATIONS:
        if name in command: return _violation("protected-name", name)
    opaque = False
    try:
        words = _shell_words(command, scratch)
    except ValueError:
        opaque = True
        indirect = re.search(r"(?<![\w.-])(?:eval|source)(?![\w-])|(?<![\w.-])(?:ba|z|fi|da)?sh\s+-\w*c", command)
        if indirect: return _violation("indirect-exec", indirect.group())
        if re.search(r"(?<![\w.-])git(?![\w-])", command):
            opaque_git = _opaque_git_violation(command)
            if opaque_git is not None: return _violation("opaque:git-not-read-only", opaque_git)
        try:
            words = _shell_words(command, scratch, allow_unknown=True)
        except ValueError:
            words = [item.value for item in inspected]
    for word in words:
        if Path(word).name in {"eval", "sh", "bash", "dash", "zsh", "fish", "source"}: return _violation("indirect-exec", word)
    rejected = _write_boundary(words, root, paths, scratch, resolve_symlinks)
    if rejected is not None and rejected.disposition == "violation": return rejected
    for index, word in enumerate(words):
        if Path(word).name != "git": continue
        tail = words[index+1:]
        tail = tail[:next((i for i, part in enumerate(tail) if part in {";", "&&", "||", "|", "&"}), len(tail))]
        if not _read_only_git(tail): return _violation("git-write", " ".join(_git_arguments(tail)) or "git")
    return rejected or _tolerated("opaque-harmless" if opaque else "within-boundary", command)


def explain_implementer_denial(denial: object, repository_root: Path | None, protected_paths: tuple[Path, ...] | None,
    scratch: Path | None = None, *, resolve_symlinks: bool = True) -> DenialClassification:
    """Denied writes outside the bound repo/scratch and credential reads stop."""
    if not isinstance(denial, dict) or repository_root is None or not protected_paths: return _violation("unbound", "denial or boundary unavailable")
    tool, data = denial.get("tool_name"), denial.get("tool_input", denial.get("input"))
    if not isinstance(data, dict): return _violation("invalid-input", "tool input is not an object")
    try:
        if tool in {"Edit", "Write", "NotebookEdit"}:
            value = data.get("notebook_path") if tool == "NotebookEdit" else data.get("file_path", data.get("path"))
            if not isinstance(value, str) or not value or "\x00" in value: return _violation("invalid-input", "missing or invalid write path")
            return _write_violation(value, repository_root, protected_paths, scratch, resolve_symlinks) or _tolerated("within-boundary", value)
        if tool in {"Read", "Glob", "Grep"}:
            value = data.get("file_path", data.get("path", str(repository_root)))
            pattern = data.get("pattern") if tool == "Glob" else data.get("glob")
            if pattern is not None:
                if not isinstance(value, str) or not isinstance(pattern, str): return _violation("invalid-input", "invalid search path or pattern")
                pattern = pattern.replace("${HOME}", str(Path.home())).replace("$HOME", str(Path.home()))
                if pattern.startswith("~"): pattern = str(Path.home()) + pattern[1:]
                value = str(Path(value) / pattern)
            if not isinstance(value, str): return _violation("invalid-input", "invalid read path")
            return _violation("credential-read", value) if _credential(value, repository_root, resolve_symlinks=resolve_symlinks) else _tolerated("harmless-read", value)
        command = data.get("command")
        if tool != "Bash" or not isinstance(command, str) or not command.strip() or "\x00" in command: return _violation("invalid-input", "unsupported tool or invalid command")
        return _classify_bash(command, repository_root, protected_paths, scratch, resolve_symlinks)
    except (ValueError, OSError, RuntimeError, TypeError) as error: return _violation("inspection-error", str(error))


def classify_implementer_denial(denial: object, repository_root: Path | None, protected_paths: tuple[Path, ...] | None,
    scratch: Path | None = None, *, resolve_symlinks: bool = True) -> str:
    """Compatibility API; diagnostics use the same decision path."""
    return explain_implementer_denial(denial, repository_root, protected_paths, scratch,
                                     resolve_symlinks=resolve_symlinks).disposition
