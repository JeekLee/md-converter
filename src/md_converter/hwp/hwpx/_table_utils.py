"""HWPX table: XML → GFM conversion."""
from __future__ import annotations

from xml.etree import ElementTree as ET

from .._common import _escape_cell
from ...nested_tables import serialize_nested_table
from ._xml import _para_text, _q


def _cell_plain_text(tc: ET.Element) -> str:
    """Flat text from hp:tc — used for inner cells of nested tables.

    A table nested two or more levels deep (a tbl inside this inner cell) is
    flattened to plain text here rather than dropped, so depth>=2 content is
    preserved — matching the HWP5 backend and the spec depth>=2 behaviour.
    """
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    parts = []
    for p in sub.findall(_q("p")):
        tbl = p.find(f".//{_q('tbl')}")
        if tbl is not None:
            for tr in tbl.findall(_q("tr")):
                for tc2 in tr.findall(_q("tc")):
                    t = _cell_plain_text(tc2).strip()
                    if t:
                        parts.append(t)
        else:
            t = _para_text(p).strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _cell_text(tc: ET.Element) -> str:
    """Text from hp:tc, expanding nested tables as [[NT:r0c0|c1;r1c0|c1]].

    Paragraphs are joined with ' <br> ' to keep multi-paragraph cell content
    intact inside a GFM table cell.
    """
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    parts = []
    for p in sub.findall(_q("p")):
        tbl = p.find(f".//{_q('tbl')}")
        if tbl is not None:
            rows = []
            for tr in tbl.findall(_q("tr")):
                rows.append([_cell_plain_text(tc2) for tc2 in tr.findall(_q("tc"))])
            marker = serialize_nested_table(rows)
            if marker:
                parts.append(marker)
        else:
            t = _para_text(p).strip()
            if t:
                parts.append(t)
    return " <br> ".join(parts)


def _escape_cell_for_table(s: str) -> str:
    """Escape | for GFM, but leave [[NT:...]] markers intact."""
    if "[[NT:" in s or "[[NT64:" in s:
        return s
    return _escape_cell(s)


def table_to_md(tbl: ET.Element) -> str:
    """Convert hp:tbl to a GFM table string."""
    rows: list[list[str]] = []
    for tr in tbl.findall(_q("tr")):
        rows.append([_escape_cell_for_table(_cell_text(tc)) for tc in tr.findall(_q("tc"))])
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(padded) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in padded) + "|")
    return "\n".join(lines)
