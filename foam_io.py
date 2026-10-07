"""Small, comment-aware ASCII dictionary helpers; no OpenFOAM dependency.

This is not a preprocessor. Included/generated boundary dictionaries are refused
when editing so their meaning cannot silently change.
"""
from dataclasses import dataclass
import math
from pathlib import Path
import os
import re
import stat
import tempfile

_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[{}();]|[^\s{}();"]+', re.S)


def tokens(text):
    return [m for m in _TOKEN.finditer(text) if not m[0].startswith(('//', '/*'))]


def matching(items, index):
    pairs = {'{': '}', '(': ')'}
    stack = []
    for i in range(index, len(items)):
        value = items[i][0]
        if value in pairs:
            stack.append(pairs[value])
        elif value in ('}', ')'):
            if not stack or stack.pop() != value:
                raise ValueError('Unbalanced OpenFOAM dictionary delimiters.')
            if not stack:
                return i
    raise ValueError('Unterminated OpenFOAM dictionary block.')


@dataclass
class Entry:
    name: str
    start: int
    end: int
    value_start: int
    value_end: int
    block: bool


def entries(text):
    """Top-level dictionary entries, with character spans into original text."""
    items = tokens(text)
    result = {}
    i = 0
    while i < len(items):
        key = items[i]
        if key[0].startswith('#'):
            raise ValueError('Included or generated dictionaries require manual editing.')
        if key[0] in '{}();':
            raise ValueError(f'Unexpected dictionary token: {key[0]}')
        i += 1
        if i >= len(items):
            raise ValueError(f'Missing value for {key[0]}.')
        start = items[i].start()
        block = items[i][0] == '{'
        if block:
            end = matching(items, i)
            value_end = items[end].end()
        else:
            end = i
            while end < len(items) and items[end][0] != ';':
                if items[end][0] in ('{', '('):
                    end = matching(items, end)
                elif items[end][0] in ('}', ')'):
                    raise ValueError(f'Missing semicolon for {key[0]}.')
                end += 1
            if end == len(items):
                raise ValueError(f'Missing semicolon for {key[0]}.')
            value_end = items[end].start()
        name = key[0].strip('"')
        if name in result:
            raise ValueError(f'Duplicate dictionary entry: {name}')
        result[name] = Entry(name, key.start(), items[end].end(), start, value_end, block)
        i = end + 1
        if block and i < len(items) and items[i][0] == ';':
            i += 1
    return result


def value(text, name):
    item = entries(text).get(name)
    if item is None:
        raise ValueError(f'Missing dictionary entry: {name}')
    return text[item.value_start:item.value_end].strip()


def scalar(text, name):
    item = value(text, name)
    parts = tokens(item)
    if len(parts) != 1:
        raise ValueError(f'{name} must be an explicit numeric scalar.')
    number = float(parts[0][0])
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite.')
    return number


def update_values(text, updates):
    parsed = entries(text)
    replacements = []
    for name, new in updates.items():
        item = parsed.get(name)
        if item is not None:
            if item.block:
                raise ValueError(f'{name} is a dictionary, not a scalar entry.')
            replacements.append((item.value_start, item.value_end, str(new)))
        else:
            text += f'\n{name} {new};\n'
    for start, end, new in sorted(replacements, reverse=True):
        text = text[:start]+new+text[end:]
    return text


def replace_block(text, name, body):
    item = entries(text).get(name)
    block = '{\n'+body.strip()+'\n}'
    if item is None:
        return text+f'\n{name}\n{block}\n'
    if not item.block:
        raise ValueError(f'{name} must be a dictionary.')
    return text[:item.value_start]+block+text[item.value_end:]


def update_nested(text, path, updates):
    if not path:
        return update_values(text, updates)
    body = value(text, path[0])
    if not body.startswith('{'):
        raise ValueError(f'{path[0]} must be a dictionary.')
    return replace_block(text, path[0], update_nested(body[1:-1], path[1:], updates))


def boundary_patches(path):
    text = Path(path).read_text(encoding='utf-8')
    items = tokens(text)
    # Skip the FoamFile dictionary before the numbered patch list.
    i = 0
    if items and items[0][0] == 'FoamFile':
        i = matching(items, 1) + 1
    if i + 1 >= len(items) or not items[i][0].isdigit() or items[i+1][0] != '(':
        raise ValueError('Expected an ASCII, numbered OpenFOAM boundary patch list.')
    count = int(items[i][0])
    end = matching(items, i+1)
    content = text[items[i+1].end():items[end].start()]
    patches = []
    for name, item in entries(content).items():
        if not item.block:
            raise ValueError(f'Boundary patch {name} must be a dictionary.')
        body = content[item.value_start+1:item.value_end-1]
        patches.append({'name': name, 'type': value(body, 'type').strip('"')})
    if len(patches) != count:
        raise ValueError('Boundary patch count does not match its list.')
    return patches


def atomic_write(path, text):
    """Replace a complete file only after its new contents have been written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        options = {} if isinstance(text, bytes) else {'encoding': 'utf-8', 'newline': '\n'}
        with os.fdopen(descriptor, 'wb' if isinstance(text, bytes) else 'w', **options) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_case_files(case_dir, contents):
    """Keep a unique backup of every existing target before any file is changed."""
    case = Path(case_dir).resolve()
    if not case.is_dir():
        raise ValueError('Select an existing case folder first.')
    targets = {}
    for relative, text in contents.items():
        path = (case/relative).resolve()
        if not path.is_relative_to(case):
            raise ValueError('Output file must remain inside the selected case.')
        targets[path] = text
    existing = {p: p.read_bytes() for p in targets if p.exists()}
    backup = None
    if existing:
        base = case/'.ofsolvers-backups'
        base.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='edit-', dir=base))
        for path, text in existing.items():
            atomic_write(backup/path.relative_to(case), text)
    changed = []
    try:
        for path, text in targets.items():
            atomic_write(path, text)
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            if path in existing:
                atomic_write(path, existing[path])
            else:
                path.unlink(missing_ok=True)
        raise
    return backup
