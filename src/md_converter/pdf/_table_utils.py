"""GFM table utilities for PDF extraction — ported from md_table_merge.py."""
from __future__ import annotations

import re


# ── raw cell → GFM cell text ──────────────────────────────────────────────────

_CJK = re.compile(r"[가-힣一-鿿㐀-䶿]")


def _join_lines(text: str) -> str:
    """Join PDF line-wrap newlines inside a cell.

    PDF column-wrap splits lines mid-word (CJK↔CJK, no space before \\n)
    or at word boundaries. Heuristic: if the last char before \\n is CJK and
    the first char after \\n is CJK, join without space (mid-word break);
    otherwise join with a space.
    """
    parts = text.split("\n")
    if len(parts) == 1:
        return text
    out = parts[0]
    for part in parts[1:]:
        if out and _CJK.search(out[-1]) and part and _CJK.search(part[0]):
            out = out + part
        else:
            out = out.rstrip() + " " + part.lstrip()
    return out


def _clean_cell(cell: str | None) -> str:
    """Cell text cleaning WITHOUT pipe escaping: join PDF line-wraps + remove
    CJK char-spacing artifacts. Used by _cell_text and by serialize_nt."""
    if cell is None:
        return ""
    text = _join_lines(cell)
    # Remove character-level spacing artifacts: "다 음" → "다음"
    # Only fires when each CJK char is individually space-separated (no multi-char words involved)
    text = re.sub(
        r"(?<![가-힣])([가-힣])( [가-힣])+(?![가-힣])",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )
    return text.strip()


def _cell_text(cell: str | None) -> str:
    return _clean_cell(cell).replace("|", "\\|")


def serialize_nt(rows: list[list[str | None]]) -> str:
    """Serialize a nested sub-table's rows to the shared [[NT:row;row]] marker.

    Cells are cleaned (line-join + CJK spacing) but NOT pipe-escaped, since
    '|' and ';' are the marker's own separators. Returns '' if all cells blank.
    """
    if not any((c or "").strip() for row in rows for c in row):
        return ""
    return "[[NT:" + ";".join("|".join(_clean_cell(c) for c in row) for row in rows) + "]]"


def bbox_in_cell(
    sub_bbox: tuple[float, float, float, float],
    cell_bbox: tuple[float, float, float, float],
    tol: float = 2.0,
) -> bool:
    """True if sub_bbox is fully inside cell_bbox within tol. bbox = (x0, top, x1, bottom)."""
    sx0, st, sx1, sb = sub_bbox
    cx0, ct, cx1, cb = cell_bbox
    return sx0 >= cx0 - tol and sx1 <= cx1 + tol and st >= ct - tol and sb <= cb + tol


def bbox_area(b: tuple[float, float, float, float]) -> float:
    """Area of a bbox (x0, top, x1, bottom); clamped non-negative."""
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bbox_near_equal(a, b, margin: float = 3.0) -> bool:
    """True if two bboxes match within margin on all four coords (a duplicate
    region, NOT a nesting relationship)."""
    return all(abs(a[i] - b[i]) <= margin for i in range(4))


def _escape_cell_for_table(s: str | None) -> str:
    """Leave [[NT:...]] marker cells intact (so the marker survives); escape others."""
    if s is not None and "[[NT:" in s:
        return s
    return _cell_text(s)


# ── GFM table rendering ───────────────────────────────────────────────────────

def table_to_md(rows: list[list[str | None]]) -> str:
    """Convert a pdfplumber row list to a GFM markdown table string."""
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    norm = []
    for row in rows:
        cells = [_escape_cell_for_table(c) for c in row]
        while len(cells) < col_count:
            cells.append("")
        norm.append(cells)

    header = "| " + " | ".join(norm[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join([header, sep] + body)


# ── merge-overflow helpers (operate on rendered GFM strings) ──────────────────

def _col_count(table_md: str) -> int:
    first = table_md.splitlines()[0] if table_md else ""
    if not first.strip():
        return 0
    return max(0, len(first.split("|")) - 2)


def _header_cells(table_md: str) -> list[str]:
    first = table_md.splitlines()[0] if table_md else ""
    return [c.strip() for c in first.split("|")[1:-1]]


def _clean_table_block(block: list[str]) -> list[str]:
    """Remove duplicate sub-header rows and merge wrapped cell lines."""
    if len(block) < 3:
        return list(block)
    sub_hdr = block[2].strip()
    result: list[str] = list(block[:3])
    for row in block[3:]:
        if row.strip() == sub_hdr:
            continue
        parts = row.split("|")
        if len(parts) >= 3 and not parts[1].strip() and result:
            prev = result[-1].split("|")
            if len(prev) == len(parts):
                merged_cells = [prev[0]]
                for pp, cp in zip(prev[1:-1], parts[1:-1]):
                    if cp.strip():
                        merged_cells.append(
                            (pp.rstrip() + " " + cp.strip()) if pp.strip() else cp.strip()
                        )
                    else:
                        merged_cells.append(pp)
                merged_cells.append(prev[-1])
                result[-1] = "|".join(merged_cells)
                continue
        result.append(row)
    return result


def merge_overflow_tables(md: str) -> str:
    """Merge adjacent overflow / continuation / duplicate GFM tables.

    - overflow:    B has more columns and the first N headers match A → append overflow values to A
    - exact_duplicate: identical header+data → drop B
    - continuation: same header, separated by blank/image lines → concatenate data rows
    - subtable:    B headers already appear inside A's last cell → drop B
    """
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue

        block_a: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block_a.append(lines[i])
            i += 1

        gap: list[str] = []
        j = i
        while j < len(lines) and (
            not lines[j].strip() or lines[j].strip().startswith("![")
        ):
            gap.append(lines[j])
            j += 1

        if j < len(lines) and lines[j].lstrip().startswith("|"):
            block_b: list[str] = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                block_b.append(lines[j])
                j += 1

            na = _col_count("\n".join(block_a))
            nb = _col_count("\n".join(block_b))
            ha = _header_cells("\n".join(block_a))
            hb = _header_cells("\n".join(block_b))
            tb_data = [l for l in block_b[2:] if l.strip()]

            is_overflow = (
                nb > na and na >= 2 and
                len(ha) == na and len(hb) == nb and
                hb[:na] == ha and bool(ha) and
                all(c == ha[-1] for c in hb[na:]) and
                bool(tb_data) and
                all(
                    all(not c.strip() for c in row.split("|")[1:-1][:na - 1])
                    for row in tb_data
                )
            )

            a_data = [l.strip() for l in block_a[2:] if l.strip()]
            b_data = [l.strip() for l in block_b[2:] if l.strip()]
            is_exact_duplicate = (
                not is_overflow and
                nb == na and na >= 2 and
                ha == hb and bool(ha) and
                bool(a_data) and a_data == b_data
            )
            is_continuation = (
                not is_overflow and not is_exact_duplicate and
                nb == na and na >= 2 and
                ha == hb and bool(ha) and
                bool(gap) and bool(tb_data)
            )
            a_last_cell = block_a[-1].split("|")[1:-1][-1].strip() if block_a else ""
            is_subtable = (
                not is_overflow and not is_exact_duplicate and not is_continuation and
                nb > na and na >= 2 and
                bool(a_last_cell) and bool(hb) and
                sum(1 for h in hb[:3] if h and h in a_last_cell) >= 2
            )

            if is_overflow:
                overflow_vals = [
                    c.strip()
                    for row in tb_data
                    for c in row.split("|")[1:-1][na - 1:]
                    if c.strip()
                ]
                if overflow_vals:
                    cells = block_a[-1].split("|")
                    cells[-2] = cells[-2].rstrip() + " " + " ".join(overflow_vals)
                    block_a[-1] = "|".join(cells)
                out.extend(_clean_table_block(block_a))
                i = j
            elif is_exact_duplicate:
                out.extend(_clean_table_block(block_a))
                i = j
            elif is_continuation:
                merged = list(block_a) + list(block_b[2:])
                out.extend(_clean_table_block(merged))
                i = j
            elif is_subtable:
                out.extend(_clean_table_block(block_a))
                i = j
            else:
                out.extend(_clean_table_block(block_a))
                out.extend(gap)
                out.extend(_clean_table_block(block_b))
                i = j
        else:
            out.extend(_clean_table_block(block_a))
            out.extend(gap)
            i = j

    return "\n".join(out)
