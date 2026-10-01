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


def tokens(text):
    result, word, subcommands, quote, index, substitution, indirect = [], [], [], None, 0, False, False
    def flush():
        nonlocal substitution, indirect
        if not word: return
        raw = ''.join(word)
        if substitution:
            value = raw
        else:
            # ANSI-C quotes are decoded as data, never executed.
            import re
            cooked = re.sub(r"\$'((?:[^'\\]|\\.)*)'", lambda m: shlex.quote(codecs.decode(m[1], 'unicode_escape')), raw)
            values = shlex.split(cooked)
            if len(values) != 1: raise ValueError('ambiguous shell word')
            value = values[0]
        result.append(Token(value, raw, substitution=substitution, indirect=indirect, globs=_glob_components(raw), subcommands=tuple(subcommands)))
        word.clear()
        subcommands.clear()
        substitution, indirect = False, False
    while index < len(text):
        char = text[index]
        if char == '\\' and quote != "'":
            if index + 1 >= len(text): raise ValueError('incomplete escape')
            word.extend(text[index:index+2]); indirect = True; index += 2; continue
        if quote != "'" and (char == '`' or text[index:index+2] in {'$(', '<(', '>('}):
            end = _substitution_end(text, index)
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
            if depth: raise ValueError('incomplete expansion')
            word.append(text[index:end]); index = end; continue
        elif char == '#' and not word:
            end = text.find('\n', index)
            if end == -1: break
            index = end; continue
        elif char.isspace():
            flush()
            if char == '\n': result.append(Token(';', ';', operator=True))
        elif char in ';&|()<>':
            flush(); end = index + 1
            while end < len(text) and text[end] in ';&|()<>': end += 1
            result.append(Token(text[index:end], text[index:end], operator=True)); index = end; continue
        else:
            word.append(char)
        index += 1
    if quote is not None: raise ValueError('unterminated quote')
    flush()
    return result


def protected_glob(items, names):
    return any(fnmatch.fnmatchcase(name, component) for item in items for component in item.globs for name in names)


def command_indirection(items):
    start = True
    for item in items:
        if item.operator:
            if item.value in {';', '&&', '||', '|', '&', '('}: start = True
            continue
        if start and (item.value in {'if', 'then', 'else', 'elif', '!', 'fi'} or re.match(r"^[A-Za-z_]\w*=", item.value) and not item.indirect): continue
        if start and item.indirect: return True
        start = False
    return False
