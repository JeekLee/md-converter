"""Separate nested tables ([[NT:...]] markers) into standalone GFM tables.

A nested table inside a parent cell is emitted by the HWPX/HWP5 parsers as a
flat marker:  [[NT:r0c0|r0c1;r1c0|r1c1]]  (';' = row, '|' = cell).

extract_nested_tables() replaces each marker with a human-readable reference
"→ 표 N" in the parent cell and appends the nested table as a standalone GFM
table ("**[표 N]**" + table) right after the parent block.  No LLM involved.
"""
from __future__ import annotations

import base64
import json
import re

_NT_OPEN = "[[NT:"
_NT64_OPEN = "[[NT64:"


def _escape(cell: str) -> str:
    """Collapse whitespace and escape pipes for a GFM cell (matches _escape_cell)."""
    return re.sub(r"\s+", " ", cell.replace("|", "\\|")).strip()


def _parse_nt(content: str) -> list[list[str]]:
    """Parse marker inner text 'r0c0|r0c1;r1c0|r1c1' into rows of cells."""
    return [row.split("|") for row in content.split(";")]


def _parse_nt64(content: str) -> list[list[str]]:
    padded = content + "=" * (-len(content) % 4)
    data = base64.urlsafe_b64decode(padded.encode("ascii"))
    rows = json.loads(data.decode("utf-8"))
    if not isinstance(rows, list):
        return []
    return [
        ["" if cell is None else str(cell) for cell in row]
        for row in rows
        if isinstance(row, list)
    ]


def serialize_nested_table(rows: list[list[object]]) -> str:
    """Serialize nested-table rows into a delimiter-safe marker."""
    normalized = [["" if cell is None else str(cell) for cell in row] for row in rows]
    if _is_empty(normalized):
        return ""
    data = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return f"{_NT64_OPEN}{token}]]"


def _is_empty(rows: list[list[str]]) -> bool:
    return not any(cell.strip() for row in rows for cell in row)


def _to_gfm(rows: list[list[str]]) -> str:
    """Render parsed rows as a GFM table (first row = header)."""
    col_count = max((len(r) for r in rows), default=0)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(_escape(c) for c in padded) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)


def extract_nested_tables(md: str) -> str:
    """Replace [[NT:...]] markers with '→ 표 N' refs + standalone tables.

    Standalone tables are inserted as separate blocks right after the block
    that contained the marker.  Numbering is a single document-wide counter.
    """
    if _NT_OPEN not in md and _NT64_OPEN not in md:
        return md

    out: list[str] = []
    counter = 0
    for block in md.split("\n\n"):
        if _NT_OPEN not in block and _NT64_OPEN not in block:
            out.append(block)
            continue
        result: list[str] = []
        extracted: list[str] = []
        remaining = block
        while _NT_OPEN in remaining or _NT64_OPEN in remaining:
            legacy_start = remaining.find(_NT_OPEN)
            safe_start = remaining.find(_NT64_OPEN)
            starts = [s for s in (legacy_start, safe_start) if s != -1]
            start = min(starts)
            marker_open = _NT64_OPEN if start == safe_start else _NT_OPEN
            result.append(remaining[:start])
            after = remaining[start + len(marker_open):]
            end = after.find("]]")
            if end == -1:                       # malformed: keep verbatim, stop
                result.append(remaining[start:])
                remaining = ""
                break
            try:
                rows = (
                    _parse_nt64(after[:end])
                    if marker_open == _NT64_OPEN
                    else _parse_nt(after[:end])
                )
            except Exception:
                result.append(remaining[start:start + len(marker_open) + end + 2])
                remaining = after[end + 2:]
                continue
            if not _is_empty(rows):
                counter += 1
                result.append(f"→ 표 {counter}")
                extracted.append(f"**[표 {counter}]**\n\n{_to_gfm(rows)}")
            remaining = after[end + 2:]
        # Parent-cell text is emitted verbatim — it is NOT run through _escape.
        # Escaping pipes in the surrounding cell content is the caller/parser's responsibility;
        # only the extracted standalone table cells are escaped (via _escape above).
        result.append(remaining)
        out.append("".join(result))
        out.extend(extracted)
    return "\n\n".join(out)
