import re

_SEQ_RE = re.compile(r'note (?:\(slow \d+ )?"<(.*?)>"')


def top_level_entries(seq: str) -> list[str]:
    """Split a `<...>` body into its top-level entries."""
    entries, depth, current = [], 0, ""
    for ch in seq:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == " " and depth == 0:
            if current:
                entries.append(current)
            current = ""
        else:
            current += ch
    if current:
        entries.append(current)
    return entries


def note_numbers(expr: str) -> list[int]:
    notes = []
    for body in re.findall(r'note (?:\(slow \d+ )?"([^"]*)"', expr):
        notes.extend(int(n) for n in re.findall(r"-?\d+", body))
    return notes


def sequence(expr: str) -> list[str]:
    m = _SEQ_RE.search(expr)
    assert m, expr
    return top_level_entries(m.group(1))
