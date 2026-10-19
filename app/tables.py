"""Markdown table helpers. Tables travel through the app as Markdown (docling exports them that way)."""
import re

SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def parse(md):
    """Markdown table -> list of rows (header first), separator row dropped. Non-table lines are ignored."""
    rows = []
    for line in md.strip().splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(SEPARATOR_CELL.match(c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def render(rows):
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def shape(rows):
    return [len(r) for r in rows]


def split(md, max_words):
    """Split a long table into smaller tables of whole rows, each repeating the header row."""
    rows = parse(md)
    if not rows:
        return [md]
    header, body = rows[0], rows[1:]
    chunks, current, words = [], [], 0
    for row in body:
        n = sum(len(c.split()) for c in row)
        if current and words + n > max_words:
            chunks.append(current)
            current, words = [], 0
        current.append(row)
        words += n
    if current or not chunks:
        chunks.append(current)
    return [render([header] + c) for c in chunks]
