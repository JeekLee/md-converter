"""HWPX → Markdown converter.

HWPX is a ZIP archive. Body text lives in Contents/section*.xml.
This module mirrors rhwp's extract_document_markdown_with_images_native:
document-level paragraph traversal, not per-page render trees, so
cross-page tables appear exactly once in the output.

No external dependencies — stdlib only.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _q(local: str) -> str:
    return f"{{{_HP}}}{local}"


def _section_names(z: zipfile.ZipFile) -> list[str]:
    """Contents/section*.xml paths sorted by numeric index."""
    names = [n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)]
    return sorted(names, key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()))


def _escape_cell(s: str) -> str:
    """Escape | and collapse whitespace for a markdown table cell."""
    return re.sub(r"\s+", " ", s.replace("|", "\\|")).strip()


def _run_text(run: ET.Element) -> str:
    """Text from a single hp:run, filtering HWP control chars (≤ U+001F)."""
    parts = []
    for t in run.findall(_q("t")):
        if t.text:
            parts.append("".join(c for c in t.text if c > ""))
    return "".join(parts)


def _para_text(p: ET.Element) -> str:
    """Plain text from hp:p, skipping runs that carry table controls."""
    parts = []
    for run in p.findall(_q("run")):
        if run.find(_q("tbl")) is not None:
            continue  # table-bearing run; text here is just a placeholder char
        parts.append(_run_text(run))
    return "".join(parts)


# ── table helpers ──────────────────────────────────────────────────────────

def _cell_plain_text(tc: ET.Element) -> str:
    """Flat text from hp:tc — used inside [[NT:...]] inner cells."""
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    parts = []
    for p in sub.findall(_q("p")):
        t = _para_text(p).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _cell_text(tc: ET.Element) -> str:
    """Text from hp:tc, expanding any nested table as [[NT:r0c0|c1;r1c0|c1]]."""
    sub = tc.find(_q("subList"))
    if sub is None:
        return ""
    nested_tbl = sub.find(f".//{_q('tbl')}")
    if nested_tbl is not None:
        rows = []
        for tr in nested_tbl.findall(_q("tr")):
            row = "|".join(_cell_plain_text(tc2) for tc2 in tr.findall(_q("tc")))
            rows.append(row)
        return "[[NT:" + ";".join(rows) + "]]"
    parts = []
    for p in sub.findall(_q("p")):
        t = _para_text(p).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _table_to_md(tbl: ET.Element) -> str:
    """Convert hp:tbl to a GFM table string."""
    rows: list[list[str]] = []
    for tr in tbl.findall(_q("tr")):
        rows.append([_escape_cell(_cell_text(tc)) for tc in tr.findall(_q("tc"))])
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


# ── main entry ─────────────────────────────────────────────────────────────

def convert(data: bytes) -> str:
    """Convert HWPX bytes to Markdown.

    Images are omitted (text-only output). Tables are rendered as GFM tables.
    """
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in _section_names(z):
            with z.open(name) as f:
                root = ET.parse(f).getroot()
            for p in root.findall(_q("p")):
                tbl = p.find(f".//{_q('tbl')}")
                if tbl is not None:
                    md = _table_to_md(tbl)
                    if md:
                        parts.append(md)
                else:
                    text = _para_text(p).strip()
                    if text:
                        parts.append(text)
    return "\n\n".join(parts)
