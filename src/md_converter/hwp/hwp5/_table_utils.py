"""HWP5 table: records → GFM conversion."""
from __future__ import annotations

from .._common import _escape_cell


def _escape_cell_for_table(s: str) -> str:
    """Escape | for GFM, but leave [[NT:...]] markers intact.

    Mirrors hwpx/_table_utils._escape_cell_for_table so a nested-table marker
    embedded in a cell survives for extract_nested_tables() to parse.
    """
    if "[[NT:" in s:
        return s
    return _escape_cell(s)


def table_to_md(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        escaped = [_escape_cell_for_table(c) for c in padded]
        lines.append("| " + " | ".join(escaped) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


def _serialize_nt(rows: list[list[str]]) -> str:
    """Serialize a depth-1 nested table to the shared [[NT:...]] marker.

    Returns "" when the table has no non-blank content.
    """
    if not any(cell.strip() for row in rows for cell in row):
        return ""
    return "[[NT:" + ";".join("|".join(row) for row in rows) + "]]"


def _serialize_flat(rows: list[list[str]]) -> str:
    """Flatten a deeply-nested (depth >= 2) table to plain space-joined text."""
    return " ".join(cell.strip() for row in rows for cell in row if cell.strip())
