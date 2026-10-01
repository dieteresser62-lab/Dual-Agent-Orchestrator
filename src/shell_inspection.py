"""Quote-aware shell inspection; no shell text is ever evaluated."""
from dataclasses import dataclass
import codecs
import fnmatch
import shlex
import re


@dataclass(frozen=True)
class Token:
    value: str
    raw: str
    operator: bool = False
    substitution: bool = False
    indirect: bool = False
    globs: tuple[str, ...] = ()
    subcommands: tuple[str, ...] = ()


class HeredocError(ValueError):
    """A heredoc cannot be interpreted unambiguously without executing it."""


def _heredoc_subcommands(body):
    # Quotes in an unquoted heredoc body are data, not shell quoting. Only
    # backslash escapes suppress expansion there (Bash's here-document rules).
    commands, index = [], 0
    while index < len(body):
        if body[index] == '\\' and index + 1 < len(body) and body[index + 1] in '\\$`\n':
            index += 2
        elif body[index] == '`' or body[index:index + 2] == '$(':
            end = _substitution_end(body, index)
            commands.append(body[index + (1 if body[index] == '`' else 2):end - 1])
            index = end
        else:
            index += 1
    return tuple(commands)


def _substitution_end(text, start):
    if text[start] == '`':
        index = start + 1
        while index < len(text):
            if text[index] == '\\':
                index += 2
            elif text[index] == '`':
                return index + 1
            else:
                index += 1
        raise ValueError('unterminated backtick')
    index, depth, quote = start + 2, 1, None
    while index < len(text):
        char = text[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if char in {'"', "'"}:
            quote = None if char == quote else char if quote is None else quote
        elif quote is None:
            depth += (char == '(') - (char == ')')
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError('unterminated substitution')


def _glob_components(raw):
    components, word, quote, active, index = [], [], None, False, 0
    while index < len(raw):
        char = raw[index]
        if char == '\\' and quote != "'":
            index += 1
            if index < len(raw): word.append(raw[index])
        elif char in {'"', "'"}:
            quote = None if char == quote else char if quote is None else quote
            if quote is not None and char != quote: word.append(char)
        elif char == '/':
            if active: components.append(''.join(word))
            word, active = [], False
        else:
            word.append(char)
            active |= quote is None and char in '*?['
        index += 1
    if active: components.append(''.join(word))
    return tuple(components)


def _inspect(text):
    result, word, subcommands, quote, index, substitution, indirect = [], [], [], None, 0, False, False
    pending, spans, delimiter = [], [], None
    def invalid(message):
        return (HeredocError if pending or delimiter is not None else ValueError)(message)

    def flush():
        nonlocal substitution, indirect, delimiter
        if not word: return
        raw = ''.join(word)
        if substitution:
            value = raw
        else:
            # ANSI-C quotes are decoded as data, never executed.
            import re
            cooked = re.sub(r"\$'((?:[^'\\]|\\.)*)'", lambda m: shlex.quote(codecs.decode(m[1], 'unicode_escape')), raw)
            try:
                values = shlex.split(cooked)
            except ValueError as error:
                raise invalid('ambiguous shell word') from error
            if len(values) != 1: raise invalid('ambiguous shell word')
            value = values[0]
        if delimiter is not None:
            if substitution or not value or '\n' in raw:
                raise HeredocError('ambiguous heredoc delimiter')
            start, strip_tabs = delimiter
            quoted = any(char in raw for char in "'\"\\")
            pending.append((value, strip_tabs, quoted))
            spans.append((start, index, quoted))
            delimiter = None
        result.append(Token(value, raw, substitution=substitution, indirect=indirect, globs=_glob_components(raw), subcommands=tuple(subcommands)))
        word.clear()
        subcommands.clear()
        substitution, indirect = False, False
    while index < len(text):
        char = text[index]
        if char == '\\' and quote != "'":
            if index + 1 >= len(text): raise invalid('incomplete escape')
            word.extend(text[index:index+2]); indirect = True; index += 2; continue
        if quote != "'" and (char == '`' or text[index:index+2] in {'$(', '<(', '>('}):
            if delimiter is not None: raise HeredocError('ambiguous heredoc delimiter')
            try:
                end = _substitution_end(text, index)
            except ValueError as error:
                raise invalid('unterminated substitution') from error
            subcommands.append(text[index + (1 if char == "`" else 2):end - 1])
            word.append(text[index:end]); substitution = indirect = True; index = end; continue
        if quote is None and text[index:index+2] == "$'":
            word.append('$'); indirect = True; index += 1; char = "'"
        if char in {'"', "'"}:
            quote = None if char == quote else char if quote is None else quote
            word.append(char)
        elif quote is not None:
            word.append(char)
        elif text[index:index+2] == '${':
            end, depth = index + 2, 1
            while end < len(text) and depth:
                depth += (text[end] == '{') - (text[end] == '}'); end += 1
            if depth: raise invalid('incomplete expansion')
            word.append(text[index:end]); index = end; continue
        elif char == '#' and not word:
            end = text.find('\n', index)
            if end == -1: break
            index = end; continue
        elif char.isspace():
            flush()
            if char == '\n':
                if delimiter is not None: raise HeredocError('missing heredoc delimiter')
                cursor = index + 1
                for marker, strip_tabs, quoted in pending:
                    start = cursor
                    while cursor < len(text):
                        end = text.find('\n', cursor)
                        end = len(text) if end == -1 else end
                        line = text[cursor:end]
                        if (line.lstrip('\t') if strip_tabs else line) == marker:
                            body = text[start:cursor]
                            cursor = end + (end < len(text))
                            spans.append((start, cursor, quoted))
                            if not quoted:
                                try:
                                    commands = _heredoc_subcommands(body)
                                except ValueError as error:
                                    raise HeredocError('ambiguous expanding heredoc') from error
                                if commands:
                                    result.append(Token(body, body, substitution=True, subcommands=commands))
                            break
                        cursor = end + (end < len(text))
                    else:
                        raise HeredocError('unterminated heredoc')
                pending.clear()
                index = cursor - 1
                result.append(Token(';', ';', operator=True))
        elif char in ';&|()<>':
            flush()
            if delimiter is not None: raise HeredocError('missing heredoc delimiter')
            end = index + 1
            if text[index:index + 2] == '<<' and text[index:index + 3] != '<<<':
                end = index + (3 if text[index:index + 3] == '<<-' else 2)
                delimiter = (index, end - index == 3)
            else:
                while end < len(text) and text[end] in ';&|()<>': end += 1
            result.append(Token(text[index:end], text[index:end], operator=True)); index = end; continue
        else:
            word.append(char)
        index += 1
    if quote is not None:
        if delimiter is not None or pending: raise HeredocError('unterminated heredoc quote')
        raise ValueError('unterminated quote')
    flush()
    if pending or delimiter is not None: raise HeredocError('unterminated heredoc')
    return result, spans


def tokens(text):
    return _inspect(text)[0]


def quoted_heredoc_layout(text):
    """Remove validated quoted heredoc redirects/data, retaining offsets."""
    _, spans = _inspect(text)
    if any(not quoted for _, _, quoted in spans):
        raise HeredocError('expanding heredoc requires separate inspection')
    layout = list(text)
    for start, end, _ in spans:
        layout[start:end] = ['\n' if char == '\n' else ' ' for char in text[start:end]]
    return ''.join(layout)


def protected_glob(items, names):
    return any(fnmatch.fnmatchcase(name, component) for item in items for component in item.globs for name in names)


def indirect_command(items):
    start = True
    for item in items:
        if item.operator:
            if item.value in {';', '&&', '||', '|', '&', '('}: start = True
            continue
        if start and (item.value in {'if', 'then', 'else', 'elif', '!', 'fi'} or re.match(r"^[A-Za-z_]\w*=", item.value) and not item.indirect): continue
        if start and item.indirect: return item
        start = False
    return None


def command_indirection(items):
    return indirect_command(items) is not None
